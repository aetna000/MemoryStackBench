from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

import yaml

from memorybench.adapters import load_adapter
from memorybench.auditability import build_auditability_scorecard
from memorybench.report import write_failure_report, write_html_scorecard
from memorybench.scenarios import load_suite
from memorybench.scoring import build_scorecard, evaluate_memory, evaluate_response
from memorybench.schemas import CheckResult

T = TypeVar("T")


def run_benchmark(target_path: str | Path, suite_path: str | Path, out_dir: str | Path) -> dict[str, Any]:
    target_file = Path(target_path)
    suite_file = Path(suite_path)
    run_dir = Path(out_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    target_manifest = _load_target(target_file)
    scenarios = load_suite(suite_file)
    adapter = load_adapter(target_manifest)
    checks: list[CheckResult] = []

    _copy_target_manifest(target_file, run_dir)
    transcript_path = run_dir / "transcript.jsonl"
    memory_path = run_dir / "memory_snapshots.jsonl"
    retrieval_path = run_dir / "retrieval_events.jsonl"
    timings_path = run_dir / "timings.jsonl"

    timing_file = timings_path.open("w", encoding="utf-8")
    try:
        with (
            transcript_path.open("w", encoding="utf-8") as transcript_file,
            memory_path.open("w", encoding="utf-8") as memory_file,
            retrieval_path.open("w", encoding="utf-8") as retrieval_file,
        ):
            for scenario in scenarios:
                subject_id = f"{target_manifest['id']}::{scenario.id}"
                _timed_call(
                    timing_file,
                    "reset_subject",
                    {"scenario_id": scenario.id, "subject_id": subject_id},
                    adapter.reset_subject,
                    subject_id,
                )
                for session in scenario.sessions:
                    _timed_call(
                        timing_file,
                        "start_session",
                        {
                            "scenario_id": scenario.id,
                            "subject_id": subject_id,
                            "session_id": session.id,
                        },
                        adapter.start_session,
                        subject_id,
                        session.id,
                    )
                    for turn_index, turn in enumerate(session.turns, start=1):
                        response = _timed_call(
                            timing_file,
                            "send",
                            {
                                "scenario_id": scenario.id,
                                "subject_id": subject_id,
                                "session_id": session.id,
                                "turn_index": turn_index,
                            },
                            adapter.send,
                            subject_id,
                            session.id,
                            turn.user,
                        )
                        _write_jsonl(
                            transcript_file,
                            {
                                "scenario_id": scenario.id,
                                "subject_id": subject_id,
                                "session_id": session.id,
                                "turn_index": turn_index,
                                "user": turn.user,
                                "assistant": response,
                                "created_at": _utc_now(),
                            },
                        )
                        checks.extend(
                            evaluate_response(
                                scenario,
                                session.id,
                                turn_index,
                                turn.expect_response,
                                response,
                            )
                        )

                    retrievals = _timed_call(
                        timing_file,
                        "get_retrieval_log",
                        {
                            "scenario_id": scenario.id,
                            "subject_id": subject_id,
                            "session_id": session.id,
                        },
                        adapter.get_retrieval_log,
                        subject_id,
                        session.id,
                    )
                    if retrievals:
                        for event in retrievals:
                            _write_jsonl(
                                retrieval_file,
                                {
                                    "scenario_id": scenario.id,
                                    "subject_id": subject_id,
                                    "session_id": session.id,
                                    **event,
                                },
                            )

                    records = _timed_call(
                        timing_file,
                        "inspect_memory",
                        {
                            "scenario_id": scenario.id,
                            "subject_id": subject_id,
                            "session_id": session.id,
                        },
                        adapter.inspect_memory,
                        subject_id,
                    )
                    _write_jsonl(
                        memory_file,
                        {
                            "scenario_id": scenario.id,
                            "subject_id": subject_id,
                            "session_id": session.id,
                            "created_at": _utc_now(),
                            "records": records,
                        },
                    )

                final_records = _timed_call(
                    timing_file,
                    "final_inspect_memory",
                    {"scenario_id": scenario.id, "subject_id": subject_id},
                    adapter.inspect_memory,
                    subject_id,
                )
                checks.extend(evaluate_memory(scenario, scenario.expect_memory, final_records))
    finally:
        try:
            _timed_call(timing_file, "close", {}, adapter.close)
        finally:
            timing_file.close()

    scorecard = build_scorecard(target_manifest, suite_file.name, checks)
    _write_json(run_dir / "scorecard.json", scorecard)
    _write_jsonl_many(run_dir / "checks.jsonl", [check.to_dict() for check in checks])
    _write_json(
        run_dir / "auditability_scorecard.json",
        build_auditability_scorecard(target_manifest, run_dir, suite=suite_file.name),
    )
    _write_json(
        run_dir / "run_manifest.json",
        {
            "created_at": _utc_now(),
            "target_path": str(target_file),
            "suite_path": str(suite_file),
            "scenario_count": len(scenarios),
            "output_files": sorted(item.name for item in run_dir.iterdir()),
        },
    )
    write_failure_report(scorecard, run_dir / "failure_report.md")
    write_html_scorecard(scorecard, run_dir / "scorecard.html", run_dir=run_dir)
    return scorecard


def _load_target(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Target manifest must be a YAML object: {path}")
    for key in ("id", "framework", "adapter"):
        if key not in data:
            raise ValueError(f"Target manifest {path} is missing {key}")
    return data


def _copy_target_manifest(source: Path, run_dir: Path) -> None:
    shutil.copyfile(source, run_dir / "target_manifest.yaml")


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(handle: Any, data: Any) -> None:
    handle.write(json.dumps(data, sort_keys=True) + "\n")


def _write_jsonl_many(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            _write_jsonl(handle, row)


def _timed_call(
    handle: Any,
    operation: str,
    context: dict[str, Any],
    func: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    started_at = _utc_now()
    start_ns = time.perf_counter_ns()
    try:
        result = func(*args, **kwargs)
    except BaseException as exc:
        duration_ns = time.perf_counter_ns() - start_ns
        _write_jsonl(
            handle,
            {
                **context,
                "operation": operation,
                "created_at": started_at,
                "duration_ms": round(duration_ns / 1_000_000, 3),
                "duration_ns": duration_ns,
                "success": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        handle.flush()
        raise

    duration_ns = time.perf_counter_ns() - start_ns
    _write_jsonl(
        handle,
        {
            **context,
            "operation": operation,
            "created_at": started_at,
            "duration_ms": round(duration_ns / 1_000_000, 3),
            "duration_ns": duration_ns,
            "success": True,
        },
    )
    handle.flush()
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
