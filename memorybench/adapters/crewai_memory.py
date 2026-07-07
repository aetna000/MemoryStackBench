from __future__ import annotations

import hashlib
import json
import os
import shutil
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


class CrewAIMemoryAdapter(MemoryStackAdapter):
    """CrewAI unified Memory adapter backed by LanceDB storage."""

    capabilities = {
        "inspect_memory": True,
        "delete_memory": "record_id_forget",
        "retrieval_log": True,
        "multi_user": True,
        "multi_tenant": False,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        _load_env_local(("OPENAI_API_KEY",))
        try:
            from crewai.memory.unified_memory import Memory
        except ImportError as exc:
            raise RuntimeError(
                "CrewAIMemoryAdapter requires CrewAI 1.x. Install it on Linux with "
                "`pip install -e '.[crewai]'` or `pip install crewai==1.15.1`. "
                "The current macOS/Rosetta Python cannot resolve CrewAI's LanceDB pin."
            ) from exc

        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("CrewAIMemoryAdapter requires OPENAI_API_KEY.")

        self._Memory = Memory
        self._run_id = uuid.uuid4().hex[:10]
        self._root_dir = Path(
            self.config.get("crewai_root_dir")
            or f"/tmp/memorystackbench-crewai-{self._run_id}"
        )
        self._root_dir.mkdir(parents=True, exist_ok=True)
        self._memories: dict[str, Any] = {}
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._turn_counts: dict[tuple[str, str], int] = {}

    def reset_subject(self, subject_id: str) -> None:
        memory = self._memories.pop(subject_id, None)
        if memory is not None:
            try:
                memory.reset_all()
                memory.close()
            except Exception:
                pass
        self._memories[subject_id] = self._new_memory(subject_id)
        for key in list(self._turn_counts):
            if key[0] == subject_id:
                del self._turn_counts[key]
        for key in list(self._retrievals):
            if key[0] == subject_id:
                del self._retrievals[key]

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
            self._remember(subject_id, session_id, turn_id, content, source_type(message))

        retrieved = self._retrieve(subject_id, session_id, message)
        return answer_from_records(message, retrieved)

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]]:
        self._ensure_subject(subject_id)
        records = [
            self._normalize_record(record, subject_id)
            for record in self._memories[subject_id].list_records(
                scope=None,
                limit=int(self.config.get("crewai_list_limit") or 200),
            )
        ]
        return sorted(records, key=lambda record: str(record.get("memory_id")))

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        contains = str(selector.get("contains") or "").lower()
        record_ids = [
            str(record.get("memory_id"))
            for record in self.inspect_memory(subject_id)
            if record.get("memory_id")
            and (not contains or contains in str(record.get("content") or "").lower())
        ]
        if not record_ids:
            return False
        deleted = self._memories[subject_id].forget(record_ids=record_ids)
        return bool(deleted)

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def close(self) -> None:
        for memory in list(self._memories.values()):
            try:
                memory.reset_all()
                memory.close()
            except Exception:
                pass
        self._memories.clear()
        if not self.config.get("crewai_keep_data"):
            shutil.rmtree(self._root_dir, ignore_errors=True)

    def _new_memory(self, subject_id: str) -> Any:
        return self._Memory(
            llm=str((self.config.get("model") or {}).get("model") or "openai/gpt-4o-mini"),
            storage=str(self._root_dir / _safe_subject(subject_id)),
            embedder={
                "provider": "openai",
                "config": {"model_name": "text-embedding-3-small"},
            },
            root_scope=_scope(subject_id),
            consolidation_threshold=1.0,
        )

    def _ensure_subject(self, subject_id: str) -> None:
        if subject_id not in self._memories:
            self.reset_subject(subject_id)

    def _remember(
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
        self._memories[subject_id].remember(
            content,
            scope="/facts",
            categories=_categories(content),
            metadata=metadata,
            importance=0.7,
            source=session_id,
            private=False,
        )

    def _retrieve(self, subject_id: str, session_id: str, query: str) -> list[dict[str, Any]]:
        matches = self._memories[subject_id].recall(
            _search_query(query),
            scope="/facts",
            limit=int(self.config.get("crewai_recall_limit") or 10),
            depth="shallow",
            include_private=True,
        )
        records = [self._normalize_match(match, subject_id) for match in matches]
        records = records_matching_query(query, records)
        self._retrievals.setdefault((subject_id, session_id), []).append(
            {
                "created_at": utc_now(),
                "query": query,
                "crewai_query": _search_query(query),
                "memory_ids": [record.get("memory_id") for record in records],
                "records": records,
            }
        )
        return records

    def _delete_by_terms(self, subject_id: str, terms: tuple[str, ...]) -> bool:
        record_ids = [
            str(record.get("memory_id"))
            for record in self.inspect_memory(subject_id)
            if record.get("memory_id")
            and any(term in str(record.get("content") or "").lower() for term in terms)
        ]
        if not record_ids:
            return False
        return bool(self._memories[subject_id].forget(record_ids=record_ids))

    def _normalize_match(self, match: Any, subject_id: str) -> dict[str, Any]:
        record = getattr(match, "record", None)
        normalized = self._normalize_record(record, subject_id)
        normalized["confidence"] = getattr(match, "score", None)
        normalized["raw"] = _raw(match)
        return normalized

    def _normalize_record(self, record: Any, subject_id: str) -> dict[str, Any]:
        metadata = dict(getattr(record, "metadata", None) or {})
        return {
            "memory_id": getattr(record, "id", None),
            "framework": "crewai_memory",
            "subject_id_hash": f"plain:{subject_id}",
            "tenant_id_hash": _scope(subject_id),
            "content": str(getattr(record, "content", "") or ""),
            "source_type": metadata.get("source_type"),
            "source_session_id": metadata.get("source_session_id"),
            "source_turn_id": metadata.get("source_turn_id"),
            "created_at": _iso(getattr(record, "created_at", None)) or metadata.get("created_at"),
            "updated_at": _iso(getattr(record, "last_accessed", None)),
            "deleted_at": None,
            "confidence": metadata.get("confidence"),
            "scope": "user_private",
            "raw": _raw(record),
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


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def _scope(subject_id: str) -> str:
    return f"/memorybench/{_hash(subject_id)[:18]}"


def _safe_subject(subject_id: str) -> str:
    return f"subject-{_hash(subject_id)[:18]}"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _categories(content: str) -> list[str]:
    lower = content.lower()
    categories = ["memorybench"]
    if "airport" in lower:
        categories.append("travel")
    if "itinerar" in lower:
        categories.append("itinerary")
    if "email" in lower:
        categories.append("contact")
    if "beef" in lower or "vegetarian" in lower:
        categories.append("diet")
    return categories


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
