"""macro の記録が、展開で実行された関数の code だけを覆う形の失敗ケース(#3769 の子 — 日次 10-12 04:00 の 1,197 本の作り直し)。

記録の digest は、閉包の歩みが辿った物(関数の名・class の member の名・値・束縛の文)を今までどおり全部入れ、関数の code の
中身(:attr:`doeff_hy_bytecode_guard.macro_use.CodeFacts.shape`)だけを、その module の展開で実行された関数に絞る
(:mod:`doeff_hy_bytecode_guard.called_code` が展開の間に実行の始まった code を拾う)。呼ばれない method の code の変更では
古いと判じず、呼ばれる method の変更では今までどおり古いと判じる。展開の外(import の時)に実行されて値を作った関数
(束縛の文が呼ぶ関数・書き換える関数)と、関数の中身を値として読む code(説明の文・引数の形)の先は絞らない。

記録は import と同じ口(``source_to_code_as_import``)で作り、``record_is_current_here`` で照らす(test_macro_use_record.py と同じ)。
"""

import importlib
import importlib.machinery
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from doeff_hy_bytecode_guard import (
    loaded_record,
    record_is_current_here,
    records,
    source_to_code_as_import,
)

#: macro の module。rewrites = 引数が真なら Rewriter の assign を、偽なら walk を呼ぶ(defk が := の有無で _Walker._assign を
#: 通るかが変わる形)・reads-doc = 呼ばない関数の説明の文を読む・reads-signature = 呼ばない関数の引数の形を読む・builds = 表を
#: 引く関数を呼び、表を作る関数(import の時に呼ばれる)を名で引くが呼ばない・builds-plainly = 表を引く関数だけを呼ぶ。
MACROS = """\
(import {pkg}.tables :as tables)
(import inspect)
(defclass Rewriter []
  (defn walk [self x] (+ x 1))
  (defn assign [self x] (* x 2)))
(defmacro rewrites [assign] (if assign (.assign (Rewriter) 1) (.walk (Rewriter) 1)))
(defn documented-helper [] "説明の一" 1)
(defmacro reads-doc [] (len (. documented-helper __doc__)))
(defn sig-helper [a] a)
(defmacro reads-signature [] (len (. (inspect.signature sig-helper) parameters)))
(defmacro builds [] (if (is tables._build None) 0 (tables.built "k")))
(defmacro builds-plainly [] (tables.plain-built "k"))
"""

#: macro が展開の時に読む表(Python の module)。BUILT と PLAIN は import の時に呼ぶ関数が作る(展開の間には呼ばれない)。
TABLES = """\
def _build():
    return {"k": 1}


BUILT = _build()


def built(k):
    return BUILT[k]


def _build_plain():
    return {"k": 1}


PLAIN = _build_plain()


def plain_built(k):
    return PLAIN[k]
"""

USERS = {
    "plain": "(require {pkg}.macros [rewrites])\n(setv value (rewrites 0))\n",
    "assigning": "(require {pkg}.macros [rewrites])\n(setv value (rewrites 1))\n",
    "doc_reader": "(require {pkg}.macros [reads-doc])\n(setv value (reads-doc))\n",
    "signature_reader": "(require {pkg}.macros [reads-signature])\n(setv value (reads-signature))\n",
    "builder": "(require {pkg}.macros [builds])\n(setv value (builds))\n",
    "plain_builder": "(require {pkg}.macros [builds-plainly])\n(setv value (builds-plainly))\n",
    # 表を通らない hy.R の呼び出し(記録は source の文から macro を拾う)。
    "hy_r_plain": "(setv value (hy.R.{pkg}/macros.rewrites 0))\n",
}


@dataclass(frozen=True)
class Tree:
    """sys.path に置いた一時の dir の中の検の package(名は検ごとに違う)。"""

    package: str
    directory: Path

    @property
    def macros(self) -> Path:
        return self.directory / "macros.hy"

    @property
    def tables(self) -> Path:
        return self.directory / "tables.py"

    def forget(self) -> None:
        """検の package の module を sys.modules から外す(次の実行と同じく、次に macro の module を読み直させる)。"""
        for name in [
            n for n in sys.modules if n == self.package or n.startswith(f"{self.package}.")
        ]:
            del sys.modules[name]

    def record(self, user: str, *, tables_first: bool = False) -> records.MacroRecord:
        """使う側の module を import と同じ口で compile し、code に載った記録を返す。tables_first = 表の module を先に読む
        (表を作る関数を展開の外で実行させる — 長く走る process で、先に別の module が読んだ形)。"""
        if tables_first:
            importlib.import_module(f"{self.package}.tables")
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
    package = f"macro_use_called_{uuid.uuid4().hex[:8]}"
    directory = tmp_path / package
    directory.mkdir()
    (directory / "__init__.py").write_text("", encoding="utf-8")
    (directory / "macros.hy").write_text(MACROS.format(pkg=package), encoding="utf-8")
    (directory / "tables.py").write_text(TABLES, encoding="utf-8")
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


