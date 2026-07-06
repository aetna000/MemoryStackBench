from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResponseExpectation:
    must_include: tuple[str, ...] = ()
    must_include_any: tuple[str, ...] = ()
    must_not_include: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ResponseExpectation":
        raw = raw or {}
        return cls(
            must_include=tuple(raw.get("must_include", ()) or ()),
            must_include_any=tuple(raw.get("must_include_any", ()) or ()),
            must_not_include=tuple(raw.get("must_not_include", ()) or ()),
        )


@dataclass(frozen=True)
class MemoryExpectation:
    should_contain: tuple[str, ...] = ()
    should_not_contain: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "MemoryExpectation":
        raw = raw or {}
        return cls(
            should_contain=tuple(raw.get("should_contain", ()) or ()),
            should_not_contain=tuple(raw.get("should_not_contain", ()) or ()),
            required_fields=tuple(raw.get("required_fields", ()) or ()),
        )


@dataclass(frozen=True)
class Turn:
    user: str
    expect_response: ResponseExpectation = field(default_factory=ResponseExpectation)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Turn":
        if "user" not in raw:
            raise ValueError("Each turn must include a 'user' message")
        return cls(
            user=str(raw["user"]),
            expect_response=ResponseExpectation.from_dict(raw.get("expect_response")),
        )


@dataclass(frozen=True)
class Session:
    id: str
    turns: tuple[Turn, ...]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Session":
        if "id" not in raw:
            raise ValueError("Each session must include an id")
        turns = tuple(Turn.from_dict(item) for item in raw.get("turns", ()))
        if not turns:
            raise ValueError(f"Session {raw['id']} must include at least one turn")
        return cls(id=str(raw["id"]), turns=turns)


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    category: str
    severity: str
    sessions: tuple[Session, ...]
    expect_memory: MemoryExpectation = field(default_factory=MemoryExpectation)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Scenario":
        for key in ("id", "title", "category", "severity", "sessions"):
            if key not in raw:
                raise ValueError(f"Scenario is missing required field: {key}")
        sessions = tuple(Session.from_dict(item) for item in raw.get("sessions", ()))
        if not sessions:
            raise ValueError(f"Scenario {raw['id']} must include at least one session")
        return cls(
            id=str(raw["id"]),
            title=str(raw["title"]),
            category=str(raw["category"]),
            severity=str(raw["severity"]),
            sessions=sessions,
            expect_memory=MemoryExpectation.from_dict(raw.get("expect_memory")),
            raw=raw,
        )


@dataclass(frozen=True)
class CheckResult:
    scenario_id: str
    category: str
    severity: str
    kind: str
    passed: bool
    expected: Any
    actual: Any
    session_id: str | None = None
    turn_index: int | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "category": self.category,
            "severity": self.severity,
            "kind": self.kind,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
            "session_id": self.session_id,
            "turn_index": self.turn_index,
            "message": self.message,
        }

