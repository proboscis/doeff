"""item を作る macro が展開の式で module に積む pytest の item の記録(doeff_hy/pytest_items.py・agora-redesign #1291)。

記録は展開の式として module に入るので、test module を import して ``__doeff_pytest_items__`` を読めば確かめられる
(Hy の importer にも pyc にも頼らない — #1290 の利用者の決定 "not touch hy at all")。
"""

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from doeff_hy.pytest_items import (
    Dynamic,
    FunctionItem,
    LiteralValue,
    Mark,
    MalformedRecord,
    ModuleMarks,
    OpaqueValue,
    Parametrize,
    Record,
    SkipIf,
    decode_records,
    module_record_texts,
)

CORPUS = """
(require doeff-hy.macros [deftest val])
(import pytest)
(val pytestmark pytest.mark.real-world)
(val RIGS [1 2])
(deftest test-plain (assert True))
(deftest test-marked {:marks ["slow" "e2e"]} (assert True))
(deftest test-params [x y] {:params {"x" ["a" 1 2.5 True None] "y" [[1 2] :k]}} (assert True))
(deftest test-interp {:interpreters ["a" "b"] :skip-if (= 1 2) :skip-reason "never"} (assert True))
(deftest test-dynamic [open-rig] {:params {"open_rig" RIGS}} (assert True))
"""


@pytest.fixture
def hy_dir(tmp_path: Path) -> Iterator[Path]:
    """test module を置いて import できる dir(終わったら import の路と sys.modules から外す)。"""
    sys.path.insert(0, str(tmp_path))
    yield tmp_path
    sys.path.remove(str(tmp_path))
    for name, module in list(sys.modules.items()):
        if str(tmp_path) in (getattr(module, "__file__", None) or ""):
            del sys.modules[name]


def _records(root: Path, name: str, source: str) -> list[Record]:
    """source を ``<name>.hy`` に置いて import し、展開の式が module に積んだ記録を読む。"""
    (root / f"{name}.hy").write_text(source)
    importlib.invalidate_caches()
    return decode_records(module_record_texts(importlib.import_module(name)))


def test_deftest_records_each_function_in_pytest_terms(hy_dir: Path) -> None:
    """deftest の鍵(:marks・:params・:interpreters・:skip-if)は、pytest の言葉に直した形で記録される。"""
    assert _records(hy_dir, "test_corpus_a", CORPUS) == [
        ModuleMarks(("real_world",)),
        FunctionItem("test_plain", ("doeff_interpreter",), ()),
        FunctionItem("test_marked", ("doeff_interpreter",), (Mark("slow"), Mark("e2e"))),
        FunctionItem(
            "test_params",
            ("doeff_interpreter", "x", "y"),
            (
                Parametrize(
                    "x",
                    (
                        LiteralValue("a"),
                        LiteralValue(1),
                        LiteralValue(2.5),
                        LiteralValue(True),
                        LiteralValue(None),
                    ),
                ),
                Parametrize("y", (OpaqueValue(), OpaqueValue())),
            ),
        ),
        FunctionItem(
            "test_interp",
            ("doeff_interpreter",),
            (Parametrize("doeff_interpreter_name", (LiteralValue("a"), LiteralValue("b"))), SkipIf()),
        ),
        Dynamic("test_dynamic", "values 'RIGS"),
    ]


def test_module_marks_that_shape_collection_are_dynamic(hy_dir: Path) -> None:
    """module の直下の parametrize の呼び出しは item の本数を変えるので、展開の時に決まらない Dynamic になる。"""
    records = _records(
        hy_dir,
        "test_marks_b",
        '(require doeff-hy.macros [val])\n(import pytest)\n(val pytestmark (pytest.mark.parametrize "x" [1 2]))\n',
    )
    assert [type(r) for r in records] == [Dynamic]


def test_module_mark_calls_are_recorded_by_name(hy_dir: Path) -> None:
    """module の直下の skipif の呼び出しは名で記録される(条件は plugin が setup で実物の印から評価する)。"""
    records = _records(
        hy_dir,
        "test_marks_c",
        "(require doeff-hy.macros [val])\n(import os pytest)\n"
        '(val pytestmark [pytest.mark.slow (pytest.mark.skipif (not (os.getenv "X")) :reason "r")])\n',
    )
    assert records == [ModuleMarks(("slow", "skipif"))]


def test_a_module_without_item_macros_has_no_records(hy_dir: Path) -> None:
    """記録を作る macro を使わない module は、記録が空。"""
    assert _records(hy_dir, "plain_d", "(setv x 1)\n") == []


def test_malformed_record_is_refused() -> None:
    """記録の形が違う(版の違う doeff-hy が書いた等)は MalformedRecord で止める(黙って空にしない)。"""
    with pytest.raises(MalformedRecord):
        decode_records(['{"unknown": 1}'])


def test_two_meta_maps_after_the_name_are_refused(hy_dir: Path) -> None:
    """失敗ケース(agora-redesign #2726): 名の後に meta の map が 2 つ並ぶと、2 つ目は黙って本文の式になり、その鍵(:skip-if)が
    捨てられていた(agora の検で印の map を既存の meta の前に足し、skip が消えて日次で KeyError — #2704)。展開の時に、捨てられる
    鍵を名指して断る。meta を 1 つの map にまとめた形は今どおり通る。"""
    split = (
        "(require doeff-hy.macros [deftest])\n"
        '(deftest test-split {:marks ["real_world"]} {:skip-if (= 1 1) :skip-reason "never"} (assert False))\n'
    )
    with pytest.raises(Exception, match=r"meta の map が 2 つ並んでいる.*:skip-if.*:skip-reason"):
        _records(hy_dir, "probe_two_meta_maps", split)
    merged = (
        "(require doeff-hy.macros [deftest])\n"
        '(deftest test-merged {:marks ["real_world"] :skip-if (= 1 1) :skip-reason "never"} (assert False))\n'
    )
    assert [record.name for record in _records(hy_dir, "probe_one_meta_map", merged)] == ["test_merged"]
