"""閉包の歩みは、source の中身で引く保存先(doeff-hy の code_store・種類 .imports)の import の名を使い、中身の変わった file だけ構文を
読み直す(#3694・#3858)。

bytecode の準備の道具(worker/entry/code_prepare.hy)は --entries から import を静的に辿って焼く範囲を決める。import の名は構文の読み
(hy.read-many / ast.parse — 業務の木の Hy 692 file で 79 秒)で求める。#3694 は木の根に表を残し、引き継ぎ元の木(--from)と変わった
path の一覧(--changed)で使い回したので、引き継ぎ元の無い準備(新しい worker・空の /work・テストの 1 台・別の dir の木)は全部を読み
直した(日次の Pod の上の test_same_program_daily.hy の準備で閉包 61 秒)。直した後は、中身の同じ source の entry が保存先に在れば、
版・木・root が違っても読み直さない。

台 = 5 つの package: macro の定義元(mac)・macro を使う側(use)・macro の定義元を import する側(imp)・無関係(other)・閉包の外の
package(extra — imp が import を足した時だけ閉包に入る)。入口 = use.user・imp.importer・other.plain。1 回目の木を空の保存先で焼き、
2 回目は別の dir の木を、引き継ぎの引数なしで同じ保存先を使って焼く。

構文を読み直した file の観測: 道具の process の起動の時に読まれる sitecustomize が hy.read_many と ast.parse を包み、閉包の歩みの
読み(code_plan の imported_names)から呼ばれた時だけ、読んだ相対 path を log へ 1 行書く(道具の外から数える — 道具の答えを信じない)。

速さ(1 件 30 秒の上限)は test_code_prepare_content_store.py と同じ形: module の最初の 1 回だけ道具自身の import の .pyc を
PYTHONPYCACHEPREFIX の dir に書かせ、以後は読むだけ。台の中身は件ごとに違う(註の行 — 件をまたいで entry を共有しない)。
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

#: 準備の道具(本番の準備の処理ステージが起こす script と同じ file)。
TOOL = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster" / "worker" / "entry" / "code_prepare.hy"

_ENTRIES = "use.user,imp.importer,other.plain"

_READ_HOOK = """\
import os
import sys

_LOG = os.environ.get("CLOSURE_READ_LOG")
if _LOG:
    import ast
    import hy

    def _note(original):
        def wrapped(*args, **kwargs):
            caller = sys._getframe(1)
            if caller.f_code.co_name == "imported_names":
                with open(_LOG, "a") as log:
                    log.write(str(caller.f_locals.get("rel")) + "\\n")
            return original(*args, **kwargs)
        return wrapped

    hy.read_many = _note(hy.read_many)
    ast.parse = _note(ast.parse)
