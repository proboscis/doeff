"""Failure cases of the commit hook scripts/check_hy_stubs.py.

A commit that changes only .hy and does not regenerate the neighboring .pyi is stopped, and passes once regenerated. Reproduces the shape of afaf956cd (added an import to kube.hy and forgot to fix kube.pyi)
with a small package of .hy.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HY = "packages/demo/src/demo/shape.hy"

BEFORE = "(defn double [x] (* x 2))\n"
AFTER = BEFORE + "(defn triple [x] (* x 3))\n"


@pytest.fixture(autouse=True)
def _project_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The uv run called by the hook script uses the environment of the real tree (the one with doeff_hy installed), not the repo under test."""
    monkeypatch.setenv("UV_PROJECT", str(ROOT))


def _hook(repository: Path, path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repository / "scripts" / "check_hy_stubs.py"), path],
        cwd=repository, capture_output=True, text=True, check=False,
    )


def _repository(directory: Path) -> Path:
    (directory / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "check_hy_stubs.py", directory / "scripts" / "check_hy_stubs.py")
    source = directory / HY
    source.parent.mkdir(parents=True)
    source.write_text(BEFORE, encoding="utf-8")
    _write_stub(directory)
    return directory


def _write_stub(repository: Path) -> None:
    source = repository / HY
    subprocess.run(
        ["uv", "run", "--project", str(ROOT), "python", "-m", "doeff_hy._static_stub", "--write", "--root", str(source.parents[1]), str(source)],
        check=True, capture_output=True,
    )


def test_hook_passes_when_the_stub_matches_the_source(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    assert _hook(repository, HY).returncode == 0


def test_hook_stops_a_source_change_that_leaves_the_stub_stale(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / HY).write_text(AFTER, encoding="utf-8")
    result = _hook(repository, HY)
    assert result.returncode == 1
    assert "shape.hy" in result.stderr


def test_hook_passes_after_the_stub_is_rebuilt(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / HY).write_text(AFTER, encoding="utf-8")
    _write_stub(repository)
    assert _hook(repository, HY).returncode == 0
