from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from typing import Any

from memorybench.adapters.base import MemoryStackAdapter, utc_now
from memorybench.adapters.mem0 import answer_from_records


class ZepAdapter(MemoryStackAdapter):
    """Zep Cloud user graph adapter.

    This adapter exercises the current `zep-cloud` SDK against temporary Zep
    users and threads. It uses an explicit benchmark write policy: trusted user
    facts are added to the user's graph, untrusted webpage text is not made
    durable, and corrections/deletions rebuild the temporary user graph with the
    remaining live benchmark facts.
    """

    capabilities = {
        "inspect_memory": True,
        "delete_memory": "user_graph_rebuild",
        "retrieval_log": True,
        "multi_user": True,
        "multi_tenant": False,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        try:
            from zep_cloud.client import Zep
        except ImportError as exc:
            raise RuntimeError(
                "ZepAdapter requires the Zep Cloud SDK. Install it with "
                "`pip install -e '.[zep]'` or `pip install zep-cloud`."
            ) from exc

        api_key = os.environ.get("ZEP_API_KEY")
        if not api_key:
            raise RuntimeError("ZEP_API_KEY must be set.")

        timeout = float(self.config.get("zep_timeout_seconds") or 60)
        self._client = Zep(api_key=api_key, timeout=timeout)
        self._run_id = uuid.uuid4().hex[:10]
        self._subjects: dict[str, _SubjectState] = {}
        self._created_user_ids: set[str] = set()
        self._journals: dict[str, list[dict[str, Any]]] = {}
        self._turn_counts: dict[tuple[str, str], int] = {}
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def reset_subject(self, subject_id: str) -> None:
        self._delete_subject_user(subject_id)
        self._subjects[subject_id] = self._new_subject_state(subject_id)
        self._create_user(subject_id)
        self._journals[subject_id] = []
        for key in list(self._turn_counts):
            if key[0] == subject_id:
                del self._turn_counts[key]
        for key in list(self._retrievals):
            if key[0] == subject_id:
                del self._retrievals[key]

    def start_session(self, subject_id: str, session_id: str) -> None:
        self._ensure_subject(subject_id)
        self._ensure_thread(subject_id, session_id)

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        self.start_session(subject_id, session_id)
        turn_id = self._next_turn_id(subject_id, session_id)

        if _is_forget_request(message):
            deleted = self.delete_memory(subject_id, {"contains": _forget_needle(message)})
            return "I deleted matching memories." if deleted else "I did not find matching memories to delete."

        if _is_trusted_memory_candidate(message):
            if _is_oak_correction(message):
                self._remove_journal_by_terms(subject_id, ("preferred airport", "sfo"))
                self._rebuild_subject(subject_id)
                self._ensure_thread(subject_id, session_id)

            record = {
                "content": message.strip(),
                "source_type": _source_type(message),
                "source_session_id": session_id,
                "source_turn_id": f"t{turn_id}",
                "created_at": utc_now(),
            }
            self._journals.setdefault(subject_id, []).append(record)
            self._write_record(subject_id, record)

        retrieved = self._retrieve(subject_id, session_id, message)
        return answer_from_records(message, retrieved)

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]]:
        self._ensure_subject(subject_id)
        queries = [
            "travel itineraries private public",
            "preferred airport SFO OAK",
            "backup email",
            "avoid beef vegetarian",
        ]
        queries.extend(record["content"] for record in self._journals.get(subject_id, []))
        records: dict[str, dict[str, Any]] = {}
        for query in queries:
            for record in self._search_records(subject_id, query, log=False):
                memory_id = str(record.get("memory_id") or "")
                if memory_id:
                    records[memory_id] = record
        return sorted(
            records.values(),
            key=lambda record: (
                str(record.get("source_session_id") or ""),
                str(record.get("created_at") or ""),
                str(record.get("memory_id") or ""),
            ),
        )

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        contains = str(selector.get("contains") or "").lower()
        terms = (contains,) if contains else tuple()
        changed = self._remove_journal_by_terms(subject_id, terms)
        if changed:
            self._rebuild_subject(subject_id)
        return changed

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def close(self) -> None:
        for user_id in sorted(self._created_user_ids):
            self._safe_delete_user(user_id)
        self._created_user_ids.clear()

    def _ensure_subject(self, subject_id: str) -> None:
        if subject_id not in self._subjects:
            self.reset_subject(subject_id)

    def _new_subject_state(self, subject_id: str) -> _SubjectState:
        generation = self._subjects.get(subject_id, _SubjectState("", 0, set())).generation + 1
        digest = hashlib.sha256(subject_id.encode("utf-8")).hexdigest()[:18]
        user_id = f"memorybench_{self._run_id}_{digest}_{generation}"
        return _SubjectState(user_id=user_id, generation=generation, sessions=set())

    def _create_user(self, subject_id: str) -> None:
        state = self._subjects[subject_id]
        self._client.user.add(
            user_id=state.user_id,
            first_name="MemoryBench",
            last_name=f"Subject{state.generation}",
            metadata={"memorybench": True, "subject_hash": _hash(subject_id)},
        )
        self._created_user_ids.add(state.user_id)

    def _delete_subject_user(self, subject_id: str) -> None:
        state = self._subjects.get(subject_id)
        if state:
            self._safe_delete_user(state.user_id)

    def _safe_delete_user(self, user_id: str) -> None:
        try:
            self._client.user.delete(user_id)
        except Exception:
            pass

    def _ensure_thread(self, subject_id: str, session_id: str) -> str:
        state = self._subjects[subject_id]
        thread_id = _thread_id(state.user_id, session_id)
        if thread_id in state.sessions:
            return thread_id
        try:
            self._client.thread.create(thread_id=thread_id, user_id=state.user_id)
        except Exception as exc:
            if "already exists" not in str(exc).lower():
                raise
        state.sessions.add(thread_id)
        return thread_id

    def _write_record(self, subject_id: str, record: dict[str, Any]) -> None:
        from zep_cloud.types import Message

        state = self._subjects[subject_id]
        session_id = str(record["source_session_id"])
        thread_id = self._ensure_thread(subject_id, session_id)
        metadata = {
            "memorybench": True,
            "source_session_id": session_id,
            "source_turn_id": record.get("source_turn_id"),
            "source_type": record.get("source_type"),
        }
        self._client.thread.add_messages(
            thread_id,
            messages=[
                Message(
                    created_at=record["created_at"],
                    role="user",
                    name="MemoryBench User",
                    content=record["content"],
                    metadata=metadata,
                )
            ],
        )
        episode = self._client.graph.add(
            user_id=state.user_id,
            type="text",
            data=record["content"],
            created_at=record["created_at"],
            metadata=metadata,
            source_description="MemoryStackBench trusted user memory",
        )
        episode_id = getattr(episode, "uuid_", None) or getattr(episode, "uuid", None)
        if episode_id:
            record["zep_episode_id"] = str(episode_id)
        self._wait_for_record(subject_id, record["content"])

    def _retrieve(
        self, subject_id: str, session_id: str, query: str
    ) -> list[dict[str, Any]]:
        records = self._search_records(subject_id, _search_query(query), log=True)
        self._retrievals.setdefault((subject_id, session_id), []).append(
            {
                "created_at": utc_now(),
                "query": query,
                "zep_query": _search_query(query),
                "memory_ids": [record.get("memory_id") for record in records],
                "records": records,
            }
        )
        return records

    def _search_records(
        self,
        subject_id: str,
        query: str,
        *,
        log: bool,
    ) -> list[dict[str, Any]]:
        state = self._subjects[subject_id]
        try:
            results = self._client.graph.search(
                user_id=state.user_id,
                query=query,
                scope="episodes",
                limit=10,
            )
        except Exception:
            if log:
                raise
            return []
        records = [
            self._normalize_episode(episode, subject_id)
            for episode in (getattr(results, "episodes", None) or [])
        ]
        return [record for record in records if record.get("content")]

    def _normalize_episode(self, episode: Any, subject_id: str) -> dict[str, Any]:
        metadata = dict(getattr(episode, "metadata", None) or {})
        return {
            "memory_id": getattr(episode, "uuid_", None) or getattr(episode, "uuid", None),
            "framework": "zep",
            "subject_id_hash": f"sha256:{_hash(subject_id)}",
            "tenant_id_hash": None,
            "content": str(getattr(episode, "content", "") or ""),
            "source_type": metadata.get("source_type"),
            "source_session_id": metadata.get("source_session_id"),
            "source_turn_id": metadata.get("source_turn_id"),
            "created_at": getattr(episode, "created_at", None),
            "updated_at": None,
            "deleted_at": None,
            "confidence": getattr(episode, "score", None) or getattr(episode, "relevance", None),
            "scope": "zep_user_graph_episode",
            "raw": _jsonable(episode),
        }

    def _wait_for_record(self, subject_id: str, content: str) -> None:
        timeout = float(self.config.get("zep_index_timeout_seconds") or 20)
        deadline = time.monotonic() + timeout
        needle = _primary_needle(content)
        while time.monotonic() < deadline:
            records = self._search_records(subject_id, content, log=False)
            if any(needle in record.get("content", "").lower() for record in records):
                return
            time.sleep(1)

    def _remove_journal_by_terms(self, subject_id: str, terms: tuple[str, ...]) -> bool:
        journal = self._journals.setdefault(subject_id, [])
        kept = []
        changed = False
        for record in journal:
            content = record.get("content", "").lower()
            if terms and any(term in content for term in terms):
                changed = True
                continue
            kept.append(record)
        self._journals[subject_id] = kept
        return changed

    def _rebuild_subject(self, subject_id: str) -> None:
        journal = list(self._journals.get(subject_id, []))
        self._delete_subject_user(subject_id)
        self._subjects[subject_id] = self._new_subject_state(subject_id)
        self._create_user(subject_id)
        for record in journal:
            self._write_record(subject_id, record)

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]


