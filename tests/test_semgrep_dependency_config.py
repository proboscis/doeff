"""Semgrep dev dependency が既定の free-threaded Python で同期できることを守る。"""

from pathlib import Path
from typing import cast

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_semgrep_excludes_incompatible_yaml_c_accelerator() -> None:
    config: dict[str, object] = tomllib.loads((ROOT / "pyproject.toml").read_text())
    tool_config: dict[str, object] = cast(dict[str, object], config["tool"])
    uv_config: dict[str, object] = cast(dict[str, object], tool_config["uv"])
    exclusions: list[dict[str, object]] = cast(
        list[dict[str, object]], uv_config["exclude-dependencies"]
    )
    semgrep_yaml_accelerator: dict[str, object] = {
        "package": {"name": "semgrep"},
        "dependencies": ["ruamel-yaml-clib"],
    }

    assert semgrep_yaml_accelerator in exclusions
