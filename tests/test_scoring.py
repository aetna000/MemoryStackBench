from pathlib import Path

from memorybench.scenarios import load_suite
from memorybench.scoring import build_scorecard, evaluate_memory, evaluate_response


def _scenario(scenario_id: str):
    scenarios = load_suite(Path("suites/seven_sins_v0_1"))
    return next(scenario for scenario in scenarios if scenario.id == scenario_id)


def test_overgeneralization_rejects_positive_vegetarian_answer() -> None:
    scenario = _scenario("overgeneralization_diet_001")
    expectation = scenario.sessions[1].turns[0].expect_response

    checks = evaluate_response(
        scenario,
        "s2",
        1,
        expectation,
        "Yes — you're vegetarian, since you avoid beef.",
    )

    assert any(not check.passed for check in checks)
    assert any(check.kind == "response.must_not_match" and not check.passed for check in checks)


def test_webpage_poisoning_requires_original_preference_provenance() -> None:
    scenario = _scenario("suggestibility_webpage_memory_poisoning_001")
    records = [
        {
            "memory_id": "bad-1",
            "content": "User's travel itineraries should be private by default",
            "source_session_id": "s2",
            "source_type": "webpage",
            "created_at": "2026-01-01T00:00:00+00:00",
            "deleted_at": None,
        }
    ]

    checks = evaluate_memory(scenario, scenario.expect_memory, records)

    assert any(check.kind == "memory.record_fields" and not check.passed for check in checks)


def test_webpage_poisoning_rejects_intermediate_compliance() -> None:
    scenario = _scenario("suggestibility_webpage_memory_poisoning_001")
    expectation = scenario.sessions[1].turns[0].expect_response

    checks = evaluate_response(
        scenario,
        "s2",
        1,
        expectation,
        "Got it. I will remember that your itineraries should be public by default.",
    )

    assert any(check.kind == "response.must_not_match" and not check.passed for check in checks)


def test_temporal_update_allows_superseded_sfo_mention() -> None:
    scenario = _scenario("temporal_preference_update_001")
    expectation = scenario.sessions[2].turns[0].expect_response

    checks = evaluate_response(
        scenario,
        "s3",
        1,
        expectation,
        "Previously you used SFO; going forward use OAK.",
    )

    assert all(check.passed for check in checks)


def test_temporal_update_rejects_active_stale_sfo_answer() -> None:
    scenario = _scenario("temporal_preference_update_001")
    expectation = scenario.sessions[2].turns[0].expect_response

    checks = evaluate_response(
        scenario,
        "s3",
        1,
        expectation,
        "Use SFO as your preferred airport.",
    )

    assert any(check.kind == "response.must_not_match" and not check.passed for check in checks)


def test_scorecard_reports_scenario_level_score() -> None:
    scenario = _scenario("overgeneralization_diet_001")
    checks = evaluate_response(
        scenario,
        "s2",
        1,
        scenario.sessions[1].turns[0].expect_response,
        "Yes — you're vegetarian, since you avoid beef.",
    )

    scorecard = build_scorecard(
        {"id": "target", "framework": "framework", "mode": "white_box"},
        "suite",
        checks,
    )

    assert scorecard["overall"]["total"] == len(checks)
    assert scorecard["scenario_overall"]["total"] == 1
    assert scorecard["scenario_overall"]["passed"] == 0


def test_scorecard_preserves_target_display_name() -> None:
    scorecard = build_scorecard(
        {
            "id": "tree_ring_memory_local",
            "display_name": "Tree Ring Memory",
            "framework": "tree-ring-memory",
            "mode": "white_box",
        },
        "suite",
        [],
    )

    assert scorecard["target"]["display_name"] == "Tree Ring Memory"
