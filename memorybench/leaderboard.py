from __future__ import annotations

import html
import json
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from memorybench.report import _write_site_index


def collect_scorecards(runs_dir: Path) -> list[dict[str, Any]]:
    scorecards: list[dict[str, Any]] = []
    for path in sorted(runs_dir.glob("*/scorecard.json")):
        with path.open("r", encoding="utf-8") as handle:
            scorecard = json.load(handle)
        if not _is_publishable(scorecard):
            continue
        scorecard["_run_dir"] = path.parent.name
        scorecards.append(scorecard)
    return scorecards


def _is_publishable(scorecard: dict[str, Any]) -> bool:
    target = scorecard.get("target") or {}
    framework = target.get("framework")
    mode = target.get("mode")
    return framework != "toy" and mode not in {"reference_only", "white_box_reference"}


def collect_targets(targets_dir: Path) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for path in sorted(targets_dir.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as handle:
            manifest = yaml.safe_load(handle)
        if not isinstance(manifest, dict):
            continue
        if not _is_registry_target(manifest):
            continue
        manifest["_manifest"] = path.name
        targets.append(manifest)
    return targets


def _is_registry_target(manifest: dict[str, Any]) -> bool:
    framework = manifest.get("framework")
    mode = manifest.get("mode")
    return framework != "toy" and mode not in {"reference_only", "white_box_reference"}


def write_leaderboard(
    runs_dir: Path,
    out_dir: Path,
    targets_dir: Path = Path("targets"),
) -> None:
    scorecards = collect_scorecards(runs_dir)
    targets = collect_targets(targets_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "leaderboard.json").write_text(
        json.dumps(_summary(scorecards), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "targets.json").write_text(
        json.dumps(_target_summary(targets, scorecards), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "index.html").write_text(_html(scorecards, targets, run_prefix="../"), encoding="utf-8")
    if out_dir.parent.name == "site":
        (out_dir.parent / "index.html").write_text(
            _html(scorecards, targets, run_prefix=""),
            encoding="utf-8",
        )


def _summary(scorecards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for scorecard in sorted(
        scorecards,
        key=lambda item: item.get("overall", {}).get("score") or 0,
        reverse=True,
    ):
        rows.append(
            {
                "run": scorecard.get("_run_dir"),
                "target": scorecard.get("target", {}),
                "suite": scorecard.get("suite"),
                "overall": scorecard.get("overall"),
                "categories": scorecard.get("categories"),
                "failure_count": len(scorecard.get("failures", [])),
            }
        )
    return rows


def _target_summary(
    targets: list[dict[str, Any]],
    scorecards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    score_by_target = {
        scorecard.get("target", {}).get("id"): scorecard
        for scorecard in scorecards
    }
    rows = []
    for target in targets:
        scorecard = score_by_target.get(target.get("id"))
        runtime = target.get("runtime") or {}
        required_env = list(runtime.get("required_env", []))
        missing_env = [key for key in required_env if not os.environ.get(str(key))]
        blockers = _target_blockers(target, scorecard, missing_env)
        rows.append(
            {
                "target": target.get("id"),
                "framework": target.get("framework"),
                "status": target.get("status", "unknown"),
                "manifest": target.get("_manifest"),
                "runtime": runtime.get("type"),
                "required_env": required_env,
                "missing_env": missing_env,
                "blockers": blockers,
                "score": None if scorecard is None else scorecard.get("overall", {}).get("score"),
                "run": None if scorecard is None else scorecard.get("_run_dir"),
            }
        )
    return rows


def _target_blockers(
    target: dict[str, Any],
    scorecard: dict[str, Any] | None,
    missing_env: list[str],
) -> list[str]:
    runtime = target.get("runtime") or {}
    blockers = [f"missing env: {key}" for key in missing_env]
    runtime_type = str(runtime.get("type") or "")
    if "docker" in runtime_type and shutil.which("docker") is None:
        blockers.append("Docker runtime missing")

    compose_file = runtime.get("compose_file")
    if compose_file and not Path(str(compose_file)).exists():
        blockers.append(f"compose file missing: {compose_file}")

    status = str(target.get("status") or "")
    if status == "pending_adapter":
        blockers.append("adapter pending")
    elif scorecard is None and status.startswith("implemented"):
        blockers.append("not run locally")

    return blockers


def _html(scorecards: list[dict[str, Any]], targets: list[dict[str, Any]], run_prefix: str) -> str:
    rows = _summary(scorecards)
    table_rows = "\n".join(
        _table_row(row, index + 1, run_prefix) for index, row in enumerate(rows)
    )
    cards = "\n".join(_target_card(row) for row in rows)
    chart = _overall_chart(rows)
    status_rows = "\n".join(
        _status_row(row, run_prefix) for row in _target_summary(targets, scorecards)
    )
    if not rows:
        table_rows = "<tr><td colspan=\"7\">No scorecards found.</td></tr>"
        cards = "<p>No runs found yet.</p>"
    if not status_rows:
        status_rows = "<tr><td colspan=\"7\">No target manifests found.</td></tr>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MemoryStackBench Leaderboard</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --text: #202124;
      --muted: #62665f;
      --border: #d9ddd2;
      --accent: #0f766e;
      --warn: #b45309;
      --fail: #b42318;
    }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.45;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    h1 {{
      font-size: clamp(2.1rem, 5vw, 4.8rem);
      line-height: 1;
      margin: 0 0 10px;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 32px 0 12px;
      font-size: 1.3rem;
    }}
    .meta {{
      color: var(--muted);
      margin-bottom: 26px;
    }}
    nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      margin-bottom: 24px;
      color: var(--muted);
      font-size: .95rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--border);
    }}
    th, td {{
      padding: 11px 12px;
      text-align: left;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
      font-size: .95rem;
    }}
    th {{
      color: var(--muted);
      background: #f0f2ec;
      font-weight: 650;
    }}
    a {{
      color: var(--accent);
      font-weight: 650;
    }}
    .score {{
      font-variant-numeric: tabular-nums;
      font-weight: 750;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 14px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
    }}
    .card h3 {{
      margin: 0 0 10px;
      font-size: 1rem;
    }}
    .bar {{
      height: 10px;
      background: #e5e7df;
      overflow: hidden;
      border-radius: 999px;
      margin: 7px 0 10px;
    }}
    .bar span {{
      display: block;
      height: 100%;
      background: var(--accent);
    }}
    .cat {{
      display: grid;
      grid-template-columns: minmax(130px, 1fr) 54px;
      gap: 8px;
      align-items: center;
      color: var(--muted);
      font-size: .88rem;
    }}
    .chart {{
      background: var(--panel);
      border: 1px solid var(--border);
      padding: 14px;
      overflow-x: auto;
    }}
    svg text {{
      font-family: Inter, ui-sans-serif, system-ui, sans-serif;
      fill: var(--text);
      font-size: 12px;
    }}
    .pill {{
      display: inline-block;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 2px 8px;
      background: #f0f2ec;
      color: var(--muted);
      font-size: .8rem;
      white-space: nowrap;
      margin: 1px 2px 1px 0;
    }}
  </style>
</head>
<body>
  <main>
    <nav>
      <a href="{html.escape(run_prefix)}guide/">Seven Poisons Guide</a>
      <a href="{html.escape(run_prefix)}leaderboard/">Leaderboard JSON</a>
      <a href="https://github.com/aetna000/MemoryStackBench">GitHub</a>
    </nav>
    <h1>MemoryStackBench Leaderboard</h1>
    <p class="meta">Quantitative memory safety scores from local and CI benchmark runs.</p>
    <section>
      <h2>Overall Scores</h2>
      <div class="chart">{chart}</div>
    </section>
    <section>
      <h2>Ranked Runs</h2>
      <table>
        <thead>
          <tr><th>#</th><th>Run</th><th>Target</th><th>Framework</th><th>Suite</th><th>Score</th><th>Failures</th></tr>
        </thead>
        <tbody>{table_rows}</tbody>
      </table>
    </section>
    <section>
      <h2>Category Breakdown</h2>
      <div class="cards">{cards}</div>
    </section>
    <section>
      <h2>Target Coverage</h2>
      <table>
        <thead>
          <tr><th>Target</th><th>Framework</th><th>Status</th><th>Runtime</th><th>Score</th><th>Run</th><th>Blockers / Notes</th></tr>
        </thead>
        <tbody>{status_rows}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


def _table_row(row: dict[str, Any], rank: int, run_prefix: str) -> str:
    target = row.get("target", {})
    run = str(row.get("run"))
    return (
        "<tr>"
        f"<td>{rank}</td>"
        f"<td><a href=\"{html.escape(run_prefix + run)}/\">{html.escape(run)}</a></td>"
        f"<td>{html.escape(str(target.get('id')))}</td>"
        f"<td>{html.escape(str(target.get('framework')))}</td>"
        f"<td>{html.escape(str(row.get('suite')))}</td>"
        f"<td class=\"score\">{_pct(row.get('overall', {}).get('score'))}</td>"
        f"<td>{row.get('failure_count')}</td>"
        "</tr>"
    )


def _target_card(row: dict[str, Any]) -> str:
    target = row.get("target", {})
    categories = row.get("categories") or {}
    category_rows = "\n".join(
        f"<div class=\"cat\"><span>{html.escape(name)}</span><strong>{_pct(value.get('score'))}</strong></div>"
        f"<div class=\"bar\"><span style=\"width: {_width(value.get('score'))}%\"></span></div>"
        for name, value in sorted(categories.items())
    )
    return f"""
    <article class="card">
      <h3>{html.escape(str(target.get('id')))}</h3>
      <div class="score">Overall {_pct(row.get('overall', {}).get('score'))}</div>
      <div class="bar"><span style="width: {_width(row.get('overall', {}).get('score'))}%"></span></div>
      {category_rows}
    </article>
    """


def _status_row(row: dict[str, Any], run_prefix: str) -> str:
    run = row.get("run")
    run_cell = ""
    if run:
        escaped = html.escape(str(run))
        run_cell = f'<a href="{html.escape(run_prefix + str(run))}/">{escaped}</a>'

    blockers = list(row.get("blockers") or [])
    runtime = str(row.get("runtime") or "")
    blockers_cell = " ".join(
        f'<span class="pill">{html.escape(str(item))}</span>' for item in blockers
    )
    return (
        "<tr>"
        f"<td>{html.escape(str(row.get('target')))}</td>"
        f"<td>{html.escape(str(row.get('framework')))}</td>"
        f"<td>{html.escape(str(row.get('status')))}</td>"
        f"<td>{html.escape(runtime)}</td>"
        f"<td class=\"score\">{_pct(row.get('score'))}</td>"
        f"<td>{run_cell}</td>"
        f"<td>{blockers_cell}</td>"
        "</tr>"
    )


def _overall_chart(rows: list[dict[str, Any]]) -> str:
    width = max(520, 120 + len(rows) * 130)
    height = 280
    baseline = 220
    max_bar_height = 160
    bars = []
    for index, row in enumerate(rows):
        x = 70 + index * 130
        score = row.get("overall", {}).get("score") or 0
        bar_height = score * max_bar_height
        y = baseline - bar_height
        label = str(row.get("target", {}).get("framework") or row.get("run"))
        bars.append(
            f'<rect x="{x}" y="{y:.1f}" width="72" height="{bar_height:.1f}" fill="#0f766e"></rect>'
            f'<text x="{x + 36}" y="{y - 8:.1f}" text-anchor="middle">{_pct(score)}</text>'
            f'<text x="{x + 36}" y="246" text-anchor="middle">{html.escape(label[:14])}</text>'
        )
    parts = [
        f'<svg role="img" aria-label="Overall benchmark scores" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<line x1="48" y1="220" x2="{width - 30}" y2="220" stroke="#9ca39a"></line>',
        '<line x1="48" y1="60" x2="48" y2="220" stroke="#9ca39a"></line>',
        '<text x="42" y="66" text-anchor="end">100%</text>',
        '<text x="42" y="224" text-anchor="end">0%</text>',
        *bars,
        "</svg>",
    ]
    return "".join(parts)


def _pct(score: float | None) -> str:
    if score is None:
        return "N/A"
    return f"{round(score * 100)}%"


def _width(score: float | None) -> int:
    if score is None:
        return 0
    return max(0, min(100, round(score * 100)))
