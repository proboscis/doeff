"""Typing regressions for vendored utility module."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_vendor_module_passes_pyright() -> None:
    pyright = shutil.which("pyright")
    assert pyright is not None, "pyright executable is required for typing regression checks"

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [pyright, "doeff/_vendor.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    output = "\n".join(part for part in (result.stdout, result.stderr) if part.strip())
    assert result.returncode == 0, output
