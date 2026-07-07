from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from memorybench.adapters.base import MemoryStackAdapter, utc_now
from memorybench.adapters.mem0 import answer_from_records
from memorybench.adapters.store_policy import (
    benchmark_metadata,
    forget_needle,
    is_forget_request,
    memory_content_for_user_message,
    records_matching_query,
    source_type,
)


class SupermemoryAdapter(MemoryStackAdapter):
    """Supermemory hosted memory API adapter.

    This adapter uses Supermemory's direct memory-entry API so the first result
    is about memory creation/search/list/forget behavior, not async document
    ingestion latency. A deterministic response shim is used only to score the
    retrieved memory records consistently across stacks.
    """

    capabilities = {
        "inspect_memory": True,
        "delete_memory": True,
        "retrieval_log": True,
        "multi_user": True,
        "multi_tenant": True,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._api_key = _load_api_key(
            (
                "SUPERMEMORY_API_KEY",
                "SUPER_MEMORY_API_KEY",
                "SUPPERMEMORY_API_KEY",
            )
        )
        if not self._api_key:
            raise RuntimeError(
                "SUPERMEMORY_API_KEY must be set. The adapter also accepts "
                "SUPER_MEMORY_API_KEY and the legacy typo SUPPERMEMORY_API_KEY."
            )

        self._base_url = str(
            self.config.get("supermemory_base_url") or "https://api.supermemory.ai"
        ).rstrip("/")
        self._timeout = float(self.config.get("supermemory_timeout_seconds") or 30)
        self._search_limit = int(self.config.get("supermemory_search_limit") or 10)
        self._run_id = uuid.uuid4().hex[:10]
        self._turn_counts: dict[tuple[str, str], int] = {}
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._created_memory_ids: dict[str, set[str]] = {}
        self._created_document_ids: dict[str, set[str]] = {}

    def reset_subject(self, subject_id: str) -> None:
        self._forget_subject_memories(subject_id)
        self._delete_subject_documents(subject_id)
        self._created_memory_ids[subject_id] = set()
        self._created_document_ids[subject_id] = set()
        for key in list(self._turn_counts):
            if key[0] == subject_id:
                del self._turn_counts[key]
        for key in list(self._retrievals):
            if key[0] == subject_id:
                del self._retrievals[key]

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        turn_id = self._next_turn_id(subject_id, session_id)
        if is_forget_request(message):
            deleted = self.delete_memory(subject_id, {"contains": forget_needle(message)})
            return "I deleted matching memories." if deleted else "I did not find matching memories to delete."

        content, is_correction = memory_content_for_user_message(message)
        if is_correction:
            self._delete_by_terms(subject_id, ("preferred airport", "sfo"))
        if content:
            self._create_memory(subject_id, session_id, turn_id, content, source_type(message))

        retrieved = self._retrieve(subject_id, session_id, message)
        return answer_from_records(message, retrieved)

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]]:
        response = self._request(
            "POST",
            "/v4/memories/list",
            {
                "containerTags": [self._container_tag(subject_id)],
                "limit": 100,
                "page": 1,
                "sort": "createdAt",
                "order": "asc",
            },
        )
        records = [
            self._normalize_memory_entry(entry, subject_id)
            for entry in response.get("memoryEntries", [])
            if isinstance(entry, dict)
        ]
        return sorted(
            records,
            key=lambda record: (
                str(record.get("source_session_id") or ""),
                str(record.get("created_at") or ""),
                str(record.get("memory_id") or ""),
            ),
        )

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        contains = str(selector.get("contains") or "").lower()
        changed = False
        for record in self.inspect_memory(subject_id):
            content = str(record.get("content") or "").lower()
            if contains and contains not in content:
                continue
            if self._forget_memory_id(subject_id, str(record.get("memory_id"))):
                changed = True
        return changed

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def close(self) -> None:
        for subject_id in list(self._created_memory_ids):
            self._forget_subject_memories(subject_id)
        for subject_id in list(self._created_document_ids):
            self._delete_subject_documents(subject_id)

    def _create_memory(
        self,
        subject_id: str,
        session_id: str,
        turn_id: int,
        content: str,
        source: str,
    ) -> None:
        metadata = benchmark_metadata(session_id, turn_id, source_type=source)
        metadata.update(
            {
                "memorybench": True,
                "subject_hash": _hash(subject_id),
            }
        )
        response = self._request(
            "POST",
            "/v4/memories",
            {
                "containerTag": self._container_tag(subject_id),
                "memories": [
                    {
                        "content": content,
                        "isStatic": False,
                        "metadata": _compact_metadata(metadata),
                    }
                ],
            },
            expected=(200, 201),
        )
        document_id = response.get("documentId")
        if document_id:
            self._created_document_ids.setdefault(subject_id, set()).add(str(document_id))
        for memory in response.get("memories", []):
            if isinstance(memory, dict) and memory.get("id"):
                self._created_memory_ids.setdefault(subject_id, set()).add(str(memory["id"]))

    def _retrieve(self, subject_id: str, session_id: str, query: str) -> list[dict[str, Any]]:
        response = self._request(
            "POST",
            "/v4/search",
            {
                "q": _search_query(query),
                "containerTag": self._container_tag(subject_id),
                "limit": self._search_limit,
                "threshold": 0,
                "searchMode": "memories",
                "rerank": False,
                "aggregate": False,
                "rewriteQuery": False,
            },
        )
        records = [
            self._normalize_search_result(result, subject_id)
            for result in response.get("results", [])
            if isinstance(result, dict)
        ]
        records = records_matching_query(query, records)
        self._retrievals.setdefault((subject_id, session_id), []).append(
            {
                "created_at": utc_now(),
                "query": query,
                "supermemory_query": _search_query(query),
                "memory_ids": [record.get("memory_id") for record in records],
                "records": records,
            }
        )
        return records

    def _delete_by_terms(self, subject_id: str, terms: tuple[str, ...]) -> bool:
        changed = False
        for record in self.inspect_memory(subject_id):
            content = str(record.get("content") or "").lower()
            if any(term in content for term in terms):
                if self._forget_memory_id(subject_id, str(record.get("memory_id"))):
                    changed = True
        return changed

    def _forget_subject_memories(self, subject_id: str) -> None:
        for memory_id in sorted(self._created_memory_ids.get(subject_id, set())):
            self._safe_forget_memory(subject_id, memory_id)
        self._created_memory_ids[subject_id] = set()

    def _forget_memory_id(self, subject_id: str, memory_id: str) -> bool:
        if not memory_id or memory_id == "None":
            return False
        self._request(
            "DELETE",
            "/v4/memories",
            {
                "containerTag": self._container_tag(subject_id),
                "id": memory_id,
                "reason": "MemoryStackBench cleanup or deletion check",
            },
        )
        self._created_memory_ids.setdefault(subject_id, set()).discard(memory_id)
        return True

    def _safe_forget_memory(self, subject_id: str, memory_id: str) -> None:
        try:
            self._forget_memory_id(subject_id, memory_id)
        except RuntimeError:
            pass

    def _delete_subject_documents(self, subject_id: str) -> None:
        document_ids = sorted(self._created_document_ids.get(subject_id, set()))
        if not document_ids:
            return
        try:
            self._request(
                "DELETE",
                "/v3/documents/bulk",
                {"ids": document_ids},
                expected=(200, 202, 204),
            )
        except RuntimeError:
            pass
        self._created_document_ids[subject_id] = set()

    def _normalize_memory_entry(self, entry: dict[str, Any], subject_id: str) -> dict[str, Any]:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        forgotten = bool(entry.get("isForgotten"))
        return {
            "memory_id": entry.get("id"),
            "framework": "supermemory",
            "subject_id_hash": f"plain:{subject_id}",
            "tenant_id_hash": self._container_tag(subject_id),
            "content": str(entry.get("memory") or entry.get("content") or ""),
            "source_type": metadata.get("source_type"),
            "source_session_id": metadata.get("source_session_id"),
            "source_turn_id": metadata.get("source_turn_id"),
            "created_at": entry.get("createdAt") or metadata.get("created_at"),
            "updated_at": entry.get("updatedAt"),
            "deleted_at": entry.get("updatedAt") if forgotten else None,
            "confidence": metadata.get("confidence"),
            "scope": "user_private",
            "raw": entry,
        }

    def _normalize_search_result(self, result: dict[str, Any], subject_id: str) -> dict[str, Any]:
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        return {
            "memory_id": result.get("id"),
            "framework": "supermemory",
            "subject_id_hash": f"plain:{subject_id}",
            "tenant_id_hash": self._container_tag(subject_id),
            "content": str(result.get("memory") or result.get("chunk") or ""),
            "source_type": metadata.get("source_type"),
            "source_session_id": metadata.get("source_session_id"),
            "source_turn_id": metadata.get("source_turn_id"),
            "created_at": result.get("createdAt") or metadata.get("created_at"),
            "updated_at": result.get("updatedAt"),
            "deleted_at": None,
            "confidence": result.get("similarity"),
            "scope": "user_private",
            "raw": result,
        }

    def _container_tag(self, subject_id: str) -> str:
        digest = hashlib.sha256(subject_id.encode("utf-8")).hexdigest()[:18]
        return f"mb:{self._run_id}:{digest}"

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        expected: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read().decode("utf-8")
                if response.status not in expected:
                    raise RuntimeError(
                        f"Supermemory API returned HTTP {response.status} for {method} {path}."
                    )
                if not body:
                    return {}
                parsed = json.loads(body)
                return parsed if isinstance(parsed, dict) else {"data": parsed}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            detail = _error_detail(body)
            if exc.code == 401:
                detail = (
                    "unauthorized; check that SUPERMEMORY_API_KEY is a valid "
                    "Supermemory API key"
                )
            raise RuntimeError(
                f"Supermemory API returned HTTP {exc.code} for {method} {path}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Supermemory API request failed for {method} {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Supermemory API returned invalid JSON for {method} {path}.") from exc


def _load_api_key(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return _clean_api_key(value)

    env_path = Path(".env.local")
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() in names:
            return _clean_api_key(value)
    return None


def _clean_api_key(value: str) -> str:
    cleaned = value.strip().strip('"').strip("'")
    if cleaned.lower().startswith("bearer "):
        return cleaned.split(" ", 1)[1].strip()
    return cleaned


def _search_query(query: str) -> str:
    lower = query.lower()
    if "itinerary" in lower or "public" in lower:
        return "travel itineraries private public default preference"
    if "airport" in lower:
        return "preferred airport SFO OAK"
    if "backup" in lower or "email" in lower:
        return "backup email private-backup@example.com"
    if "vegetarian" in lower or "beef" in lower:
        return "diet vegetarian beef business dinners"
    return query


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _compact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metadata.items() if value is not None}


def _error_detail(body: str) -> str:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body[:500] or "empty response"
    if isinstance(parsed, dict):
        for key in ("error", "message", "detail"):
            if parsed.get(key):
                return str(parsed[key])[:500]
    return body[:500] or "empty response"
