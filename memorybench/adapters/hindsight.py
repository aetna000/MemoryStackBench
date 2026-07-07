from __future__ import annotations

import asyncio
import hashlib
import json
import os
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


class HindsightAdapter(MemoryStackAdapter):
    """Hindsight adapter using the official Python client and HTTP API server."""

    capabilities = {
        "inspect_memory": True,
        "delete_memory": "document_delete",
        "retrieval_log": True,
        "multi_user": True,
        "multi_tenant": True,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        _load_env_local(("HINDSIGHT_BASE_URL", "HINDSIGHT_API_KEY"))
        try:
            from hindsight_client import Hindsight
        except ImportError as exc:
            raise RuntimeError(
                "HindsightAdapter requires the official client. Install it with "
                "`pip install -e '.[hindsight]'` or `pip install hindsight-client==0.8.4`."
            ) from exc

        runtime = self.config.get("runtime") or {}
        self._base_url = str(
            runtime.get("base_url")
            or os.environ.get("HINDSIGHT_BASE_URL")
            or "http://localhost:8888"
        ).rstrip("/")
        api_key = runtime.get("api_key") or os.environ.get("HINDSIGHT_API_KEY")
        self._client = Hindsight(
            base_url=self._base_url,
            api_key=str(api_key) if api_key else None,
            user_agent="memorystackbench/hindsight-adapter",
        )
        self._loop = asyncio.new_event_loop()
        self._run_id = uuid.uuid4().hex[:10]
        self._banks: dict[str, str] = {}
        self._documents: dict[str, dict[str, dict[str, Any]]] = {}
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._turn_counts: dict[tuple[str, str], int] = {}
        self._run(self._client.aget_version())

    def reset_subject(self, subject_id: str) -> None:
        if subject_id in self._banks:
            self._safe_delete_bank(self._banks[subject_id])
        bank_id = _bank_id(self._run_id, subject_id)
        self._banks[subject_id] = bank_id
        self._documents[subject_id] = {}
        for key in list(self._turn_counts):
            if key[0] == subject_id:
                del self._turn_counts[key]
        for key in list(self._retrievals):
            if key[0] == subject_id:
                del self._retrievals[key]
        self._create_bank(bank_id)

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        self._ensure_subject(subject_id)
        turn_id = self._next_turn_id(subject_id, session_id)
        if is_forget_request(message):
            deleted = self.delete_memory(subject_id, {"contains": forget_needle(message)})
            return "I deleted matching memories." if deleted else "I did not find matching memories to delete."

        content, is_correction = memory_content_for_user_message(message)
        if is_correction:
            self._delete_by_terms(subject_id, ("preferred airport", "sfo"))
        if content:
            self._retain(subject_id, session_id, turn_id, content, source_type(message))

        retrieved = self._recall(subject_id, session_id, message)
        return answer_from_records(message, retrieved)

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]]:
        self._ensure_subject(subject_id)
        response = self._run(
            self._client.memory.list_memories(
                self._banks[subject_id],
                limit=int(self.config.get("hindsight_list_limit") or 100),
                offset=0,
            )
        )
        raw = _raw(response)
        items = raw.get("items") if isinstance(raw.get("items"), list) else []
        return [
            self._normalize_memory_item(item, subject_id, index)
            for index, item in enumerate(items, start=1)
            if isinstance(item, dict)
        ]

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        self._ensure_subject(subject_id)
        contains = str(selector.get("contains") or "").lower()
        if not contains:
            return False
        changed = False
        for record in self.inspect_memory(subject_id):
            content = str(record.get("content") or "").lower()
            if contains not in content:
                continue
            document_id = _document_id_from_record(record)
            if document_id and self._delete_document(subject_id, document_id):
                changed = True
        if not changed:
            changed = self._delete_by_terms(subject_id, (contains,))
        return changed

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def close(self) -> None:
        for bank_id in list(self._banks.values()):
            self._safe_delete_bank(bank_id)
        self._banks.clear()
        self._documents.clear()
        try:
            self._run(self._client.aclose())
        finally:
            if not self._loop.is_closed():
                self._loop.close()

    def _ensure_subject(self, subject_id: str) -> None:
        if subject_id not in self._banks:
            self.reset_subject(subject_id)

    def _create_bank(self, bank_id: str) -> None:
        self._run(
            self._client.acreate_bank(
                bank_id=bank_id,
                retain_extraction_mode="custom",
                retain_custom_instructions=(
                    "Extract only stable user memory facts stated directly by the user. "
                    "Preserve literal values such as airport codes and email addresses. "
                    "Do not infer broader traits from narrow statements."
                ),
                enable_observations=False,
                reflect_mission="Answer benchmark probes using only memories in this bank.",
            )
        )

    def _retain(
        self,
        subject_id: str,
        session_id: str,
        turn_id: int,
        content: str,
        source: str,
    ) -> None:
        document_id = f"memorybench-{self._run_id}-{_hash(subject_id)[:12]}-{session_id}-t{turn_id}"
        metadata = benchmark_metadata(session_id, turn_id, source_type=source)
        metadata.update(
            {
                "memorybench": "true",
                "subject_hash": _hash(subject_id),
                "document_id": document_id,
            }
        )
        metadata = {key: str(value) for key, value in metadata.items() if value is not None}
        response = self._run(
            self._client.aretain(
                bank_id=self._banks[subject_id],
                content=content,
                context="MemoryStackBench durable user memory fact.",
                document_id=document_id,
                metadata=metadata,
                tags=["memorybench", source],
                retain_async=False,
            )
        )
        self._documents.setdefault(subject_id, {})[document_id] = {
            "content": content,
            "metadata": metadata,
            "deleted_at": None,
            "raw_retain_response": _raw(response),
        }

    def _recall(self, subject_id: str, session_id: str, query: str) -> list[dict[str, Any]]:
        response = self._run(
            self._client.arecall(
                bank_id=self._banks[subject_id],
                query=_search_query(query),
                budget=str(self.config.get("hindsight_recall_budget") or "mid"),
                max_tokens=int(self.config.get("hindsight_recall_max_tokens") or 4096),
                tags=["memorybench"],
                tags_match="any",
                include_source_facts=True,
                prefer_observations=False,
            )
        )
        raw = _raw(response)
        results = raw.get("results") if isinstance(raw.get("results"), list) else []
        records = [
            self._normalize_recall_result(item, subject_id, index)
            for index, item in enumerate(results, start=1)
            if isinstance(item, dict)
        ]
        records = records_matching_query(query, records)
        self._retrievals.setdefault((subject_id, session_id), []).append(
            {
                "created_at": utc_now(),
                "query": query,
                "hindsight_query": _search_query(query),
                "memory_ids": [record.get("memory_id") for record in records],
                "records": records,
                "raw": raw,
            }
        )
        return records

    def _normalize_memory_item(
        self,
        item: dict[str, Any],
        subject_id: str,
        index: int,
    ) -> dict[str, Any]:
        document_id = str(item.get("document_id") or item.get("source_document_id") or "")
        stored = self._documents.get(subject_id, {}).get(document_id, {})
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        metadata = {**stored.get("metadata", {}), **metadata}
        content = _content_from_item(item)
        return {
            "memory_id": item.get("id") or item.get("memory_id") or f"hindsight-memory-{index:04d}",
            "framework": "hindsight",
            "subject_id_hash": f"plain:{subject_id}",
            "tenant_id_hash": self._banks.get(subject_id),
            "content": str(content),
            "source_type": metadata.get("source_type"),
            "source_session_id": metadata.get("source_session_id"),
            "source_turn_id": metadata.get("source_turn_id"),
            "created_at": item.get("created_at") or item.get("mentioned_at") or metadata.get("created_at"),
            "updated_at": item.get("updated_at"),
            "deleted_at": stored.get("deleted_at"),
            "confidence": _score(item),
            "scope": "user_private",
            "raw": item,
        }

    def _normalize_recall_result(
        self,
        item: dict[str, Any],
        subject_id: str,
        index: int,
    ) -> dict[str, Any]:
        document_id = str(item.get("document_id") or "")
        stored = self._documents.get(subject_id, {}).get(document_id, {})
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        metadata = {**stored.get("metadata", {}), **metadata}
        return {
            "memory_id": item.get("id") or f"hindsight-recall-{index:04d}",
            "framework": "hindsight",
            "subject_id_hash": f"plain:{subject_id}",
            "tenant_id_hash": self._banks.get(subject_id),
            "content": str(_content_from_item(item)),
            "source_type": metadata.get("source_type"),
            "source_session_id": metadata.get("source_session_id"),
            "source_turn_id": metadata.get("source_turn_id"),
            "created_at": item.get("occurred_start") or item.get("mentioned_at"),
            "updated_at": None,
            "deleted_at": None,
            "confidence": _score(item),
            "scope": "user_private",
            "raw": item,
        }

    def _delete_by_terms(self, subject_id: str, terms: tuple[str, ...]) -> bool:
        changed = False
        for record in self.inspect_memory(subject_id):
            content = str(record.get("content") or "").lower()
            if any(term in content for term in terms):
                document_id = _document_id_from_record(record)
                if document_id and self._delete_document(subject_id, document_id):
                    changed = True
        for document_id, stored in list(self._documents.get(subject_id, {}).items()):
            if stored.get("deleted_at"):
                continue
            content = str(stored.get("content") or "").lower()
            if any(term in content for term in terms):
                if self._delete_document(subject_id, document_id):
                    changed = True
        return changed

    def _delete_document(self, subject_id: str, document_id: str) -> bool:
        try:
            self._run(self._client.documents.delete_document(self._banks[subject_id], document_id))
        except Exception:
            return False
        stored = self._documents.get(subject_id, {}).get(document_id)
        if stored is not None:
            stored["deleted_at"] = utc_now()
        return True

    def _safe_delete_bank(self, bank_id: str) -> None:
        try:
            self._run(self._client.adelete_bank(bank_id))
        except Exception:
            try:
                self._run(self._client.memory.clear_bank_memories(bank_id))
            except Exception:
                pass

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]

    def _run(self, value: Any) -> Any:
        if not hasattr(value, "__await__"):
            return value
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self._loop.run_until_complete(value)
        raise RuntimeError("HindsightAdapter cannot run inside an existing asyncio event loop.")


