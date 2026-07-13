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
    assert (out_dir / "auditability.json").exists()
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


def test_leaderboard_ranks_and_reports_check_score_as_headline(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()

    _write_scorecard(
        runs_dir / "scenario-heavy",
        target_id="scenario-heavy",
        check_score={"passed": 20, "total": 33, "score": 0.6061},
        scenario_score={"passed": 4, "total": 5, "score": 0.8},
    )
    _write_scorecard(
        runs_dir / "check-heavy",
        target_id="check-heavy",
        check_score={"passed": 24, "total": 33, "score": 0.7273},
        scenario_score={"passed": 0, "total": 5, "score": 0.0},
    )
    (targets_dir / "scenario-heavy.yaml").write_text(
        "id: scenario-heavy\nframework: scenario-heavy\nmode: white_box\nstatus: implemented\n",
        encoding="utf-8",
    )
    (targets_dir / "check-heavy.yaml").write_text(
        "id: check-heavy\nframework: check-heavy\nmode: white_box\nstatus: implemented\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "site" / "leaderboard"
    write_leaderboard(runs_dir, out_dir, targets_dir)

    leaderboard = json.loads((out_dir / "leaderboard.json").read_text(encoding="utf-8"))
    targets = json.loads((out_dir / "targets.json").read_text(encoding="utf-8"))
    html = (out_dir / "index.html").read_text(encoding="utf-8")

    assert leaderboard[0]["run"] == "check-heavy"
    assert {target["target"]: target["score"] for target in targets}["check-heavy"] == 0.7273
    assert "<th>Checks</th><th>Scenarios</th>" in html
    assert "Checks 24/33 (73%) · Scenarios 0/5 (0%)" in html


def test_leaderboard_uses_display_name_when_present(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    _write_scorecard(
        runs_dir / "tree-ring-memory-local",
        target_id="tree_ring_memory_local",
        check_score={"passed": 33, "total": 33, "score": 1.0},
        scenario_score={"passed": 5, "total": 5, "score": 1.0},
        extra_target={"display_name": "Tree Ring Memory", "framework": "tree-ring-memory"},
    )
    (targets_dir / "tree_ring_memory.yaml").write_text(
        "\n".join(
            [
                "id: tree_ring_memory_local",
                "display_name: Tree Ring Memory",
                "framework: tree-ring-memory",
                "mode: white_box",
                "status: implemented_store_harness",
            ]
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "site" / "leaderboard"
    write_leaderboard(runs_dir, out_dir, targets_dir)

    html = (out_dir / "index.html").read_text(encoding="utf-8")
    targets = json.loads((out_dir / "targets.json").read_text(encoding="utf-8"))

    assert "<h3>Tree Ring Memory</h3>" in html
    assert "<td>Tree Ring Memory</td>" in html
    assert targets[0]["display_name"] == "Tree Ring Memory"


def _write_scorecard(
    run_dir: Path,
    *,
    target_id: str,
    check_score: dict[str, float],
    scenario_score: dict[str, float],
    extra_target: dict[str, object] | None = None,
) -> None:
    target = {"id": target_id, "framework": target_id, "mode": "white_box"}
    if extra_target:
        target.update(extra_target)
    run_dir.mkdir(parents=True)
    (run_dir / "scorecard.json").write_text(
        json.dumps(
            {
                "target": target,
                "suite": "seven_sins_v0_1",
                "overall": check_score,
                "scenario_overall": scenario_score,
                "categories": {},
                "failures": [],
            }
        ),
        encoding="utf-8",
    )
