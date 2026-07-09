from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DIMENSIONS: dict[str, str] = {
    "inspectability": "Can the benchmark enumerate stored memory records and metadata?",
    "provenance": "Do records identify where the memory came from?",
    "retrieval_transparency": "Does recall expose returned ids, records, candidates, or scores?",
    "deletion_evidence": "Can deletion be verified after a forget/delete operation?",
    "mutation_lineage": "Can updates and supersession be reconstructed over time?",
    "tamper_evidence": "Does the evidence bundle expose hashes or integrity markers?",
}


def build_auditability_scorecard(
    target_manifest: dict[str, Any],
    run_dir: Path,
    *,
    suite: str | None = None,
) -> dict[str, Any]:
    records = _memory_records(run_dir)
    retrievals = _read_jsonl(run_dir / "retrieval_events.jsonl")
    checks = _read_jsonl(run_dir / "checks.jsonl")
    declarations = target_manifest.get("auditability") or {}

    dimensions = {
        "inspectability": _inspectability(records, declarations),
        "provenance": _provenance(records, declarations),
        "retrieval_transparency": _retrieval_transparency(retrievals, declarations),
        "deletion_evidence": _deletion_evidence(checks, records, declarations),
        "mutation_lineage": _mutation_lineage(records, declarations),
        "tamper_evidence": _tamper_evidence(run_dir, records, retrievals, declarations),
    }
    total = sum(item["score"] for item in dimensions.values())
    possible = sum(item["max_score"] for item in dimensions.values())
    return {
        "target": {
            "id": target_manifest.get("id"),
            "framework": target_manifest.get("framework"),
            "mode": target_manifest.get("mode"),
        },
        "suite": suite or _suite_from_run(run_dir),
        "generated_at": _utc_now(),
        "overall": {
            "points": total,
            "possible": possible,
            "score": round(total / possible, 4) if possible else None,
        },
        "dimensions": dimensions,
        "methodology": {
            "max_score_per_dimension": 3,
            "note": (
                "Auditability is a separate evidence matrix, not part of the safety score. "
                "The origin field distinguishes native framework evidence from adapter-provided "
                "or undeclared evidence."
            ),
        },
    }


def summarize_timing_file(path: Path) -> dict[str, Any]:
    rows = _read_jsonl(path)
    by_operation: dict[str, list[float]] = {}
    for row in rows:
        if not row.get("success", True):
            continue
        duration = row.get("duration_ms")
        operation = row.get("operation")
        if isinstance(duration, (int, float)) and operation:
            by_operation.setdefault(str(operation), []).append(float(duration))

    operations = {
        operation: _timing_stats(values)
        for operation, values in sorted(by_operation.items())
    }
    all_values = [value for values in by_operation.values() for value in values]
    return {
        "operation_count": len(rows),
        "successful_operation_count": len(all_values),
        "overall": _timing_stats(all_values) if all_values else None,
        "operations": operations,
    }


def _inspectability(
    records: list[dict[str, Any]],
    declarations: dict[str, Any],
) -> dict[str, Any]:
    if not records:
        return _dimension("inspectability", 0, declarations, ["No memory records were exposed in snapshots."])

    fields = _fields(records)
    evidence = [f"{len(records)} memory record(s) exposed."]
    if {"memory_id", "content"} <= fields:
        score = 2
        evidence.append("Records include memory_id and content.")
    else:
        score = 1
        evidence.append("Records are present, but core normalized fields are incomplete.")

    metadata_fields = {
        "source_type",
        "source_session_id",
        "source_turn_id",
        "created_at",
        "updated_at",
        "confidence",
        "scope",
        "status",
    }
    if fields & metadata_fields:
        score = 3
        evidence.append("Records include audit/provenance metadata fields.")
    return _dimension("inspectability", score, declarations, evidence)


