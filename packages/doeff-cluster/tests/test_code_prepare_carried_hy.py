"""前の木から引き継いだ Hy の .pyc を、bytecode の準備の道具(doeff_cluster/code_prepare.hy)が新しい木の macro と照らす(#2598)。

準備は、lock と Python が同じ前の root の木から .pyc を hardlink で引き継ぐ(source の中身が同じ file だけを import が使う
checked-hash の .pyc)。Hy の module の展開は macro にも依るので、macro の変わった版で引き継いだ .pyc を準備がそのまま残すと、
子の job の初回の import が doeff-hy の古さの検めで Hy の module を全部 compile し直す(起動が遅い)。準備の中で照らし、macro の
合わない物はそこで焼き直し、合う物は引き継いだまま残す。

道具は本番の準備の処理ステージと同じく、別の process で script の path から起こす(cwd = 木・import の根 = 木の根)。macro は展開の
たびに木の expansions.log へ 1 行書く(展開をやり直したかを数える)。子は bytecode を自分で書かない(PYTHONDONTWRITEBYTECODE=1 —
checkout の中の package の .pyc を書かない。道具は .pyc を自分で書く)。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

#: 準備の道具(本番の準備の処理ステージが起こす script と同じ file)。
TOOL = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster" / "worker" / "entry" / "code_prepare.hy"

#: 読む側 — 子の job と同じく、準備した木を import する。
_IMPORT = """\
import sys
sys.path.insert(0, sys.argv[1])
import hy
import pkg.user
print(pkg.user.value)
"""


def _write_package(root: Path, add: int) -> None:
    """macro の提供元(macros)・macro が展開の時に呼ぶ補助(helpers)・使う側(user)。macro は展開のたびに cwd の log へ 1 行書く。"""
    package = root / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "helpers.hy").write_text("(defn base [] 10)\n")
    (package / "macros.hy").write_text(
        "(import pkg.helpers [base])\n"
        '(defmacro answer [] (with [f (open "expansions.log" "a")] (.write f "x\\n"))'
        f" (+ (base) {add}))\n"
    )
    (package / "user.hy").write_text("(require pkg.macros [answer])\n(setv value (answer))\n")


def _child(command: list[str], tree: Path, store: Path) -> str:
    # 子はこの process の環境を継ぐ — PYTHONPYCACHEPREFIX だけ外し(`env -u`)、2 つの名を足す。
    completed = subprocess.run(
        [
            "env",
            "-u",
            "PYTHONPYCACHEPREFIX",
            "PYTHONDONTWRITEBYTECODE=1",
            f"DOEFF_HY_CODE_STORE={store}",
            *command,
        ],
        capture_output=True,
        text=True,
        cwd=tree,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _prepare(tree: Path, store: Path, old: Path | None = None) -> None:
    """本番の準備の処理ステージと同じ引数の形で道具を起こす(前の木 old から引き継ぐ・変わった file の一覧は渡さない)。"""
    carry = [] if old is None else ["--from", str(old)]
    _child(
        [sys.executable, "-m", "hy", str(TOOL), "--revision", "r", "--tree", str(tree), "--roots", ".",
         "--jobs", "1", *carry],
        tree,
        store,
    )


def _import(tree: Path, store: Path) -> int:
    return int(_child([sys.executable, "-c", _IMPORT, str(tree)], tree, store).split()[-1])


def _expansions(tree: Path) -> int:
    log = tree / "expansions.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


def _user_pyc(tree: Path) -> Path:
    [pyc] = (tree / "pkg" / "__pycache__").glob("user.*.pyc")
    return pyc


def test_a_carried_hy_pyc_whose_macro_changed_is_recompiled_while_preparing(tmp_path: Path) -> None:
    """macro だけ変えた木は、引き継いだ使う側の .pyc を準備の中で焼き直し(展開 1 回)、import は展開をやり直さない。準備が
    引き継いだ .pyc をそのまま残すと、準備の展開は 0 回で、import の時に展開する(起動が遅い)。"""
    first, second, store = tmp_path / "first", tmp_path / "second", tmp_path / "store"
    _write_package(first, 1)
    _write_package(second, 5)
    _prepare(first, store)
    assert _expansions(first) == 1
    _prepare(second, store, old=first)
    assert _expansions(second) == 1
    assert _import(second, store) == 15
    assert _expansions(second) == 1


def test_a_carried_hy_pyc_whose_macro_is_unchanged_stays_carried(tmp_path: Path) -> None:
    """macro も同じ木は、引き継いだ .pyc を焼き直さずに残し(同じ inode のまま・展開 0 回)、import も展開しない。"""
    first, second, store = tmp_path / "first", tmp_path / "second", tmp_path / "store"
    _write_package(first, 1)
    _write_package(second, 1)
    _prepare(first, store)
    _prepare(second, store, old=first)
    assert _expansions(second) == 0
    assert _user_pyc(second).stat().st_ino == _user_pyc(first).stat().st_ino
    assert _import(second, store) == 11
    assert _expansions(second) == 0
