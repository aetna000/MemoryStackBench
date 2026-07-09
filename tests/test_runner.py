from pathlib import Path
import json

from memorybench.report import copy_site
from memorybench.runner import run_benchmark


def test_runner_writes_result_bundle(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"

    scorecard = run_benchmark("targets/toy.yaml", "suites/seven_sins_v0_1", out_dir)

    assert scorecard["overall"]["total"] > 0
    assert (out_dir / "scorecard.json").exists()
    assert (out_dir / "scorecard.html").exists()
    assert (out_dir / "transcript.jsonl").exists()
    assert (out_dir / "checks.jsonl").exists()
    assert (out_dir / "timings.jsonl").exists()
    assert (out_dir / "auditability_scorecard.json").exists()

    first_timing = json.loads((out_dir / "timings.jsonl").read_text(encoding="utf-8").splitlines()[0])
    auditability = json.loads((out_dir / "auditability_scorecard.json").read_text(encoding="utf-8"))
    assert first_timing["operation"] == "reset_subject"
    assert "duration_ms" in first_timing
    assert auditability["overall"]["possible"] == 18


def test_report_backfills_auditability_for_legacy_run(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    site_dir = tmp_path / "site" / "toy"
    run_benchmark("targets/toy.yaml", "suites/seven_sins_v0_1", out_dir)
    (out_dir / "auditability_scorecard.json").unlink()

    copy_site(out_dir, site_dir)

    assert (site_dir / "auditability_scorecard.json").exists()
    assert "Auditability Matrix" in (site_dir / "index.html").read_text(encoding="utf-8")
