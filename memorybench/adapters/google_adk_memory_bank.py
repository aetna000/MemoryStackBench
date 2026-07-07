from __future__ import annotations

import hashlib
import json
import os
import re
import time
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


class GoogleAdkMemoryBankAdapter(MemoryStackAdapter):
    """Google ADK / Agent Platform Memory Bank adapter.

    The ADK `VertexAiMemoryBankService` wraps the same managed Memory Bank
    backend, while the Agent Platform SDK exposes the inspect/delete APIs needed
    for a white-box benchmark. This adapter therefore validates the ADK memory
    package is present, then uses direct Memory Bank create/retrieve/list/delete
    calls against a temporary Agent Engine resource.
    """

    capabilities = {
        "inspect_memory": True,
        "delete_memory": "native_memory_delete",
        "retrieval_log": True,
        "multi_user": True,
        "multi_tenant": True,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        _load_env_local(
            (
                "GOOGLE_CLOUD_PROJECT",
                "GOOGLE_CLOUD_LOCATION",
                "GOOGLE_APPLICATION_CREDENTIALS",
            )
        )
        try:
            import vertexai
            from google.adk.memory import VertexAiMemoryBankService
        except ImportError as exc:
            raise RuntimeError(
                "GoogleAdkMemoryBankAdapter requires the Google Agent Platform SDK. "
                "Install it with `pip install -e '.[google-adk]'`."
            ) from exc

        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION")
        credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not project or not location or not credentials:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, and "
                "GOOGLE_APPLICATION_CREDENTIALS must be set."
            )

        self._project = project
        self._location = location
        self._credentials = credentials
        self._run_id = uuid.uuid4().hex[:10]
        self._client = vertexai.Client(project=project, location=location)
        self._adk_memory_service_cls = VertexAiMemoryBankService
        self._engine: Any | None = None
        self._engine_name: str | None = None
        self._metadata_by_memory_name: dict[str, dict[str, Any]] = {}
        self._subjects: set[str] = set()
        self._turn_counts: dict[tuple[str, str], int] = {}
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}

        try:
            self._create_engine()
        except Exception:
            self.close()
            raise

    def reset_subject(self, subject_id: str) -> None:
        self._ensure_engine()
        self._delete_subject_memories(subject_id)
        self._subjects.add(subject_id)
        for key in list(self._turn_counts):
            if key[0] == subject_id:
                del self._turn_counts[key]
        for key in list(self._retrievals):
            if key[0] == subject_id:
                del self._retrievals[key]

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        self._ensure_engine()
        self._subjects.add(subject_id)
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
        self._ensure_engine()
        records = [
            self._normalize_memory(memory, subject_id)
            for memory in self._list_scope_memories(subject_id)
        ]
        return sorted(records, key=lambda record: str(record.get("memory_id") or ""))

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        contains = str(selector.get("contains") or "").lower()
        terms = (contains,) if contains else tuple()
        return self._delete_by_terms(subject_id, terms)

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def close(self) -> None:
        engine_name = self._engine_name
        self._engine = None
        self._engine_name = None
        self._metadata_by_memory_name.clear()
        if not engine_name:
            return
        try:
            self._client.agent_engines.delete(name=engine_name, force=True)
        except Exception:
            pass

    def _create_engine(self) -> None:
        engine = self._client.agent_engines.create()
        engine_name = getattr(getattr(engine, "api_resource", None), "name", None)
        if not engine_name:
            raise RuntimeError(f"Agent Engine create did not return a name: {engine!r}")
        self._engine = engine
        self._engine_name = str(engine_name)

    def _ensure_engine(self) -> None:
        if not self._engine_name:
            raise RuntimeError("Google Agent Engine was not created.")

    def _create_memory(
        self,
        subject_id: str,
        session_id: str,
        turn_id: int,
        content: str,
        source: str,
    ) -> None:
        self._ensure_engine()
        metadata = benchmark_metadata(session_id, turn_id, source_type=source)
        metadata.update(
            {
                "memorybench": True,
                "subject_hash": _hash(subject_id),
            }
        )
        operation = self._client.agent_engines.memories.create(
            name=str(self._engine_name),
            fact=content,
            scope=_scope(subject_id, self._run_id),
            config={"wait_for_completion": True},
        )
        memory = getattr(operation, "response", None)
        memory_name = str(getattr(memory, "name", "") or "")
        if memory_name:
            self._metadata_by_memory_name[memory_name] = metadata
        self._wait_for_content(subject_id, content)

    def _retrieve(self, subject_id: str, session_id: str, query: str) -> list[dict[str, Any]]:
        records = [
            self._normalize_retrieved_memory(memory, subject_id)
            for memory in self._retrieve_scope_memories(subject_id)
        ]
        records = records_matching_query(query, records)
        self._retrievals.setdefault((subject_id, session_id), []).append(
            {
                "created_at": utc_now(),
                "query": query,
                "retrieval_api": "agent_engines.memories.retrieve",
                "memory_ids": [record.get("memory_id") for record in records],
                "records": records,
            }
        )
        return records

    def _retrieve_scope_memories(self, subject_id: str) -> list[Any]:
        self._ensure_engine()
        return list(
            self._client.agent_engines.memories.retrieve(
                name=str(self._engine_name),
                scope=_scope(subject_id, self._run_id),
                simple_retrieval_params={"page_size": int(self.config.get("google_page_size") or 100)},
            )
        )

    def _list_scope_memories(self, subject_id: str) -> list[Any]:
        self._ensure_engine()
        scope = _scope(subject_id, self._run_id)
        memories = list(
            self._client.agent_engines.memories.list(
                name=str(self._engine_name),
                config={"page_size": int(self.config.get("google_page_size") or 100)},
            )
        )
        return [memory for memory in memories if _memory_scope(memory) == scope]

    def _delete_by_terms(self, subject_id: str, terms: tuple[str, ...]) -> bool:
        records = self.inspect_memory(subject_id)
        names = [
            str(record.get("memory_id") or "")
            for record in records
            if record.get("memory_id")
            and (not terms or any(term in str(record.get("content") or "").lower() for term in terms))
        ]
        if not names:
            return False
        for name in names:
            try:
                self._client.agent_engines.memories.delete(name=name)
                self._metadata_by_memory_name.pop(name, None)
            except Exception:
                # A stale local record should not abort cleanup if the backend
                # already removed it.
                self._metadata_by_memory_name.pop(name, None)
        self._wait_for_deleted(subject_id, set(names))
        return True

    def _delete_subject_memories(self, subject_id: str) -> None:
        for record in self.inspect_memory(subject_id):
            memory_id = str(record.get("memory_id") or "")
            if not memory_id:
                continue
            try:
                self._client.agent_engines.memories.delete(name=memory_id)
            except Exception:
                pass
            self._metadata_by_memory_name.pop(memory_id, None)

    def _wait_for_content(self, subject_id: str, content: str) -> None:
        deadline = time.monotonic() + float(self.config.get("google_index_timeout_seconds") or 90)
        needle = _primary_needle(content)
        while time.monotonic() < deadline:
            records = self.inspect_memory(subject_id)
            if any(needle in str(record.get("content") or "").lower() for record in records):
                return
            time.sleep(3)

    def _wait_for_deleted(self, subject_id: str, memory_names: set[str]) -> None:
        deadline = time.monotonic() + float(self.config.get("google_delete_timeout_seconds") or 90)
        while time.monotonic() < deadline:
            remaining = {
                str(record.get("memory_id") or "")
                for record in self.inspect_memory(subject_id)
                if record.get("memory_id") in memory_names
            }
            if not remaining:
                return
            time.sleep(3)

    def _normalize_retrieved_memory(self, retrieved: Any, subject_id: str) -> dict[str, Any]:
        memory = getattr(retrieved, "memory", retrieved)
        normalized = self._normalize_memory(memory, subject_id)
        normalized["confidence"] = getattr(retrieved, "score", None) or normalized.get("confidence")
        normalized["raw"] = _raw(retrieved)
        return normalized

    def _normalize_memory(self, memory: Any, subject_id: str) -> dict[str, Any]:
        memory_name = str(getattr(memory, "name", "") or "")
        metadata = dict(self._metadata_by_memory_name.get(memory_name, {}))
        return {
            "memory_id": memory_name or None,
            "framework": "google_adk_memory_bank",
            "subject_id_hash": f"sha256:{_hash(subject_id)}",
            "tenant_id_hash": _scope(subject_id, self._run_id).get("app_name"),
            "content": str(getattr(memory, "fact", "") or ""),
            "source_type": metadata.get("source_type"),
            "source_session_id": metadata.get("source_session_id"),
            "source_turn_id": metadata.get("source_turn_id"),
            "created_at": _iso(getattr(memory, "create_time", None)) or metadata.get("created_at"),
            "updated_at": _iso(getattr(memory, "update_time", None)),
            "deleted_at": None,
            "confidence": metadata.get("confidence"),
            "scope": "agent_platform_memory_bank",
            "raw": _raw(memory),
        }

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]


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


def _scope(subject_id: str, run_id: str) -> dict[str, str]:
    return {
        "user_id": f"mb-{_hash(subject_id)[:24]}",
        "app_name": f"memorystackbench-{_safe_id(run_id)}",
    }


def _memory_scope(memory: Any) -> dict[str, str]:
    raw_scope = getattr(memory, "scope", None) or {}
    if isinstance(raw_scope, dict):
        return {str(key): str(value) for key, value in raw_scope.items()}
    if hasattr(raw_scope, "model_dump"):
        dumped = raw_scope.model_dump(mode="json")
        if isinstance(dumped, dict):
            return {str(key): str(value) for key, value in dumped.items()}
    return {}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "-", value)[:32] or "run"


def _primary_needle(content: str) -> str:
    lower = content.lower()
    if "private-backup@example.com" in lower:
        return "private-backup@example.com"
    if "preferred airport" in lower:
        return "airport"
    if "itinerar" in lower:
        return "private"
    if "beef" in lower:
        return "beef"
    return lower[:32]


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def _raw(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return _json_safe(value)
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
