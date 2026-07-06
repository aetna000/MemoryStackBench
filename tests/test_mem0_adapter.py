from memorybench.adapters.mem0 import _forget_needle, _records_from_response


def test_records_from_mem0_envelope() -> None:
    raw = {"results": [{"id": "mem-1", "memory": "User likes tea"}]}

    assert _records_from_response(raw) == [{"id": "mem-1", "memory": "User likes tea"}]


def test_forget_needle() -> None:
    assert _forget_needle("Forget my backup email.") == "backup email"
