from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

# Friendly name + one-line plain-English explanation for each scorecard category.
# Falls back to a humanized slug for any category not listed here, so new suites
# and new categories don't break the report.
CATEGORY_GUIDE: dict[str, tuple[str, str]] = {
    "deletion_behavior": (
        "Deletion Behavior",
        "When the user says “forget this,” does the fact actually stop being usable — not just hidden from the reply?",
    ),
    "retrieval_correctness": (
        "Retrieval Correctness",
        "When the user asks about something they said earlier, does the agent recall the right fact?",
    ),
    "temporal_update_handling": (
        "Handling Corrections",
        "When the user changes their mind, does the new fact replace the old one instead of both being remembered?",
    ),
    "untrusted_source_resistance": (
        "Resisting Untrusted Input",
        "Can text from a webpage, email, or document plant a fake instruction that the agent later treats as something the user asked for?",
    ),
    "write_correctness": (
        "Write Correctness",
        "Does the agent store what the user actually said, without jumping to a broader conclusion?",
    ),
}

_KIND_LABELS: dict[str, str] = {
    "response.must_include": "The reply should mention",
    "response.must_include_any": "The reply should mention at least one of",
    "response.must_not_include": "The reply should NOT mention",
    "response.must_match": "The reply should match",
    "response.must_match_any": "The reply should match at least one pattern",
    "response.must_not_match": "The reply should NOT match",
    "memory.should_contain": "Stored memory should contain",
    "memory.should_not_contain": "Stored memory should no longer contain",
    "memory.should_match": "Stored memory should match",
    "memory.should_not_match": "Stored memory should NOT match",
    "memory.required_field": "Every stored memory should record the field",
    "memory.record_fields": "A matching stored memory should have fields",
    "memory.inspectable": "The adapter should expose memory records for inspection",
}

GUIDE_URL = "https://aetna000.github.io/MemoryStackBench/guide/"


