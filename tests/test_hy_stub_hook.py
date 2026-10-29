"""commit の hook scripts/check_hy_stubs.py の失敗ケース(card ki-5755818ef0d8)。

.hy だけを変えて隣の .pyi を作り直さない commit は止まり、作り直せば通る。afaf956cd(kube.hy に import を足して kube.pyi を直し忘れた)の
形を、小さな package の .hy で再現する。
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
    """hook の script が呼ぶ uv run は、検の repo でなく本物の木の環境(doeff_hy が入った物)を使う。"""
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