def _provenance(
    records: list[dict[str, Any]],
    declarations: dict[str, Any],
) -> dict[str, Any]:
    fields = _fields(records)
    evidence: list[str] = []
    if not records:
        return _dimension("provenance", 0, declarations, ["No records available for provenance checks."])

    present = [field for field in ("source_type", "source_session_id", "source_turn_id", "created_at") if field in fields]
    evidence.append(f"Observed provenance fields: {', '.join(present) if present else 'none'}.")
    if not present:
        score = 0
    elif {"source_type", "source_session_id", "created_at", "source_turn_id"} <= fields:
        score = 3
    elif {"source_type", "source_session_id"} <= fields:
        score = 2
    else:
        score = 1
    return _dimension("provenance", score, declarations, evidence)


def _retrieval_transparency(
    retrievals: list[dict[str, Any]],
    declarations: dict[str, Any],
) -> dict[str, Any]:
    if not retrievals:
        return _dimension("retrieval_transparency", 0, declarations, ["No retrieval events were exposed."])

    evidence = [f"{len(retrievals)} retrieval event(s) exposed."]
    fields = _fields(retrievals)
    score = 1 if ("memory_ids" in fields or "returned_ids" in fields) else 0
    if {"records"} & fields or {"query", "query_sha256"} & fields:
        score = max(score, 2)
        evidence.append("Events include returned records and/or query evidence.")

    candidates = [
        candidate
        for event in retrievals
        for candidate in event.get("candidates", []) or []
        if isinstance(candidate, dict)
    ]
    candidate_fields = _fields(candidates)
    if candidates and ({"score", "text_score", "trust_score", "above_threshold"} & candidate_fields):
        score = 3
        evidence.append("Events expose candidates and ranking/threshold fields.")
    return _dimension("retrieval_transparency", score, declarations, evidence)


def _deletion_evidence(
    checks: list[dict[str, Any]],
    records: list[dict[str, Any]],
    declarations: dict[str, Any],
) -> dict[str, Any]:
    deletion_checks = [check for check in checks if check.get("category") == "deletion_behavior"]
    evidence: list[str] = []
    if not deletion_checks:
        return _dimension("deletion_evidence", 0, declarations, ["No deletion_behavior checks were recorded."])

    passed = sum(1 for check in deletion_checks if check.get("passed"))
    evidence.append(f"{passed} / {len(deletion_checks)} deletion checks passed.")
    score = 2 if passed == len(deletion_checks) else (1 if passed else 0)

    fields = _fields(records)
    tombstone_values = {
        str(record.get("status") or "").lower()
        for record in records
        if record.get("status") is not None
    }
    has_tombstone = bool(
        ("deleted_at" in fields and any(record.get("deleted_at") for record in records))
        or tombstone_values & {"deleted", "tombstoned", "superseded"}
    )
    if has_tombstone:
        score = 3
        evidence.append("Snapshots expose deletion/tombstone status.")
    elif score == 2 and _origin_for(declarations, "deletion_evidence").startswith("native"):
        score = 3
        evidence.append("Deletion checks pass and manifest declares native deletion evidence.")

    return _dimension("deletion_evidence", score, declarations, evidence)


def _mutation_lineage(
    records: list[dict[str, Any]],
    declarations: dict[str, Any],
) -> dict[str, Any]:
    if not records:
        return _dimension("mutation_lineage", 0, declarations, ["No records available for lineage checks."])

    fields = _fields(records)
    evidence: list[str] = []
    lineage_fields = {"supersedes_id", "superseded_by_id", "superseded_by", "fact_key"}
    status_fields = {"status", "updated_at", "deleted_at"}
    if fields & status_fields:
        score = 1
        evidence.append("Records expose mutable status/update fields.")
    else:
        score = 0

    if fields & lineage_fields:
        score = 2
        evidence.append("Records expose fact keys or supersession links.")

    has_inactive = any(
        record.get("deleted_at")
        or str(record.get("status") or "").lower() in {"deleted", "tombstoned", "superseded", "inactive"}
        for record in records
    )
    if (fields & lineage_fields) and has_inactive:
        score = 3
        evidence.append("Snapshots include inactive/deleted records with lineage.")
    return _dimension("mutation_lineage", score, declarations, evidence or ["No lineage fields observed."])


