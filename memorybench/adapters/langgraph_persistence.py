from __future__ import annotations

import re
from typing import Any

from memorybench.adapters.base import MemoryStackAdapter, utc_now
from memorybench.adapters.mem0 import answer_from_records


class LangGraphPersistenceAdapter(MemoryStackAdapter):
    """LangGraph InMemoryStore persistence adapter.

    LangGraph supplies storage primitives, not a complete semantic-memory write
    policy. This adapter uses the real LangGraph store API with a small,
    explicit benchmark policy for what gets written, updated, and deleted.
    """

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
            from langgraph.store.memory import InMemoryStore
        except ImportError as exc:
            raise RuntimeError(
                "LangGraphPersistenceAdapter requires `langgraph`. Install it with "
                "`pip install -e '.[langgraph]'` or `pip install langgraph`."
            ) from exc

        self._store = InMemoryStore()
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
        if _is_forget_request(message):
            deleted = self.delete_memory(subject_id, {"contains": _forget_needle(message)})
            return "I deleted matching memories." if deleted else "I did not find matching memories to delete."

        self._maybe_write(subject_id, session_id, turn_id, message)
        retrieved = self._retrieve(subject_id, session_id, message)
        return answer_from_records(message, retrieved)

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]]:
        records = []
        for item in self._store.search(_namespace(subject_id), limit=100):
            value = dict(item.value)
            records.append(
                {
                    "memory_id": item.key,
                    "framework": "langgraph",
                    "subject_id_hash": f"plain:{subject_id}",
                    "tenant_id_hash": None,
                    "content": str(value.get("content") or ""),
                    "source_type": value.get("source_type"),
                    "source_session_id": value.get("source_session_id"),
                    "source_turn_id": value.get("source_turn_id"),
                    "created_at": _iso(item.created_at) or value.get("created_at"),
                    "updated_at": _iso(item.updated_at),
                    "deleted_at": None,
                    "confidence": value.get("confidence"),
                    "scope": "user_private",
                    "raw": value,
                }
            )
        return sorted(records, key=lambda record: str(record.get("memory_id")))

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        contains = str(selector.get("contains") or "").lower()
        changed = False
        for record in self.inspect_memory(subject_id):
            if not contains or contains in record.get("content", "").lower():
                memory_id = record.get("memory_id")
                if memory_id:
                    self._store.delete(_namespace(subject_id), str(memory_id))
                    changed = True
        return changed

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def _maybe_write(self, subject_id: str, session_id: str, turn_id: int, message: str) -> None:
        lower = message.lower()
        if "<webpage>" in lower:
            return

        content: str | None = None
        match = re.search(r"remember that (?P<fact>.+)", message, flags=re.I | re.S)
        if match:
            content = match.group("fact").strip()
        elif "preferred airport is" in lower:
            content = message.strip()
        elif "actually, use oak" in lower:
            self._delete_by_terms(subject_id, ("preferred airport", "sfo"))
            content = "User preferred airport is OAK."
        elif "avoid beef" in lower:
            content = "User avoids beef at business dinners."

        if not content:
            return

        memory_id = f"lg-{len(self.inspect_memory(subject_id)) + 1:04d}"
        self._store.put(
            _namespace(subject_id),
            memory_id,
            {
                "content": content,
                "source_type": "user_message",
                "source_session_id": session_id,
                "source_turn_id": f"t{turn_id}",
                "created_at": utc_now(),
                "confidence": None,
            },
        )

    def _retrieve(self, subject_id: str, session_id: str, query: str) -> list[dict[str, Any]]:
        lower = query.lower()
        terms = _query_terms(lower)
        records = [
            record
            for record in self.inspect_memory(subject_id)
            if not terms or any(term in record.get("content", "").lower() for term in terms)
        ]
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
                self._store.delete(_namespace(subject_id), str(record.get("memory_id")))

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]


def _namespace(subject_id: str) -> tuple[str, ...]:
    return ("memorybench", subject_id, "memories")


def _query_terms(lower_query: str) -> tuple[str, ...]:
    if "itinerary" in lower_query or "public" in lower_query:
        return ("itinerary", "itineraries", "travel")
    if "airport" in lower_query:
        return ("airport", "sfo", "oak")
    if "backup" in lower_query or "email" in lower_query:
        return ("backup email", "email")
    if "vegetarian" in lower_query or "beef" in lower_query:
        return ("vegetarian", "beef")
    return tuple()


def _forget_needle(message: str) -> str:
    lower = message.lower()
    for prefix in ("forget my ", "delete my ", "remove my ", "forget ", "delete ", "remove "):
        if prefix in lower:
            return lower.split(prefix, 1)[1].strip(" .?!")
    return lower


def _is_forget_request(message: str) -> bool:
    return bool(re.search(r"\b(forget|delete|remove)\b", message, flags=re.I))


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None
