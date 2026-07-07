from memorybench.adapters.hindsight import _bank_id, _content_from_item, _search_query


def test_hindsight_bank_id_is_stable_and_prefixed() -> None:
    bank_id = _bank_id("run123", "subject-a")
    assert bank_id.startswith("memorybench-run123-")
    assert bank_id == _bank_id("run123", "subject-a")


def test_hindsight_content_from_item_prefers_text_fields() -> None:
    assert _content_from_item({"text": "remembered fact", "content": "other"}) == "remembered fact"
    assert "fallback" in _content_from_item({"fallback": True})


def test_hindsight_search_query_uses_scenario_terms() -> None:
    assert "airport" in _search_query("Which airport should you use for me?")
    assert "backup email" in _search_query("Are you allowed to use my backup email?")
