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
#: hands = 別の module を引数で渡し、受けた補助が getattr で読む macro・documented = 別の module の定数を読み、展開の結果に
#: 関数の __doc__ を書く macro(code の文字列の定数に "__doc__" が出る — defhandler の形)。
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
(defmacro check [x] 0)
(import {pkg}.tables :as tables)
(import {pkg}.fakeext [Thing])
(import {pkg}.contexts [MODE])
(setv HY-TABLE {{"a" 1}})
(setv (get HY-TABLE "b") 2)
(setv HY-OTHER {{"z" 0}})
(setv HY-ALIAS HY-TABLE)
(defn _hy-register [] (setv (get HY-ALIAS "c") 3))
(_hy-register)
(defn hy-look [k] (get HY-TABLE k))
(defmacro looks [] (tables.lookup "a"))
(defmacro hy-looks [] (hy-look "a"))
(defmacro thing [] (do Thing 1))
(defmacro mode [] (do (.get MODE) 1))
(defmacro documented [] `(do (defn f [] ~consts.OFFSET) (setv (. f __doc__) "f") (f)))
"""

CONSTS = '"定数の module。"\n(setv OFFSET 1)\n(setv HIDDEN 5)\n'

#: macro が展開の時に読む大域の表(Python の module)。TABLE は top-level の文と、import の時に呼ぶ登録の関数で行が足される。
#: OTHER と unrelated は閉包から辿られない。
TABLES = """\
TABLE = {"a": 1}
TABLE["b"] = 2
OTHER = {"z": 0}


def _register():
    TABLE["c"] = 3


_register()
TABLE["n"] = {"x": 1}
TABLE["l"] = []
ALIAS = TABLE


def _register_nested():
    TABLE["n"]["x"] = 5
    TABLE["l"].append(7)
    TABLE.get("n")["y"] = 6
    for row in TABLE.values():
        if isinstance(row, dict):
            row["z"] = 8


def _register_alias():
    ALIAS["d"] = 4


_register_nested()
_register_alias()


def lookup(k):
    return TABLE[k]


def unrelated():
    return OTHER
"""

#: 拡張の module の写し(Python の source から作るが、__file__ を .so に向ける — 記録の側からは source の無い拡張の module に見える)。
FAKEEXT = """\
import pathlib


class Thing:
    KIND = "a"

    def run(self):
        return 1


__file__ = str(pathlib.Path(__file__).with_name("fakeext.cpython-test.so"))
"""

CONTEXTS = """\
from contextvars import ContextVar

MODE = ContextVar("mode", default="plain")
LATER = 1
"""

#: 使う側の module(名 → source)。user = used だけを名指して require する・shadowed = macro の module の全部を require し、
#: 自分の関数 shadow を呼ぶ(後から macro の module に同じ名の macro が足されると、展開が変わる)。
USERS = {
    "user": "(require {pkg}.macros [used])\n(setv value (used))\n",
    "reader": "(require {pkg}.macros [reads])\n(setv value (reads))\n",
    "peeker": "(require {pkg}.macros [peeks])\n(setv value (peeks))\n",
    "hander": "(require {pkg}.macros [hands])\n(setv value (hands))\n",
    "chooser": "(require {pkg}.macros [used])\n(defn run [check] (check (used)))\n(setv value (run (fn [x] x)))\n",
    "shadowed": "(require {pkg}.macros *)\n(defn shadow [x] x)\n(setv value (shadow (used)))\n",
    "looker": "(require {pkg}.macros [looks])\n(setv value (looks))\n",
    "hylooker": "(require {pkg}.macros [hy-looks])\n(setv value (hy-looks))\n",
    "thinger": "(require {pkg}.macros [thing])\n(setv value (thing))\n",
    "moder": "(require {pkg}.macros [mode])\n(setv value (mode))\n",
    "documenter": "(require {pkg}.macros [documented])\n(setv value (documented))\n",
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

    @property
    def tables(self) -> Path:
        return self.directory / "tables.py"

    @property
    def fakeext(self) -> Path:
        return self.directory / "fakeext.py"

    @property
    def fakeext_binary(self) -> Path:
        return self.directory / "fakeext.cpython-test.so"

    @property
    def contexts(self) -> Path:
        return self.directory / "contexts.py"

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
    (directory / "tables.py").write_text(TABLES, encoding="utf-8")
    (directory / "fakeext.py").write_text(FAKEEXT, encoding="utf-8")
    (directory / "fakeext.cpython-test.so").write_bytes(b"binary one")
    (directory / "contexts.py").write_text(CONTEXTS, encoding="utf-8")
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


def test_a_function_named_like_an_unrequired_macro_keeps_the_record_current(tree: Tree) -> None:
    # 直す前は赤(agora の records_turns.hy・intake_requests.hy が import のたびに compile し直された): 名を選んで require した
    # 使い手が、提供元にある別の macro(check)と同じ名の関数を呼ぶ。名を選んだ require の使い手の表には、提供元に何が
    # あっても入らないので、記録は作った直後に今のまま。
    record = tree.record("chooser")
    assert tree.is_current(record), "選んで require していない macro と同じ名の関数の呼び出しで古いと判じた"


# ---- 辿れない値を file の全体でなく狭く覆う(agora-redesign #3938)-------------------------------------------------------
# 冷えるべき時(直す前から緑でよい — 直した後も緑であることが要)。


def test_a_changed_table_the_macro_reads_is_stale(tree: Tree) -> None:
    record = tree.record("looker")
    _replace(tree.tables, 'TABLE = {"a": 1}', 'TABLE = {"a": 10}')
    assert not tree.is_current(record), "macro が読む大域の表の中身を替えたのに古いと判じない"


def test_a_changed_top_level_row_of_the_table_is_stale(tree: Tree) -> None:
    record = tree.record("looker")
    _replace(tree.tables, 'TABLE["b"] = 2', 'TABLE["b"] = 20')
    assert not tree.is_current(record), "top-level の文で表に足す行を替えたのに古いと判じない"


def test_a_changed_row_added_by_a_registering_function_is_stale(tree: Tree) -> None:
    # 閉包から辿られない関数(import の時に呼ぶ登録)の中で表に足す行。
    record = tree.record("looker")
    _replace(tree.tables, 'TABLE["c"] = 3', 'TABLE["c"] = 30')
    assert not tree.is_current(record), "import の時に呼ぶ関数が表に足す行を替えたのに古いと判じない"


def test_a_changed_hy_table_the_macro_reads_is_stale(tree: Tree) -> None:
    record = tree.record("hylooker")
    _replace(tree.macros, '(setv HY-TABLE {"a" 1})', '(setv HY-TABLE {"a" 10})')
    assert not tree.is_current(record), "macro が読む Hy の大域の表の中身を替えたのに古いと判じない"


def test_a_changed_top_level_row_of_the_hy_table_is_stale(tree: Tree) -> None:
    record = tree.record("hylooker")
    _replace(tree.macros, '(setv (get HY-TABLE "b") 2)', '(setv (get HY-TABLE "b") 20)')
    assert not tree.is_current(record), "top-level の文で Hy の表に足す行を替えたのに古いと判じない"


def test_a_new_public_attribute_of_an_extension_class_is_stale(tree: Tree) -> None:
    # 直す前は赤: 拡張の module の class は .so の file の sha256 で覆っていたので、公開の形が変わっても .so が同じなら古くないと判じた
    # (ここでは .so の写しの中身を替えずに class の属性を足す)。
    record = tree.record("thinger")
    _replace(tree.fakeext, '    KIND = "a"\n', '    KIND = "a"\n    EXTRA = 1\n')
    assert not tree.is_current(record), "拡張の class の公開の属性が増えたのに古いと判じない"


def test_a_removed_public_attribute_of_an_extension_class_is_stale(tree: Tree) -> None:
    # 直す前は赤(上と同じ訳 — .so の写しの中身は替えない)。
    record = tree.record("thinger")
    _replace(tree.fakeext, "    def run(self):\n        return 1\n", "    pass\n")
    assert not tree.is_current(record), "拡張の class の公開の属性が減ったのに古いと判じない"


def test_a_changed_default_of_a_context_variable_is_stale(tree: Tree) -> None:
    # 直す前は赤: ContextVar は読む関数の module(macros.hy)の file で覆っていたので、定義した module で既定値を替えても古くない。
    record = tree.record("moder")
    _replace(tree.contexts, 'default="plain"', 'default="fancy!"')
    assert not tree.is_current(record), "ContextVar の既定値を替えたのに古いと判じない"


def test_a_changed_value_set_into_a_nested_row_is_stale(tree: Tree) -> None:
    # 直す前は赤(確かめで見つかった後退): 添字の結果の中の値の書き換え(添字の添字への代入)を、読むだけと数えていた。
    record = tree.record("looker")
    _replace(tree.tables, 'TABLE["n"]["x"] = 5', 'TABLE["n"]["x"] = 50')
    assert not tree.is_current(record), "import の時に呼ぶ関数が表の中の行を書き換える値を替えたのに古いと判じない"


def test_a_changed_value_appended_to_a_nested_row_is_stale(tree: Tree) -> None:
    # 直す前は赤: 添字の結果への .append を読むだけと数えていた。
    record = tree.record("looker")
    _replace(tree.tables, 'TABLE["l"].append(7)', 'TABLE["l"].append(70)')
    assert not tree.is_current(record), "import の時に呼ぶ関数が表の中の list に足す値を替えたのに古いと判じない"


def test_a_changed_value_set_through_get_is_stale(tree: Tree) -> None:
    # 直す前は赤: 読むだけの method(.get)の結果への添字の代入を読むだけと数えていた。
    record = tree.record("looker")
    _replace(tree.tables, 'TABLE.get("n")["y"] = 6', 'TABLE.get("n")["y"] = 60')
    assert not tree.is_current(record), ".get の結果を書き換える値を替えたのに古いと判じない"


def test_a_changed_value_set_into_iterated_rows_is_stale(tree: Tree) -> None:
    # 直す前は赤: for の繰り返しの元(.values())の要素は束縛されて書き換えられうる。
    record = tree.record("looker")
    _replace(tree.tables, 'row["z"] = 8', 'row["z"] = 80')
    assert not tree.is_current(record), "表の行を回して書き換える値を替えたのに古いと判じない"


def test_a_changed_value_set_through_an_alias_is_stale(tree: Tree) -> None:
    # 直す前は赤: top-level の別名(ALIAS = TABLE)を通した書き換えを、表の名の書き換えと数えていなかった。
    record = tree.record("looker")
    _replace(tree.tables, 'ALIAS["d"] = 4', 'ALIAS["d"] = 40')
    assert not tree.is_current(record), "別名を通して表を書き換える値を替えたのに古いと判じない"


def test_a_changed_value_set_through_a_hy_alias_is_stale(tree: Tree) -> None:
    # Hy の別名((setv HY-ALIAS HY-TABLE) の後の defn が HY-ALIAS を書き換える)。
    record = tree.record("hylooker")
    _replace(tree.macros, '(setv (get HY-ALIAS "c") 3)', '(setv (get HY-ALIAS "c") 30)')
    assert not tree.is_current(record), "Hy の別名を通して表を書き換える値を替えたのに古いと判じない"


# 冷えなくてよい時(直す前は赤)。


def test_a_changed_unrelated_function_beside_the_table_keeps_the_record_current(tree: Tree) -> None:
    # 直す前は赤: 表(dict)を辿ると、その module(tables.py)の file の sha256 で覆っていた。
    record = tree.record("looker")
    _replace(tree.tables, "    return OTHER\n", "    return OTHER, 1\n")
    assert tree.is_current(record), "閉包から辿られない関数を替えただけで古いと判じた"


def test_a_changed_unrelated_global_beside_the_table_keeps_the_record_current(tree: Tree) -> None:
    record = tree.record("looker")
    _replace(tree.tables, 'OTHER = {"z": 0}', 'OTHER = {"z": 100}')
    assert tree.is_current(record), "閉包から辿られない別の大域を替えただけで古いと判じた"


def test_a_changed_unused_macro_beside_a_hy_table_keeps_the_record_current(tree: Tree) -> None:
    # 直す前は赤: Hy の表(macros.hy の HY-TABLE)を辿ると macros.hy の file の sha256 で覆っていた。
    record = tree.record("hylooker")
    _replace(tree.macros, "(defmacro unused [] 100)", "(defmacro unused [] 1000)")
    assert tree.is_current(record), "表を名で引く macro の module の別の行を替えただけで古いと判じた"


def test_a_changed_unrelated_hy_global_beside_a_hy_table_keeps_the_record_current(tree: Tree) -> None:
    record = tree.record("hylooker")
    _replace(tree.macros, '(setv HY-OTHER {"z" 0})', '(setv HY-OTHER {"z" 100})')
    assert tree.is_current(record), "表を名で引く macro の module の別の大域を替えただけで古いと判じた"


def test_a_changed_binary_of_an_extension_with_the_same_shape_keeps_the_record_current(tree: Tree) -> None:
    # 直す前は赤: 拡張の module の class は .so の file の sha256 で覆っていた。
    record = tree.record("thinger")
    tree.fakeext_binary.write_bytes(b"binary two, rebuilt")
    assert tree.is_current(record), "公開の形が同じ拡張の .so の中身だけが変わって古いと判じた"


def test_a_changed_unrelated_line_beside_a_context_variable_keeps_the_record_current(tree: Tree) -> None:
    record = tree.record("moder")
    _replace(tree.contexts, "LATER = 1", "LATER = 100")
    assert tree.is_current(record), "ContextVar の module の別の行を替えただけで古いと判じた"


def test_a_changed_unused_macro_beside_a_context_variable_keeps_the_record_current(tree: Tree) -> None:
    # 直す前は赤: ContextVar は読む関数の module(macros.hy)の file で覆っていた。
    record = tree.record("moder")
    _replace(tree.macros, "(defmacro unused [] 100)", "(defmacro unused [] 1000)")
    assert tree.is_current(record), "ContextVar を読む macro の module の別の行を替えただけで古いと判じた"


# ---- module の説明の文(card ki-70d92767a917 の子)----------------------------------------------------------------------


def test_a_changed_docstring_of_a_module_the_macro_reads_keeps_the_record_current(tree: Tree) -> None:
    # 直す前は赤: macro の code の文字列の定数 "__doc__"(展開の結果の関数に書く説明)を名の候補として、macro が参照する
    # module(consts)の __doc__ を digest に入れていた。module の説明の文は展開の結果に効かない。
    record = tree.record("documenter")
    _replace(tree.consts, '"定数の module。"', '"定数の module(説明を書き替えた)。"')
    assert tree.is_current(record), "macro が参照する module の説明の文を替えただけで古いと判じた"


def test_a_changed_constant_of_a_module_the_documenting_macro_reads_is_stale(tree: Tree) -> None:
    # 冷えるべき時: 同じ macro が読む同じ module の定数を替えたら古い(__doc__ を外しても module の属性の読みは覆う)。
    record = tree.record("documenter")
    _replace(tree.consts, "(setv OFFSET 1)", "(setv OFFSET 10)")
    assert not tree.is_current(record), "macro が読む module の定数を替えたのに古いと判じない"


# ---- 実物の doeff_hy の macro(doeff_hy を一時の dir に写し、別の process で記録を作って照らす)------------------------------

#: defk・val・<-・defrecord・defhandler だけを使う module(agora の controllers の module の形)。
REAL_USER = """\
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defrecord])
(require doeff-hy.handle [defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Fetch [EffectBase]
  #^ str source)

(defrecord Row
  "行"
  {:tags {:context "macro-use-probe" :role "type"}}
  (#^ int value))

(val base 1)

(defk step [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "macro-use-probe" :role "judgment"}}
  "1 を足す。"
  (+ n base))

(defk job [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "macro-use-probe" :role "entry"}}
  "束ね。"
  (<- a (step n))
  a)

(defhandler fetch-handler
  (Fetch [source]
    (resume source)))
"""

#: 子の process: 写した doeff_hy で使い手を import と同じ口で compile して記録を JSON で書く(record)か、書いた記録を照らす
#: (check)。読んだ doeff_hy の在りかと、digest を持たない(file の行で比べる)macro の行の名も出す。
REAL_CHILD = """\
import importlib.machinery, json, sys
from pathlib import Path
root, mode, stored = sys.argv[1], sys.argv[2], Path(sys.argv[3])
sys.path.insert(0, root)
import hy
import doeff_hy
from doeff_hy_bytecode_guard import record_from_json, record_is_current_here, record_to_json
from doeff_hy_bytecode_guard import records, source_to_code_as_import
if mode == "record":
    path = root + "/macro_use_probe/user.hy"
    loader = importlib.machinery.SourceFileLoader("macro_use_probe.user", path)
    record = records.record_of(source_to_code_as_import(loader, open(path, "rb").read(), path))
    stored.write_text(json.dumps(record_to_json(record)))
    by_files = [row.module + ":" + row.name for row in record.macros if row.digest is None]
    print(json.dumps({"by_files": by_files, "doeff_hy": doeff_hy.__file__}))
else:
    record = record_from_json(json.loads(stored.read_text()))
    print(json.dumps({"current": record_is_current_here(record), "doeff_hy": doeff_hy.__file__}))
"""


@dataclass(frozen=True)
class RealTree:
    """一時の dir に写した doeff_hy(lib/doeff_hy)と、それを使う module の根・記録の file。"""

    lib: Path
    root: Path
    stored: Path

    @property
    def macros(self) -> Path:
        return self.lib / "doeff_hy" / "macros.hy"

    def run(self, mode: str) -> dict[str, object]:
        """子の process で 1 度走らせた答え(写した doeff_hy を読んだことも確かめる)。"""
        import json
        import subprocess

        done = subprocess.run(
            [sys.executable, "-c", REAL_CHILD, str(self.root), mode, str(self.stored)],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        answer = json.loads(done.stdout.strip().splitlines()[-1])
        assert Path(str(answer["doeff_hy"])).is_relative_to(self.lib), answer
        return answer


@pytest.fixture
def real_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RealTree:
    import shutil

    import doeff_hy

    lib = tmp_path / "lib"
    shutil.copytree(
        Path(doeff_hy.__file__).parent, lib / "doeff_hy", ignore=shutil.ignore_patterns("__pycache__")
    )
    root = tmp_path / "proj"
    (root / "macro_use_probe").mkdir(parents=True)
    (root / "macro_use_probe" / "__init__.py").write_text("", encoding="utf-8")
    (root / "macro_use_probe" / "user.hy").write_text(REAL_USER, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(lib))
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pycache"))
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")
    return RealTree(lib, root, tmp_path / "record.json")


def test_the_record_of_a_defk_user_does_not_cover_doeff_hy_macros_by_its_file(real_tree: RealTree) -> None:
    # 直す前は赤(agora の records_turns.hy の記録): defk などの閉包の途中で、関数の中の import の先(outcome_forms・
    # declarations・defhandler が読む doeff_hy.macros)を module ごと file 単位で覆い、macros.hy が入っていた。記録の file の行
    # (閉包が依った module の file — macros.hy も入る)は、digest を持たない行がある時だけ比べるので、全部の行が digest を持つ
    # (読み込みの済んだ状態で作った)ことを確かめる。macros.hy の別の行を替えても古くないことは次の検が確かめる。
    by_files = real_tree.run("record")["by_files"]
    assert by_files == [], by_files


def test_a_changed_deftest_keeps_the_record_of_a_defk_user_current(real_tree: RealTree) -> None:
    # 直す前は赤: macros.hy の deftest(使い手が使わない macro)の本体を 1 行替えても古くない。
    real_tree.run("record")
    _replace(
        real_tree.macros,
        "Define an effectful test that expands to a pytest-compatible function.",
        "Define an effectful test (changed).",
    )
    assert real_tree.run("check")["current"] is True, "使わない deftest を替えただけで古いと判じた"


def test_a_changed_defk_makes_the_record_of_a_defk_user_stale(real_tree: RealTree) -> None:
    # 冷えるべき時: 使った defk の本体(docstring の定数)を替えたら古い。
    real_tree.run("record")
    _replace(
        real_tree.macros,
        '"Define a kleisli function (@do decorator)',
        '"Define a kleisli function (@do decorator, changed)',
    )
    assert real_tree.run("check")["current"] is False, "使った defk を替えたのに古いと判じない"


#: defk・val・<- だけを使う module。
REAL_DEFK_USER = """\
(require doeff-hy.macros [defk <- val])

(val base 1)

(defk step [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "macro-use-probe" :role "judgment"}}
  "1 を足す。"
  (+ n base))

(defk job [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "macro-use-probe" :role "entry"}}
  "束ね。"
  (<- a (step n))
  a)
"""

#: 閉包から辿られない、展開に効かない関数(file の末尾に足す)。
UNREACHED_PY = "\n\ndef _unreached_probe() -> int:\n    return 1\n"
UNREACHED_HY = "\n(defn _unreached-probe [] 1)\n"


def _append(path: Path, text: str) -> None:
    """file の末尾に文を足す。"""
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def test_an_unreached_function_in_binding_forms_and_static_view_keeps_a_defk_user_current(
    real_tree: RealTree,
) -> None:
    # 直す前は赤(agora の Hy の module 414 個のほぼ全部が pin を上げるたびに作り直された): binding_forms の大域の dict・
    # dataclass の欄の名 vars の読み・kwdefaults の dict・static_view の ContextVar を辿ると、その module の file の sha256 で
    # 覆っていた。
    (real_tree.root / "macro_use_probe" / "user.hy").write_text(REAL_DEFK_USER, encoding="utf-8")
    real_tree.run("record")
    _append(real_tree.lib / "doeff_hy" / "binding_forms.py", UNREACHED_PY)
    _append(real_tree.lib / "doeff_hy" / "static_view.py", UNREACHED_PY)
    assert real_tree.run("check")["current"] is True, (
        "binding_forms と static_view に辿られない関数を足しただけで defk・val の使い手を古いと判じた"
    )


def test_an_unreached_function_in_clause_endings_keeps_a_defhandler_user_current(
    real_tree: RealTree,
) -> None:
    # 直す前は赤: defhandler が読む clause_endings.hy の表(NAMED-RESUMPTION)を辿ると clause_endings.hy の file で覆っていた。
    real_tree.run("record")
    _append(real_tree.lib / "doeff_hy" / "clause_endings.hy", UNREACHED_HY)
    _append(real_tree.lib / "doeff_hy" / "binding_forms.py", UNREACHED_PY)
    _append(real_tree.lib / "doeff_hy" / "static_view.py", UNREACHED_PY)
    assert real_tree.run("check")["current"] is True, (
        "clause_endings・binding_forms・static_view に辿られない関数を足しただけで defhandler の使い手を古いと判じた"
    )


#: defhandler だけを使う module。
REAL_HANDLER_USER = """\
(require doeff-hy.handle [defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Fetch [EffectBase]
  #^ str source)

(defhandler fetch-handler
  (Fetch [source]
    (resume source)))
"""

#: defhandler の閉包が辿る、説明の文(module の docstring)を持つ module の file と、その説明の文の頭。
DOCUMENTED_MODULES = {
    "__init__.py": '"""doeff-hy — Standard Hy macros for doeff effect composition.',
    "record_runtime.py": '"""defrecord と defeffect の実行の展開が読み込みの時に読む部品',
    "type_forms.py": '"""型の式を、実行時に ``isinstance`` の第 2 引数へ渡せる form へ写す',
}


def test_changed_module_docstrings_keep_a_defhandler_user_current(real_tree: RealTree) -> None:
    # 直す前は赤(card ki-70d92767a917 の子・doeff b44c49c → 0f2fa6f で defhandler の使い手 540 file が Hy の展開の置き場で
    # 外れた): defhandler の展開の code の文字列の定数 "__doc__" を名の候補として、閉包で辿った module の __doc__ を digest に
    # 入れていた。digest の材料の違いは record_runtime の docstring の 1 行だけだった。
    (real_tree.root / "macro_use_probe" / "user.hy").write_text(REAL_HANDLER_USER, encoding="utf-8")
    real_tree.run("record")
    for name, head in DOCUMENTED_MODULES.items():
        _replace(real_tree.lib / "doeff_hy" / name, head, f"{head}(説明を書き替えた)")
    assert real_tree.run("check")["current"] is True, (
        "defhandler の閉包の module の説明の文を替えただけで defhandler の使い手を古いと判じた"
    )


def test_a_changed_table_in_binding_forms_makes_a_defk_user_stale(real_tree: RealTree) -> None:
    # 冷えるべき時: defk・val が読む binding_forms の表(_KINDS)の行を替えたら古い。
    (real_tree.root / "macro_use_probe" / "user.hy").write_text(REAL_DEFK_USER, encoding="utf-8")
    real_tree.run("record")
    _replace(
        real_tree.lib / "doeff_hy" / "binding_forms.py",
        '_KINDS: Mapping[str, Mutability] = {"val": Mutability.VAL, "var": Mutability.VAR}',
        '_KINDS: Mapping[str, Mutability] = {"val": Mutability.VAR, "var": Mutability.VAR, "v": Mutability.VAL}',
    )
    assert real_tree.run("check")["current"] is False, "binding_forms の _KINDS を替えたのに古いと判じない"


# ---- import の順に依らない digest(閉包の中の module が循環する import の途中で引けない時)-------------------------------------
#
# deff の閉包は、関数の中の import で doeff_hy.outcome_forms を引く。outcome_forms は doeff_hy.record の defrecord を require し、
# record.hy は deff を使う。.pyc から record を読む process では、record.hy の記録の照らしが record の本体の実行の前に走るので、
# outcome_forms を import できない(record の macro の表がまだ空)。直す前は、その時の digest(名だけ)が process の覚えに残り、
# その process の deff の使い手の照らしと記録が全部その値になって、import の順の違う process と置き場の entry を書き直し合った
# (agora-redesign card ki-464fa307a62b・日次 10-11 16:03 の回)。

#: deff だけを使う module。
REAL_DEFF_USER = """\
(require doeff-hy.macros [deff])

(deff add-one [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "macro-use-probe" :role "foundation"}}
  "1 を足す。"
  (+ n 1))
"""

#: 子の process(import の順を変える): 名指した macro(module:名)の digest を順に計算する(digests)・使い手の記録を作る
#: (record)・名指した macro の digest を先に計算してから記録を照らす(check)・module を順に import して compile し終えた Hy の
#: file を出す(import — 循環する import の途中で require が落ちた compile は何も作らないので数えない)。
ORDER_CHILD = """\
import importlib, importlib.machinery, json, sys
from pathlib import Path
root, stored, mode, *names = sys.argv[1], Path(sys.argv[2]), sys.argv[3], *sys.argv[4:]
sys.path.insert(0, root)
import hy
compiled = ()
previous = importlib.machinery.SourceFileLoader.source_to_code
def counting(self, data, path, *args, **kwargs):
    global compiled
    code = previous(self, data, path, *args, **kwargs)
    if isinstance(path, str) and path.endswith(".hy"):
        compiled = (*compiled, Path(path).name)
    return code
importlib.machinery.SourceFileLoader.source_to_code = counting
import doeff_hy
from doeff_hy_bytecode_guard import loader_hooks, macro_use, record_from_json, record_is_current_here, record_to_json
from doeff_hy_bytecode_guard import records, source_to_code_as_import
def digest(spec):
    module, name = spec.split(":")
    key = macro_use.UsedMacroKey("_hy_macros", module, hy.mangle(name))
    macro = vars(importlib.import_module(module))["_hy_macros"][key.name]
    return macro_use.macro_digest(key, macro, loader_hooks.file_sha256).digest
answer = {"doeff_hy": doeff_hy.__file__}
if mode == "digests":
    answer["digests"] = [digest(spec) for spec in names]
elif mode == "record":
    path = root + "/macro_use_probe/user.hy"
    loader = importlib.machinery.SourceFileLoader("macro_use_probe.user", path)
    code = source_to_code_as_import(loader, open(path, "rb").read(), path)
    stored.write_text(json.dumps(record_to_json(records.record_of(code))))
elif mode == "check":
    for spec in names:
        digest(spec)
    answer["current"] = record_is_current_here(record_from_json(json.loads(stored.read_text())))
else:
    for name in names:
        importlib.import_module(name)
    answer["compiled"] = compiled
print(json.dumps(answer))
"""

DEFF = "doeff_hy.macros:deff"
DEFRECORD = "doeff_hy.record:defrecord"
DEFWIRE = "doeff_hy.record:defwire"


def _order_child(real_tree: RealTree, mode: str, *names: str, **env: str) -> dict[str, object]:
    """ORDER_CHILD を子の process で 1 度走らせた答え(env = 足す環境変数 — 写した doeff_hy を読んだことも確かめる)。

    子は .pyc を書く(検の一時 dir の PYTHONPYCACHEPREFIX の下 — real_tree が置く)。揺れは record を .pyc から読む process
    (record.hy の記録の照らしが本体の実行の前に走る)でだけ起きるので、検の一式が子へ渡す ``PYTHONDONTWRITEBYTECODE`` を外す。"""
    import json
    import os
    import subprocess

    inherited = {name: value for name, value in os.environ.items() if name != "PYTHONDONTWRITEBYTECODE"}
    done = subprocess.run(
        [sys.executable, "-c", ORDER_CHILD, str(real_tree.root), str(real_tree.stored), mode, *names],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**inherited, **env},
    )
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout.strip().splitlines()[-1])
    assert Path(str(answer["doeff_hy"])).is_relative_to(real_tree.lib), answer
    return answer


def test_the_digest_of_deff_does_not_depend_on_computing_defrecord_and_defwire_first(
    real_tree: RealTree,
) -> None:
    # 直す前は赤: 先に defrecord・defwire の digest を計算した process(record を .pyc から読み、record.hy の照らしの途中で
    # outcome_forms を import できない)の deff の digest は、単独の process の digest と違った(959a4c15 / f24dde8b)。
    _order_child(real_tree, "import", "doeff_hy.record", "doeff_hy.wire")  # .pyc を作る(日次の 2 回目からの形)
    alone = _order_child(real_tree, "digests", DEFF)["digests"]
    after = _order_child(real_tree, "digests", DEFRECORD, DEFWIRE, DEFF)["digests"]
    assert isinstance(alone, list), alone
    assert isinstance(after, list), after
    assert alone[0] is not None, "単独の process で deff の digest を作れない"
    assert after[-1] == alone[0], (
        f"先に defrecord・defwire の digest を計算した process の deff の digest {after[-1]} が単独の {alone[0]} と違う"
    )


def test_a_deff_user_stays_current_in_a_process_that_computed_defrecord_and_defwire_first(
    real_tree: RealTree,
) -> None:
    # 直す前は赤: 単独の process では当たる deff の使い手の記録が、先に defrecord・defwire の digest を計算した process では
    # 古い(deff)と判じられた — 置き場の entry を、import の順の違う process が互いに書き直す元。
    (real_tree.root / "macro_use_probe" / "user.hy").write_text(REAL_DEFF_USER, encoding="utf-8")
    _order_child(real_tree, "import", "doeff_hy.record", "doeff_hy.wire")  # .pyc を作る
    _order_child(real_tree, "record")
    assert _order_child(real_tree, "check")["current"] is True, "単独の process で deff の使い手の記録が古い"
    assert _order_child(real_tree, "check", DEFRECORD, DEFWIRE)["current"] is True, (
        "先に defrecord・defwire の digest を計算した process で、deff の使い手の記録を古いと判じた"
    )


#: import の順の違う 2 つの process の形 — record を先に読む(record.hy の照らしの途中で outcome_forms を import できない)と、
#: deff の使い手を先に読む(record.hy の照らしの途中で outcome_forms が import の途中)。
RECORD_FIRST = ("doeff_hy.record", "doeff_hy.wire", "doeff_hy.clause_endings")
DEFF_USER_FIRST = ("doeff_hy.macros", "doeff_hy.clause_endings", "doeff_hy.wire", "doeff_hy.record")


def test_processes_importing_in_different_orders_share_the_store_without_recompiling(
    real_tree: RealTree, tmp_path: Path
) -> None:
    # 直す前は赤: 共有の置き場 1 つを、import の順の違う process が交互に使うと(pycache は毎回空 — 日次の pytest の段の形)、
    # 2 巡目からも毎回 record.hy・outcome_forms.hy・wire.hy・clause_endings.hy を作り直して置き場を書き直した。
    store = tmp_path / "store"
    store.mkdir()
    rounds = tuple(
        (
            order[0],
            _order_child(
                real_tree,
                "import",
                *order,
                DOEFF_HY_CODE_STORE=str(store),
                PYTHONPYCACHEPREFIX=str(tmp_path / f"pycache-{index}"),
            )["compiled"],
        )
        for index, order in enumerate((RECORD_FIRST, DEFF_USER_FIRST) * 2)
    )
    assert rounds[0][1], "1 巡目(冷えた置き場)で何も compile していない — 検の前提が崩れた"
    again = [(first, compiled) for first, compiled in rounds[2:] if compiled]
    assert not again, f"2 巡目からも Hy の module を作り直した: {again}(全部の回: {rounds})"


# ---- process をまたぐ覚え(agora-redesign card ki-38508e6e56fd)----------------------------------------------------------
#
# 新しい process の照らしの費用の大半は、閉包の歩みが辿れない大域を覆う source の文の読み(bound_source.binding_of — source を
# 解いて構文木を歩く)だった。中身と名で決まる純な読みは置き場(DOEFF_HY_CODE_STORE)に覚え、次の process は解き直さない。
# digest そのものは process をまたいで覚えない — digest は生きた値(macro の file・別の module から来た素の値・環境変数で決まる値)に
# 依る。下の 3 つ(条 1〜3)は、digest を辿った file の状態(path・更新時刻・大きさ)で覚えて次の process が信じる素朴な形で赤になる
# (依った物が覚えた時と違うのに、前の process の digest で今に合うと判じ、古い展開を使う)。

#: macro の module(条ごとの macro)。body = 本体だけの macro(条 1 — macro を定義した file の中身)・scaled = 別の module から import
#: した素の値を返す macro(条 2 — 値は helper.hy の中身で決まるが、macro の閉包が辿る file は macros.hy だけ)・moded = 読み込みの時に
#: 環境変数から組んだ値を返す macro(条 3 — file の外で決まる値)・looks と rows = 辿れない大域の表を読む macro(Hy と Python の
#: source の文で覆う — binding_of を通る)。
CROSS_MACROS = """\
(import os)
(import {pkg}.helper [SCALE])
(import {pkg}.tables :as tables)
(setv TABLE {{"a" 1}})
(setv MODE (os.environ.get "MACRO_USE_PROBE_MODE" "plain"))
(defmacro body [] 1)
(defmacro scaled [] SCALE)
(defmacro moded [] MODE)
(defmacro looks [] (get TABLE "a"))
(defmacro rows [] (get tables.ROWS "a"))
"""

CROSS_FILES = {
    "__init__.py": "",
    "macros.hy": CROSS_MACROS,
    "helper.hy": "(setv SCALE 3)\n",
    "tables.py": 'ROWS = {{"a": 1}}\nROWS["b"] = 2\n',
    "user.hy": "(require {pkg}.macros [body scaled moded looks rows])\n"
    "(setv value [(body) (scaled) (moded) (looks) (rows)])\n",
}

#: 子の process: 使い手の記録を作り、同じ process でも照らす(record — 記録を作る歩みと照らしの両方を通す)・記録を照らす(check)。
#: 答えに、照らしの間に source を解いた回数(parsed — bound_source の解き)を出す。
CROSS_CHILD = """\
import importlib.machinery, json, sys
from pathlib import Path
root, stored, mode, package = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
sys.path.insert(0, root)
import hy
import doeff_hy
from doeff_hy_bytecode_guard import bound_source, record_from_json, record_is_current_here, record_to_json
from doeff_hy_bytecode_guard import records, source_to_code_as_import
parsed = {"count": 0}
reading = bound_source._read
def counting(*args, **kwargs):
    parsed["count"] += 1
    return reading(*args, **kwargs)
bound_source._read = counting
if mode == "record":
    path = f"{root}/{package}/user.hy"
    loader = importlib.machinery.SourceFileLoader(f"{package}.user", path)
    record = records.record_of(source_to_code_as_import(loader, open(path, "rb").read(), path))
    stored.write_text(json.dumps(record_to_json(record)))
    current = record_is_current_here(record)
else:
    current = record_is_current_here(record_from_json(json.loads(stored.read_text())))
print(json.dumps({"current": current, "parsed": parsed["count"]}))
"""

CROSS_PACKAGE = "macro_use_cross"


@dataclass(frozen=True)
class CrossTree:
    """子の process で読む検の package の根(作業木 1 つの代わり)と、process をまたいで共有する置き場・記録の file。"""

    root: Path
    store: Path
    stored: Path

    @property
    def package(self) -> Path:
        return self.root / CROSS_PACKAGE

    def run(self, mode: str, **env: str) -> dict[str, object]:
        """子の process で 1 度走らせた答え(env = 足す環境変数)。"""
        import json
        import os
        import subprocess

        inherited = {
            name: value
            for name, value in os.environ.items()
            if name not in ("PYTHONDONTWRITEBYTECODE", "MACRO_USE_PROBE_MODE")
        }
        done = subprocess.run(
            [sys.executable, "-c", CROSS_CHILD, str(self.root), str(self.stored), mode, CROSS_PACKAGE],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={
                **inherited,
                "DOEFF_HY_CODE_STORE": str(self.store),
                "PYTHONPYCACHEPREFIX": str(self.root.parent / f"pycache-{self.root.name}"),
                **env,
            },
        )
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip().splitlines()[-1])

    def elsewhere(self, name: str) -> "CrossTree":
        """同じ中身の package を別の根(別の作業木)に写す — 置き場と記録の file は同じ物を使う。"""
        import shutil

        root = self.root.parent / name
        shutil.copytree(self.package, root / CROSS_PACKAGE, ignore=shutil.ignore_patterns("__pycache__"))
        return CrossTree(root, self.store, self.stored)


@pytest.fixture
def cross_tree(tmp_path: Path) -> CrossTree:
    root = tmp_path / "tree-a"
    (root / CROSS_PACKAGE).mkdir(parents=True)
    for name, text in CROSS_FILES.items():
        (root / CROSS_PACKAGE / name).write_text(text.format(pkg=CROSS_PACKAGE), encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    return CrossTree(root, store, tmp_path / "record.json")


def test_a_second_process_does_not_parse_the_sources_the_first_one_read(cross_tree: CrossTree) -> None:
    # 直す前は赤: 新しい process の照らしは、前の process と同じ中身の source(macros.hy の TABLE・tables.py の ROWS)を毎回
    # 解き直した(test_emulated_screen.hy の読み込みで照らしの歩みの約 2/3)。
    assert cross_tree.run("record")["current"] is True, "記録を作った process で、作った記録を古いと判じた"
    answer = cross_tree.run("check")
    assert answer["current"] is True, "何も変えていないのに 2 つ目の process が古いと判じた"
    assert answer["parsed"] == 0, f"2 つ目の process が同じ中身の source を {answer['parsed']} 回解き直した"


def test_a_macro_changed_in_another_tree_is_stale_in_a_second_process(cross_tree: CrossTree) -> None:
    # 条 1(macro を定義した file の中身): 別の作業木の macros.hy は body の本体が違う。前の process が覚えた digest の file
    # (前の木の macros.hy)は変わっていないので、file の状態で覚えを信じる形は前の digest を使い、古い展開を今に合うと判じる。
    cross_tree.run("record")
    other = cross_tree.elsewhere("tree-b")
    _replace(other.package / "macros.hy", "(defmacro body [] 1)", "(defmacro body [] 20)")
    assert other.run("check")["current"] is False, "別の木で macro の本体が違うのに、2 つ目の process が古いと判じない"


def test_a_changed_value_from_another_module_is_stale_in_a_second_process(cross_tree: CrossTree) -> None:
    # 条 2(macro が辿る値の module の file の中身): SCALE は helper.hy の中身で決まるが、scaled の閉包が辿る file は macros.hy
    # だけ(素の値はどの module から来たかを持たない)。file の状態で覚えを信じる形は、helper.hy を替えても前の digest を使う。
    cross_tree.run("record")
    _replace(cross_tree.package / "helper.hy", "(setv SCALE 3)", "(setv SCALE 30)")
    assert cross_tree.run("check")["current"] is False, "macro が返す別の module の値を替えたのに、2 つ目の process が古いと判じない"


def test_a_changed_environment_value_is_stale_in_a_second_process(cross_tree: CrossTree) -> None:
    # 条 3(file の外で決まる値): MODE は読み込みの時に環境変数から組む。どの file も変わらないので、file の状態で覚えを
    # 信じる形は前の digest を使う。
    cross_tree.run("record")
    answer = cross_tree.run("check", MACRO_USE_PROBE_MODE="strict")
    assert answer["current"] is False, "macro が返す環境変数の値が違うのに、2 つ目の process が古いと判じない"
