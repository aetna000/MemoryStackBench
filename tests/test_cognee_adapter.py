from memorybench.adapters.cognee import _search_query


def test_cognee_search_query_uses_scenario_terms() -> None:
    assert "airport" in _search_query("Which airport should you use for me?")
    assert "backup email" in _search_query("Are you allowed to use my backup email?")
