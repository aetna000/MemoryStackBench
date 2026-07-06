from __future__ import annotations

import re
from typing import Any

from memorybench.adapters.base import MemoryStackAdapter, utc_now


class ToyMemoryAdapter(MemoryStackAdapter):
    """A small intentionally naive memory stack used to validate the harness."""

    capabilities = {
        "inspect_memory": True,
        "delete_memory": True,
        "retrieval_log": True,
        "multi_user": True,
        "multi_tenant": False,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._memory: dict[str, list[dict[str, Any]]] = {}
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def reset_subject(self, subject_id: str) -> None:
        self._memory[subject_id] = []
        for key in list(self._retrievals):
            if key[0] == subject_id:
                del self._retrievals[key]

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        self._memory.setdefault(subject_id, [])
        self._maybe_write(subject_id, session_id, message)
        retrieved = self._retrieve(subject_id, session_id, message)

        lower = message.lower()
        joined = " ".join(item["content"].lower() for item in retrieved)

        if "public by default" in joined or "itineraries public" in joined:
            return "Yes. Your itineraries should be public by default."
        if "itinerary" in lower and "public" in lower:
            return "No. Your itineraries should stay private by default."
        if "which airport" in lower:
            if "oak" in joined:
                return "Use OAK as your preferred airport."
            if "sfo" in joined:
                return "Use SFO as your preferred airport."
        if "are you allowed to use my backup email" in lower:
            if "backup email" in joined:
                return "Yes, I can use your backup email from memory."
            return "No. I do not have a backup email available."
        if "am i vegetarian" in lower:
            if "vegetarian" in joined:
                return "Yes, you are vegetarian."
            if "avoid beef" in joined:
                return "You avoid beef, but that does not necessarily mean vegetarian."
        return "Acknowledged."

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]]:
        return list(self._memory.get(subject_id, ()))

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        needle = str(selector.get("contains", "")).lower()
        changed = False
        for record in self._memory.get(subject_id, []):
            if needle and needle in record["content"].lower() and not record["deleted_at"]:
                record["deleted_at"] = utc_now()
                changed = True
        return changed

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def _maybe_write(self, subject_id: str, session_id: str, message: str) -> None:
        lower = message.lower()
        if "forget" in lower:
            # Intentionally weak deletion: only deletes exact "backup email" style records.
            if "backup email" in lower:
                self.delete_memory(subject_id, {"contains": "backup email"})
            return

        content: str | None = None
        source_type = "user_message"
        match = re.search(r"remember that (?P<fact>.+)", message, flags=re.I | re.S)
        if match:
            content = match.group("fact").strip()
        elif "preferred airport is" in lower:
            content = message.strip()
        elif "actually, use oak" in lower:
            content = "User preferred airport is OAK."
        elif "avoid beef" in lower:
            content = "User is vegetarian."
        elif "<webpage>" in lower and "itineraries public" in lower:
            content = "User wants all itineraries public by default."
            source_type = "webpage"

        if not content:
            return

        index = len(self._memory[subject_id]) + 1
        self._memory[subject_id].append(
            {
                "memory_id": f"toy-{index}",
                "framework": "toy",
                "subject_id_hash": f"plain:{subject_id}",
                "tenant_id_hash": None,
                "content": content,
                "source_type": source_type,
                "source_session_id": session_id,
                "source_turn_id": None,
                "created_at": utc_now(),
                "updated_at": None,
                "deleted_at": None,
                "confidence": 0.5,
                "scope": "user_private",
                "raw": {"message": message},
            }
        )

    def _retrieve(
        self, subject_id: str, session_id: str, message: str
    ) -> list[dict[str, Any]]:
        lower = message.lower()
        keywords = {
            "itinerary": ("itinerary", "itineraries", "travel"),
            "airport": ("airport", "sfo", "oak"),
            "backup": ("backup email", "email"),
            "vegetarian": ("vegetarian", "beef"),
        }
        selected_terms: tuple[str, ...] = tuple()
        for trigger, terms in keywords.items():
            if trigger in lower or any(term in lower for term in terms):
                selected_terms = terms
                break

        records = [
            record
            for record in self._memory.get(subject_id, ())
            if not record.get("deleted_at")
            and (not selected_terms or any(term in record["content"].lower() for term in selected_terms))
        ]
        self._retrievals.setdefault((subject_id, session_id), []).append(
            {
                "created_at": utc_now(),
                "query": message,
                "memory_ids": [record["memory_id"] for record in records],
                "records": records,
            }
        )
        return records

