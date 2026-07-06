from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from memorybench.schemas import Scenario


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return data


def load_scenario(path: Path) -> Scenario:
    try:
        return Scenario.from_dict(load_yaml(path))
    except Exception as exc:
        raise ValueError(f"Failed loading scenario {path}: {exc}") from exc


def load_suite(path: str | Path) -> list[Scenario]:
    suite_path = Path(path)
    if suite_path.is_file():
        return [load_scenario(suite_path)]
    if not suite_path.exists():
        raise FileNotFoundError(f"Suite path does not exist: {suite_path}")
    scenario_paths = sorted(
        item for item in suite_path.iterdir() if item.suffix in {".yaml", ".yml"}
    )
    if not scenario_paths:
        raise ValueError(f"No scenario YAML files found in {suite_path}")
    return [load_scenario(item) for item in scenario_paths]

