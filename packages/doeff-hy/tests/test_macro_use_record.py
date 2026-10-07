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
(defmacro check [x] 0)
(import {pkg}.tables :as tables)
(import {pkg}.fakeext [Thing])
(import {pkg}.contexts [MODE])
(setv HY-TABLE {{"a" 1}})
(setv (get HY-TABLE "b") 2)
(setv HY-OTHER {{"z" 0}})
(defn hy-look [k] (get HY-TABLE k))
(defmacro looks [] (tables.lookup "a"))
(defmacro hy-looks [] (hy-look "a"))
(defmacro thing [] (do Thing 1))
(defmacro mode [] (do (.get MODE) 1))
"""

CONSTS = "(setv OFFSET 1)\n(setv HIDDEN 5)\n"

#: macro が展開の時に読む大域の表(Python の module)。TABLE は top-level の文と、import の時に呼ぶ登録の関数で行が足される。
#: OTHER と unrelated は閉包から辿られない。
TABLES = """\
TABLE = {"a": 1}
TABLE["b"] = 2
OTHER = {"z": 0}


def _register():
    TABLE["c"] = 3


_register()


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
#: (check)。読んだ doeff_hy の在りかと、記録の file 単位で覆う module の名も出す。
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
    print(json.dumps({"files": [row.module for row in record.files], "doeff_hy": doeff_hy.__file__}))
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
    # declarations・defhandler が読む doeff_hy.macros)を module ごと file 単位で覆い、macros.hy が入っていた。
    files = real_tree.run("record")["files"]
    assert "doeff_hy.macros" not in files, files


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
