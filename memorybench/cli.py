from __future__ import annotations

import argparse
import json
from pathlib import Path

from memorybench.leaderboard import write_leaderboard
from memorybench.report import copy_site
from memorybench.runner import run_benchmark
from memorybench.scenarios import load_suite


def main() -> None:
    parser = argparse.ArgumentParser(prog="memorybench")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a target against a scenario suite")
    run_parser.add_argument("--target", required=True, help="Path to target manifest YAML")
    run_parser.add_argument("--suite", required=True, help="Path to suite directory or scenario YAML")
    run_parser.add_argument("--out", required=True, help="Output run directory")

    validate_parser = subparsers.add_parser("validate-suite", help="Validate scenario YAML")
    validate_parser.add_argument("--suite", required=True, help="Path to suite directory or scenario YAML")

    report_parser = subparsers.add_parser("report", help="Copy run report files into a static site directory")
    report_parser.add_argument("--run", required=True, help="Existing run directory")
    report_parser.add_argument("--site", required=True, help="Destination static site directory")

    leaderboard_parser = subparsers.add_parser("leaderboard", help="Build a static leaderboard from run scorecards")
    leaderboard_parser.add_argument("--runs", default="runs", help="Directory containing run subdirectories")
    leaderboard_parser.add_argument("--out", default="site/leaderboard", help="Destination leaderboard directory")
    leaderboard_parser.add_argument("--targets", default="targets", help="Directory containing target manifests")

    args = parser.parse_args()

    if args.command == "run":
        scorecard = run_benchmark(args.target, args.suite, args.out)
        print(json.dumps(scorecard["overall"], indent=2))
        print(f"Scorecard: {Path(args.out) / 'scorecard.html'}")
    elif args.command == "validate-suite":
        scenarios = load_suite(args.suite)
        print(f"Loaded {len(scenarios)} scenario(s).")
    elif args.command == "report":
        copy_site(Path(args.run), Path(args.site))
        print(f"Site written to {args.site}")
    elif args.command == "leaderboard":
        write_leaderboard(Path(args.runs), Path(args.out), Path(args.targets))
        print(f"Leaderboard written to {args.out}")


if __name__ == "__main__":
    main()
