"""閉包の歩みは、引き継ぎ元の木が残した import の名の表を使い回し、変わった file だけ構文を読み直す(#3694)。

bytecode の準備の道具(worker/entry/code_prepare.hy)は --entries から import を静的に辿って焼く範囲を決める。直す前は、閉包に入る
source を版ごとに全部 hy.read-many / ast.parse で読み直していた(業務の木の Hy 692 file で 79 秒・cluster の実測 80 秒)。直した後は、
木の根に module ごとの import の名の表(source の相対 path → その source の sha256 と import の名の列)を残し、次の版の準備は
引き継ぎ元(--from)の表のうち、--changed に無く sha256 が今の source と合う行を使い回す。

台 = 5 つの package: macro の定義元(mac)・macro を使う側(use)・macro の定義元を import する側(imp)・無関係(other)・閉包の外の
package(extra — imp が import を足した時だけ閉包に入る)。入口 = use.user・imp.importer・other.plain。

構文を読み直した file の観測: 道具の process の起動の時に読まれる sitecustomize が hy.read_many と ast.parse を包み、閉包の歩みの
読み(code_plan の imported_names)から呼ばれた時だけ、読んだ相対 path を log へ 1 行書く(道具の外から数える — 道具の答えを信じない。
焼きの pool の子 process の読みは imported_names から呼ばれないので数えない)。
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: 準備の道具(本番の準備の処理ステージが起こす script と同じ file)。
TOOL = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster" / "worker" / "entry" / "code_prepare.hy"

#: 木の根に残る import の名の表(隠し file — 走査の source に混ざらない)。
TABLE = ".doeff-import-names.json"

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


def _files(*, importer: str = "(import mac.macros)\n(setv value 2)\n", plain: int = 3) -> dict[str, str]:
    return {
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


def _plant(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _bake(tree: Path, base: Path, old: Path | None, changed: tuple[str, ...]) -> list[str]:
    """本番の準備と同じ引数の形で道具を起こし(引き継ぐ時は --from と --changed)、閉包の歩みが構文を読んだ相対 path の列を返す。"""
    hook, store, read = base / "hook", base / "store", base / f"{tree.name}.read.log"
    if not hook.exists():
        hook.mkdir()
        (hook / "sitecustomize.py").write_text(_READ_HOOK)
    carry: list[str] = []
    if old is not None:
        listed = base / f"{tree.name}.changed"
        listed.write_text("".join(f"{rel}\n" for rel in changed))
        carry = ["--from", str(old), "--changed", str(listed)]
    completed = subprocess.run(
        [
            "env", "-u", "PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE=1", f"DOEFF_HY_CODE_STORE={store}",
            f"PYTHONPATH={hook}", f"CLOSURE_READ_LOG={read}",
            sys.executable, "-m", "hy", str(TOOL), "--revision", "r", "--entries", _ENTRIES,
            "--tree", str(tree), "--roots", ".", "--jobs", "2", *carry,
        ],
        capture_output=True, text=True, cwd=tree, timeout=180, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return read.read_text().split() if read.exists() else []


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
    """2 回目の焼きの観測: tree = 2 回目の木・read = 閉包の歩みが構文を読んだ相対 path。"""

    tree: Path
    read: list[str]


def _second(tmp_path: Path, files: dict[str, str], changed: tuple[str, ...], *, drop_table: bool = False) -> _Second:
    """1 回目(全部を読む)の木から、files の 2 回目の木を --changed 付きで焼き、2 回目の木と構文を読んだ物を返す。"""
    first, second = tmp_path / "first", tmp_path / "second"
    _plant(first, _files())
    assert sorted(_bake(first, tmp_path, None, ())) == sorted(_CLOSURE)
    if drop_table:
        (first / TABLE).unlink()
    _plant(second, files)
    return _Second(second, _bake(second, tmp_path, first, changed))


def _full(tmp_path: Path, files: dict[str, str]) -> set[str]:
    """同じ中身の木を引き継ぎなしで焼いた時の閉包(全部を読み直した時の答え)。"""
    fresh = tmp_path / "fresh"
    _plant(fresh, files)
    _bake(fresh, tmp_path, None, ())
    return _baked(fresh)


def test_a_one_file_change_rereads_only_that_file(tmp_path: Path) -> None:
    """失敗ケース(a)(b): 1 file だけ変わった木の閉包の歩みは、変わった file だけ構文を読み直す(直す前は閉包の 8 file を全部)。
    閉包(焼いた module の集合)は全部を読み直した時と同じ。"""
    files = _files(importer="(import mac.macros)\n(setv value 7)\n")
    seen = _second(tmp_path, files, ("imp/importer.hy",))
    second, read = seen.tree, seen.read
    assert read == ["imp/importer.hy"]
    assert _baked(second) == _full(tmp_path, files) == _CLOSURE


def test_c_a_new_import_in_the_changed_file_pulls_its_target_into_the_closure(tmp_path: Path) -> None:
    """失敗ケース(c): 変わった file が import を足すと、その先の module(表に無い)も閉包に入り、読まれる。"""
    files = _files(importer="(import mac.macros extra.thing)\n(setv value 2)\n")
    seen = _second(tmp_path, files, ("imp/importer.hy",))
    second, read = seen.tree, seen.read
    assert sorted(read) == ["extra/__init__.py", "extra/thing.hy", "imp/importer.hy"]
    assert _baked(second) == _full(tmp_path, files) == _CLOSURE | {"extra/__init__.py", "extra/thing.hy"}


def test_d_an_old_tree_without_a_table_reads_everything(tmp_path: Path) -> None:
    """失敗ケース(d): 表の無い引き継ぎ元(表を残す前の形の木)からは、今どおり閉包の全部を読む。"""
    seen = _second(tmp_path, _files(), (), drop_table=True)
    second, read = seen.tree, seen.read
    assert sorted(read) == sorted(_CLOSURE)
    assert _baked(second) == _CLOSURE


def test_e_a_changed_file_missing_from_the_changed_list_is_reread_by_its_hash(tmp_path: Path) -> None:
    """失敗ケース(e): --changed に載らない変わった file(一覧の漏れ・手で直した木)も、sha256 が表と違うので読み直す。"""
    files = _files(importer="(import mac.macros extra.thing)\n(setv value 2)\n")
    seen = _second(tmp_path, files, ())
    second, read = seen.tree, seen.read
    assert sorted(read) == ["extra/__init__.py", "extra/thing.hy", "imp/importer.hy"]
    assert _baked(second) == _full(tmp_path, files)


def test_the_table_drops_removed_files_and_keeps_only_the_closure(tmp_path: Path) -> None:
    """消した file(--changed に載る)の行は新しい木の表から外れ、表の行は今の閉包の source だけ(sha256 と import の名の列)。"""
    files = _files(importer="(setv value 2)\n")
    del files["mac/macros.hy"]
    files["use/user.hy"] = "(setv value 1)\n"
    seen = _second(tmp_path, files, ("imp/importer.hy", "mac/macros.hy", "use/user.hy"))
    second, read = seen.tree, seen.read
    assert sorted(read) == ["imp/importer.hy", "use/user.hy"]
    table = json.loads((second / TABLE).read_text())
    assert set(table["modules"]) == _CLOSURE - {"mac/__init__.py", "mac/macros.hy"}
    assert table["modules"]["imp/importer.hy"]["imports"] == []
