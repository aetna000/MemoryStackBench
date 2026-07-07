import json
from pathlib import Path

from memorybench.leaderboard import write_leaderboard


def test_write_leaderboard(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "mem0"
    run_dir.mkdir(parents=True)
    (run_dir / "scorecard.json").write_text(
        json.dumps(
            {
                "target": {"id": "mem0", "framework": "mem0", "mode": "white_box"},
                "suite": "seven_sins_v0_1",
                "overall": {"passed": 2, "total": 4, "score": 0.5},
                "categories": {"retrieval": {"passed": 1, "total": 2, "score": 0.5}},
                "failures": [{"scenario_id": "x"}],
            }
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "site" / "leaderboard"
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    (targets_dir / "mem0.yaml").write_text(
        "id: mem0\nframework: mem0\nmode: white_box\nstatus: implemented\n",
        encoding="utf-8",
    )
    write_leaderboard(tmp_path / "runs", out_dir, targets_dir)

    assert (out_dir / "index.html").exists()
    assert (out_dir / "leaderboard.json").exists()
    assert (out_dir / "targets.json").exists()


def test_leaderboard_excludes_toy_targets(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "local-toy"
    run_dir.mkdir(parents=True)
    (run_dir / "scorecard.json").write_text(
        json.dumps(
            {
                "target": {"id": "toy", "framework": "toy", "mode": "white_box"},
                "suite": "seven_sins_v0_1",
                "overall": {"passed": 1, "total": 1, "score": 1.0},
                "categories": {},
                "failures": [],
            }
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "site" / "leaderboard"
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    write_leaderboard(tmp_path / "runs", out_dir, targets_dir)

    leaderboard = json.loads((out_dir / "leaderboard.json").read_text(encoding="utf-8"))
    assert leaderboard == []


def test_scored_target_does_not_report_missing_env_blocker(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "hosted"
    run_dir.mkdir(parents=True)
    (run_dir / "scorecard.json").write_text(
        json.dumps(
            {
                "target": {"id": "hosted", "framework": "hosted", "mode": "white_box"},
                "suite": "seven_sins_v0_1",
                "overall": {"passed": 1, "total": 1, "score": 1.0},
                "categories": {},
                "failures": [],
            }
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "site" / "leaderboard"
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    (targets_dir / "hosted.yaml").write_text(
        "\n".join(
            [
                "id: hosted",
                "framework: hosted",
                "mode: white_box",
                "status: implemented",
                "runtime:",
                "  required_env:",
                "    - HOSTED_API_KEY",
            ]
        ),
        encoding="utf-8",
    )
    write_leaderboard(tmp_path / "runs", out_dir, targets_dir)

    targets = json.loads((out_dir / "targets.json").read_text(encoding="utf-8"))
    assert targets[0]["blockers"] == []
