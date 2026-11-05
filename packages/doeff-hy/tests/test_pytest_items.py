"""The pytest item record (doeff_hy/_pytest_items.py) that item-making macros push onto a module through an expansion expression.

The record enters the module as an expansion expression, so it can be checked by importing the test module and reading ``__doeff_pytest_items__``
(relying on neither the Hy importer nor pyc).
"""

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from doeff_hy import MalformedRecord
from doeff_hy._pytest_items import (
    Dynamic,
    FunctionItem,
    LiteralValue,
    Mark,
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
    """A dir where a test module can be placed and imported (afterwards removed from the import path and sys.modules)."""
    sys.path.insert(0, str(tmp_path))
    yield tmp_path
    sys.path.remove(str(tmp_path))
    for name, module in list(sys.modules.items()):
        if str(tmp_path) in (getattr(module, "__file__", None) or ""):
            del sys.modules[name]


def _records(root: Path, name: str, source: str) -> list[Record]:
    """Place source at ``<name>.hy``, import it, and read the record that the expansion expression pushed onto the module."""
    (root / f"{name}.hy").write_text(source)
    importlib.invalidate_caches()
    return decode_records(module_record_texts(importlib.import_module(name)))


def test_deftest_records_each_function_in_pytest_terms(hy_dir: Path) -> None:
    """The deftest keys (:marks, :params, :interpreters, :skip-if) are recorded in a form translated into pytest terms."""
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
    """A parametrize call directly under a module changes the number of items, so it becomes Dynamic, which is not decided at expansion time."""
    records = _records(
        hy_dir,
        "test_marks_b",
        '(require doeff-hy.macros [val])\n(import pytest)\n(val pytestmark (pytest.mark.parametrize "x" [1 2]))\n',
    )
    assert [type(r) for r in records] == [Dynamic]


def test_module_mark_calls_are_recorded_by_name(hy_dir: Path) -> None:
    """A skipif call directly under a module is recorded by name (the plugin evaluates the condition at setup from the real marker)."""
    records = _records(
        hy_dir,
        "test_marks_c",
        "(require doeff-hy.macros [val])\n(import os pytest)\n"
        '(val pytestmark [pytest.mark.slow (pytest.mark.skipif (not (os.getenv "X")) :reason "r")])\n',
    )
    assert records == [ModuleMarks(("slow", "skipif"))]


def test_a_module_without_item_macros_has_no_records(hy_dir: Path) -> None:
    """A module that does not use a record-making macro has an empty record."""
    assert _records(hy_dir, "plain_d", "(setv x 1)\n") == []


def test_malformed_record_is_refused() -> None:
    """A record with a different shape (written by a doeff-hy of a different version, etc.) stops with MalformedRecord (not silently made empty)."""
    with pytest.raises(MalformedRecord):
        decode_records(['{"unknown": 1}'])


def test_two_meta_maps_after_the_name_are_refused(hy_dir: Path) -> None:
    """Failure case: when two meta maps follow the name, the second silently became a body expression and its keys (:skip-if) were
    dropped (an agora check added a marker map before the existing meta, the skip vanished and the daily run hit KeyError -- #2704). At expansion time the dropped
    keys are named and refused. A form that combines the meta into one map still passes as before."""
    split = (
        "(require doeff-hy.macros [deftest])\n"
        '(deftest test-split {:marks ["real_world"]} {:skip-if (= 1 1) :skip-reason "never"} (assert False))\n'
    )
    with pytest.raises(Exception, match=r"two meta maps in a row.*:skip-if.*:skip-reason"):
        _records(hy_dir, "probe_two_meta_maps", split)
    merged = (
        "(require doeff-hy.macros [deftest])\n"
        '(deftest test-merged {:marks ["real_world"] :skip-if (= 1 1) :skip-reason "never"} (assert False))\n'
    )
    assert [record.name for record in _records(hy_dir, "probe_one_meta_map", merged)] == ["test_merged"]
