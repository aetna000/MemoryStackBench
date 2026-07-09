import json
from pathlib import Path

from memorybench.auditability import build_auditability_scorecard, summarize_timing_file


def test_build_auditability_scorecard_from_run_evidence(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "memory_snapshots.jsonl",
        [
            {
                "records": [
                    {
                        "memory_id": "mem-1",
                        "content": "User's preferred airport is SFO.",
                        "source_type": "user_message",
                        "source_session_id": "s1",
                        "source_turn_id": "t1",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "status": "superseded",
                        "fact_key": "preferred airport",
                    },
                    {
                        "memory_id": "mem-2",
                        "content": "User's preferred airport is OAK.",
                        "source_type": "user_message",
                        "source_session_id": "s2",
                        "source_turn_id": "t1",
                        "created_at": "2026-01-01T00:00:01+00:00",
                        "status": "active",
                        "fact_key": "preferred airport",
                        "supersedes_id": "mem-1",
                    },
                ]
            }
        ],
    )
    _write_jsonl(
        tmp_path / "retrieval_events.jsonl",
        [
            {
                "memory_ids": ["mem-2"],
                "records": [{"memory_id": "mem-2"}],
                "query_sha256": "abc",
                "candidates": [{"record_id": "mem-2", "score": 0.9}],
            }
        ],
    )
    _write_jsonl(
        tmp_path / "checks.jsonl",
        [
            {"category": "deletion_behavior", "passed": True},
            {"category": "deletion_behavior", "passed": True},
        ],
    )
    (tmp_path / "scorecard.json").write_text(
        json.dumps({"suite": "seven_sins_v0_1"}),
        encoding="utf-8",
    )
    (tmp_path / "target_manifest.yaml").write_text("id: target\n", encoding="utf-8")

    scorecard = build_auditability_scorecard(
        {
            "id": "target",
            "framework": "framework",
            "mode": "white_box",
            "auditability": {"provenance": {"origin": "native"}},
        },
        tmp_path,
    )

    assert scorecard["overall"]["possible"] == 18
    assert scorecard["dimensions"]["provenance"]["score"] == 3
    assert scorecard["dimensions"]["provenance"]["origin"] == "native"
    assert scorecard["dimensions"]["retrieval_transparency"]["score"] == 3
    assert scorecard["dimensions"]["mutation_lineage"]["score"] == 3
    assert scorecard["dimensions"]["tamper_evidence"]["score"] == 2


def test_summarize_timing_file(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "timings.jsonl",
        [
            {"operation": "send", "duration_ms": 10.0, "success": True},
            {"operation": "send", "duration_ms": 20.0, "success": True},
            {"operation": "inspect_memory", "duration_ms": 5.0, "success": True},
            {"operation": "send", "duration_ms": 999.0, "success": False},
        ],
    )

    summary = summarize_timing_file(tmp_path / "timings.jsonl")

    assert summary["operation_count"] == 4
    assert summary["successful_operation_count"] == 3
    assert summary["operations"]["send"]["median_ms"] == 15.0
    assert summary["operations"]["inspect_memory"]["p95_ms"] == 5.0


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