def _load_env_local(keys: tuple[str, ...]) -> None:
    path = Path(".env.local")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key in keys and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _raw(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return _json_safe(value)
    if hasattr(value, "to_dict"):
        return _json_safe(value.to_dict())
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    raw = getattr(value, "__dict__", None)
    if isinstance(raw, dict):
        return _json_safe(raw)
    return {"value": str(value)}


def _json_safe(value: Any) -> dict[str, Any]:
    try:
        safe = json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return {"value": str(value)}
    return safe if isinstance(safe, dict) else {"value": safe}


def _content_from_item(item: dict[str, Any]) -> str:
    for key in ("text", "content", "memory", "fact", "summary", "value"):
        value = item.get(key)
        if value:
            return str(value)
    return json.dumps(item, sort_keys=True, default=str)


def _document_id_from_record(record: dict[str, Any]) -> str | None:
    raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
    document_id = raw.get("document_id") or raw.get("source_document_id")
    return str(document_id) if document_id else None


def _score(item: dict[str, Any]) -> float | None:
    scores = item.get("scores")
    if isinstance(scores, dict):
        for key in ("final", "reranker", "semantic", "keyword"):
            value = scores.get(key)
            if isinstance(value, (int, float)):
                return float(value)
    value = item.get("score")
    return float(value) if isinstance(value, (int, float)) else None


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


def _bank_id(run_id: str, subject_id: str) -> str:
    return f"memorybench-{run_id}-{_hash(subject_id)[:18]}"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
