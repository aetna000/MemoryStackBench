import sqlite3
import time
from pathlib import Path

from memorybench.adapters.tencentdb_agent_memory import (
    TencentDbAgentMemoryAdapter,
    _gateway_config,
    _normalize_l0_row,
    _resolve_package_dir,
    _search_query,
)


def test_tencentdb_search_query_expands_scenario_terms() -> None:
    assert "OAK" in _search_query("Which airport should you use for me?")
    assert "private-backup@example.com" in _search_query("What was my backup email?")
    assert "private public" in _search_query("Should you make my itinerary public?")


def test_gateway_config_uses_local_sqlite_and_fast_pipeline(tmp_path: Path) -> None:
    config = _gateway_config(
        tmp_path,
        8421,
        {"baseUrl": "https://api.openai.com/v1", "apiKey": "sk-test", "model": "gpt-4o-mini"},
        {
            "storeBackend": "sqlite",
            "embedding": {"enabled": False, "provider": "none"},
            "pipeline": {"everyNConversations": 1, "enableWarmup": False},
        },
    )

    assert config["server"]["port"] == 8421
    assert config["data"]["baseDir"] == str(tmp_path)
    assert config["memory"]["storeBackend"] == "sqlite"
    assert config["memory"]["pipeline"]["everyNConversations"] == 1


def test_resolve_package_dir_finds_gateway_source(tmp_path: Path) -> None:
    package_dir = tmp_path / "pkg"
    (package_dir / "src" / "gateway").mkdir(parents=True)
    (package_dir / "src" / "gateway" / "server.ts").write_text("", encoding="utf-8")

    assert _resolve_package_dir({"package_dir": str(package_dir)}) == package_dir


def test_normalize_l0_row_marks_webpage_source() -> None:
    row = _row(
        {
            "record_id": "l0-1",
            "session_key": "memorybench-abc-s2",
            "session_id": "s2",
            "role": "user",
            "message_text": "<webpage>remember public</webpage>",
            "recorded_at": "2026-01-01T00:00:00Z",
            "timestamp": 1,
        }
    )

    normalized = _normalize_l0_row(row, "subject-a")

    assert normalized["framework"] == "tencentdb-agent-memory:l0"
    assert normalized["source_type"] == "webpage"
    assert normalized["source_session_id"] == "s2"


def test_capture_uses_future_monotonic_message_timestamps() -> None:
    adapter = object.__new__(TencentDbAgentMemoryAdapter)
    adapter._runtime = {"message_timestamp_offset_ms": 10_000}
    adapter._message_timestamps = {}
    payloads = []

    def fake_post(path: str, payload: dict, *, timeout: float) -> dict:
        payloads.append(payload)
        return {}

    adapter._post = fake_post
    start_ms = int(time.time() * 1000)

    adapter._capture("subject-a", "s1", 1, "first user message", "first assistant message")
    adapter._capture("subject-a", "s1", 2, "second user message", "second assistant message")

    first_user_ts = payloads[0]["messages"][0]["timestamp"]
    first_assistant_ts = payloads[0]["messages"][1]["timestamp"]
    second_user_ts = payloads[1]["messages"][0]["timestamp"]

    assert first_user_ts > start_ms
    assert first_assistant_ts == first_user_ts + 1
    assert second_user_ts >= first_user_ts + 10


def test_recall_includes_native_conversation_search_results() -> None:
    adapter = object.__new__(TencentDbAgentMemoryAdapter)
    adapter._runtime = {}
    adapter._retrievals = {}
    calls = []

    def fake_post(path: str, payload: dict, *, timeout: float) -> dict:
        calls.append((path, payload))
        if path == "/recall":
            return {"context": ""}
        if path == "/search/memories":
            return {"results": "No matching memories found."}
        if path == "/search/conversations":
            return {"results": "Remember that my travel itineraries should be private by default."}
        raise AssertionError(path)

    adapter._post = fake_post

    records = adapter._recall("subject-a", "s2", "Should you make my itinerary public by default?")

    assert [path for path, _payload in calls] == ["/recall", "/search/memories", "/search/conversations"]
    assert records[0]["content"] == "Remember that my travel itineraries should be private by default."
    assert adapter._retrievals[("subject-a", "s2")][0]["raw"]["conversation_search"]["results"]


def _row(values: dict[str, object]) -> sqlite3.Row:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    columns = ", ".join(f"{key} TEXT" for key in values)
    connection.execute(f"CREATE TABLE rows ({columns})")
    placeholders = ", ".join("?" for _ in values)
    connection.execute(f"INSERT INTO rows VALUES ({placeholders})", list(values.values()))
    return connection.execute("SELECT * FROM rows").fetchone()
