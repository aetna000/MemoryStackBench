from memorybench.adapters.google_adk_memory_bank import _memory_scope, _scope


def test_google_scope_is_hashed_and_run_scoped() -> None:
    scope = _scope("subject-123", "run_abc")

    assert scope["user_id"].startswith("mb-")
    assert scope["app_name"] == "memorystackbench-run_abc"
    assert "subject-123" not in scope["user_id"]


def test_google_memory_scope_normalizes_dict_scope() -> None:
    class Memory:
        scope = {"user_id": "u1", "app_name": "app"}

    assert _memory_scope(Memory()) == {"user_id": "u1", "app_name": "app"}