def test_an_imported_module_exposes_the_record_of_its_expansion(tree: Tree) -> None:
    # Hy の test file の item の記録の cache(doeff-adr の item_cache)は、import した file の展開が使った macro の記録を、
    # 読みの口の覚え(loaded_record)から引いて保存し、record_is_current_here で照らす(#3769 の子)。
    module = importlib.import_module(f"{tree.package}.plain")
    record = loaded_record(str(module.__file__))
    assert record is not None, "import した Hy の module の展開の記録を引けない"
    assert [row.name for row in record.macros] == ["rewrites"], record.macros
    assert record_is_current_here(record), "何も変えていないのに古いと判じた"


def test_a_record_is_current_while_nothing_changed(tree: Tree) -> None:
    for user in USERS:
        record = tree.record(user)
        assert tree.is_current(record), f"{user}: 何も変えていないのに古いと判じた"


def test_a_changed_method_the_expansion_does_not_call_keeps_the_record_current(tree: Tree) -> None:
    # 直す前は赤: 閉包の歩みが class(Rewriter)を method ごと全部辿り、展開で呼ばれない assign の code を digest に入れていた。
    record = tree.record("plain")
    _replace(tree.macros, "(* x 2)", "(* x 22)")
    assert tree.is_current(record), "展開で呼ばれない method を替えただけで古いと判じた"


def test_a_changed_method_a_hy_r_call_does_not_reach_keeps_the_record_current(tree: Tree) -> None:
    # hy.R で呼ぶ macro も、展開で実行された関数だけを覆う(記録を作る口が、source の文から hy.R の呼び出しを拾う所を通る)。
    record = tree.record("hy_r_plain")
    assert [row.name for row in record.macros] == ["rewrites"], record.macros
    _replace(tree.macros, "(* x 2)", "(* x 22)")
    assert tree.is_current(record), "hy.R の展開で呼ばれない method を替えただけで古いと判じた"


def test_a_changed_method_the_expansion_calls_is_stale(tree: Tree) -> None:
    # 冷えるべき時(対照): 展開で assign を呼ぶ使い手は、同じ変更で古い。
    record = tree.record("assigning")
    _replace(tree.macros, "(* x 2)", "(* x 22)")
    assert not tree.is_current(record), "展開で呼ぶ method を替えたのに古いと判じない"


def test_a_method_added_beside_the_called_one_is_stale(tree: Tree) -> None:
    # 冷えるべき時: class の member の名は今までどおり入る(名の引き方が変わりうる — 足した名が呼ぶ名を覆うなど)。
    record = tree.record("plain")
    _replace(
        tree.macros,
        "(defn assign [self x] (* x 2))",
        "(defn assign [self x] (* x 2))\n  (defn added [self] 0)",
    )
    assert not tree.is_current(record), "呼ぶ method の class に member を足したのに古いと判じない"


def test_a_changed_docstring_of_an_uncalled_function_the_macro_reads_is_stale(tree: Tree) -> None:
    # 冷えるべき時: 呼ばない関数の説明の文(__doc__)を展開が値として読む — 関数の中身を読む code の閉包は絞らない。
    record = tree.record("doc_reader")
    _replace(tree.macros, '"説明の一"', '"説明の二つ"')
    assert not tree.is_current(record), "展開が読む関数の説明の文を替えたのに古いと判じない"


def test_a_changed_signature_of_an_uncalled_function_the_macro_reads_is_stale(tree: Tree) -> None:
    # 冷えるべき時: 呼ばない関数の引数の形(inspect.signature)を展開が読む。
    record = tree.record("signature_reader")
    _replace(tree.macros, "(defn sig-helper [a] a)", "(defn sig-helper [a b] a)")
    assert not tree.is_current(record), "展開が読む関数の引数の形を替えたのに古いと判じない"


def test_a_changed_function_that_built_the_table_at_import_is_stale(tree: Tree) -> None:
    # 冷えるべき時: 表を作る関数(_build_plain)は表の module を読む時に呼ばれ、展開の間には呼ばれない。表(辿れない dict)は
    # 束縛の文で覆い、文が呼ぶ関数の code は絞らない。
    record = tree.record("plain_builder", tables_first=True)
    _replace(
        tree.tables,
        'def _build_plain():\n    return {"k": 1}',
        'def _build_plain():\n    return {"k": 10}',
    )
    assert not tree.is_current(record), "import の時に表を作る関数を替えたのに古いと判じない"


def test_a_changed_function_that_built_the_table_is_stale_when_first_reached_by_name(
    tree: Tree,
) -> None:
    # 冷えるべき時: 表を作る関数(_build)を、macro が先に名で引いて(呼ばずに)辿り、後から表の束縛の文が同じ関数を呼ぶ。2 度目に
    # 出会った関数(番号だけ入る)も、束縛の文の先として絞らない。
    record = tree.record("builder", tables_first=True)
    _replace(
        tree.tables, 'def _build():\n    return {"k": 1}', 'def _build():\n    return {"k": 10}'
    )
    assert not tree.is_current(record), (
        "名で先に辿った、import の時に表を作る関数を替えたのに古いと判じない"
    )
