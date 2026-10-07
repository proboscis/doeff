"""Hy の module の macro の記録(doeff_hy_bytecode_guard)を、展開が使った macro 単位で照らす形の失敗ケース。

記録は「展開が実際に使った macro と、その macro が実行の時に辿る物」が変わった時だけ古いと判じる。前半は冷えるべき時の
ケース(使った macro・それが呼ぶ補助・それが読む定数・名前で引かない読みの先を替えたら古い)、後半は冷えなくてよい時の
ケース(使っていない macro を替えても・行がずれただけでも古くない)。

記録は import と同じ口(``source_to_code_as_import`` — module を置いた中で compile し、記録を code に足す)で作り、
照らすのは ``record_is_current_here``(.pyc・解析器・型検査の展開の保存が使う 1 つの照らし方)。次の実行(別の process)と
同じにするため、照らす前に検の package の module を sys.modules から外し、macro の module を読み直させる。
"""

import importlib.machinery
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from doeff_hy_bytecode_guard import record_is_current_here, records, source_to_code_as_import

#: macro の module。used = 補助の関数(同じ module)と別の module の定数を読む macro・unused = 誰も使わない macro・
#: reads = 自分の module の大域を文字列の key で読む補助を呼ぶ macro・peeks = 別の module の値を getattr で読む補助を呼ぶ macro・
#: hands = 別の module を引数で渡し、受けた補助が getattr で読む macro。
MACROS = """\
(import {pkg}.consts :as consts)
(setv FACTOR 3)
(setv LIMIT 7)
(defn helper [x] (* x FACTOR))
(defn by-key [] (get (globals) "LIMIT"))
(defn by-getattr [] (getattr consts "HIDDEN"))
(defn by-argument [module] (getattr module "HIDDEN"))
(defmacro used [] (+ (helper 1) consts.OFFSET))
(defmacro reads [] (by-key))
(defmacro peeks [] (by-getattr))
(defmacro hands [] (by-argument consts))
(defmacro unused [] 100)
"""

CONSTS = "(setv OFFSET 1)\n(setv HIDDEN 5)\n"

#: 使う側の module(名 → source)。user = used だけを名指して require する・shadowed = macro の module の全部を require し、
#: 自分の関数 shadow を呼ぶ(後から macro の module に同じ名の macro が足されると、展開が変わる)。
USERS = {
    "user": "(require {pkg}.macros [used])\n(setv value (used))\n",
    "reader": "(require {pkg}.macros [reads])\n(setv value (reads))\n",
    "peeker": "(require {pkg}.macros [peeks])\n(setv value (peeks))\n",
    "hander": "(require {pkg}.macros [hands])\n(setv value (hands))\n",
    "shadowed": "(require {pkg}.macros *)\n(defn shadow [x] x)\n(setv value (shadow (used)))\n",
}