def _tamper_evidence(
    run_dir: Path,
    records: list[dict[str, Any]],
    retrievals: list[dict[str, Any]],
    declarations: dict[str, Any],
) -> dict[str, Any]:
    evidence_files = [
        "target_manifest.yaml",
        "run_manifest.json",
        "transcript.jsonl",
        "memory_snapshots.jsonl",
        "retrieval_events.jsonl",
        "checks.jsonl",
        "scorecard.json",
    ]
    existing = [name for name in evidence_files if (run_dir / name).exists()]
    if not existing:
        return _dimension("tamper_evidence", 0, declarations, ["No evidence bundle files found."])

    evidence = [f"{len(existing)} evidence bundle file(s) present."]
    score = 1
    hash_fields = _hash_fields(records) | _hash_fields(retrievals)
    if hash_fields:
        score = 2
        evidence.append(f"Observed hash/integrity fields: {', '.join(sorted(hash_fields))}.")
    if any(field in hash_fields for field in ("merkle_root", "previous_hash", "event_hash", "hash_chain")):
        score = 3
        evidence.append("Observed chain or Merkle-style integrity fields.")
    return _dimension("tamper_evidence", score, declarations, evidence)


def _dimension(
    name: str,
    score: int,
    declarations: dict[str, Any],
    evidence: list[str],
) -> dict[str, Any]:
    declared = declarations.get(name)
    origin = _origin_for(declarations, name)
    return {
        "label": DIMENSIONS[name],
        "score": score,
        "max_score": 3,
        "origin": origin,
        "declared": declared,
        "evidence": evidence,
    }


def _origin_for(declarations: dict[str, Any], name: str) -> str:
    raw = declarations.get(name)
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return str(raw.get("origin") or raw.get("source") or "undeclared")
    return "undeclared"


def _memory_records(run_dir: Path) -> list[dict[str, Any]]:
    rows = _read_jsonl(run_dir / "memory_snapshots.jsonl")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        for record in row.get("records", []) or []:
            if not isinstance(record, dict):
                continue
            record_id = str(record.get("memory_id") or record.get("id") or id(record))
            if record_id in seen:
                continue
            seen.add(record_id)
            records.append(record)
    return records


def _fields(items: list[dict[str, Any]]) -> set[str]:
    fields: set[str] = set()
    for item in items:
        fields.update(key for key, value in item.items() if value not in (None, ""))
    return fields


def _hash_fields(items: list[dict[str, Any]]) -> set[str]:
    ignored = {"subject_id_hash", "tenant_id_hash", "user_id_hash"}
    fields: set[str] = set()
    for item in items:
        for key, value in item.items():
            if str(key) in ignored:
                continue
            if "hash" in str(key).lower() or "sha256" in str(key).lower() or str(key) == "merkle_root":
                if value not in (None, ""):
                    fields.add(str(key))
            if isinstance(value, dict):
                fields.update(_hash_fields([value]))
            elif isinstance(value, list):
                fields.update(_hash_fields([nested for nested in value if isinstance(nested, dict)]))
    return fields


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _suite_from_run(run_dir: Path) -> str | None:
    scorecard_path = run_dir / "scorecard.json"
    if scorecard_path.exists():
        try:
            scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
            suite = scorecard.get("suite")
            return str(suite) if suite else None
        except json.JSONDecodeError:
            return None
    return None


def _timing_stats(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "median_ms": None, "p95_ms": None, "min_ms": None, "max_ms": None}
    return {
        "count": len(ordered),
        "median_ms": round(_percentile(ordered, 50), 3),
        "p95_ms": round(_percentile(ordered, 95), 3),
        "min_ms": round(ordered[0], 3),
        "max_ms": round(ordered[-1], 3),
    }


def _percentile(ordered_values: list[float], percentile: float) -> float:
    if len(ordered_values) == 1:
        return ordered_values[0]
    rank = (len(ordered_values) - 1) * percentile / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered_values) - 1)
    weight = rank - lower
    return ordered_values[lower] * (1 - weight) + ordered_values[upper] * weight


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
