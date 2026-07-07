from memorybench.adapters.supermemory import _clean_api_key, _compact_metadata, _search_query


def test_clean_api_key_accepts_raw_or_bearer_value() -> None:
    assert _clean_api_key("  sm_test  ") == "sm_test"
    assert _clean_api_key('"Bearer sm_test"') == "sm_test"


def test_compact_metadata_removes_nulls() -> None:
    assert _compact_metadata({"source_type": "user_message", "confidence": None}) == {
        "source_type": "user_message"
    }


def test_supermemory_search_query_uses_scenario_terms() -> None:
    assert "airport" in _search_query("Which airport should you use for me?")
