from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStackAdapter(ABC):
    """Common benchmark contract implemented by every memory stack target."""

    capabilities: dict[str, Any] = {
        "inspect_memory": False,
        "delete_memory": False,
        "retrieval_log": False,
        "multi_user": False,
        "multi_tenant": False,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    @abstractmethod
    def reset_subject(self, subject_id: str) -> None:
        """Remove all benchmark state for a subject before a scenario starts."""

    def start_session(self, subject_id: str, session_id: str) -> None:
        """Start or select a session for a subject."""

    @abstractmethod
    def send(self, subject_id: str, session_id: str, message: str) -> str:
        """Send one user message and return the target's response."""

    def inspect_memory(self, subject_id: str) -> list[dict[str, Any]] | None:
        """Return normalized memory records if the target supports inspection."""
        return None

    def delete_memory(self, subject_id: str, selector: dict[str, Any]) -> bool | None:
        """Delete memory matching a selector if the target supports deletion."""
        return None

    def get_retrieval_log(
        self, subject_id: str, session_id: str
    ) -> list[dict[str, Any]] | None:
        """Return normalized retrieval events if the target exposes them."""
        return None

    def close(self) -> None:
        """Release network clients or subprocess resources."""

