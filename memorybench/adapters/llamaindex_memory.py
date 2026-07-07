from __future__ import annotations

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


class LlamaIndexMemoryAdapter(MemoryStackAdapter):
    """LlamaIndex ChatMemoryBuffer adapter.

    LlamaIndex exposes memory primitives rather than a full benchmark-specific
    extraction policy. This adapter uses the real ChatMemoryBuffer API and an
    explicit policy for trusted writes, corrections, retrieval, and deletion.
    """

    capabilities = {
        "inspect_memory": True,
        "delete_memory": "buffer_rebuild",
        "retrieval_log": True,
        "multi_user": True,
        "multi_tenant": False,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        try:
            from llama_index.core.llms import ChatMessage, MessageRole
            from llama_index.core.memory import ChatMemoryBuffer
        except ImportError as exc:
            raise RuntimeError(
                "LlamaIndexMemoryAdapter requires LlamaIndex. Install it with "
                "`pip install -e '.[llamaindex]'` or `pip install llama-index`."
            ) from exc

        self._ChatMemoryBuffer = ChatMemoryBuffer
        self._ChatMessage = ChatMessage
        self._MessageRole = MessageRole
        self._buffers: dict[str, Any] = {}
        self._memory_counts: dict[str, int] = {}
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._turn_counts: dict[tuple[str, str], int] = {}

    def reset_subject(self, subject_id: str) -> None:
        if subject_id in self._buffers:
            self._buffers[subject_id].reset()
        self._buffers[subject_id] = self._new_buffer()
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
        buffer = self._buffer(subject_id)
        records = []
        for index, message in enumerate(buffer.get_all(), start=1):
            metadata = dict(getattr(message, "additional_kwargs", None) or {})
            content = _message_content(message)
            memory_id = metadata.get("memory_id") or f"li-unknown-{index:04d}"
            records.append(
                {
                    "memory_id": memory_id,
                    "framework": "llamaindex_memory",
                    "subject_id_hash": f"plain:{subject_id}",
                    "tenant_id_hash": None,
                    "content": content,
                    "source_type": metadata.get("source_type"),
                    "source_session_id": metadata.get("source_session_id"),
                    "source_turn_id": metadata.get("source_turn_id"),
                    "created_at": metadata.get("created_at"),
                    "updated_at": metadata.get("updated_at"),
                    "deleted_at": None,
                    "confidence": metadata.get("confidence"),
                    "scope": "user_private",
                    "raw": _message_raw(message),
                }
            )
        return sorted(records, key=lambda record: str(record.get("memory_id")))

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        contains = str(selector.get("contains") or "").lower()
        return self._delete_messages(
            subject_id,
            lambda message: not contains or contains in _message_content(message).lower(),
        )

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def _new_buffer(self) -> Any:
        return self._ChatMemoryBuffer.from_defaults(token_limit=10000)

    def _buffer(self, subject_id: str) -> Any:
        if subject_id not in self._buffers:
            self._buffers[subject_id] = self._new_buffer()
        return self._buffers[subject_id]

    def _write_memory(
        self,
        subject_id: str,
        session_id: str,
        turn_id: int,
        content: str,
        source: str,
    ) -> None:
        self._memory_counts[subject_id] = self._memory_counts.get(subject_id, 0) + 1
        metadata = benchmark_metadata(session_id, turn_id, source_type=source)
        metadata["memory_id"] = f"li-{self._memory_counts[subject_id]:04d}"
        metadata["updated_at"] = utc_now()
        self._buffer(subject_id).put(
            self._ChatMessage(
                role=self._MessageRole.USER,
                content=content,
                additional_kwargs=metadata,
            )
        )

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
        self._delete_messages(
            subject_id,
            lambda message: any(term in _message_content(message).lower() for term in terms),
        )

    def _delete_messages(self, subject_id: str, predicate: Any) -> bool:
        buffer = self._buffer(subject_id)
        messages = list(buffer.get_all())
        kept = [message for message in messages if not predicate(message)]
        if len(kept) == len(messages):
            return False
        buffer.reset()
        for message in kept:
            buffer.put(message)
        return True

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]


def _message_content(message: Any) -> str:
    content = getattr(message, "content", None)
    if content:
        return str(content)
    blocks = getattr(message, "blocks", None) or []
    return " ".join(str(getattr(block, "text", "")) for block in blocks).strip()


def _message_raw(message: Any) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        return message.model_dump(mode="json")
    if hasattr(message, "dict"):
        return message.dict()
    return {"content": _message_content(message)}
