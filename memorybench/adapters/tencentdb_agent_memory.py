from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from memorybench.adapters.base import MemoryStackAdapter, utc_now
from memorybench.adapters.mem0 import answer_from_records


class TencentDbAgentMemoryAdapter(MemoryStackAdapter):
    """TencentDB Agent Memory adapter through the standalone HTTP gateway.

    The public gateway exposes capture, recall, search, seed, and session flush
    endpoints, but not a direct memory-record write/delete API. This adapter
    therefore benchmarks the native auto-capture path:

    1. recall/search existing L1 memories before the response,
    2. answer deterministically from the recalled memory text,
    3. capture the user/assistant turn through /capture,
    4. flush the session through /session/end so L1 extraction completes.

    Each benchmark subject gets a fresh local gateway process and data
    directory to avoid cross-scenario contamination.
    """

    capabilities = {
        "inspect_memory": True,
        "delete_memory": False,
        "retrieval_log": True,
        "multi_user": True,
        "multi_tenant": False,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        _load_env_local(("OPENAI_API_KEY", "TDAI_LLM_API_KEY", "TENCENTDB_AGENT_MEMORY_DIR"))
        self._runtime = self.config.get("runtime") or {}
        self._package_dir = _resolve_package_dir(self._runtime)
        self._run_id = _hash(str(time.time()))[:10]
        self._tmp_root = Path(
            self._runtime.get("tmp_root")
            or tempfile.mkdtemp(prefix="memorystackbench-tencentdb-")
        )
        self._base_url: str | None = None
        self._port: int | None = None
        self._data_dir: Path | None = None
        self._proc: subprocess.Popen[str] | None = None
        self._subject_id: str | None = None
        self._retrievals: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._turn_counts: dict[tuple[str, str], int] = {}
        self._message_timestamps: dict[tuple[str, str], int] = {}

    def reset_subject(self, subject_id: str) -> None:
        self._stop_gateway()
        self._subject_id = subject_id
        self._data_dir = self._tmp_root / f"subject-{_hash(subject_id)[:18]}"
        shutil.rmtree(self._data_dir, ignore_errors=True)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._start_gateway(subject_id)
        for key in list(self._retrievals):
            if key[0] == subject_id:
                del self._retrievals[key]
        for key in list(self._turn_counts):
            if key[0] == subject_id:
                del self._turn_counts[key]
        for key in list(self._message_timestamps):
            if key[0] == subject_id:
                del self._message_timestamps[key]

    def start_session(self, subject_id: str, session_id: str) -> None:
        self._ensure_subject(subject_id)

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        self._ensure_subject(subject_id)
        turn_id = self._next_turn_id(subject_id, session_id)

        retrieved = self._recall(subject_id, session_id, message)
        response = answer_from_records(message, retrieved)
        self._capture(subject_id, session_id, turn_id, message, response)
        self._flush_session(subject_id, session_id)
        return response

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]]:
        self._ensure_subject(subject_id)
        return self._read_active_l1_records(subject_id) + self._read_l0_records(subject_id)

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool | None:
        return None

    def get_retrieval_log(self, subject_id: str, session_id: str) -> list[dict[str, Any]]:
        return list(self._retrievals.get((subject_id, session_id), ()))

    def close(self) -> None:
        self._stop_gateway()
        if self._runtime.get("keep_tmp"):
            return
        shutil.rmtree(self._tmp_root, ignore_errors=True)

    def _ensure_subject(self, subject_id: str) -> None:
        if self._subject_id != subject_id or not self._proc:
            self.reset_subject(subject_id)

    def _start_gateway(self, subject_id: str) -> None:
        if not self._package_dir:
            raise RuntimeError(
                "TencentDB Agent Memory package not found. Set TENCENTDB_AGENT_MEMORY_DIR "
                "to a checkout or installed package directory, or run "
                "`npm install @tencentdb-agent-memory/memory-tencentdb@0.3.6 tsx` "
                "and set the package directory explicitly."
            )
        if self._data_dir is None:
            raise RuntimeError("TencentDB data directory was not initialized")

        self._port = int(self._runtime.get("port") or _free_port())
        self._base_url = f"http://127.0.0.1:{self._port}"
        config_path = self._data_dir / "tdai-gateway.json"
        config_path.write_text(
            json.dumps(
                _gateway_config(
                    self._data_dir,
                    self._port,
                    self._llm_config(),
                    self._memory_config(),
                ),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        log_dir = self._data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout = (log_dir / "gateway.stdout.log").open("w", encoding="utf-8")
        stderr = (log_dir / "gateway.stderr.log").open("w", encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "TDAI_GATEWAY_CONFIG": str(config_path),
                "TDAI_DATA_DIR": str(self._data_dir),
                "TDAI_GATEWAY_HOST": "127.0.0.1",
                "TDAI_GATEWAY_PORT": str(self._port),
            }
        )
        llm = self._llm_config()
        env.update(
            {
                "TDAI_LLM_BASE_URL": str(llm["baseUrl"]),
                "TDAI_LLM_API_KEY": str(llm["apiKey"]),
                "TDAI_LLM_MODEL": str(llm["model"]),
                "TDAI_LLM_MAX_TOKENS": str(llm["maxTokens"]),
                "TDAI_LLM_TIMEOUT_MS": str(llm["timeoutMs"]),
            }
        )

        command = self._gateway_command()
        self._proc = subprocess.Popen(
            command,
            cwd=self._package_dir,
            env=env,
            stdout=stdout,
            stderr=stderr,
            text=True,
        )
        stdout.close()
        stderr.close()
        self._wait_for_health(subject_id)

    def _stop_gateway(self) -> None:
        proc = self._proc
        self._proc = None
        self._base_url = None
        self._port = None
        if not proc:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def _wait_for_health(self, subject_id: str) -> None:
        deadline = time.monotonic() + float(self._runtime.get("startup_timeout_seconds") or 60)
        last_error: str | None = None
        while time.monotonic() < deadline:
            if self._proc and self._proc.poll() is not None:
                raise RuntimeError(self._gateway_failure("gateway exited during startup"))
            try:
                health = self._get("/health", timeout=2)
                if health.get("status") in {"ok", "degraded"}:
                    return
            except Exception as exc:  # noqa: BLE001 - surfaced below with logs
                last_error = str(exc)
            time.sleep(0.5)
        raise RuntimeError(
            self._gateway_failure(f"gateway did not become healthy for {subject_id}: {last_error}")
        )

    def _gateway_failure(self, message: str) -> str:
        logs = []
        if self._data_dir:
            for name in ("gateway.stdout.log", "gateway.stderr.log"):
                path = self._data_dir / "logs" / name
                if path.exists():
                    tail = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])
                    logs.append(f"{name}:\n{tail}")
        return "\n\n".join([message, *logs])

    def _recall(self, subject_id: str, session_id: str, message: str) -> list[dict[str, Any]]:
        session_key = self._session_key(subject_id, session_id)
        query = _search_query(message)
        recall_raw = self._post(
            "/recall",
            {"query": query, "session_key": session_key, "user_id": _safe_subject(subject_id)},
            timeout=float(self._runtime.get("request_timeout_seconds") or 120),
        )
        search_raw = self._post(
            "/search/memories",
            {"query": query, "limit": int(self._runtime.get("search_limit") or 8)},
            timeout=float(self._runtime.get("request_timeout_seconds") or 120),
        )
        conversation_raw = self._post(
            "/search/conversations",
            {"query": query, "limit": int(self._runtime.get("conversation_search_limit") or 8)},
            timeout=float(self._runtime.get("request_timeout_seconds") or 120),
        )
        records = _records_from_gateway_text(
            subject_id,
            [
                str(recall_raw.get("context") or ""),
                str(search_raw.get("results") or ""),
                str(conversation_raw.get("results") or ""),
            ],
        )
        self._retrievals.setdefault((subject_id, session_id), []).append(
            {
                "created_at": utc_now(),
                "query": message,
                "tencentdb_query": query,
                "memory_ids": [record.get("memory_id") for record in records],
                "records": records,
                "raw": {
                    "recall": recall_raw,
                    "search": search_raw,
                    "conversation_search": conversation_raw,
                },
            }
        )
        return records

    def _capture(
        self,
        subject_id: str,
        session_id: str,
        turn_id: int,
        user_message: str,
        assistant_message: str,
    ) -> None:
        timestamp_key = (subject_id, session_id)
        offset_ms = int(self._runtime.get("message_timestamp_offset_ms") or 10_000)
        now_ms = max(
            int(time.time() * 1000) + offset_ms,
            self._message_timestamps.get(timestamp_key, 0) + 10,
        )
        self._message_timestamps[timestamp_key] = now_ms + 1
        self._post(
            "/capture",
            {
                "user_content": user_message,
                "assistant_content": assistant_message,
                "session_key": self._session_key(subject_id, session_id),
                "session_id": session_id,
                "user_id": _safe_subject(subject_id),
                "messages": [
                    {
                        "id": f"{session_id}-t{turn_id}-user",
                        "role": "user",
                        "content": user_message,
                        "timestamp": now_ms,
                    },
                    {
                        "id": f"{session_id}-t{turn_id}-assistant",
                        "role": "assistant",
                        "content": assistant_message,
                        "timestamp": now_ms + 1,
                    },
                ],
            },
            timeout=float(self._runtime.get("request_timeout_seconds") or 120),
        )

    def _flush_session(self, subject_id: str, session_id: str) -> None:
        self._post(
            "/session/end",
            {"session_key": self._session_key(subject_id, session_id), "user_id": _safe_subject(subject_id)},
            timeout=float(self._runtime.get("request_timeout_seconds") or 180),
        )

    def _get(self, path: str, *, timeout: float) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post(self, path: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"TencentDB gateway {path} failed: HTTP {exc.code}: {detail}") from exc

    def _read_active_l1_records(self, subject_id: str) -> list[dict[str, Any]]:
        db_path = self._vectors_db()
        if not db_path.exists():
            return []
        jsonl_by_id = _read_l1_jsonl_records(self._data_dir)
        l0_by_id = {record.get("memory_id"): record for record in self._read_l0_records(subject_id)}
        rows: list[sqlite3.Row]
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            rows = list(
                connection.execute(
                    """
                    SELECT record_id, content, type, priority, scene_name, session_key,
                           session_id, timestamp_start, timestamp_end, created_time,
                           updated_time, metadata_json
                    FROM l1_records
                    ORDER BY updated_time ASC, created_time ASC
                    """
                )
            )
        except sqlite3.Error:
            return []
        finally:
            if connection is not None:
                connection.close()

        return [
            _normalize_l1_row(row, subject_id, jsonl_by_id.get(str(row["record_id"])), l0_by_id)
            for row in rows
        ]

    def _read_l0_records(self, subject_id: str) -> list[dict[str, Any]]:
        db_path = self._vectors_db()
        if not db_path.exists():
            return []
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            rows = list(
                connection.execute(
                    """
                    SELECT record_id, session_key, session_id, role, message_text,
                           recorded_at, timestamp
                    FROM l0_conversations
                    ORDER BY timestamp ASC, recorded_at ASC
                    """
                )
            )
        except sqlite3.Error:
            return []
        finally:
            if connection is not None:
                connection.close()
        return [_normalize_l0_row(row, subject_id) for row in rows]

    def _vectors_db(self) -> Path:
        if self._data_dir is None:
            return Path("__missing_vectors_db__")
        return self._data_dir / "vectors.db"

    def _gateway_command(self) -> list[str]:
        raw = self._runtime.get("gateway_command")
        if isinstance(raw, list) and raw:
            return [str(item) for item in raw]
        return ["npx", "tsx", "src/gateway/server.ts"]

    def _llm_config(self) -> dict[str, Any]:
        llm = self._runtime.get("llm") if isinstance(self._runtime.get("llm"), dict) else {}
        api_key = (
            llm.get("api_key")
            or os.environ.get("TDAI_LLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        return {
            "baseUrl": llm.get("base_url") or os.environ.get("TDAI_LLM_BASE_URL") or "https://api.openai.com/v1",
            "apiKey": api_key,
            "model": llm.get("model") or os.environ.get("TDAI_LLM_MODEL") or "gpt-4o-mini",
            "maxTokens": int(llm.get("max_tokens") or os.environ.get("TDAI_LLM_MAX_TOKENS") or 2048),
            "timeoutMs": int(llm.get("timeout_ms") or os.environ.get("TDAI_LLM_TIMEOUT_MS") or 120000),
            "disableThinking": llm.get("disable_thinking") or False,
        }

    def _memory_config(self) -> dict[str, Any]:
        memory = self._runtime.get("memory") if isinstance(self._runtime.get("memory"), dict) else {}
        return {
            "storeBackend": "sqlite",
            "embedding": {
                "enabled": False,
                "provider": "none",
            },
            "bm25": {
                "enabled": True,
                "language": "en",
            },
            "extraction": {
                "enabled": True,
                "enableDedup": bool(memory.get("enable_dedup", True)),
                "maxMemoriesPerSession": int(memory.get("max_memories_per_session") or 20),
            },
            "pipeline": {
                "everyNConversations": int(memory.get("every_n_conversations") or 1),
                "enableWarmup": False,
                "l1IdleTimeoutSeconds": int(memory.get("l1_idle_timeout_seconds") or 1),
                "l2DelayAfterL1Seconds": int(memory.get("l2_delay_after_l1_seconds") or 1),
                "l2MinIntervalSeconds": int(memory.get("l2_min_interval_seconds") or 1),
                "l2MaxIntervalSeconds": int(memory.get("l2_max_interval_seconds") or 60),
                "sessionActiveWindowHours": int(memory.get("session_active_window_hours") or 1),
            },
            "persona": {
                "triggerEveryN": int(memory.get("persona_trigger_every_n") or 9999),
                "maxScenes": int(memory.get("persona_max_scenes") or 5),
            },
            "recall": {
                "enabled": True,
                "maxResults": int(memory.get("recall_max_results") or 8),
                "scoreThreshold": float(memory.get("recall_score_threshold") or 0),
                "strategy": str(memory.get("recall_strategy") or "keyword"),
                "timeoutMs": int(memory.get("recall_timeout_ms") or 10000),
            },
        }

    def _session_key(self, subject_id: str, session_id: str) -> str:
        return f"memorybench-{_hash(subject_id)[:12]}-{session_id}"

    def _next_turn_id(self, subject_id: str, session_id: str) -> int:
        key = (subject_id, session_id)
        self._turn_counts[key] = self._turn_counts.get(key, 0) + 1
        return self._turn_counts[key]


def _gateway_config(
    data_dir: Path,
    port: int,
    llm: dict[str, Any],
    memory: dict[str, Any],
) -> dict[str, Any]:
    return {
        "server": {
            "host": "127.0.0.1",
            "port": port,
            "corsOrigins": [],
        },
        "data": {
            "baseDir": str(data_dir),
        },
        "llm": llm,
        "memory": memory,
    }


def _resolve_package_dir(runtime: dict[str, Any]) -> Path | None:
    candidates = [
        runtime.get("package_dir"),
        os.environ.get("TENCENTDB_AGENT_MEMORY_DIR"),
        Path("node_modules") / "@tencentdb-agent-memory" / "memory-tencentdb",
        Path.home() / ".memory-tencentdb" / "tdai-memory-openclaw-plugin",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(str(candidate)).expanduser()
        if (path / "src" / "gateway" / "server.ts").exists():
            return path
    return None


def _records_from_gateway_text(subject_id: str, texts: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, text in enumerate(texts, start=1):
        clean = text.strip()
        if not clean or "No matching" in clean:
            continue
        records.append(
            {
                "memory_id": f"tencentdb-gateway-result-{index:04d}",
                "framework": "tencentdb-agent-memory",
                "subject_id_hash": f"plain:{subject_id}",
                "tenant_id_hash": None,
                "content": clean,
                "source_type": None,
                "source_session_id": None,
                "source_turn_id": None,
                "created_at": None,
                "updated_at": None,
                "deleted_at": None,
                "confidence": None,
                "scope": "user_private",
                "raw": {"text": clean},
            }
        )
    return records


def _normalize_l1_row(
    row: sqlite3.Row,
    subject_id: str,
    jsonl_record: dict[str, Any] | None,
    l0_by_id: dict[Any, dict[str, Any]],
) -> dict[str, Any]:
    source_record = _source_record(jsonl_record, l0_by_id)
    metadata = _json_dict(row["metadata_json"])
    return {
        "memory_id": row["record_id"],
        "framework": "tencentdb-agent-memory:l1",
        "subject_id_hash": f"plain:{subject_id}",
        "tenant_id_hash": None,
        "content": str(row["content"] or ""),
        "source_type": source_record.get("source_type") or metadata.get("source_type") or "user_message",
        "source_session_id": source_record.get("source_session_id") or row["session_id"] or None,
        "source_turn_id": source_record.get("source_turn_id"),
        "created_at": row["created_time"] or None,
        "updated_at": row["updated_time"] or None,
        "deleted_at": None,
        "confidence": None,
        "scope": "user_private",
        "raw": {
            "layer": "l1",
            "type": row["type"],
            "priority": row["priority"],
            "scene_name": row["scene_name"],
            "session_key": row["session_key"],
            "session_id": row["session_id"],
            "timestamp_start": row["timestamp_start"],
            "timestamp_end": row["timestamp_end"],
            "metadata": metadata,
            "jsonl_record": jsonl_record or {},
        },
    }


def _normalize_l0_row(row: sqlite3.Row, subject_id: str) -> dict[str, Any]:
    content = str(row["message_text"] or "")
    role = str(row["role"] or "")
    return {
        "memory_id": row["record_id"],
        "framework": "tencentdb-agent-memory:l0",
        "subject_id_hash": f"plain:{subject_id}",
        "tenant_id_hash": None,
        "content": content,
        "source_type": _source_type(content, role),
        "source_session_id": row["session_id"] or _session_id_from_key(str(row["session_key"] or "")),
        "source_turn_id": None,
        "created_at": row["recorded_at"] or None,
        "updated_at": row["recorded_at"] or None,
        "deleted_at": None,
        "confidence": None,
        "scope": "user_private",
        "raw": {
            "layer": "l0",
            "role": role,
            "session_key": row["session_key"],
            "timestamp": row["timestamp"],
        },
    }


def _source_record(
    jsonl_record: dict[str, Any] | None,
    l0_by_id: dict[Any, dict[str, Any]],
) -> dict[str, Any]:
    ids = jsonl_record.get("source_message_ids") if isinstance(jsonl_record, dict) else None
    if not isinstance(ids, list):
        return {}
    candidates = [l0_by_id.get(item) for item in ids]
    candidates = [item for item in candidates if isinstance(item, dict)]
    user_candidates = [item for item in candidates if item.get("raw", {}).get("role") == "user"]
    selected = (user_candidates or candidates or [{}])[0]
    return selected


def _read_l1_jsonl_records(data_dir: Path | None) -> dict[str, dict[str, Any]]:
    if data_dir is None:
        return {}
    records: dict[str, dict[str, Any]] = {}
    records_dir = data_dir / "records"
    if not records_dir.exists():
        return records
    for path in sorted(records_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            record_id = record.get("id")
            if record_id:
                records[str(record_id)] = record
    return records


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source_type(content: str, role: str) -> str:
    if "<webpage>" in content.lower():
        return "webpage"
    if role == "assistant":
        return "assistant_message"
    return "user_message"


def _search_query(query: str) -> str:
    lower = query.lower()
    if "itinerary" in lower or "public" in lower:
        return "travel itinerary itineraries private public default"
    if "airport" in lower:
        return "preferred airport SFO OAK"
    if "backup" in lower or "email" in lower:
        return "backup email private-backup@example.com"
    if "vegetarian" in lower or "beef" in lower:
        return "vegetarian beef business dinners avoid"
    return query


def _session_id_from_key(session_key: str) -> str | None:
    if "-" not in session_key:
        return None
    return session_key.rsplit("-", 1)[-1] or None


def _safe_subject(subject_id: str) -> str:
    return f"subject-{_hash(subject_id)[:18]}"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _load_env_local(keys: tuple[str, ...]) -> None:
    path = Path(".env.local")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key in keys and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")
