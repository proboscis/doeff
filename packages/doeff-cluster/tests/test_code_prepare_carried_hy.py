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
from dataclasses import dataclass
from pathlib import Path

import pytest

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


# --- 引き継いだ .pyc のうち今の source と macro に合う物は pool へ送らない(#3675)--------------------------------------------
#
# 台 = 4 つの package: macro の定義元(mac)・その macro を使う側(use)・macro の定義元を import だけする側(imp — require しないので
# 展開は macro に依らない)・無関係(other)。1 回目に全部を焼き、2 回目は 1 つの file を変えた木を、1 回目の木から --from と --changed
# (変わった path の一覧 — 本番の準備は git diff で作る)付きで焼く。
# 組み直す module は「変えた file」と「macro の定義元を変えた時はその使い手」だけで、pool へ送る数(焼きの道具の pool が compile-one を
# 呼んだ数)も同じ・報告の行の数は rebuilt と reused に分かれ、その和が焼く計画の数(Hy の source 4 つ — Python の __init__ は .pyc を
# 引き継ぐので計画に入らない)。直す前は Hy の source を全部 pool へ送り(4)、報告は compiled = 計画の数だった。
#
# pool へ送った数の観測: 焼きの子 process の起動の時に読まれる sitecustomize が、root の側の焼き(python_bytecode の compile-one)を、
# 受けた相対 path を log へ 1 行書いてから元を呼ぶ物に包む(道具の外から数える — 道具の答えを信じない)。

_SENT_HOOK = """\
import os

_LOG = os.environ.get("BAKE_SENT_LOG")
if _LOG:
    import doeff_core_effects.python_bytecode as _bytecode

    _original = _bytecode.compile_one

    def compile_one(tree, rel, name):
        with open(_LOG, "a") as log:
            log.write(rel + "\\n")
        return _original(tree, rel, name)

    _bytecode.compile_one = compile_one
"""

_HY_SOURCES = ("mac/macros.hy", "use/user.hy", "imp/importer.hy", "other/plain.hy")


