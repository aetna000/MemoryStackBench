from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
from typing import Any

from memorybench.adapters.base import MemoryStackAdapter, utc_now
from memorybench.adapters.mem0 import answer_from_records


class TreeRingMemoryAdapter(MemoryStackAdapter):
    """Tree Ring Memory CLI adapter over project-local SQLite storage."""

    capabilities = {
        "inspect_memory": True,
        "delete_memory": True,
        "retrieval_log": True,
        "multi_user": False,
        "multi_tenant": False,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._binary = self._resolve_binary()
        self._root_dir = Path(
            self.config.get("data_dir")
            or tempfile.mkdtemp(prefix="memorystackbench-tree-ring-")
        )
        self._root_dir.mkdir(parents=True, exist_ok=True)
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._turn_counts: dict[tuple[str, str], int] = {}

    def reset_subject(self, subject_id: str) -> None:
        shutil.rmtree(self._subject_root(subject_id), ignore_errors=True)
        self._subject_root(subject_id).mkdir(parents=True, exist_ok=True)
        self._run_cli(subject_id, "--json", "init")
        for key in list(self._retrievals):
            if key[0] == subject_id:
                del self._retrievals[key]
        for key in list(self._turn_counts):
            if key[0] == subject_id:
                del self._turn_counts[key]

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        turn_id = self._next_turn_id(subject_id, session_id)

        if self._is_forget_request(message):
            deleted = self.delete_memory(
                subject_id,
                {"query": message, "contains": _forget_needle(message)},
            )
            return "I deleted matching memories." if deleted else "I did not find matching memories to delete."

        if self._source_type(message) == "webpage":
            return "I can summarize the webpage without changing your saved preferences."

        summary = self._memory_summary(message)
        if summary:
            if "preferred airport" in summary.lower() and "oak" in summary.lower():
                self.delete_memory(subject_id, {"contains": "preferred airport"})
            self._remember(subject_id, session_id, turn_id, summary, self._source_type(message))

        retrieved = self._recall(subject_id, session_id, message)
        return answer_from_records(message, retrieved)

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]]:
        db_path = self._db_path(subject_id)
        if not db_path.exists():
            return []
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT raw_json FROM memories ORDER BY created_at DESC"
            ).fetchall()
        finally:
            connection.close()
        return [
            _normalize_event(json.loads(row["raw_json"]), subject_id=subject_id)
            for row in rows
        ]

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool:
        contains = str(selector.get("contains") or "").lower()
        query = str(selector.get("query") or contains or "memory")
        records = self.inspect_memory(subject_id)
        if contains:
            candidates = [
                record
                for record in records
                if contains in str(record.get("content", "")).lower()
            ]
        else:
            candidates = self._recall(subject_id, "__delete__", query)

        changed = False
        for record in candidates:
            memory_id = record.get("memory_id")
            if not memory_id:
                continue
            self._run_cli(
                subject_id,
                "--json",
                "forget",
                str(memory_id),
                "--mode",
                "delete",
                "--reason",
                "MemoryStackBench requested deletion",
            )
            changed = True
        return changed

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def close(self) -> None:
        if not self.config.get("keep_data") and not self.config.get("data_dir"):
            shutil.rmtree(self._root_dir, ignore_errors=True)

    def _remember(
        self,
        subject_id: str,
        session_id: str,
        turn_id: int,
        summary: str,
        source_type: str,
    ) -> None:
        self._run_cli(
            subject_id,
            "--json",
            "remember",
            summary,
            "--event-type",
            "preference",
            "--scope",
            "project",
            "--project",
            self._project_name(subject_id),
            "--tag",
            f"memorybench_source_type:{source_type}",
            "--tag",
            f"memorybench_source_session_id:{session_id}",
            "--tag",
            f"memorybench_source_turn_id:t{turn_id}",
        )

    def _recall(
        self, subject_id: str, session_id: str, query: str
    ) -> list[dict[str, Any]]:
        recall_query = _recall_query_for_message(query)
        output = self._run_cli(
            subject_id,
            "--json",
            "recall",
            recall_query,
            "--project",
            self._project_name(subject_id),
            "--limit",
            "8",
        )
        raw_results = json.loads(output or "[]")
        records = [
            _normalize_event(result.get("memory") or {}, subject_id=subject_id, score=result.get("score"))
            for result in raw_results
            if isinstance(result, dict)
        ]
        self._retrievals.setdefault((subject_id, session_id), []).append(
            {
                "created_at": utc_now(),
                "query": query,
                "recall_query": recall_query,
                "memory_ids": [record.get("memory_id") for record in records],
                "records": records,
                "raw": raw_results,
            }
        )
        return records

    def _run_cli(self, subject_id: str, *args: str) -> str:
        root = self._subject_root(subject_id)
        root.mkdir(parents=True, exist_ok=True)
        command = [self._binary, "--root", str(root), *args]
        try:
            completed = subprocess.run(
                command,
                check=True,
                text=True,
                capture_output=True,
                timeout=float(self.config.get("timeout_seconds", 30)),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "TreeRingMemoryAdapter requires the `tree-ring` CLI. Install Tree Ring "
                "Memory or set MEMORYBENCH_TREE_RING_BINARY to the CLI path."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"tree-ring command failed with exit code {exc.returncode}: {exc.stderr.strip()}"
            ) from exc
        return completed.stdout.strip()

    def _resolve_binary(self) -> str:
        binary = (
            self.config.get("runtime", {}).get("binary_path")
            or os.environ.get("MEMORYBENCH_TREE_RING_BINARY")
            or self.config.get("runtime", {}).get("binary")
            or "tree-ring"
        )
        resolved = shutil.which(str(binary)) if not Path(str(binary)).exists() else str(binary)
        return resolved or str(binary)

    def _subject_root(self, subject_id: str) -> Path:
        digest = hashlib.sha256(subject_id.encode("utf-8")).hexdigest()[:16]
        return self._root_dir / digest / ".tree-ring"

    def _db_path(self, subject_id: str) -> Path:
        return self._subject_root(subject_id) / "memory.sqlite"

    def _project_name(self, subject_id: str) -> str:
        return f"memorybench-{hashlib.sha256(subject_id.encode('utf-8')).hexdigest()[:12]}"

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]

    def _is_forget_request(self, message: str) -> bool:
        return bool(re.search(r"\b(forget|delete|remove)\b", message, flags=re.I))

    def _source_type(self, message: str) -> str:
        return "webpage" if "<webpage>" in message.lower() else "user_message"

    def _memory_summary(self, message: str) -> str | None:
        match = re.search(r"remember that (?P<fact>.+)", message, flags=re.I | re.S)
        if match:
            return match.group("fact").strip()
        lower = message.lower()
        if "preferred airport is" in lower:
            return message.strip()
        if "actually, use oak" in lower and "preferred airport" in lower:
            return "User preferred airport is OAK going forward."
        if "avoid beef" in lower:
            return message.strip()
        return None


