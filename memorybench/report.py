from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


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


def write_html_scorecard(scorecard: dict[str, Any], path: Path) -> None:
    target = scorecard["target"]
    overall = scorecard["overall"]
    score = _format_score(overall.get("score"))
    category_rows = "\n".join(
        f"<tr><td>{html.escape(name)}</td><td>{_format_score(value.get('score'))}</td>"
        f"<td>{value['passed']} / {value['total']}</td></tr>"
        for name, value in scorecard.get("categories", {}).items()
    )
    failure_items = "\n".join(_failure_item(item) for item in scorecard.get("failures", []))
    if not failure_items:
        failure_items = "<p>No failed checks.</p>"

    data = html.escape(json.dumps(scorecard, indent=2))
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
      margin-top: 32px;
      font-size: 1.35rem;
    }}
    .meta {{
      color: var(--muted);
      display: flex;
      flex-wrap: wrap;
      gap: 12px 20px;
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
    .failure {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-left: 4px solid var(--fail);
      padding: 14px 16px;
      margin: 12px 0;
    }}
    .failure strong {{
      color: var(--fail);
    }}
    code, pre {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }}
    pre {{
      overflow: auto;
      background: #191b1f;
      color: #f3f4f6;
      padding: 16px;
      border-radius: 6px;
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
      <div class="score"><strong>{score}</strong><span>{overall['passed']} / {overall['total']} checks passed</span></div>
    </header>

    <section>
      <h2>Category Scores</h2>
      <table>
        <thead><tr><th>Category</th><th>Score</th><th>Checks</th></tr></thead>
        <tbody>{category_rows}</tbody>
      </table>
    </section>

    <section>
      <h2>Failed Checks</h2>
      {failure_items}
    </section>

    <section>
      <h2>Raw Scorecard</h2>
      <pre>{data}</pre>
    </section>
  </main>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def copy_site(run_dir: Path, site_dir: Path) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    for name in ("scorecard.html", "scorecard.json", "failure_report.md"):
        source = run_dir / name
        if source.exists():
            (site_dir / ("index.html" if name == "scorecard.html" else name)).write_bytes(
                source.read_bytes()
            )
    if site_dir.parent.name == "site":
        _write_site_index(site_dir.parent)


def _format_score(score: float | None) -> str:
    if score is None:
        return "N/A"
    return f"{round(score * 100)}%"


def _failure_item(item: dict[str, Any]) -> str:
    expected = html.escape(str(item.get("expected")))
    actual = html.escape(str(item.get("actual")))
    return f"""
    <article class="failure">
      <strong>{html.escape(str(item.get('scenario_id')))}</strong>
      <div>{html.escape(str(item.get('kind')))} · {html.escape(str(item.get('category')))} · {html.escape(str(item.get('severity')))}</div>
      <p>Expected: <code>{expected}</code></p>
      <p>Actual: <code>{actual}</code></p>
    </article>
    """


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
