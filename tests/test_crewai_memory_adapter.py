from memorybench.adapters.crewai_memory import _categories, _scope, _search_query


def test_crewai_categories_detect_benchmark_domains() -> None:
    assert "travel" in _categories("User's preferred airport is OAK.")
    assert "contact" in _categories("User's backup email is private-backup@example.com.")
    assert "diet" in _categories("User avoids beef at business dinners.")


def test_crewai_scope_is_hashed() -> None:
    scope = _scope("subject-123")

    assert scope.startswith("/memorybench/")
    assert "subject-123" not in scope


def test_crewai_search_query_expands_common_probes() -> None:
    assert "OAK" in _search_query("Which airport should I use?")
    assert "private-backup@example.com" in _search_query("What backup email do we have?")
