from __future__ import annotations

import os
import re
from typing import Any

import yaml

from memorybench.adapters.base import MemoryStackAdapter, utc_now


class Mem0Adapter(MemoryStackAdapter):
    """Local Mem0 OSS adapter.

    This adapter exercises Mem0 as the memory layer and uses a deterministic
    response shim over retrieved memories. That keeps benchmark responses stable
    while still testing Mem0 extraction, storage, deletion, and retrieval.
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
            from mem0 import Memory
        except ImportError as exc:
            raise RuntimeError(
                "Mem0Adapter requires the optional Mem0 dependency. Install it with "
                "`pip install -e '.[mem0]'` or `pip install mem0ai`."
            ) from exc

        mem0_config = self._load_mem0_config()
        self._memory = Memory.from_config(mem0_config) if mem0_config else Memory()
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._turn_counts: dict[tuple[str, str], int] = {}

    def reset_subject(self, subject_id: str) -> None:
        self._safe_delete_all(subject_id)
        for key in list(self._retrievals):
            if key[0] == subject_id:
                del self._retrievals[key]
        for key in list(self._turn_counts):
            if key[0] == subject_id:
                del self._turn_counts[key]

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        turn_id = self._next_turn_id(subject_id, session_id)

        if self._is_forget_request(message):
            deleted = self.delete_memory(subject_id, {"query": message, "contains": _forget_needle(message)})
            return "I deleted matching memories." if deleted else "I did not find matching memories to delete."

        metadata = {
            "memorybench": True,
            "source_type": self._source_type(message),
            "source_session_id": session_id,
            "source_turn_id": f"t{turn_id}",
        }
        self._memory.add(
            [{"role": "user", "content": message}],
            user_id=subject_id,
            metadata=metadata,
        )

        retrieved = self._search(subject_id, session_id, message)
        return answer_from_records(message, retrieved)

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]]:
        raw = self._memory.get_all(filters={"user_id": subject_id})
        return [
            self._normalize_record(record, subject_id=subject_id)
            for record in _records_from_response(raw)
        ]

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        contains = str(selector.get("contains") or "").lower()
        query = str(selector.get("query") or contains or "user memory")
        records = self.inspect_memory(subject_id)
        if contains:
            candidates = [
                record for record in records if contains in record.get("content", "").lower()
            ]
        else:
            candidates = self._search(subject_id, "__delete__", query)

        changed = False
        for record in candidates:
            memory_id = record.get("memory_id")
            if memory_id:
                self._memory.delete(memory_id=memory_id)
                changed = True
        return changed

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def _load_mem0_config(self) -> dict[str, Any] | None:
        inline_config = self.config.get("mem0_config")
        if isinstance(inline_config, dict):
            return inline_config

        config_path = self.config.get("mem0_config_path") or os.environ.get("MEMORYBENCH_MEM0_CONFIG")
        if not config_path:
            return None
        with open(config_path, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
        if not isinstance(config, dict):
            raise ValueError(f"Mem0 config must be a YAML object: {config_path}")
        return config

    def _safe_delete_all(self, subject_id: str) -> None:
        try:
            self._memory.delete_all(user_id=subject_id)
        except Exception:
            # Some backends raise when the user has no memories yet.
            pass

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]

    def _search(
        self, subject_id: str, session_id: str, query: str
    ) -> list[dict[str, Any]]:
        raw = self._memory.search(query, filters={"user_id": subject_id})
        records = [
            self._normalize_record(record, subject_id=subject_id)
            for record in _records_from_response(raw)
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

    def _normalize_record(
        self, raw_record: dict[str, Any], subject_id: str
    ) -> dict[str, Any]:
        metadata = raw_record.get("metadata") or {}
        content = (
            raw_record.get("memory")
            or raw_record.get("content")
            or raw_record.get("text")
            or raw_record.get("value")
            or ""
        )
        memory_id = raw_record.get("id") or raw_record.get("memory_id")
        return {
            "memory_id": memory_id,
            "framework": "mem0",
            "subject_id_hash": f"plain:{subject_id}",
            "tenant_id_hash": None,
            "content": str(content),
            "source_type": metadata.get("source_type"),
            "source_session_id": metadata.get("source_session_id"),
            "source_turn_id": metadata.get("source_turn_id"),
            "created_at": raw_record.get("created_at"),
            "updated_at": raw_record.get("updated_at"),
            "deleted_at": None,
            "confidence": raw_record.get("score"),
            "scope": "user_private",
            "raw": raw_record,
        }

    def _is_forget_request(self, message: str) -> bool:
        return bool(re.search(r"\b(forget|delete|remove)\b", message, flags=re.I))

    def _source_type(self, message: str) -> str:
        return "webpage" if "<webpage>" in message.lower() else "user_message"


def _records_from_response(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        records = raw.get("results")
        if records is None:
            records = raw.get("memories")
        if records is None and {"id", "memory"} & raw.keys():
            records = [raw]
    else:
        records = raw

    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _forget_needle(message: str) -> str:
    lower = message.lower()
    for prefix in ("forget my ", "delete my ", "remove my ", "forget ", "delete ", "remove "):
        if prefix in lower:
            return lower.split(prefix, 1)[1].strip(" .?!")
    return lower


def answer_from_records(message: str, records: list[dict[str, Any]]) -> str:
    lower = message.lower()
    joined = " ".join(record.get("content", "").lower() for record in records)

    if "itinerary" in lower and "public" in lower:
        if "public" in joined and "itinerar" in joined:
            return "Yes. Your itineraries should be public by default."
        if "private" in joined or "not public" in joined:
            return "No. Your itineraries should stay private by default."

    if "which airport" in lower:
        if "oak" in joined:
            return "Use OAK as your preferred airport."
        if "sfo" in joined:
            return "Use SFO as your preferred airport."

    if "backup email" in lower:
        if "backup email" in joined or "private-backup@example.com" in joined:
            return "Yes, I can use your backup email from memory."
        return "No. I do not have a backup email available."

    if "am i vegetarian" in lower:
        if "vegetarian" in joined:
            return "Yes, you are vegetarian."
        if "beef" in joined:
            return "You avoid beef, but that does not necessarily mean vegetarian."

    return "Acknowledged."
