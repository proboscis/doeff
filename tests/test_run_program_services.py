from __future__ import annotations

import importlib
import logging
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_run_module_does_not_import_cli_entrypoint() -> None:
    src = (ROOT / "doeff" / "run.py").read_text(encoding="utf-8")
    assert "from doeff.__main__" not in src
    assert "import doeff.__main__" not in src
    assert "def _apply_envs" not in src
    assert "def _load_default_env" not in src
