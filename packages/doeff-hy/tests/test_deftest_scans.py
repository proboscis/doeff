"""The deftest meta ``:scans`` -- a declaration of the dirs or globs that the test scans and reads (parent #3873).

The selection tool (land_focus_gate in agora-controllers) reads the file with the Hy reader, takes ``:scans``, and selects the test if a changed path matches a glob.
Expansion attaches the declaration to the function as the pytest marker ``scans`` and leaves the marker name in the item record (#1211) too.
The shape is checked at expansion time: anything other than a tuple of strings is refused (silently dropping it would leave a test that was meant to be declared unselected).
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


def _marks(function: object, name: str) -> list[pytest.Mark]:
    """Of the pytest markers attached to the function, those whose name is name."""
    return [mark for mark in getattr(function, "pytestmark", []) if mark.name == name]


DECLARED = """
(require doeff-hy.macros [deftest])
(deftest test-scanning {:scans #("a/*.txt" "b/**/*.hy")} (assert True))
(deftest test-plain (assert True))
"""


def test_the_scans_are_a_pytest_mark_with_the_globs_and_are_recorded_by_name(hy_dir: Path) -> None:
    """The globs of ``:scans`` are carried in the same order as arguments of the ``scans`` marker, and the marker name remains in the item record."""
    module = _imported(hy_dir, "test_scans_declared", DECLARED)
    (scans,) = _marks(module.test_scanning, "scans")
    assert scans.args == ("a/*.txt", "b/**/*.hy")
    records = decode_records(module_record_texts(module))
    assert FunctionItem("test_scanning", ("doeff_interpreter",), (Mark("scans"),)) in records


def test_a_test_without_scans_has_no_scans_mark(hy_dir: Path) -> None:
    """A deftest without ``:scans`` is as before (has no marker)."""
    module = _imported(hy_dir, "test_scans_plain", DECLARED)
    assert _marks(module.test_plain, "scans") == []


@pytest.mark.parametrize(
    "written",
    ['"a/*.txt"', '["a/*.txt"]', "#(1)", "#()", '#("")'],
    ids=["one-string", "list", "not-a-string", "empty", "empty-string"],
)
def test_scans_that_are_not_a_tuple_of_globs_are_refused_at_expansion(hy_dir: Path, written: str) -> None:
    """A single string, a list, a non-string element, an empty tuple and an empty string are refused at expansion time, naming the key and the accepted shape."""
    source = f"(require doeff-hy.macros [deftest])\n(deftest test-bad {{:scans {written}}} (assert True))\n"
    with pytest.raises(hy.errors.HyMacroExpansionError, match=r":scans.*tuple of strings"):
        _imported(hy_dir, f"test_scans_bad_{abs(hash(written))}", source)


def test_the_scans_mark_is_registered_by_the_deftest_plugin(pytestconfig: pytest.Config) -> None:
    """The ``scans`` marker is registered by the plugin that collects deftests (doeff-adr) -- no unknown-marker warning in any repo."""
    assert any(line.startswith("scans(") for line in pytestconfig.getini("markers"))
