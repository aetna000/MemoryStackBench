from __future__ import annotations

from typing import Any

from memorybench.adapters.base import MemoryStackAdapter


class LettaAdapter(MemoryStackAdapter):
    """Letta integration placeholder for a pinned self-hosted App Server target."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        raise RuntimeError(
            "LettaAdapter is not implemented in v0. Wire this against a pinned "
            "Letta server/client version before publishing framework scores."
        )

    def reset_subject(self, subject_id: str) -> None:
        raise NotImplementedError

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        raise NotImplementedError