def _write_four(root: Path, *, macro_add: int, importer: int, plain: int) -> None:
    """macro の定義元・使う側・import だけする側・無関係の 4 package を置く(数は file の中身を変えるため)。"""
    files = {
        "mac/__init__.py": "",
        "mac/macros.hy": f"(defmacro answer [] (+ 1 {macro_add}))\n(setv TAG \"mac\")\n",
        "use/__init__.py": "",
        "use/user.hy": "(require mac.macros [answer])\n(setv value (answer))\n",
        "imp/__init__.py": "",
        "imp/importer.hy": f"(import mac.macros)\n(setv value {importer})\n",
        "other/__init__.py": "",
        "other/plain.hy": f"(setv value {plain})\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _bake(tree: Path, store: Path, hook: Path, sent: Path, old: Path | None, changed: tuple[str, ...]) -> str:
    """本番の準備と同じ引数の形で道具を起こし(引き継ぐ時は --from と --changed)、stderr の報告の行を返す。"""
    carry: list[str] = []
    if old is not None:
        listed = tree.parent / f"{tree.name}.changed"
        listed.write_text("".join(f"{rel}\n" for rel in changed))
        carry = ["--from", str(old), "--changed", str(listed)]
    completed = subprocess.run(
        [
            "env", "-u", "PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE=1", f"DOEFF_HY_CODE_STORE={store}",
            f"PYTHONPATH={hook}", f"BAKE_SENT_LOG={sent}",
            sys.executable, "-m", "hy", str(TOOL), "--revision", "r", "--tree", str(tree), "--roots", ".", "--jobs", "1", *carry,
        ],
        capture_output=True, text=True, cwd=tree, timeout=180, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stderr


def _counts(report: str, tree: Path) -> dict[str, int]:
    """木ごとの報告の行(tree=<木> carried=… rebuilt=… reused=… failed=… problem=…)の数(行が無ければ空 — 呼び手の断言が赤になる)。"""
    for line in report.splitlines():
        if f"tree={tree} " in line:
            return {k: int(v) for k, v in (part.split("=", 1) for part in line.split() if "=" in part) if v.isdigit()}
    return {}


def _inode(tree: Path, rel: str) -> int:
    source = tree / rel
    [pyc] = (source.parent / "__pycache__").glob(f"{source.stem}.*.pyc")
    return pyc.stat().st_ino


@dataclass(frozen=True)
class _FirstBake:
    """1 回目に焼いた木(4 つの package の元の中身)と、2 回目の焼きが使う code の置き場・pool を数える hook の dir。"""

    tree: Path
    store: Path
    hook: Path


_BASE = {"macro_add": 1, "importer": 2, "plain": 3}


@pytest.fixture(scope="module")
def first_bake(tmp_path_factory: pytest.TempPathFactory) -> _FirstBake:
    """1 回目の焼き(引き継ぎなし・全部を焼く)— 2 つの筋が同じ 1 回目の木から引き継ぐ(引き継ぎは hardlink、焼き直しは別の file へ書いて
    置き換えるので、2 回目の焼きは 1 回目の木を変えない)。"""
    base = tmp_path_factory.mktemp("first-bake")
    first, store, hook = base / "first", base / "store", base / "hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(_SENT_HOOK)
    _write_four(first, **_BASE)
    _bake(first, store, hook, base / "sent-first.log", None, ())
    return _FirstBake(first, store, hook)


@dataclass(frozen=True)
class _Rebake:
    """2 回目の焼きの観測: rebuilt = 組み直した module(.pyc の inode が 1 回目と違う Hy の source)・pooled = pool へ送った相対 path・
    counts = 2 回目の木の報告の行の数。"""

    rebuilt: set[str]
    pooled: list[str]
    counts: dict[str, int]


def _rebake(first: _FirstBake, tmp_path: Path, changed_rel: str, **second: int) -> _Rebake:
    """changed_rel だけ変えた 2 回目の木を 1 回目の木から引き継ぎつきで焼き、組み直した module・pool へ送った物・報告の数を観測する。"""
    after = tmp_path / "second"
    _write_four(after, **{**_BASE, **second})
    sent = tmp_path / "sent.log"
    report = _bake(after, first.store, first.hook, sent, first.tree, (changed_rel,))
    rebuilt = {rel for rel in _HY_SOURCES if _inode(after, rel) != _inode(first.tree, rel)}
    pooled = sent.read_text().split() if sent.exists() else []
    return _Rebake(rebuilt, pooled, _counts(report, after))


def test_changing_one_plain_file_rebuilds_and_pools_only_that_file(first_bake: _FirstBake, tmp_path: Path) -> None:
    """失敗ケース: macro の定義元以外の 1 file(import だけする側)を変えた木は、その file だけを組み直し、pool へもそれだけを送る。"""
    seen = _rebake(first_bake, tmp_path, "imp/importer.hy", importer=7)
    rebuilt, pooled, counts = seen.rebuilt, seen.pooled, seen.counts
    assert rebuilt == {"imp/importer.hy"}
    assert sorted(pooled) == ["imp/importer.hy"]
    assert counts.get("rebuilt") == 1 and counts.get("reused") == 3 and counts.get("failed") == 0, counts
    assert counts["rebuilt"] + counts["reused"] + counts["failed"] == len(_HY_SOURCES), counts


def test_changing_the_macro_definition_rebuilds_it_and_its_users_only(first_bake: _FirstBake, tmp_path: Path) -> None:
    """失敗ケース: macro の定義元を変えた木は、定義元とその macro を使う側だけを組み直し(import だけする側と無関係は残す)、pool へも
    その 2 つだけを送る。"""
    seen = _rebake(first_bake, tmp_path, "mac/macros.hy", macro_add=5)
    rebuilt, pooled, counts = seen.rebuilt, seen.pooled, seen.counts
    assert rebuilt == {"mac/macros.hy", "use/user.hy"}
    assert sorted(pooled) == ["mac/macros.hy", "use/user.hy"]
    assert counts.get("rebuilt") == 2 and counts.get("reused") == 2 and counts.get("failed") == 0, counts
    assert counts["rebuilt"] + counts["reused"] + counts["failed"] == len(_HY_SOURCES), counts
