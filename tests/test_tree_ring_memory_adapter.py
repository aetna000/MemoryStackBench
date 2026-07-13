from __future__ import annotations

import json
import sqlite3

from memorybench.adapters.tree_ring_memory import (
    TreeRingMemoryAdapter,
    _metadata_from_tags,
    _normalize_event,
    _recall_query_for_message,
)


def test_tree_ring_metadata_from_tags() -> None:
    assert _metadata_from_tags(
        [
            "memorybench_source_type:user_message",
            "memorybench_source_session_id:s1",
            "memorybench_source_turn_id:t2",
            "other",
        ]
    ) == {
        "source_type": "user_message",
        "source_session_id": "s1",
        "source_turn_id": "t2",
    }


def test_tree_ring_normalizes_event() -> None:
    record = _normalize_event(
        {
            "id": "mem_1",
            "summary": "Travel itineraries should be private by default.",
            "created_at": "2026-07-13T00:00:00Z",
            "updated_at": "2026-07-13T00:00:00Z",
            "scope": "project",
            "confidence": 0.7,
            "source": {"type": "manual"},
            "tags": [
                "memorybench_source_type:user_message",
                "memorybench_source_session_id:s1",
            ],
        },
        subject_id="subject-1",
    )

    assert record["memory_id"] == "mem_1"
    assert record["content"] == "Travel itineraries should be private by default."
    assert record["source_type"] == "user_message"
    assert record["source_session_id"] == "s1"
    assert record["scope"] == "user_private"


def test_tree_ring_inspect_memory_reads_sqlite(tmp_path) -> None:
    adapter = TreeRingMemoryAdapter(
        {
            "runtime": {"binary_path": "/bin/echo"},
            "data_dir": str(tmp_path),
            "keep_data": True,
        }
    )
    subject_id = "subject-1"
    root = adapter._subject_root(subject_id)
    root.mkdir(parents=True)
    connection = sqlite3.connect(root / "memory.sqlite")
    try:
        connection.execute("CREATE TABLE memories (created_at TEXT NOT NULL, raw_json TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO memories (created_at, raw_json) VALUES (?, ?)",
            [
                "2026-07-13T00:00:00Z",
                json.dumps(
                    {
                        "id": "mem_1",
                        "summary": "Use OAK as preferred airport.",
                        "created_at": "2026-07-13T00:00:00Z",
                        "updated_at": "2026-07-13T00:00:00Z",
                        "scope": "project",
                        "source": {"type": "manual"},
                        "tags": ["memorybench_source_session_id:s2"],
                    }
                )
            ],
        )
        connection.commit()
    finally:
        connection.close()

    records = adapter.inspect_memory(subject_id)

    assert [record["memory_id"] for record in records] == ["mem_1"]
    assert records[0]["source_session_id"] == "s2"


def test_tree_ring_recall_query_for_probe() -> None:
    assert _recall_query_for_message("Should you make my itinerary public by default?") == (
        "itineraries"
    )
    assert _recall_query_for_message("Which airport should you use for me?") == (
        "airport"
    )
    assert _recall_query_for_message("Am I vegetarian?") == "beef"
