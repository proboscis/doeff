"""Failure cases for a macro record that covers only the code of functions actually run during expansion (child of #3769 -- the daily 10-12 04:00 rebuild of 1,197 items).

The record digest still includes everything the closure walk reached (function names, class member names, values, binding statements) as before, but narrows the contents of
function code (:attr:`doeff_hy_bytecode_guard._macro_use.CodeFacts.shape`) to the functions run during that module's expansion
(:mod:`doeff_hy_bytecode_guard._called_code` picks up code whose execution started during expansion). A change to the code of a method that is not called
is not judged stale, while a change to a method that is called is judged stale as before. Functions run outside expansion (at import time) that produced a value
(functions called or rewritten by binding statements) and code that reads a function's contents as a value (docstring, signature) are not narrowed.

The record is made through the same entry as import (``source_to_code_as_import``) and checked with ``record_is_current_here`` (same as test_macro_use_record.py).
"""

import importlib
import importlib.machinery
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from doeff_hy_bytecode_guard import _records as records
from doeff_hy_bytecode_guard._loader_hooks import (
    loaded_record,
    record_is_current_here,
    source_to_code_as_import,
)

#: The macro module. rewrites = if the argument is true, call the Rewriter assign, else walk (whether defk goes through _Walker._assign changes with the presence of :=)
#: reads-doc = reads the docstring of a function it does not call; reads-signature = reads the signature of a function it does not call; builds = calls a function that looks up a table,
#: looks up by name (without calling) the function that builds the table (called at import time); builds-plainly = calls only the function that looks up the table.
MACROS = """\
(import {pkg}.tables :as tables)
(import inspect)
(defclass Rewriter []
  (defn walk [self x] (+ x 1))
  (defn assign [self x] (* x 2)))
(defmacro rewrites [assign] (if assign (.assign (Rewriter) 1) (.walk (Rewriter) 1)))
(defn documented-helper [] "doc one" 1)
(defmacro reads-doc [] (len (. documented-helper __doc__)))
(defn sig-helper [a] a)
(defmacro reads-signature [] (len (. (inspect.signature sig-helper) parameters)))
(defmacro builds [] (if (is tables._build None) 0 (tables.built "k")))
(defmacro builds-plainly [] (tables.plain-built "k"))
"""

#: Tables the macro reads at expansion time (a Python module). BUILT and PLAIN are made by functions called at import time (not called during expansion).
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
    # A hy.R call that does not go through the table (the record picks up macros from the source statements).
    "hy_r_plain": "(setv value (hy.R.{pkg}/macros.rewrites 0))\n",
}


@dataclass(frozen=True)
class Tree:
    """A check package in a temporary dir placed on sys.path (the name differs per check)."""

    package: str
    directory: Path

    @property
    def macros(self) -> Path:
        return self.directory / "macros.hy"

    @property
    def tables(self) -> Path:
        return self.directory / "tables.py"

    def forget(self) -> None:
        """Remove the check package's modules from sys.modules (as in the next run, so the next time the macro module is re-read)."""
        for name in [
            n for n in sys.modules if n == self.package or n.startswith(f"{self.package}.")
        ]:
            del sys.modules[name]

    def record(self, user: str, *, tables_first: bool = False) -> records.MacroRecord:
        """Compile the user module through the same entry as import and return the record attached to the code. tables_first = read the table module first
        (so the table-building function runs outside expansion -- the shape in a long-running process where another module read it first)."""
        if tables_first:
            importlib.import_module(f"{self.package}.tables")
        path = self.directory / f"{user}.hy"
        loader = importlib.machinery.SourceFileLoader(f"{self.package}.{user}", str(path))
        code = source_to_code_as_import(loader, path.read_bytes(), str(path))
        record = records.record_of(code)
        assert record is not None, "no record on the compiled code"
        self.forget()
        return record

    def is_current(self, record: records.MacroRecord) -> bool:
        """Check the record against the current environment (the re-read macro module)."""
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
    """Replace old with new in the file (the size changes too -- the file's sha256 memo is keyed by path, mtime and size)."""
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    assert len(old) != len(new)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_an_imported_module_exposes_the_record_of_its_expansion(tree: Tree) -> None:
    # The record cache for a Hy test file item (doeff-adr's item_cache) takes the record of macros used by the expansion of the imported file
    # from the reader entry's memo (loaded_record), saves it, and checks it with record_is_current_here (child of #3769).
    module = importlib.import_module(f"{tree.package}.plain")
    record = loaded_record(str(module.__file__))
    assert record is not None, "cannot get the expansion record of the imported Hy module"
    assert [row.name for row in record.macros] == ["rewrites"], record.macros
    assert record_is_current_here(record), "judged stale although nothing changed"


