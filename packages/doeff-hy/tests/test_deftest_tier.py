"""The deftest meta ``:tier`` -- the test's size class (small, medium or large).

Expansion attaches the tier as the pytest marker ``tier_<tier>`` and leaves the marker name in the item record (#1211), so the
``--doeff-tier`` option of doeff-hy-pytest reads it at collection without importing the module. A test without ``:tier`` has no
tier marker (the plugin gives it the default tier). Any other value is refused at expansion time: a misspelt tier read as the
default would put a slow test into the fast set.
"""

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import hy
import pytest
from doeff_hy._pytest_items import FunctionItem, Mark, decode_records, module_record_texts


@pytest.fixture
def hy_dir(tmp_path: Path) -> Iterator[Path]:
    """A dir where a test module can be placed and imported (afterwards removed from the import path and sys.modules)."""
    sys.path.insert(0, str(tmp_path))
    yield tmp_path
    sys.path.remove(str(tmp_path))
    for name, module in list(sys.modules.items()):
        if str(tmp_path) in (getattr(module, "__file__", None) or ""):
            del sys.modules[name]


def _imported(root: Path, name: str, source: str) -> object:
    """Place source at ``<name>.hy`` and import it."""
    (root / f"{name}.hy").write_text(source)
    importlib.invalidate_caches()
    return importlib.import_module(name)


def _mark_names(function: object) -> list[str]:
    return [mark.name for mark in getattr(function, "pytestmark", [])]


DECLARED = """
(require doeff-hy.macros [deftest])
(deftest test-medium {:tier "medium" :marks ["slow"]} (assert True))
(deftest test-plain (assert True))
"""


def test_the_tier_is_a_mark_name_and_is_recorded(hy_dir: Path) -> None:
    """``:tier "medium"`` becomes the marker ``tier_medium`` next to the other marks, and the item record keeps it."""
    module = _imported(hy_dir, "test_tier_declared", DECLARED)
    assert sorted(_mark_names(module.test_medium)) == ["slow", "tier_medium"]
    records = decode_records(module_record_texts(module))
    (item,) = [r for r in records if isinstance(r, FunctionItem) and r.name == "test_medium"]
    assert Mark("tier_medium") in item.decorators


def test_a_test_without_tier_has_no_tier_mark(hy_dir: Path) -> None:
    module = _imported(hy_dir, "test_tier_plain", DECLARED)
    assert not [name for name in _mark_names(module.test_plain) if name.startswith("tier_")]


@pytest.mark.parametrize("written", ['"huge"', ":medium", '["small"]', "1"], ids=["unknown", "keyword", "list", "number"])
def test_a_tier_that_is_not_one_of_the_three_strings_is_refused_at_expansion(hy_dir: Path, written: str) -> None:
    source = f"(require doeff-hy.macros [deftest])\n(deftest test-bad {{:tier {written}}} (assert True))\n"
    with pytest.raises(hy.errors.HyMacroExpansionError, match=r":tier must be one of"):
        _imported(hy_dir, f"test_tier_bad_{abs(hash(written))}", source)
