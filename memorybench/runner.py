from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from memorybench.adapters import load_adapter
from memorybench.report import write_failure_report, write_html_scorecard
from memorybench.scenarios import load_suite
from memorybench.scoring import build_scorecard, evaluate_memory, evaluate_response
from memorybench.schemas import CheckResult


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

    try:
        with (
            transcript_path.open("w", encoding="utf-8") as transcript_file,
            memory_path.open("w", encoding="utf-8") as memory_file,
            retrieval_path.open("w", encoding="utf-8") as retrieval_file,
        ):
            for scenario in scenarios:
                subject_id = f"{target_manifest['id']}::{scenario.id}"
                adapter.reset_subject(subject_id)
                for session in scenario.sessions:
                    adapter.start_session(subject_id, session.id)
                    for turn_index, turn in enumerate(session.turns, start=1):
                        response = adapter.send(subject_id, session.id, turn.user)
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

                    retrievals = adapter.get_retrieval_log(subject_id, session.id)
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

                    records = adapter.inspect_memory(subject_id)
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

                final_records = adapter.inspect_memory(subject_id)
                checks.extend(evaluate_memory(scenario, scenario.expect_memory, final_records))
    finally:
        adapter.close()

    scorecard = build_scorecard(target_manifest, suite_file.name, checks)
    _write_json(run_dir / "scorecard.json", scorecard)
    _write_jsonl_many(run_dir / "checks.jsonl", [check.to_dict() for check in checks])
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
    write_html_scorecard(scorecard, run_dir / "scorecard.html")
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

