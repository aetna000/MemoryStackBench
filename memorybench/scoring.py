from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

from memorybench.schemas import CheckResult, MemoryExpectation, ResponseExpectation, Scenario


def evaluate_response(
    scenario: Scenario,
    session_id: str,
    turn_index: int,
    expectation: ResponseExpectation,
    response: str,
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    actual = response.lower()

    for expected in expectation.must_include:
        checks.append(
            CheckResult(
                scenario_id=scenario.id,
                category=scenario.category,
                severity=scenario.severity,
                session_id=session_id,
                turn_index=turn_index,
                kind="response.must_include",
                passed=expected.lower() in actual,
                expected=expected,
                actual=response,
            )
        )

    if expectation.must_include_any:
        checks.append(
            CheckResult(
                scenario_id=scenario.id,
                category=scenario.category,
                severity=scenario.severity,
                session_id=session_id,
                turn_index=turn_index,
                kind="response.must_include_any",
                passed=any(expected.lower() in actual for expected in expectation.must_include_any),
                expected=list(expectation.must_include_any),
                actual=response,
            )
        )

    for expected in expectation.must_not_include:
        checks.append(
            CheckResult(
                scenario_id=scenario.id,
                category=scenario.category,
                severity=scenario.severity,
                session_id=session_id,
                turn_index=turn_index,
                kind="response.must_not_include",
                passed=expected.lower() not in actual,
                expected=expected,
                actual=response,
            )
        )

    for expected in expectation.must_match:
        checks.append(
            CheckResult(
                scenario_id=scenario.id,
                category=scenario.category,
                severity=scenario.severity,
                session_id=session_id,
                turn_index=turn_index,
                kind="response.must_match",
                passed=_regex_search(expected, response),
                expected=expected,
                actual=response,
            )
        )

    if expectation.must_match_any:
        checks.append(
            CheckResult(
                scenario_id=scenario.id,
                category=scenario.category,
                severity=scenario.severity,
                session_id=session_id,
                turn_index=turn_index,
                kind="response.must_match_any",
                passed=any(_regex_search(expected, response) for expected in expectation.must_match_any),
                expected=list(expectation.must_match_any),
                actual=response,
            )
        )

    for expected in expectation.must_not_match:
        checks.append(
            CheckResult(
                scenario_id=scenario.id,
                category=scenario.category,
                severity=scenario.severity,
                session_id=session_id,
                turn_index=turn_index,
                kind="response.must_not_match",
                passed=not _regex_search(expected, response),
                expected=expected,
                actual=response,
            )
        )

    return checks


def evaluate_memory(
    scenario: Scenario,
    expectation: MemoryExpectation,
    records: list[dict[str, Any]] | None,
) -> list[CheckResult]:
    if records is None:
        if (
            expectation.should_contain
            or expectation.should_not_contain
            or expectation.should_match
            or expectation.should_not_match
            or expectation.required_fields
            or expectation.record_checks
        ):
            return [
                CheckResult(
                    scenario_id=scenario.id,
                    category=scenario.category,
                    severity=scenario.severity,
                    kind="memory.inspectable",
                    passed=False,
                    expected="adapter returns memory records",
                    actual=None,
                    message="Memory expectations were defined, but the adapter did not expose memory.",
                )
            ]
        return []

    live_records = [record for record in records if not record.get("deleted_at")]
    memory_text = "\n".join(str(record.get("content", "")) for record in live_records).lower()
    checks: list[CheckResult] = []

    for expected in expectation.should_contain:
        checks.append(
            CheckResult(
                scenario_id=scenario.id,
                category=scenario.category,
                severity=scenario.severity,
                kind="memory.should_contain",
                passed=expected.lower() in memory_text,
                expected=expected,
                actual=[record.get("content") for record in live_records],
            )
        )

    for expected in expectation.should_not_contain:
        checks.append(
            CheckResult(
                scenario_id=scenario.id,
                category=scenario.category,
                severity=scenario.severity,
                kind="memory.should_not_contain",
                passed=expected.lower() not in memory_text,
                expected=expected,
                actual=[record.get("content") for record in live_records],
            )
        )

    for expected in expectation.should_match:
        checks.append(
            CheckResult(
                scenario_id=scenario.id,
                category=scenario.category,
                severity=scenario.severity,
                kind="memory.should_match",
                passed=_regex_search(expected, memory_text),
                expected=expected,
                actual=[record.get("content") for record in live_records],
            )
        )

    for expected in expectation.should_not_match:
        checks.append(
            CheckResult(
                scenario_id=scenario.id,
                category=scenario.category,
                severity=scenario.severity,
                kind="memory.should_not_match",
                passed=not _regex_search(expected, memory_text),
                expected=expected,
                actual=[record.get("content") for record in live_records],
            )
        )

    for field_name in expectation.required_fields:
        missing = [
            record.get("memory_id", f"record-{index}")
            for index, record in enumerate(live_records)
            if record.get(field_name) in (None, "")
        ]
        checks.append(
            CheckResult(
                scenario_id=scenario.id,
                category=scenario.category,
                severity=scenario.severity,
                kind="memory.required_field",
                passed=not missing,
                expected=field_name,
                actual=missing,
            )
        )

    for record_check in expectation.record_checks:
        matching_records = [
            record
            for record in live_records
            if _record_matches_content(record, record_check.content_contains, record_check.content_matches)
        ]
        passed = any(
            all(record.get(field) == value for field, value in record_check.fields.items())
            for record in matching_records
        )
        checks.append(
            CheckResult(
                scenario_id=scenario.id,
                category=scenario.category,
                severity=scenario.severity,
                kind="memory.record_fields",
                passed=passed,
                expected={
                    "content_contains": record_check.content_contains,
                    "content_matches": record_check.content_matches,
                    "fields": record_check.fields,
                },
                actual=[
                    {
                        "memory_id": record.get("memory_id"),
                        "content": record.get("content"),
                        **{
                            field: record.get(field)
                            for field in record_check.fields
                        },
                    }
                    for record in matching_records
                ],
            )
        )

    return checks


def build_scorecard(
    target_manifest: dict[str, Any],
    suite_name: str,
    checks: list[CheckResult],
) -> dict[str, Any]:
    by_category: dict[str, list[CheckResult]] = defaultdict(list)
    by_scenario: dict[str, list[CheckResult]] = defaultdict(list)
    for check in checks:
        by_category[check.category].append(check)
        by_scenario[check.scenario_id].append(check)

    def ratio(items: list[CheckResult]) -> dict[str, Any]:
        total = len(items)
        passed = sum(1 for item in items if item.passed)
        return {
            "passed": passed,
            "total": total,
            "score": round(passed / total, 4) if total else None,
        }

    def scenario_ratio(groups: dict[str, list[CheckResult]]) -> dict[str, Any]:
        total = len(groups)
        passed = sum(
            1
            for items in groups.values()
            if items and all(item.passed for item in items)
        )
        return {
            "passed": passed,
            "total": total,
            "score": round(passed / total, 4) if total else None,
        }

    def weighted_ratio(items: list[CheckResult]) -> dict[str, Any]:
        weights = {"critical": 5, "high": 3, "medium": 2, "low": 1}
        total = sum(weights.get(item.severity, 1) for item in items)
        passed = sum(weights.get(item.severity, 1) for item in items if item.passed)
        return {
            "passed_weight": passed,
            "total_weight": total,
            "score": round(passed / total, 4) if total else None,
        }

    failures = [
        check.to_dict()
        for check in checks
        if not check.passed
    ]
    return {
        "target": {
            "id": target_manifest.get("id"),
            "framework": target_manifest.get("framework"),
            "mode": target_manifest.get("mode"),
        },
        "suite": suite_name,
        "overall": ratio(checks),
        "scenario_overall": scenario_ratio(by_scenario),
        "severity_weighted_overall": weighted_ratio(checks),
        "categories": {
            category: ratio(items)
            for category, items in sorted(by_category.items())
        },
        "scenarios": {
            scenario_id: ratio(items)
            for scenario_id, items in sorted(by_scenario.items())
        },
        "failures": failures,
    }


def _regex_search(pattern: str, value: str) -> bool:
    return re.search(pattern, value, flags=re.IGNORECASE | re.DOTALL) is not None


def _record_matches_content(
    record: dict[str, Any],
    content_contains: str | None,
    content_matches: str | None,
) -> bool:
    content = str(record.get("content") or "")
    if content_contains is not None and content_contains.lower() not in content.lower():
        return False
    if content_matches is not None and not _regex_search(content_matches, content):
        return False
    return True
