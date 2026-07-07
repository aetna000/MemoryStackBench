from __future__ import annotations

import asyncio
import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any

from memorybench.adapters.base import MemoryStackAdapter, utc_now
from memorybench.adapters.mem0 import answer_from_records


class GraphitiAdapter(MemoryStackAdapter):
    """Graphiti + Neo4j adapter.

    Graphiti builds temporal facts from episodes. This adapter ingests benchmark
    turns as Graphiti episodes, retrieves facts through Graphiti search, and
    inspects/deletes graph records through Neo4j for deterministic evidence.
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
        os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")
        os.environ.setdefault("SEMAPHORE_LIMIT", "2")
        try:
            from graphiti_core import Graphiti
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise RuntimeError(
                "GraphitiAdapter requires `graphiti-core` and Neo4j. Install it with "
                "`pip install graphiti-core` and start the target Neo4j service."
            ) from exc

        neo4j_config = self.config.get("neo4j") or {}
        self._uri = str(neo4j_config.get("uri") or os.environ.get("NEO4J_URI") or "bolt://localhost:7687")
        self._user = str(neo4j_config.get("user") or os.environ.get("NEO4J_USER") or "neo4j")
        self._password = str(
            neo4j_config.get("password") or os.environ.get("NEO4J_PASSWORD") or "memorystackbench"
        )
        self._loop = asyncio.new_event_loop()
        self._graphiti = Graphiti(self._uri, self._user, self._password)
        self._driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._turn_counts: dict[tuple[str, str], int] = {}
        self._run(self._graphiti.build_indices_and_constraints(delete_existing=False))

    def reset_subject(self, subject_id: str) -> None:
        group_id = _group_id(subject_id)
        with self._driver.session() as session:
            session.run(
                """
                MATCH (n)
                WHERE n.group_id = $group_id
                DETACH DELETE n
                """,
                group_id=group_id,
            ).consume()
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

        self._run(self._add_episode(subject_id, session_id, turn_id, message))
        retrieved = self._retrieve(subject_id, session_id, message)
        return answer_from_records(message, retrieved)

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]]:
        group_id = _group_id(subject_id)
        rows = []
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (:Entity)-[edge:RELATES_TO]->(:Entity)
                WHERE edge.group_id = $group_id
                OPTIONAL MATCH (episode:Episodic {group_id: $group_id})
                WHERE edge.uuid IN coalesce(episode.entity_edges, [])
                RETURN
                  edge.uuid AS uuid,
                  edge.fact AS fact,
                  edge.name AS name,
                  edge.created_at AS created_at,
                  edge.valid_at AS valid_at,
                  edge.invalid_at AS invalid_at,
                  edge.expired_at AS expired_at,
                  edge.group_id AS group_id,
                  collect(episode.source_description)[0] AS source_description
                ORDER BY edge.created_at
                """,
                group_id=group_id,
            )
            rows = [row.data() for row in result]

        records = []
        for row in rows:
            source = _parse_source_description(row.get("source_description"))
            deleted_at = _iso(row.get("invalid_at") or row.get("expired_at"))
            records.append(
                {
                    "memory_id": row.get("uuid"),
                    "framework": "graphiti",
                    "subject_id_hash": f"plain:{subject_id}",
                    "tenant_id_hash": None,
                    "content": str(row.get("fact") or row.get("name") or ""),
                    "source_type": source.get("source_type"),
                    "source_session_id": source.get("source_session_id"),
                    "source_turn_id": source.get("source_turn_id"),
                    "created_at": _iso(row.get("created_at")) or utc_now(),
                    "updated_at": _iso(row.get("valid_at")),
                    "deleted_at": deleted_at,
                    "confidence": None,
                    "scope": "user_private",
                    "raw": {
                        "group_id": row.get("group_id"),
                        "source_description": row.get("source_description"),
                    },
                }
            )
        return records

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        group_id = _group_id(subject_id)
        contains = str(selector.get("contains") or "").lower()
        if not contains:
            return False

        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (:Entity)-[edge:RELATES_TO]->(:Entity)
                WHERE edge.group_id = $group_id
                  AND toLower(coalesce(edge.fact, edge.name, '')) CONTAINS $contains
                WITH collect(edge) AS edges
                FOREACH (edge IN edges | DELETE edge)
                RETURN size(edges) AS deleted
                """,
                group_id=group_id,
                contains=contains,
            ).single()
        return bool(result and result["deleted"])

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def close(self) -> None:
        self._run(self._graphiti.close())
        self._driver.close()
        self._loop.close()

    async def _add_episode(self, subject_id: str, session_id: str, turn_id: int, message: str) -> None:
        from graphiti_core.nodes import EpisodeType

        await self._graphiti.add_episode(
            name=f"{subject_id}:{session_id}:t{turn_id}",
            episode_body=message,
            source=EpisodeType.message,
            source_description=_source_description(session_id, turn_id, _source_type(message)),
            reference_time=datetime.now(timezone.utc),
            group_id=_group_id(subject_id),
        )

    def _retrieve(self, subject_id: str, session_id: str, query: str) -> list[dict[str, Any]]:
        raw_results = self._run(
            self._graphiti.search(
                query,
                group_ids=[_group_id(subject_id)],
                num_results=10,
            )
        )
        records = [_record_from_edge(edge, subject_id) for edge in raw_results]
        self._retrievals.setdefault((subject_id, session_id), []).append(
            {
                "created_at": utc_now(),
                "query": query,
                "memory_ids": [record.get("memory_id") for record in records],
                "records": records,
            }
        )
        return records

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]

    def _run(self, awaitable: Any) -> Any:
        return self._loop.run_until_complete(awaitable)


def _record_from_edge(edge: Any, subject_id: str) -> dict[str, Any]:
    return {
        "memory_id": getattr(edge, "uuid", None),
        "framework": "graphiti",
        "subject_id_hash": f"plain:{subject_id}",
        "tenant_id_hash": None,
        "content": str(getattr(edge, "fact", "") or ""),
        "source_type": None,
        "source_session_id": None,
        "source_turn_id": None,
        "created_at": _iso(getattr(edge, "created_at", None)) or utc_now(),
        "updated_at": _iso(getattr(edge, "valid_at", None)),
        "deleted_at": _iso(getattr(edge, "invalid_at", None) or getattr(edge, "expired_at", None)),
        "confidence": None,
        "scope": "user_private",
        "raw": {
            "name": getattr(edge, "name", None),
            "source_node_uuid": getattr(edge, "source_node_uuid", None),
            "target_node_uuid": getattr(edge, "target_node_uuid", None),
        },
    }


def _source_description(session_id: str, turn_id: int, source_type: str) -> str:
    return f"memorybench|source_session_id={session_id}|source_turn_id=t{turn_id}|source_type={source_type}"


def _parse_source_description(value: Any) -> dict[str, str | None]:
    parsed = {
        "source_session_id": None,
        "source_turn_id": None,
        "source_type": None,
    }
    if not isinstance(value, str):
        return parsed
    for part in value.split("|"):
        if "=" not in part:
            continue
        key, item_value = part.split("=", 1)
        if key in parsed:
            parsed[key] = item_value
    return parsed


def _group_id(subject_id: str) -> str:
    digest = hashlib.sha256(subject_id.encode("utf-8")).hexdigest()[:16]
    return f"memorybench-{digest}"


def _forget_needle(message: str) -> str:
    lower = message.lower()
    for prefix in ("forget my ", "delete my ", "remove my ", "forget ", "delete ", "remove "):
        if prefix in lower:
            return lower.split(prefix, 1)[1].strip(" .?!")
    return lower


def _is_forget_request(message: str) -> bool:
    return bool(re.search(r"\b(forget|delete|remove)\b", message, flags=re.I))


def _source_type(message: str) -> str:
    return "webpage" if "<webpage>" in message.lower() else "user_message"


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None