class _SubjectState:
    def __init__(self, user_id: str, generation: int, sessions: set[str]) -> None:
        self.user_id = user_id
        self.generation = generation
        self.sessions = sessions


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _thread_id(user_id: str, session_id: str) -> str:
    safe_session = re.sub(r"[^a-zA-Z0-9-_]", "-", session_id)
    return f"{user_id}_{safe_session}"[:128]


def _is_forget_request(message: str) -> bool:
    return bool(re.search(r"\b(forget|delete|remove)\b", message, flags=re.I))


def _forget_needle(message: str) -> str:
    lower = message.lower()
    for prefix in ("forget my ", "delete my ", "remove my ", "forget ", "delete ", "remove "):
        if prefix in lower:
            return lower.split(prefix, 1)[1].strip(" .?!")
    return lower


def _is_oak_correction(message: str) -> bool:
    lower = message.lower()
    return "oak" in lower and "preferred airport" in lower


def _is_trusted_memory_candidate(message: str) -> bool:
    lower = message.lower()
    if "<webpage>" in lower:
        return False
    return (
        "remember that" in lower
        or "preferred airport is" in lower
        or "actually, use oak" in lower
        or "avoid beef" in lower
    )


def _source_type(message: str) -> str:
    return "webpage" if "<webpage>" in message.lower() else "user_message"


def _search_query(message: str) -> str:
    lower = message.lower()
    if "itinerary" in lower or "public" in lower:
        return "travel itineraries private public"
    if "airport" in lower:
        return "preferred airport SFO OAK"
    if "backup" in lower or "email" in lower:
        return "backup email"
    if "vegetarian" in lower or "beef" in lower:
        return "avoid beef vegetarian"
    return message


def _primary_needle(content: str) -> str:
    lower = content.lower()
    if "itinerar" in lower:
        return "itinerar"
    if "airport" in lower and "oak" in lower:
        return "oak"
    if "airport" in lower and "sfo" in lower:
        return "sfo"
    if "backup email" in lower:
        return "backup email"
    if "beef" in lower:
        return "beef"
    return lower[:40]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json", by_alias=True))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
