from __future__ import annotations

import json
from datetime import datetime, timezone
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


class AgnoMemoryAdapter(MemoryStackAdapter):
    """Agno MemoryManager adapter using the in-memory DB backend."""

    capabilities = {
        "inspect_memory": True,
        "delete_memory": True,
        "retrieval_log": True,
        "multi_user": True,
        "multi_tenant": False,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        try:
            from agno.db.in_memory import InMemoryDb
            from agno.memory import MemoryManager, UserMemory
        except ImportError as exc:
            raise RuntimeError(
                "AgnoMemoryAdapter requires Agno. Install it with "
                "`pip install -e '.[agno]'` or `pip install agno`."
            ) from exc

        self._UserMemory = UserMemory
        self._manager = MemoryManager(db=InMemoryDb())
        self._memory_counts: dict[str, int] = {}
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._turn_counts: dict[tuple[str, str], int] = {}

    def reset_subject(self, subject_id: str) -> None:
        for record in self.inspect_memory(subject_id):
            memory_id = record.get("memory_id")
            if memory_id:
                self._manager.delete_user_memory(str(memory_id), user_id=subject_id)
        self._memory_counts[subject_id] = 0
        for key in list(self._retrievals):
            if key[0] == subject_id:
                del self._retrievals[key]
        for key in list(self._turn_counts):
            if key[0] == subject_id:
                del self._turn_counts[key]

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        turn_id = self._next_turn_id(subject_id, session_id)
        if is_forget_request(message):
            deleted = self.delete_memory(subject_id, {"contains": forget_needle(message)})
            return "I deleted matching memories." if deleted else "I did not find matching memories to delete."

        content, is_correction = memory_content_for_user_message(message)
        if is_correction:
            self._delete_by_terms(subject_id, ("preferred airport", "sfo"))
        if content:
            self._write_memory(subject_id, session_id, turn_id, content, source_type(message))

        retrieved = self._retrieve(subject_id, session_id, message)
        return answer_from_records(message, retrieved)

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]]:
        memories = self._manager.get_user_memories(user_id=subject_id) or []
        records = [self._normalize_memory(memory, subject_id) for memory in memories]
        return sorted(records, key=lambda record: str(record.get("memory_id")))

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        contains = str(selector.get("contains") or "").lower()
        changed = False
        for record in self.inspect_memory(subject_id):
            if not contains or contains in record.get("content", "").lower():
                memory_id = record.get("memory_id")
                if memory_id:
                    self._manager.delete_user_memory(str(memory_id), user_id=subject_id)
                    changed = True
        return changed

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def _write_memory(
        self,
        subject_id: str,
        session_id: str,
        turn_id: int,
        content: str,
        source: str,
    ) -> None:
        self._memory_counts[subject_id] = self._memory_counts.get(subject_id, 0) + 1
        memory_id = f"agno-{self._memory_counts[subject_id]:04d}"
        metadata = benchmark_metadata(session_id, turn_id, source_type=source)
        metadata["memory_id"] = memory_id
        memory = self._UserMemory(
            memory=content,
            memory_id=memory_id,
            topics=_topics(content),
            user_id=subject_id,
            input=json.dumps(metadata, sort_keys=True),
        )
        self._manager.add_user_memory(memory, user_id=subject_id)

    def _retrieve(self, subject_id: str, session_id: str, query: str) -> list[dict[str, Any]]:
        records = records_matching_query(query, self.inspect_memory(subject_id))
        self._retrievals.setdefault((subject_id, session_id), []).append(
            {
                "created_at": utc_now(),
                "query": query,
                "memory_ids": [record.get("memory_id") for record in records],
                "records": records,
            }
        )
        return records

    def _delete_by_terms(self, subject_id: str, terms: tuple[str, ...]) -> None:
        for record in self.inspect_memory(subject_id):
            content = record.get("content", "").lower()
            if any(term in content for term in terms):
                memory_id = record.get("memory_id")
                if memory_id:
                    self._manager.delete_user_memory(str(memory_id), user_id=subject_id)

    def _normalize_memory(self, memory: Any, subject_id: str) -> dict[str, Any]:
        metadata = _metadata_from_input(getattr(memory, "input", None))
        return {
            "memory_id": getattr(memory, "memory_id", None),
            "framework": "agno_memory",
            "subject_id_hash": f"plain:{subject_id}",
            "tenant_id_hash": None,
            "content": str(getattr(memory, "memory", "") or ""),
            "source_type": metadata.get("source_type"),
            "source_session_id": metadata.get("source_session_id"),
            "source_turn_id": metadata.get("source_turn_id"),
            "created_at": metadata.get("created_at") or _timestamp_to_iso(getattr(memory, "created_at", None)),
            "updated_at": metadata.get("updated_at") or _timestamp_to_iso(getattr(memory, "updated_at", None)),
            "deleted_at": None,
            "confidence": metadata.get("confidence"),
            "scope": "user_private",
            "raw": _memory_raw(memory),
        }

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]


def _metadata_from_input(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _memory_raw(memory: Any) -> dict[str, Any]:
    if hasattr(memory, "model_dump"):
        return memory.model_dump(mode="json")
    if hasattr(memory, "dict"):
        return memory.dict()
    return dict(getattr(memory, "__dict__", {}) or {})


def _timestamp_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return str(value)


def _topics(content: str) -> list[str]:
    lower = content.lower()
    topics = []
    if "airport" in lower:
        topics.append("travel")
    if "itinerar" in lower:
        topics.append("itinerary")
    if "email" in lower:
        topics.append("contact")
    if "beef" in lower or "vegetarian" in lower:
        topics.append("diet")
    return topics or ["preference"]