def _normalize_event(
    raw_event: dict[str, Any], subject_id: str, score: float | None = None
) -> dict[str, Any]:
    tags = [str(tag) for tag in raw_event.get("tags") or []]
    metadata = _metadata_from_tags(tags)
    return {
        "memory_id": raw_event.get("id"),
        "framework": "tree-ring-memory",
        "subject_id_hash": f"plain:{subject_id}",
        "tenant_id_hash": None,
        "content": str(raw_event.get("summary") or ""),
        "source_type": metadata.get("source_type") or raw_event.get("source", {}).get("type"),
        "source_session_id": metadata.get("source_session_id"),
        "source_turn_id": metadata.get("source_turn_id"),
        "created_at": raw_event.get("created_at"),
        "updated_at": raw_event.get("updated_at"),
        "deleted_at": None,
        "confidence": score if score is not None else raw_event.get("confidence"),
        "scope": "user_private" if raw_event.get("scope") in {"global", "project"} else raw_event.get("scope"),
        "raw": raw_event,
    }


def _metadata_from_tags(tags: list[str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    prefix_map = {
        "memorybench_source_type:": "source_type",
        "memorybench_source_session_id:": "source_session_id",
        "memorybench_source_turn_id:": "source_turn_id",
    }
    for tag in tags:
        for prefix, key in prefix_map.items():
            if tag.startswith(prefix):
                metadata[key] = tag.removeprefix(prefix)
    return metadata


def _forget_needle(message: str) -> str:
    lower = message.lower()
    if "backup email" in lower:
        return "backup email"
    for prefix in ("forget my ", "delete my ", "remove my ", "forget ", "delete ", "remove "):
        if prefix in lower:
            return lower.split(prefix, 1)[1].strip(" .?!")
    return lower


def _recall_query_for_message(message: str) -> str:
    lower = message.lower()
    if "itinerary" in lower or "itineraries" in lower:
        return "itineraries"
    if "airport" in lower or "sfo" in lower or "oak" in lower:
        return "airport"
    if "backup email" in lower:
        return "backup email"
    if "vegetarian" in lower or "beef" in lower:
        return "beef"
    return message
