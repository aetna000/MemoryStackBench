from pathlib import Path

from memorybench.runner import run_benchmark


def test_runner_writes_result_bundle(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"

    scorecard = run_benchmark("targets/toy.yaml", "suites/seven_sins_v0_1", out_dir)

    assert scorecard["overall"]["total"] > 0
    assert (out_dir / "scorecard.json").exists()
    assert (out_dir / "scorecard.html").exists()
    assert (out_dir / "transcript.jsonl").exists()
    assert (out_dir / "checks.jsonl").exists()

