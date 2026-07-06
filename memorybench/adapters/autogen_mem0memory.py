from __future__ import annotations

import asyncio
import copy
import hashlib
import re
from typing import Any

from autogen_core.memory import MemoryContent
from autogen_ext.memory.mem0 import Mem0Memory

from memorybench.adapters.base import MemoryStackAdapter, utc_now
from memorybench.adapters.mem0 import _forget_needle, _records_from_response, answer_from_records


class AutoGenMem0MemoryAdapter(MemoryStackAdapter):
    """AutoGen memory adapter backed by the official Mem0Memory component."""

    capabilities = {
        "inspect_memory": True,
        "delete_memory": True,
        "retrieval_log": True,
        "multi_user": True,
        "multi_tenant": False,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._memories: dict[str, Mem0Memory] = {}
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._turn_counts: dict[tuple[str, str], int] = {}

    def reset_subject(self, subject_id: str) -> None:
        self._close_all_memories()
        memory = self._memory_for_subject(subject_id)
        self._run(memory.clear())
        for key in list(self._retrievals):
            if key[0] == subject_id:
                del self._retrievals[key]
        for key in list(self._turn_counts):
            if key[0] == subject_id:
                del self._turn_counts[key]

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        memory = self._memory_for_subject(subject_id)
        turn_id = self._next_turn_id(subject_id, session_id)

        if any(word in message.lower() for word in ("forget", "delete", "remove")):
            deleted = self.delete_memory(
                subject_id,
                {"query": message, "contains": _forget_needle(message)},
            )
            return "I deleted matching memories." if deleted else "I did not find matching memories to delete."

        self._run(
            memory.add(
                MemoryContent(
                    content=message,
                    mime_type="text/plain",
                    metadata={
                        "source_type": "webpage" if "<webpage>" in message.lower() else "user_message",
                        "source_session_id": session_id,
                        "source_turn_id": f"t{turn_id}",
                    },
                )
            )
        )
        records = self._search(subject_id, session_id, message)
        return answer_from_records(message, records)

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]]:
        memory = self._memory_for_subject(subject_id)
        raw = memory._client.get_all(filters={"user_id": subject_id})  # type: ignore[attr-defined]
        return [
            self._normalize_record(record, subject_id)
            for record in _records_from_response(raw)
        ]

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        contains = str(selector.get("contains") or "").lower()
        records = self.inspect_memory(subject_id)
        candidates = [
            record for record in records if contains and contains in record.get("content", "").lower()
        ]
        if not candidates:
            candidates = self._search(subject_id, "__delete__", str(selector.get("query") or contains))

        memory = self._memory_for_subject(subject_id)
        changed = False
        for record in candidates:
            memory_id = record.get("memory_id")
            if memory_id:
                memory._client.delete(memory_id=memory_id)  # type: ignore[attr-defined]
                changed = True
        return changed

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def close(self) -> None:
        self._close_all_memories()

    def _close_all_memories(self) -> None:
        for memory in self._memories.values():
            self._close_qdrant_clients(memory)
            self._run(memory.close())
        self._memories.clear()

    def _close_qdrant_clients(self, memory: Mem0Memory) -> None:
        client = getattr(memory, "_client", None)
        for attr_path in (
            ("vector_store", "client"),
            ("graph", "client"),
            ("_telemetry_vector_store", "client"),
        ):
            current = client
            for attr in attr_path:
                current = getattr(current, attr, None)
                if current is None:
                    break
            close = getattr(current, "close", None)
            if callable(close):
                close()

    def _memory_for_subject(self, subject_id: str) -> Mem0Memory:
        if subject_id not in self._memories:
            self._memories[subject_id] = Mem0Memory(
                user_id=subject_id,
                limit=10,
                is_cloud=False,
                config=self._subject_config(subject_id),
            )
        return self._memories[subject_id]

    def _subject_config(self, subject_id: str) -> dict[str, Any]:
        config = copy.deepcopy(self.config["mem0_config"])
        suffix = hashlib.sha256(subject_id.encode("utf-8")).hexdigest()[:16]
        vector_config = config.setdefault("vector_store", {}).setdefault("config", {})
        base_path = str(vector_config.get("path", "/tmp/memorystackbench/autogen_qdrant"))
        vector_config["path"] = f"{base_path}/{suffix}"
        collection = str(vector_config.get("collection_name", "memorystackbench_autogen_mem0"))
        vector_config["collection_name"] = _safe_collection_name(f"{collection}_{suffix}")
        return config

    def _search(
        self, subject_id: str, session_id: str, query: str
    ) -> list[dict[str, Any]]:
        memory = self._memory_for_subject(subject_id)
        raw = memory._client.search(  # type: ignore[attr-defined]
            query,
            filters={"user_id": subject_id},
            limit=10,
        )
        records = [
            self._normalize_record(record, subject_id)
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

    def _normalize_record(self, raw_record: dict[str, Any], subject_id: str) -> dict[str, Any]:
        metadata = raw_record.get("metadata") or {}
        return {
            "memory_id": raw_record.get("id") or raw_record.get("memory_id"),
            "framework": "autogen_mem0memory",
            "subject_id_hash": f"plain:{subject_id}",
            "tenant_id_hash": None,
            "content": str(
                raw_record.get("memory")
                or raw_record.get("content")
                or raw_record.get("text")
                or raw_record.get("value")
                or ""
            ),
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

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]

    def _run(self, awaitable: Any) -> Any:
        return asyncio.run(awaitable)


def _safe_collection_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\\-]+", "_", value)
    return cleaned[:120]
