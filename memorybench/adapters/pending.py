from __future__ import annotations

from typing import Any

from memorybench.adapters.base import MemoryStackAdapter


class PendingAdapter(MemoryStackAdapter):
    """Adapter marker for registry targets that are not implemented yet."""

    capabilities = {
        "inspect_memory": False,
        "delete_memory": False,
        "retrieval_log": False,
        "multi_user": False,
        "multi_tenant": False,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.target_id = self.config.get("id", "unknown_target")
        self.framework = self.config.get("framework", "unknown_framework")
        self.status = self.config.get("status", "pending")

    def reset_subject(self, subject_id: str) -> None:
        raise RuntimeError(self._message())

    def send(self, subject_id: str, session_id: str, message: str) -> str:
        raise RuntimeError(self._message())

    def _message(self) -> str:
        return (
            f"Target {self.target_id} ({self.framework}) is registered but not "
            f"implemented yet. Status: {self.status}. Add a concrete adapter before "
            "publishing benchmark scores for this target."
        )

