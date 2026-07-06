from pathlib import Path

from memorybench.scenarios import load_suite


def test_loads_initial_suite() -> None:
    scenarios = load_suite(Path("suites/seven_sins_v0_1"))

    assert len(scenarios) == 5
    assert scenarios[0].id == "durable_preference_private_itineraries_001"