@dataclass(frozen=True)
class Tree:
    """sys.path に置いた一時の dir の中の検の package(名は検ごとに違う — 読み込み済みの module と混ざらない)。"""

    package: str
    directory: Path

    @property
    def macros(self) -> Path:
        return self.directory / "macros.hy"

    @property
    def consts(self) -> Path:
        return self.directory / "consts.hy"

    def forget(self) -> None:
        """検の package の module を sys.modules から外す(次の実行と同じく、次に macro の module を読み直させる)。"""
        for name in [n for n in sys.modules if n == self.package or n.startswith(f"{self.package}.")]:
            del sys.modules[name]

    def record(self, user: str) -> records.MacroRecord:
        """使う側の module を import と同じ口で compile し、code に載った記録を返す。"""
        path = self.directory / f"{user}.hy"
        loader = importlib.machinery.SourceFileLoader(f"{self.package}.{user}", str(path))
        code = source_to_code_as_import(loader, path.read_bytes(), str(path))
        record = records.record_of(code)
        assert record is not None, "compile した code に記録が無い"
        self.forget()
        return record

    def is_current(self, record: records.MacroRecord) -> bool:
        """今の環境(読み直した macro の module)で記録を照らす。"""
        try:
            return record_is_current_here(record)
        finally:
            self.forget()


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Tree]:
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")
    package = f"macro_use_{uuid.uuid4().hex[:8]}"
    directory = tmp_path / package
    directory.mkdir()
    (directory / "__init__.py").write_text("", encoding="utf-8")
    (directory / "macros.hy").write_text(MACROS.format(pkg=package), encoding="utf-8")
    (directory / "consts.hy").write_text(CONSTS, encoding="utf-8")
    for name, text in USERS.items():
        (directory / f"{name}.hy").write_text(text.format(pkg=package), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    made = Tree(package, directory)
    yield made
    made.forget()


def _replace(path: Path, old: str, new: str) -> None:
    """file の中の old を new に替える(大きさも替える — file の sha256 の覚えは path・更新時刻・大きさで引くため)。"""
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    assert len(old) != len(new)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---- 冷えるべき時 ------------------------------------------------------------------------------------------------


def test_a_record_is_current_while_nothing_changed(tree: Tree) -> None:
    record = tree.record("user")
    assert tree.is_current(record), "何も変えていないのに古いと判じた"


def test_a_changed_body_of_the_used_macro_is_stale(tree: Tree) -> None:
    record = tree.record("user")
    _replace(tree.macros, "(+ (helper 1) consts.OFFSET)", "(+ (helper 20) consts.OFFSET)")
    assert not tree.is_current(record), "使った macro の本体を替えたのに古いと判じない"


def test_a_changed_helper_the_used_macro_calls_is_stale(tree: Tree) -> None:
    record = tree.record("user")
    _replace(tree.macros, "(* x FACTOR)", "(* x FACTOR 2)")
    assert not tree.is_current(record), "使った macro が呼ぶ補助の関数の本体を替えたのに古いと判じない"


def test_a_changed_constant_the_helper_reads_is_stale(tree: Tree) -> None:
    record = tree.record("user")
    _replace(tree.macros, "(setv FACTOR 3)", "(setv FACTOR 30)")
    assert not tree.is_current(record), "補助が読む macro の module の定数を替えたのに古いと判じない"


def test_a_changed_constant_of_another_module_the_used_macro_reads_is_stale(tree: Tree) -> None:
    record = tree.record("user")
    _replace(tree.consts, "(setv OFFSET 1)", "(setv OFFSET 10)")
    assert not tree.is_current(record), "使った macro が読む別の module の定数を替えたのに古いと判じない"


def test_a_changed_value_a_helper_reads_by_a_string_key_is_stale(tree: Tree) -> None:
    record = tree.record("reader")
    _replace(tree.macros, "(setv LIMIT 7)", "(setv LIMIT 70)")
    assert not tree.is_current(record), "補助が文字列の key で読む値を替えたのに古いと判じない"


def test_a_changed_value_a_helper_reads_by_getattr_is_stale(tree: Tree) -> None:
    record = tree.record("peeker")
    _replace(tree.consts, "(setv HIDDEN 5)", "(setv HIDDEN 50)")
    assert not tree.is_current(record), "補助が getattr で読む別の module の値を替えたのに古いと判じない"


def test_a_changed_value_read_by_getattr_on_a_module_passed_as_an_argument_is_stale(tree: Tree) -> None:
    # 読む補助と module の参照が別の関数にある形(module を引数で渡す)。
    record = tree.record("hander")
    _replace(tree.consts, "(setv HIDDEN 5)", "(setv HIDDEN 50)")
    assert not tree.is_current(record), "引数で渡した module を getattr で読む値を替えたのに古いと判じない"


def test_a_new_macro_named_like_a_called_function_is_stale(tree: Tree) -> None:
    # 全部を require する使い手が呼ぶ関数と同じ名の macro が足されると、その呼び出しは macro の展開に変わる。
    record = tree.record("shadowed")
    _replace(tree.macros, "(defmacro unused [] 100)", "(defmacro unused [] 100)\n(defmacro shadow [x] 0)")
    assert not tree.is_current(record), "使い手の呼ぶ名の macro が足されたのに古いと判じない"


# ---- 冷えなくてよい時 --------------------------------------------------------------------------------------------


def test_a_changed_unused_macro_keeps_the_record_current(tree: Tree) -> None:
    # 直す前は赤: 記録は macro の module の file の sha256 なので、使っていない macro を 1 行替えるだけで古いと判じた。
    record = tree.record("user")
    _replace(tree.macros, "(defmacro unused [] 100)", "(defmacro unused [] 1000)")
    assert tree.is_current(record), "使っていない macro を替えただけで古いと判じた"


def test_a_line_shift_of_the_macro_module_keeps_the_record_current(tree: Tree) -> None:
    # 直す前は赤: 先頭に空行を足す(行番号だけがずれる)だけで古いと判じた。
    record = tree.record("user")
    tree.macros.write_text("\n" + tree.macros.read_text(encoding="utf-8"), encoding="utf-8")
    assert tree.is_current(record), "行番号がずれただけで古いと判じた"
