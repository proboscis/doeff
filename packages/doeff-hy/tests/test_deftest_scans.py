"""deftest の meta の ``:scans`` — そのテストが走査して読む dir か glob の宣言(agora-redesign #3874・親 #3873)。

選びの道具(agora-controllers の land_focus_gate)は file を Hy の reader で読んで ``:scans`` を引き、変えた path が glob に
当たればそのテストを選ぶ。展開は宣言を pytest の印 ``scans`` として関数に付け、item の記録(#1211)にも印の名を残す。
形は展開の時に検める: 文字列の tuple 以外は断る(黙って捨てると、宣言したつもりのテストが選ばれない)。
"""

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import hy
import pytest
from doeff_hy.pytest_items import FunctionItem, Mark, decode_records, module_record_texts


@pytest.fixture
def hy_dir(tmp_path: Path) -> Iterator[Path]:
    """test module を置いて import できる dir(終わったら import の路と sys.modules から外す)。"""
    sys.path.insert(0, str(tmp_path))
    yield tmp_path
    sys.path.remove(str(tmp_path))
    for name, module in list(sys.modules.items()):
        if str(tmp_path) in (getattr(module, "__file__", None) or ""):
            del sys.modules[name]


def _imported(root: Path, name: str, source: str) -> object:
    """source を ``<name>.hy`` に置いて import する。"""
    (root / f"{name}.hy").write_text(source)
    importlib.invalidate_caches()
    return importlib.import_module(name)


def _marks(function: object, name: str) -> list[pytest.Mark]:
    """関数に付いた pytest の印のうち、名が name の物。"""
    return [mark for mark in getattr(function, "pytestmark", []) if mark.name == name]


DECLARED = """
(require doeff-hy.macros [deftest])
(deftest test-scanning {:scans #("a/*.txt" "b/**/*.hy")} (assert True))
(deftest test-plain (assert True))
"""


def test_the_scans_are_a_pytest_mark_with_the_globs_and_are_recorded_by_name(hy_dir: Path) -> None:
    """``:scans`` の glob は印 ``scans`` の引数に同じ順で載り、item の記録には印の名が残る。"""
    module = _imported(hy_dir, "test_scans_declared", DECLARED)
    (scans,) = _marks(module.test_scanning, "scans")
    assert scans.args == ("a/*.txt", "b/**/*.hy")
    records = decode_records(module_record_texts(module))
    assert FunctionItem("test_scanning", ("doeff_interpreter",), (Mark("scans"),)) in records


def test_a_test_without_scans_has_no_scans_mark(hy_dir: Path) -> None:
    """``:scans`` の無い deftest は今までどおり(印を持たない)。"""
    module = _imported(hy_dir, "test_scans_plain", DECLARED)
    assert _marks(module.test_plain, "scans") == []


@pytest.mark.parametrize(
    "written",
    ['"a/*.txt"', '["a/*.txt"]', "#(1)", "#()", '#("")'],
    ids=["one-string", "list", "not-a-string", "empty", "empty-string"],
)
def test_scans_that_are_not_a_tuple_of_globs_are_refused_at_expansion(hy_dir: Path, written: str) -> None:
    """文字列 1 つ・list・文字列でない要素・空の tuple・空の文字列は、展開の時に鍵の名と受ける形を名指して断る。"""
    source = f"(require doeff-hy.macros [deftest])\n(deftest test-bad {{:scans {written}}} (assert True))\n"
    with pytest.raises(hy.errors.HyMacroExpansionError, match=r":scans.*文字列の tuple"):
        _imported(hy_dir, f"test_scans_bad_{abs(hash(written))}", source)


def test_the_scans_mark_is_registered_by_the_deftest_plugin(pytestconfig: pytest.Config) -> None:
    """印 ``scans`` は deftest を集める plugin(doeff-adr)が登録する — どの repo でも知らない印の警告を出さない。"""
    assert any(line.startswith("scans(") for line in pytestconfig.getini("markers"))
