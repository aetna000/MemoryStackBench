from __future__ import annotations

import re
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


class LangMemAdapter(MemoryStackAdapter):
    """LangMem manage/search tools backed by LangGraph InMemoryStore."""

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
            import langmem
            from langgraph.store.memory import InMemoryStore
        except ImportError as exc:
            raise RuntimeError(
                "LangMemAdapter requires LangMem and LangGraph. Install them with "
                "`pip install -e '.[langmem]'` or `pip install langmem langgraph`."
            ) from exc

        self._langmem = langmem
        self._store = InMemoryStore()
        self._manage_tools: dict[str, Any] = {}
        self._search_tools: dict[str, Any] = {}
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._turn_counts: dict[tuple[str, str], int] = {}

    def reset_subject(self, subject_id: str) -> None:
        for record in self.inspect_memory(subject_id):
            memory_id = record.get("memory_id")
            if memory_id:
                self._store.delete(_namespace(subject_id), str(memory_id))
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
        records = [
            self._normalize_item(item, subject_id)
            for item in self._store.search(_namespace(subject_id), limit=100)
        ]
        return sorted(records, key=lambda record: str(record.get("memory_id")))

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        contains = str(selector.get("contains") or "").lower()
        changed = False
        for record in self.inspect_memory(subject_id):
            if not contains or contains in record.get("content", "").lower():
                memory_id = str(record.get("memory_id") or "")
                if memory_id:
                    self._delete_memory_id(subject_id, memory_id)
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
        result = self._manage_tool(subject_id).invoke({"content": content, "action": "create"})
        memory_id = _created_memory_id(str(result)) or f"langmem-{len(self.inspect_memory(subject_id)) + 1:04d}"
        metadata = benchmark_metadata(session_id, turn_id, source_type=source)
        self._store.put(
            _namespace(subject_id),
            memory_id,
            {
                "content": content,
                "source_type": metadata["source_type"],
                "source_session_id": metadata["source_session_id"],
                "source_turn_id": metadata["source_turn_id"],
                "created_at": metadata["created_at"],
                "confidence": metadata["confidence"],
            },
        )

    def _retrieve(self, subject_id: str, session_id: str, query: str) -> list[dict[str, Any]]:
        self._search_tool(subject_id).invoke({"query": query})
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
                    self._delete_memory_id(subject_id, str(memory_id))

    def _delete_memory_id(self, subject_id: str, memory_id: str) -> None:
        try:
            self._manage_tool(subject_id).invoke({"id": memory_id, "action": "delete"})
        except Exception:
            self._store.delete(_namespace(subject_id), memory_id)

    def _manage_tool(self, subject_id: str) -> Any:
        if subject_id not in self._manage_tools:
            self._manage_tools[subject_id] = self._langmem.create_manage_memory_tool(
                _namespace(subject_id), store=self._store
            )
        return self._manage_tools[subject_id]

    def _search_tool(self, subject_id: str) -> Any:
        if subject_id not in self._search_tools:
            self._search_tools[subject_id] = self._langmem.create_search_memory_tool(
                _namespace(subject_id), store=self._store
            )
        return self._search_tools[subject_id]

    def _normalize_item(self, item: Any, subject_id: str) -> dict[str, Any]:
        value = dict(getattr(item, "value", None) or {})
        return {
            "memory_id": getattr(item, "key", None),
            "framework": "langmem",
            "subject_id_hash": f"plain:{subject_id}",
            "tenant_id_hash": None,
            "content": str(value.get("content") or ""),
            "source_type": value.get("source_type"),
            "source_session_id": value.get("source_session_id"),
            "source_turn_id": value.get("source_turn_id"),
            "created_at": _iso(getattr(item, "created_at", None)) or value.get("created_at"),
            "updated_at": _iso(getattr(item, "updated_at", None)),
            "deleted_at": None,
            "confidence": value.get("confidence") or getattr(item, "score", None),
            "scope": "user_private",
            "raw": {
                "namespace": list(getattr(item, "namespace", ()) or ()),
                "key": getattr(item, "key", None),
                "value": value,
            },
        }

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]


def _namespace(subject_id: str) -> tuple[str, ...]:
    return ("memorybench", subject_id, "memories")


def _created_memory_id(result: str) -> str | None:
    match = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        result,
        flags=re.I,
    )
    return match.group(0) if match else None


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)
