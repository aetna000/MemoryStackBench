from __future__ import annotations

from typing import Any

from memorybench.adapters.base import MemoryStackAdapter


class ZepAdapter(MemoryStackAdapter):
    """Zep integration placeholder for a pinned cloud or self-hosted target."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        raise RuntimeError(
            "ZepAdapter is not implemented in v0. Wire this against a pinned Zep "
            "SDK/API configuration before publishing framework scores."
        )

    def reset_subject(self, subject_id: str) -> None:
        raise NotImplementedError

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        raise NotImplementedError

