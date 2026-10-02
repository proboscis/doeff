"""前もって焼いた Hy の .pyc を、import の時に doeff-hy の古さの検めが compile し直さない(agora-redesign #2598)。

doeff-cluster の実行環境の準備は、木の source を ``compile-python-sources``(この package の python_bytecode.hy)で前もって焼く。
import の外で compile すると、Hy は仮の module を作って compile の直後に消すので、doeff-hy の compile の口の包みが macro の記録を
付けられなかった。記録の無い .pyc は import の時に古いかもしれない物として compile し直されるので、焼いた分が無駄になり、
預かり所の job の起動が macro の展開を全部やり直していた(CPU 約 46 秒のうち約 43 秒)。

焼く process と読む process は、本番と同じく別の Python(この venv の interpreter — 包みは .pth で起動時に入る)。
macro は展開のたびに木の expansions.log へ 1 行書く(展開をやり直したかを数える)。
"""

from __future__ import annotations

import marshal
import subprocess
import sys
from pathlib import Path

from doeff_hy_bytecode_guard import records

#: 子の process へ渡さない環境変数(.pyc の置き場を木の外へ移すと、読む側が焼いた .pyc を見ない)。子は bytecode を書かない
#: (PYTHONDONTWRITEBYTECODE=1 — checkout の中の package の .pyc を書かない。焼く側は .pyc を自分で書き、読む側が compile し直したかは
#: 展開の回数で数える)。
_ENV_NOT_PASSED = ("PYTHONPYCACHEPREFIX",)

#: 焼く側 — 実行環境の準備の道具(doeff-cluster の code_prepare.hy)と同じく、import の路に木の根を足してから焼く。
_PREPARE = """\
import sys
import hy
from doeff_core_effects.python_bytecode import compile_python_sources, prepare_compile_path

tree = sys.argv[1]
prepare_compile_path(tree, (".",))
items = (("pkg/__init__.py", "pkg"), ("pkg/helpers.hy", "pkg.helpers"),
         ("pkg/macros.hy", "pkg.macros"), ("pkg/user.hy", "pkg.user"))
failures = compile_python_sources(tree, items, 1, (".",))
assert not failures, failures
"""

#: 読む側 — 子の job と同じく、焼いた木を import する。
_IMPORT = """\
import sys
sys.path.insert(0, sys.argv[1])
import hy
import pkg.user
print(pkg.user.value)
"""


def _write_counting_package(root: Path) -> Path:
    """macro の提供元(macros)・macro が展開の時に呼ぶ補助(helpers)・使う側(user)。macro は展開のたびに log へ 1 行書く。"""
    package = root / "pkg"
    package.mkdir()
    log = root / "expansions.log"
    (package / "__init__.py").write_text("")
    (package / "helpers.hy").write_text("(defn base [] 10)\n")
    (package / "macros.hy").write_text(
        "(import pkg.helpers [base])\n"
        f'(defmacro answer [] (with [f (open "{log}" "a")] (.write f "x\\n")) (+ (base) 1))\n'
    )
    (package / "user.hy").write_text("(require pkg.macros [answer])\n(setv value (answer))\n")
    return log


def _python(script: str, root: Path) -> str:
    # 子はこの process の環境を継ぐ — _ENV_NOT_PASSED の名だけ外し(`env -u`)、2 つの名を足す。
    unset = [flag for name in _ENV_NOT_PASSED for flag in ("-u", name)]
    completed = subprocess.run(
        [
            "env",
            *unset,
            "PYTHONDONTWRITEBYTECODE=1",
            f"DOEFF_HY_CODE_STORE={root / 'code-store'}",
            sys.executable,
            "-c",
            script,
            str(root),
        ],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _expansions(log: Path) -> int:
    return len(log.read_text().splitlines()) if log.exists() else 0


def test_a_hy_pyc_compiled_ahead_is_used_at_import_without_expanding_again(tmp_path: Path) -> None:
    """焼いた .pyc は展開が依った macro の記録を持ち、import は記録を今の macro と照らして .pyc をそのまま使う(展開 1 回 =
    焼いた時だけ)。記録の無い .pyc だと、import が compile し直して展開が 2 回になる。"""
    log = _write_counting_package(tmp_path)
    _python(_PREPARE, tmp_path)
    assert _expansions(log) == 1
    [pyc] = (tmp_path / "pkg" / "__pycache__").glob("user.*.pyc")
    record = records.record_of(marshal.loads(pyc.read_bytes()[records.PYC_HEADER_BYTES :]))
    assert record is not None
    assert [dependency.module for dependency in record.dependencies] == ["pkg.helpers", "pkg.macros"]
    assert _python(_IMPORT, tmp_path).split() == ["11"]
    assert _expansions(log) == 1