def test_a_record_is_current_while_nothing_changed(tree: Tree) -> None:
    for user in USERS:
        record = tree.record(user)
        assert tree.is_current(record), f"{user}: judged stale although nothing changed"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring, which picks up code called during expansion, is Python 3.12+ -- the 3.11 record cannot pick it up and takes broadly (called_code.Uncollectable)")
def test_a_changed_method_the_expansion_does_not_call_keeps_the_record_current(tree: Tree) -> None:
    # Red before the fix: the closure walk traversed the class (Rewriter) with every method and put the code of assign, which is not called in expansion, into the digest.
    record = tree.record("plain")
    _replace(tree.macros, "(* x 2)", "(* x 22)")
    assert tree.is_current(record), "judged stale just by changing a method not called during expansion"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring, which picks up code called during expansion, is Python 3.12+ -- the 3.11 record cannot pick it up and takes broadly (called_code.Uncollectable)")
def test_a_changed_method_a_hy_r_call_does_not_reach_keeps_the_record_current(tree: Tree) -> None:
    # A macro called via hy.R also covers only the functions run during expansion (the entry that makes the record goes through the place that picks up hy.R calls from the source statements).
    record = tree.record("hy_r_plain")
    assert [row.name for row in record.macros] == ["rewrites"], record.macros
    _replace(tree.macros, "(* x 2)", "(* x 22)")
    assert tree.is_current(record), "judged stale just by changing a method not called during the hy.R expansion"


def test_a_changed_method_the_expansion_calls_is_stale(tree: Tree) -> None:
    # When it should go cold (contrast): a user that calls assign in expansion is stale under the same change.
    record = tree.record("assigning")
    _replace(tree.macros, "(* x 2)", "(* x 22)")
    assert not tree.is_current(record), "not judged stale although the method called in expansion was changed"


def test_a_method_added_beside_the_called_one_is_stale(tree: Tree) -> None:
    # When it should go cold: class member names go in as before (how names are looked up can change -- an added name can cover the name being called, etc.).
    record = tree.record("plain")
    _replace(
        tree.macros,
        "(defn assign [self x] (* x 2))",
        "(defn assign [self x] (* x 2))\n  (defn added [self] 0)",
    )
    assert not tree.is_current(record), "not judged stale although a member was added to the class of the called method"


def test_a_changed_docstring_of_an_uncalled_function_the_macro_reads_is_stale(tree: Tree) -> None:
    # When it should go cold: expansion reads the docstring (__doc__) of a function it does not call as a value -- the closure of code that reads a function's contents is not narrowed.
    record = tree.record("doc_reader")
    _replace(tree.macros, '"doc one"', '"doc two and more"')
    assert not tree.is_current(record), "not judged stale although the docstring of a function read by expansion was changed"


def test_a_changed_signature_of_an_uncalled_function_the_macro_reads_is_stale(tree: Tree) -> None:
    # When it should go cold: expansion reads the signature (inspect.signature) of a function it does not call.
    record = tree.record("signature_reader")
    _replace(tree.macros, "(defn sig-helper [a] a)", "(defn sig-helper [a b] a)")
    assert not tree.is_current(record), "not judged stale although the signature of a function read by expansion was changed"


def test_a_changed_function_that_built_the_table_at_import_is_stale(tree: Tree) -> None:
    # When it should go cold: the table-building function (_build_plain) is called when the table module is read, not during expansion. The table (an untraceable dict) is
    # covered by the binding statement, and the code of the function the statement calls is not narrowed.
    record = tree.record("plain_builder", tables_first=True)
    _replace(
        tree.tables,
        'def _build_plain():\n    return {"k": 1}',
        'def _build_plain():\n    return {"k": 10}',
    )
    assert not tree.is_current(record), "not judged stale although the import-time table-building function was changed"


def test_a_changed_function_that_built_the_table_is_stale_when_first_reached_by_name(
    tree: Tree,
) -> None:
    # When it should go cold: the macro first looks up (without calling) the table-building function (_build) by name and traverses it, and later the table binding statement calls the same function. The function
    # met the second time (only its number goes in) is also not narrowed as the target of a binding statement.
    record = tree.record("builder", tables_first=True)
    _replace(
        tree.tables, 'def _build():\n    return {"k": 1}', 'def _build():\n    return {"k": 10}'
    )
    assert not tree.is_current(record), (
        "not judged stale although the import-time table-building function, traversed first by name, was changed"
    )
