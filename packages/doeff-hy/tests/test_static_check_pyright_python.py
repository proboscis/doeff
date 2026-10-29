"""doeff-hy-check(doeff_hy._static_check の run_pyright)が pyright に渡す python の失敗ケース(日次 2026-10-11 16:00 の doeff・card ki-04d762c59dac)。

pyright は --pythonpath の python を検索 path を問うために `-I`(= -E — PYTHON* の環境変数を読まない)で起こすので、呼び手の
PYTHONDONTWRITEBYTECODE=1 が効かず、site の起動で .pth が読む module の .pyc 6 個(.venv の _virtualenv・__editable___doeff_vm_finder と
packages/doeff-hy/src/doeff_hy_bytecode_guard の 4 つ)を checkout へ書いた。run_pyright を通る検(doeff-cluster の *_static_types・
doeff_hy._static_stub の unknown_in_users)が走った回に、根の conftest の後片付けの検が ERROR になった。

- run_pyright が pyright に起こさせる python は、呼び手が渡した python を `-B`(-I の下でも効く)で起こす。
"""

import contextlib
import io
import shlex
import shutil
import sys
from pathlib import Path

import pytest

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")


def _recording_python(tmp_path: Path, log: Path) -> Path:
    """起こされた時の引数を log に 1 行ずつ書いてから、この process の python を同じ引数で起こす python(pyright が起こす python の見張り)。"""
    python = tmp_path / "recording-python"
    python.write_text(f'#!/bin/sh\necho "$@" >> {shlex.quote(str(log))}\nexec {shlex.quote(sys.executable)} -B "$@"\n')
    python.chmod(0o755)
    return python


@needs_pyright
def test_the_python_pyright_starts_for_doeff_hy_check_writes_no_bytecode(tmp_path: Path) -> None:
    from doeff_hy._static_check import main

    root = tmp_path / "repo"
    root.mkdir()
    (root / "probe.hy").write_text("(setv answer 1)\n", encoding="utf-8")
    log = tmp_path / "started.log"
    python = _recording_python(tmp_path, log)
    with contextlib.redirect_stdout(io.StringIO()):
        main(["--root", str(root), "--json", "--no-cache", "--python", str(python), str(root / "probe.hy")])
    started = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    assert started, "pyright が --pythonpath の python を 1 度も起こさなかった(見張りが効いていない)"
    assert all(line.split()[:1] == ["-B"] for line in started), started