def write_failure_report(scorecard: dict[str, Any], path: Path) -> None:
    lines = [
        f"# Failure Report: {scorecard['target']['id']}",
        "",
        f"Suite: `{scorecard['suite']}`",
        "",
    ]
    failures = scorecard.get("failures", [])
    if not failures:
        lines.append("No failed checks.")
    else:
        for failure in failures:
            location = failure["scenario_id"]
            if failure.get("session_id"):
                location += f" / {failure['session_id']}"
            lines.extend(
                [
                    f"## {location}",
                    "",
                    f"- kind: `{failure['kind']}`",
                    f"- category: `{failure['category']}`",
                    f"- severity: `{failure['severity']}`",
                    f"- expected: `{failure['expected']}`",
                    f"- actual: `{failure['actual']}`",
                    "",
                ]
            )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_html_scorecard(scorecard: dict[str, Any], path: Path, run_dir: Path | None = None) -> None:
    target = scorecard["target"]
    overall = scorecard["overall"]
    scenario_overall = scorecard.get("scenario_overall") or {}
    headline = scenario_overall if scenario_overall.get("score") is not None else overall
    score = _format_score(headline.get("score"))
    scenario_text = (
        f"{scenario_overall.get('passed')} / {scenario_overall.get('total')} scenarios passed; "
        if scenario_overall.get("score") is not None
        else ""
    )

    transcripts, checks_by_scenario, scenario_meta = _load_run_context(run_dir)

    category_rows = "\n".join(
        _category_row(name, value) for name, value in scorecard.get("categories", {}).items()
    )

    scenario_order = sorted(scorecard.get("scenarios", {}).items())
    scenario_sections = "\n".join(
        _scenario_section(
            scenario_id,
            summary,
            checks_by_scenario.get(scenario_id, []),
            transcripts.get(scenario_id, []),
            scenario_meta.get(scenario_id),
        )
        for scenario_id, summary in scenario_order
    )
    if not scenario_sections:
        scenario_sections = "<p>No scenarios were recorded for this run.</p>"

    data = html.escape(json.dumps(scorecard, indent=2))
    evidence_links = "\n".join(
        f'<li><a href="{name}"><code>{name}</code></a> — {desc}</li>'
        for name, desc in (
            ("transcript.jsonl", "every user message and agent reply, in order"),
            ("memory_snapshots.jsonl", "what the memory store looked like after each session"),
            ("retrieval_events.jsonl", "what the agent searched for and got back from memory"),
            ("checks.jsonl", "every pass/fail check this scorecard is built from"),
            ("run_manifest.json", "which target and suite produced this run"),
            ("target_manifest.yaml", "exact configuration of the memory stack under test"),
            ("failure_report.md", "plain-text list of failed checks only"),
            ("scorecard.json", "the machine-readable version of this page"),
        )
    )

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MemoryStackBench - {html.escape(str(target.get('id')))}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --text: #202124;
      --muted: #62665f;
      --border: #d9ddd2;
      --accent: #0f766e;
      --pass: #0f766e;
      --fail: #b42318;
    }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }}
    main {{
      max-width: 1080px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    header {{
      border-bottom: 1px solid var(--border);
      padding-bottom: 24px;
      margin-bottom: 24px;
    }}
    h1 {{
      font-size: clamp(2rem, 4vw, 4rem);
      line-height: 1;
      margin: 0 0 12px;
      letter-spacing: 0;
    }}
    h2 {{
      margin-top: 36px;
      font-size: 1.35rem;
    }}
    .meta {{
      color: var(--muted);
      display: flex;
      flex-wrap: wrap;
      gap: 12px 20px;
    }}
    .lead {{
      color: var(--muted);
      max-width: 780px;
      margin-top: 14px;
    }}
    .score {{
      display: inline-flex;
      align-items: baseline;
      gap: 10px;
      margin-top: 20px;
      color: var(--accent);
    }}
    .score strong {{
      font-size: clamp(2.8rem, 8vw, 6rem);
      line-height: .9;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--border);
    }}
    th, td {{
      padding: 12px 14px;
      text-align: left;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-weight: 650;
      background: #f0f2ec;
    }}
    .cat-desc {{
      color: var(--muted);
      font-size: .88rem;
    }}
    .bar {{
      height: 8px;
      background: #e5e7df;
      border-radius: 999px;
      overflow: hidden;
      margin-top: 6px;
      max-width: 160px;
    }}
    .bar span {{
      display: block;
      height: 100%;
      background: var(--accent);
    }}
    .scenario {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px 20px;
      margin: 14px 0;
    }}
    .scenario.has-failure {{
      border-left: 4px solid var(--fail);
    }}
    .scenario.all-pass {{
      border-left: 4px solid var(--pass);
    }}
    .scenario-title {{
      font-size: 1.05rem;
      font-weight: 700;
      margin: 0 0 4px;
    }}
    .badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 6px 0 10px;
    }}
    .badge {{
      display: inline-block;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 2px 9px;
      font-size: .78rem;
      color: var(--muted);
      background: #f0f2ec;
    }}
    .badge.result-pass {{
      color: var(--pass);
      border-color: #bfe3dc;
      background: #eaf6f3;
    }}
    .badge.result-fail {{
      color: var(--fail);
      border-color: #f0c6bd;
      background: #fdeeea;
    }}
    .conversation {{
      margin: 10px 0 14px;
      display: grid;
      gap: 6px;
    }}
    .session-label {{
      font-size: .78rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .04em;
      margin-top: 8px;
    }}
    .turn {{
      display: grid;
      grid-template-columns: 70px 1fr;
      gap: 10px;
      font-size: .92rem;
      padding: 4px 0;
    }}
    .turn .who {{
      color: var(--muted);
      font-weight: 650;
    }}
    .checklist {{
      list-style: none;
      margin: 8px 0 0;
      padding: 0;
      display: grid;
      gap: 8px;
    }}
    .checklist li {{
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 12px;
      font-size: .92rem;
    }}
    .checklist li.fail {{
      border-color: #f0c6bd;
      background: #fdf6f4;
    }}
    .checklist li.pass {{
      background: #fbfcfa;
    }}
    .why {{
      margin-top: 10px;
      padding: 10px 12px;
      background: #fff8f6;
      border: 1px solid #f0c6bd;
      border-radius: 6px;
      font-size: .92rem;
    }}
    code, pre {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }}
    code {{
      background: #eef1ea;
      padding: 1px 4px;
      border-radius: 4px;
    }}
    pre {{
      overflow: auto;
      background: #191b1f;
      color: #f3f4f6;
      padding: 16px;
      border-radius: 6px;
    }}
    details summary {{
      cursor: pointer;
      color: var(--muted);
      font-weight: 650;
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>MemoryStackBench</h1>
      <div class="meta">
        <span>Target: <strong>{html.escape(str(target.get('id')))}</strong></span>
        <span>Framework: <strong>{html.escape(str(target.get('framework')))}</strong></span>
        <span>Mode: <strong>{html.escape(str(target.get('mode')))}</strong></span>
        <span>Suite: <strong>{html.escape(str(scorecard.get('suite')))}</strong></span>
      </div>
      <div class="score"><strong>{score}</strong><span>{scenario_text}{overall['passed']} / {overall['total']} checks passed</span></div>
      <p class="lead">
        Each scenario below tells an agent a fact, then tries to knock that fact off course —
        a correction, a deletion request, time passing, or an untrusted webpage — and then asks
        the agent about it later. A pass means the agent's answer <em>and</em> its stored memory
        agreed on the right fact. See the
        <a href="{GUIDE_URL}">plain-language guide</a> for background on what each category means.
      </p>
    </header>

    <section>
      <h2>Category Scores</h2>
      <table>
        <thead><tr><th>Category</th><th>What it checks</th><th>Score</th></tr></thead>
        <tbody>{category_rows}</tbody>
      </table>
    </section>

    <section>
      <h2>Scenario Walkthrough</h2>
      {scenario_sections}
    </section>

    <section>
      <h2>Evidence Bundle</h2>
      <p class="cat-desc">Raw logs behind every number on this page, in case you want to double-check or dig deeper.</p>
      <ul>{evidence_links}</ul>
    </section>

    <section>
      <details>
        <summary>Raw scorecard.json</summary>
        <pre>{data}</pre>
      </details>
    </section>
  </main>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def copy_site(run_dir: Path, site_dir: Path) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    scorecard_path = run_dir / "scorecard.json"
    if scorecard_path.exists():
        scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
        write_html_scorecard(scorecard, site_dir / "index.html", run_dir=run_dir)
    for name in (
        "scorecard.json",
        "failure_report.md",
        "transcript.jsonl",
        "memory_snapshots.jsonl",
        "retrieval_events.jsonl",
        "checks.jsonl",
        "run_manifest.json",
        "target_manifest.yaml",
    ):
        source = run_dir / name
        if source.exists():
            (site_dir / name).write_bytes(source.read_bytes())
    if site_dir.parent.name == "site":
        _write_site_index(site_dir.parent)


def _format_score(score: float | None) -> str:
    if score is None:
        return "N/A"
    return f"{round(score * 100)}%"


def _humanize(slug: str) -> str:
    words = slug.replace("-", " ").replace("_", " ").split()
    if words and words[-1].isdigit():
        words = words[:-1]
    return " ".join(words).capitalize()


def _category_row(name: str, value: dict[str, Any]) -> str:
    label, description = CATEGORY_GUIDE.get(name, (_humanize(name), f"Checks “{_humanize(name).lower()}” behavior."))
    pct = round((value.get("score") or 0) * 100)
    return (
        "<tr>"
        f"<td><strong>{html.escape(label)}</strong></td>"
        f"<td class=\"cat-desc\">{html.escape(description)}</td>"
        f"<td>{_format_score(value.get('score'))} ({value['passed']} / {value['total']})"
        f"<div class=\"bar\"><span style=\"width:{pct}%\"></span></div></td>"
        "</tr>"
    )


def _describe_check(item: dict[str, Any]) -> str:
    kind = item.get("kind", "")
    label = _KIND_LABELS.get(kind, kind)
    expected = item.get("expected")
    if isinstance(expected, list):
        expected_text = " / ".join(f"“{e}”" for e in expected)
    else:
        expected_text = f"“{expected}”"
    return f"{label}: {expected_text}"


def _format_actual(item: dict[str, Any]) -> str:
    actual = item.get("actual")
    if isinstance(actual, list):
        if not actual:
            return "(nothing stored)"
        return "; ".join(str(a) for a in actual)
    return str(actual)


def _checklist_item(item: dict[str, Any]) -> str:
    passed = item.get("passed")
    css = "pass" if passed else "fail"
    mark = "Passed" if passed else "Failed"
    description = html.escape(_describe_check(item))
    actual = html.escape(_format_actual(item))
    detail = "" if passed else f'<div>Actual: <code>{actual}</code></div>'
    return f'<li class="{css}"><strong>{mark}.</strong> {description}{detail}</li>'


def _scenario_section(
    scenario_id: str,
    summary: dict[str, Any],
    checks: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    meta: dict[str, Any] | None,
) -> str:
    title = (meta or {}).get("title") or _humanize(scenario_id)
    category = (meta or {}).get("category") or (checks[0]["category"] if checks else "")
    severity = (meta or {}).get("severity") or (checks[0]["severity"] if checks else "")
    category_label = CATEGORY_GUIDE.get(category, (_humanize(category), ""))[0]

    passed = summary.get("passed", 0)
    total = summary.get("total", 0)
    all_pass = total > 0 and passed == total
    section_css = "all-pass" if all_pass else ("has-failure" if total else "")

    conversation_html = _conversation_html(turns)
    checklist_html = "\n".join(_checklist_item(item) for item in checks) or "<li>No checks recorded.</li>"

    why_html = ""
    if not all_pass and category in CATEGORY_GUIDE:
        why_html = f'<div class="why"><strong>Why this matters:</strong> {html.escape(CATEGORY_GUIDE[category][1])}</div>'

    return f"""
    <article class="scenario {section_css}">
      <p class="scenario-title">{html.escape(title)}</p>
      <div class="badges">
        <span class="badge">{html.escape(category_label)}</span>
        <span class="badge">severity: {html.escape(str(severity))}</span>
        <span class="badge {'result-pass' if all_pass else 'result-fail'}">{passed} / {total} checks passed</span>
      </div>
      {conversation_html}
      <ul class="checklist">{checklist_html}</ul>
      {why_html}
    </article>
    """


def _conversation_html(turns: list[dict[str, Any]]) -> str:
    if not turns:
        return ""
    rows = []
    last_session = None
    for turn in turns:
        session_id = turn.get("session_id")
        if session_id != last_session:
            rows.append(f'<div class="session-label">Session {html.escape(str(session_id))}</div>')
            last_session = session_id
        rows.append(
            '<div class="turn"><span class="who">User</span>'
            f"<span>{html.escape(str(turn.get('user', '')))}</span></div>"
        )
        rows.append(
            '<div class="turn"><span class="who">Agent</span>'
            f"<span>{html.escape(str(turn.get('assistant', '')))}</span></div>"
        )
    return f'<div class="conversation">{"".join(rows)}</div>'


def _load_run_context(
    run_dir: Path | None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    transcripts: dict[str, list[dict[str, Any]]] = {}
    checks_by_scenario: dict[str, list[dict[str, Any]]] = {}
    scenario_meta: dict[str, dict[str, Any]] = {}
    if run_dir is None:
        return transcripts, checks_by_scenario, scenario_meta

    transcript_path = run_dir / "transcript.jsonl"
    if transcript_path.exists():
        for row in _read_jsonl(transcript_path):
            transcripts.setdefault(row["scenario_id"], []).append(row)

    checks_path = run_dir / "checks.jsonl"
    if checks_path.exists():
        for row in _read_jsonl(checks_path):
            checks_by_scenario.setdefault(row["scenario_id"], []).append(row)

    scenario_meta = _load_scenario_titles(run_dir)
    return transcripts, checks_by_scenario, scenario_meta


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_scenario_titles(run_dir: Path) -> dict[str, dict[str, Any]]:
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        suite_path = manifest.get("suite_path")
        if not suite_path:
            return {}
        from memorybench.scenarios import load_suite

        scenarios = load_suite(suite_path)
        return {
            scenario.id: {
                "title": scenario.title,
                "category": scenario.category,
                "severity": scenario.severity,
            }
            for scenario in scenarios
        }
    except Exception:
        return {}


def _write_site_index(site_root: Path) -> None:
    result_dirs = sorted(
        item for item in site_root.iterdir() if item.is_dir() and (item / "index.html").exists()
    )
    links = "\n".join(
        f'<li><a href="{html.escape(item.name)}/">{html.escape(item.name)}</a></li>'
        for item in result_dirs
    )
    if not links:
        links = "<li>No published results yet.</li>"
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MemoryStackBench Results</title>
  <style>
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f7f7f4;
      color: #202124;
    }}
    main {{
      max-width: 840px;
      margin: 0 auto;
      padding: 48px 20px;
    }}
    h1 {{
      font-size: clamp(2rem, 5vw, 4rem);
      line-height: 1;
      margin: 0 0 24px;
      letter-spacing: 0;
    }}
    ul {{
      background: #fff;
      border: 1px solid #d9ddd2;
      padding: 12px 20px;
    }}
    li {{
      margin: 10px 0;
    }}
    a {{
      color: #0f766e;
      font-weight: 650;
    }}
  </style>
</head>
<body>
  <main>
    <h1>MemoryStackBench Results</h1>
    <ul>{links}</ul>
  </main>
</body>
</html>
"""
    (site_root / "index.html").write_text(document, encoding="utf-8")