"""

#: 1 回目の木で閉包に入る source(入口 3 つ・その package の __init__・macro の定義元とその package)。
_CLOSURE = {
    "use/__init__.py", "use/user.hy", "imp/__init__.py", "imp/importer.hy", "other/__init__.py", "other/plain.hy",
    "mac/__init__.py", "mac/macros.hy",
}


def _files(salt: str, *, importer: str = "(import mac.macros)\n(setv value 2)\n", plain: int = 3) -> dict[str, str]:
    """台の file(salt と相対 path の註の行 — 件ごと・file ごとに中身を変え、同じ中身の file が entry を分け合わないようにする)。"""
    files = {
        "mac/__init__.py": "",
        "mac/macros.hy": "(defmacro answer [] 2)\n(setv TAG \"mac\")\n",
        "use/__init__.py": "",
        "use/user.hy": "(require mac.macros [answer])\n(setv value (answer))\n",
        "imp/__init__.py": "",
        "imp/importer.hy": importer,
        "other/__init__.py": "",
        "other/plain.hy": f"(setv value {plain})\n",
        "extra/__init__.py": "",
        "extra/thing.hy": "(setv thing 1)\n",
    }
    return {rel: (f"; {salt} {rel}\n" if rel.endswith(".hy") else f"# {salt} {rel}\n") + text for rel, text in files.items()}


def _plant(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


@dataclass(frozen=True)
class _Rig:
    """件をまたいで使う物: hook = 読みを数える sitecustomize の dir・prefix = 道具自身の import の .pyc の dir(checkout の外)。"""

    hook: Path
    prefix: Path


@dataclass(frozen=True)
class _Run:
    """1 回の焼きの観測: read = 閉包の歩みが構文を読んだ相対 path の列・stderr = 道具の stderr。"""

    read: list[str]
    stderr: str


def _tool(tree: Path, store: Path, rig: _Rig, read: Path, *, write: bool) -> _Run:
    """本番の準備と同じ引数の形で道具を起こし(引き継ぎの引数は無い)、閉包の歩みが構文を読んだ物と stderr を返す。"""
    completed = subprocess.run(
        [
            "env", *(["-u", "PYTHONDONTWRITEBYTECODE"] if write else ["PYTHONDONTWRITEBYTECODE=1"]), f"PYTHONPYCACHEPREFIX={rig.prefix}",
            f"DOEFF_HY_CODE_STORE={store}", f"PYTHONPATH={rig.hook}", f"CLOSURE_READ_LOG={read}",
            sys.executable, "-m", "hy", str(TOOL), "--revision", "r", "--entries", _ENTRIES,
            "--tree", str(tree), "--roots", ".", "--jobs", "1",
        ],
        capture_output=True, text=True, cwd=tree, timeout=180, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return _Run(read.read_text().split() if read.exists() else [], completed.stderr)


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> _Rig:
    """hook を置き、道具を 1 度だけ .pyc を書く設定で起こして、道具自身の import の .pyc を prefix の dir に作る。"""
    base = tmp_path_factory.mktemp("rig")
    made = _Rig(base / "hook", base / "pyc")
    made.hook.mkdir()
    (made.hook / "sitecustomize.py").write_text(_READ_HOOK)
    _plant(base / "warm", _files("warm"))
    _tool(base / "warm", base / "store", made, base / "warm.read.log", write=True)
    return made


def _bake(tree: Path, store: Path, rig: _Rig) -> list[str]:
    """道具を起こし、閉包の歩みが構文を読んだ相対 path の列を返す。"""
    return _tool(tree, store, rig, tree.parent / f"{tree.name}.read.log", write=False).read


def _baked(tree: Path) -> set[str]:
    """木の中で .pyc が在る source の相対 path(焼く範囲 = 閉包)。"""
    return {
        str(pyc.parent.parent.relative_to(tree) / pyc.name.split(".", 1)[0]) + suffix
        for pyc in tree.rglob("__pycache__/*.pyc")
        for suffix in (".py", ".hy")
        if (pyc.parent.parent / (pyc.name.split(".", 1)[0] + suffix)).exists()
    }


@dataclass(frozen=True)
class _Second:
    """2 回目の焼きの観測: tree = 2 回目の木・read = 閉包の歩みが構文を読んだ相対 path の列。"""

    tree: Path
    read: list[str]


def _second(tmp_path: Path, rig: _Rig, files: dict[str, str]) -> _Second:
    """1 回目(全部を読む)の木の後に、files の 2 回目の木を別の dir で(引き継ぎの引数なしで)焼き、2 回目の木と構文を読んだ物を返す。"""
    store, first, second = tmp_path / "store", tmp_path / "first", tmp_path / "second"
    _plant(first, _files(tmp_path.name))
    assert sorted(_bake(first, store, rig)) == sorted(_CLOSURE)
    _plant(second, files)
    return _Second(second, _bake(second, store, rig))


def test_an_unchanged_tree_in_another_dir_rereads_nothing(tmp_path: Path, rig: _Rig) -> None:
    """失敗ケース: 中身の同じ木は、別の dir(別の版の展開)でも構文を 1 つも読み直さない(直す前は引き継ぎ元が無いので閉包の 8 file を
    全部)。閉包は 1 回目と同じ。"""
    seen = _second(tmp_path, rig, _files(tmp_path.name))
    assert seen.read == []
    assert _baked(seen.tree) == _CLOSURE


def test_a_one_file_change_rereads_only_that_file(tmp_path: Path, rig: _Rig) -> None:
    """失敗ケース: 1 file だけ中身の変わった木の閉包の歩みは、その file だけ構文を読み直す。"""
    seen = _second(tmp_path, rig, _files(tmp_path.name, importer="(import mac.macros)\n(setv value 7)\n"))
    assert seen.read == ["imp/importer.hy"]
    assert _baked(seen.tree) == _CLOSURE


def test_a_new_import_in_the_changed_file_pulls_its_target_into_the_closure(tmp_path: Path, rig: _Rig) -> None:
    """変わった file が import を足すと、その先の module(保存先に無い)も閉包に入り、読まれる。"""
    seen = _second(tmp_path, rig, _files(tmp_path.name, importer="(import mac.macros extra.thing)\n(setv value 2)\n"))
    assert sorted(seen.read) == ["extra/__init__.py", "extra/thing.hy", "imp/importer.hy"]
    assert _baked(seen.tree) == _CLOSURE | {"extra/__init__.py", "extra/thing.hy"}


def test_a_broken_import_names_entry_is_named_and_reread(tmp_path: Path, rig: _Rig) -> None:
    """失敗ケース: 形の違う import の名の entry は黙って使わず、名指して除き、構文を読み直して書き直す(次の準備は使う)。"""
    store, first = tmp_path / "store", tmp_path / "first"
    _plant(first, _files(tmp_path.name))
    _bake(first, store, rig)
    entries = sorted(store.rglob("*.imports"))
    assert len(entries) == len(_CLOSURE), entries
    for entry in entries:
        entry.write_bytes(b"{not json")
    second = tmp_path / "second"
    _plant(second, _files(tmp_path.name))
    run = _tool(second, store, rig, tmp_path / "second.read.log", write=False)
    assert sorted(run.read) == sorted(_CLOSURE)
    assert "bytecode の保存先の entry が壊れている" in run.stderr, run.stderr
    third = tmp_path / "third"
    _plant(third, _files(tmp_path.name))
    assert _bake(third, store, rig) == []
