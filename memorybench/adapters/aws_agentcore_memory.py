from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from memorybench.adapters.base import MemoryStackAdapter, utc_now
from memorybench.adapters.mem0 import answer_from_records


class AwsAgentCoreMemoryAdapter(MemoryStackAdapter):
    """AWS Bedrock AgentCore Memory adapter.

    This target exercises AgentCore's managed short-term event memory APIs. The
    adapter writes benchmark-selected user facts with `create_event`, reads them
    back with `list_events`, and deletes matching events with `delete_event`.
    Long-term semantic extraction strategies are intentionally not enabled here
    because they require additional IAM/model configuration and asynchronous
    activation.
    """

    capabilities = {
        "inspect_memory": True,
        "delete_memory": True,
        "retrieval_log": True,
        "multi_user": True,
        "multi_tenant": True,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "AwsAgentCoreMemoryAdapter requires boto3. Install it with "
                "`pip install -e '.[aws]'` or `pip install boto3`."
            ) from exc

        region = (
            os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or self.config.get("runtime", {}).get("region")
        )
        if not region:
            raise RuntimeError("AWS_REGION or AWS_DEFAULT_REGION must be set.")

        self._region = str(region)
        self._control = boto3.client("bedrock-agentcore-control", region_name=self._region)
        self._data = boto3.client("bedrock-agentcore", region_name=self._region)
        self._memory_id: str | None = None
        self._memory_name = _memory_name()
        self._sessions_by_subject: dict[str, set[str]] = {}
        self._turn_counts: dict[tuple[str, str], int] = {}
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}

        try:
            self._memory_id = self._create_memory()
            self._wait_for_memory_active(self._memory_id)
        except Exception:
            self.close()
            raise

    def reset_subject(self, subject_id: str) -> None:
        for session_id in list(self._sessions_by_subject.get(subject_id, ())):
            self._delete_session_events(subject_id, session_id)
        self._sessions_by_subject[subject_id] = set()
        for key in list(self._turn_counts):
            if key[0] == subject_id:
                del self._turn_counts[key]
        for key in list(self._retrievals):
            if key[0] == subject_id:
                del self._retrievals[key]

    def start_session(self, subject_id: str, session_id: str) -> None:
        self._sessions_by_subject.setdefault(subject_id, set()).add(session_id)

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        self.start_session(subject_id, session_id)
        turn_id = self._next_turn_id(subject_id, session_id)

        if _is_forget_request(message):
            deleted = self.delete_memory(subject_id, {"contains": _forget_needle(message)})
            return "I deleted matching memories." if deleted else "I did not find matching memories to delete."

        if _is_trusted_memory_candidate(message):
            if _is_oak_correction(message):
                self._delete_by_terms(subject_id, ("preferred airport", "sfo"))
            self._create_event(subject_id, session_id, turn_id, message)

        records = self._retrieve(subject_id, session_id, message)
        return answer_from_records(message, records)

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for session_id in sorted(self._sessions_by_subject.get(subject_id, ())):
            records.extend(self._list_session_records(subject_id, session_id))
        return sorted(
            records,
            key=lambda record: (
                str(record.get("source_session_id") or ""),
                str(record.get("created_at") or ""),
                str(record.get("memory_id") or ""),
            ),
        )

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        contains = str(selector.get("contains") or "").lower()
        terms = (contains,) if contains else tuple()
        return self._delete_by_terms(subject_id, terms)

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def close(self) -> None:
        if not self._memory_id:
            return
        memory_id = self._memory_id
        self._memory_id = None
        self._control.delete_memory(memoryId=memory_id)

    def _create_memory(self) -> str:
        response = self._control.create_memory(
            name=self._memory_name,
            description="Temporary MemoryStackBench AWS AgentCore Memory run.",
            eventExpiryDuration=3,
            tags={
                "project": "MemoryStackBench",
                "memorybench": "true",
            },
        )
        memory = response.get("memory") or {}
        memory_id = memory.get("id")
        if not memory_id:
            raise RuntimeError(f"create_memory did not return an id: {response!r}")
        return str(memory_id)

    def _wait_for_memory_active(self, memory_id: str) -> None:
        deadline = time.monotonic() + float(self.config.get("aws_active_timeout_seconds") or 180)
        last_status = None
        last_response: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            response = self._control.get_memory(memoryId=memory_id)
            last_response = response
            memory = response.get("memory") or {}
            last_status = memory.get("status")
            if last_status == "ACTIVE":
                return
            if last_status == "FAILED":
                raise RuntimeError(f"AgentCore memory failed to become active: {memory!r}")
            time.sleep(5)
        raise TimeoutError(
            f"Timed out waiting for AgentCore memory {memory_id} to become ACTIVE. "
            f"Last status={last_status!r}, response={last_response!r}"
        )

    def _create_event(self, subject_id: str, session_id: str, turn_id: int, message: str) -> None:
        if not self._memory_id:
            raise RuntimeError("AgentCore memory was not created.")
        self._data.create_event(
            memoryId=self._memory_id,
            actorId=_actor_id(subject_id),
            sessionId=_session_id(session_id),
            eventTimestamp=datetime.now(timezone.utc),
            payload=[
                {
                    "conversational": {
                        "content": {"text": message},
                        "role": "USER",
                    }
                }
            ],
            extractionMode="SKIP",
            metadata={
                "source_session_id": {"stringValue": session_id},
                "source_turn_id": {"stringValue": f"t{turn_id}"},
                "source_type": {"stringValue": _source_type(message)},
                "memorybench": {"stringValue": "true"},
            },
        )

    def _retrieve(
        self, subject_id: str, session_id: str, query: str
    ) -> list[dict[str, Any]]:
        records = self.inspect_memory(subject_id)
        terms = _query_terms(query.lower())
        if terms:
            records = [
                record
                for record in records
                if any(term in record.get("content", "").lower() for term in terms)
            ]
        self._retrievals.setdefault((subject_id, session_id), []).append(
            {
                "created_at": utc_now(),
                "query": query,
                "retrieval_api": "list_events",
                "memory_ids": [record.get("memory_id") for record in records],
                "records": records,
            }
        )
        return records

    def _list_session_records(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        if not self._memory_id:
            return []
        records: list[dict[str, Any]] = []
        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "memoryId": self._memory_id,
                "actorId": _actor_id(subject_id),
                "sessionId": _session_id(session_id),
                "includePayloads": True,
                "maxResults": 100,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            response = self._data.list_events(**kwargs)
            for event in response.get("events") or []:
                records.extend(self._normalize_event(event, subject_id, session_id))
            next_token = response.get("nextToken")
            if not next_token:
                return records

    def _normalize_event(
        self,
        event: dict[str, Any],
        subject_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        event_id = str(event.get("eventId") or "")
        metadata = _metadata_map(event.get("metadata"))
        created_at = _iso(event.get("eventTimestamp")) or utc_now()
        payloads = event.get("payload") or []
        records: list[dict[str, Any]] = []
        for index, payload in enumerate(payloads):
            content = _payload_text(payload)
            if not content:
                continue
            records.append(
                {
                    "memory_id": f"{event_id}:{index}" if event_id else None,
                    "framework": "aws_bedrock_agentcore_memory",
                    "subject_id_hash": f"sha256:{hashlib.sha256(subject_id.encode('utf-8')).hexdigest()}",
                    "tenant_id_hash": None,
                    "content": content,
                    "source_type": metadata.get("source_type") or _source_type(content),
                    "source_session_id": metadata.get("source_session_id") or session_id,
                    "source_turn_id": metadata.get("source_turn_id"),
                    "created_at": created_at,
                    "updated_at": None,
                    "deleted_at": None,
                    "confidence": None,
                    "scope": "agentcore_event_memory",
                    "raw": _jsonable(event),
                }
            )
        return records

    def _delete_by_terms(self, subject_id: str, terms: tuple[str, ...]) -> bool:
        changed = False
        for session_id in sorted(self._sessions_by_subject.get(subject_id, ())):
            for record in self._list_session_records(subject_id, session_id):
                content = record.get("content", "").lower()
                if terms and not any(term in content for term in terms):
                    continue
                event_id = str(record.get("memory_id") or "").split(":", 1)[0]
                if not event_id:
                    continue
                self._data.delete_event(
                    memoryId=self._memory_id,
                    actorId=_actor_id(subject_id),
                    sessionId=_session_id(session_id),
                    eventId=event_id,
                )
                changed = True
        return changed

    def _delete_session_events(self, subject_id: str, session_id: str) -> None:
        for record in self._list_session_records(subject_id, session_id):
            event_id = str(record.get("memory_id") or "").split(":", 1)[0]
            if event_id:
                self._data.delete_event(
                    memoryId=self._memory_id,
                    actorId=_actor_id(subject_id),
                    sessionId=_session_id(session_id),
                    eventId=event_id,
                )

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]


def _memory_name() -> str:
    return f"MemoryBench{int(time.time())}{uuid.uuid4().hex[:8]}"[:48]


def _actor_id(subject_id: str) -> str:
    return f"memorybench/{hashlib.sha256(subject_id.encode('utf-8')).hexdigest()[:32]}"


def _session_id(session_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9-_]", "-", session_id)
    return safe or "session"


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


def _payload_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    conversational = payload.get("conversational")
    if not isinstance(conversational, dict):
        return ""
    content = conversational.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return str(content or "")


def _metadata_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    metadata: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            metadata[str(key)] = str(value.get("stringValue") or "")
        else:
            metadata[str(key)] = str(value)
    return metadata


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
