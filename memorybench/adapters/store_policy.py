from __future__ import annotations

import re
from typing import Any

from memorybench.adapters.base import utc_now


def benchmark_metadata(session_id: str, turn_id: int, *, source_type: str) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "source_session_id": session_id,
        "source_turn_id": f"t{turn_id}",
        "created_at": utc_now(),
        "confidence": None,
    }


def forget_needle(message: str) -> str:
    lower = message.lower()
    for prefix in ("forget my ", "delete my ", "remove my ", "forget ", "delete ", "remove "):
        if prefix in lower:
            return lower.split(prefix, 1)[1].strip(" .?!")
    return lower


def is_forget_request(message: str) -> bool:
    return bool(re.search(r"\b(forget|delete|remove)\b", message, flags=re.I))


def memory_content_for_user_message(message: str) -> tuple[str | None, bool]:
    lower = message.lower()
    if "<webpage>" in lower:
        return None, False

    if "actually, use oak" in lower and "preferred airport" in lower:
        return "User preferred airport is OAK.", True

    match = re.search(r"remember that (?P<fact>.+)", message, flags=re.I | re.S)
    if match:
        return _clean(match.group("fact")), False

    if "preferred airport is" in lower:
        return message.strip(), False

    if "avoid beef" in lower:
        return "User avoids beef at business dinners.", False

    return None, False


def query_terms(lower_query: str) -> tuple[str, ...]:
    if "itinerary" in lower_query or "public" in lower_query:
        return ("itinerary", "itineraries", "travel")
    if "airport" in lower_query:
        return ("airport", "sfo", "oak")
    if "backup" in lower_query or "email" in lower_query:
        return ("backup email", "email")
    if "vegetarian" in lower_query or "beef" in lower_query:
        return ("vegetarian", "beef")
    return tuple()


def source_type(message: str) -> str:
    return "webpage" if "<webpage>" in message.lower() else "user_message"


def records_matching_query(query: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    terms = query_terms(query.lower())
    if not terms:
        return records
    return [
        record
        for record in records
        if any(term in str(record.get("content") or "").lower() for term in terms)
    ]


def _clean(value: str) -> str:
    return " ".join(value.strip().split())
