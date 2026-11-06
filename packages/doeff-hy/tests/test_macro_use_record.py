"""Failure cases that check a Hy module's macro record (doeff_hy_bytecode_guard) per macro used by the expansion.

A record is judged stale only when "the macros the expansion actually used, and what those macros walk at run time" change. The first half
covers cases that must go cold (changing a used macro, the helper it calls, the constants it reads, or a target read without going by name makes it stale); the second half covers
cases that need not go cold (changing an unused macro, or merely shifting lines, does not make it stale).

The record is made through the same entry point as import (``source_to_code_as_import`` — compile inside the module's place and add the record to the code),
and checked with ``record_is_current_here`` (the single check used by .pyc, the analyzer, and the type-check expansion cache). To match the next run (a different process),
the check package's modules are removed from sys.modules before checking, forcing the macro modules to be re-read.
"""

import importlib.machinery
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from doeff_hy_bytecode_guard import _records as records
from doeff_hy_bytecode_guard._loader_hooks import record_is_current_here, source_to_code_as_import

#: Macro module. used = a macro that reads a helper function (same module) and a constant of another module; unused = a macro nobody uses;
#: reads = a macro that calls a helper reading its own module's globals by string key; peeks = a macro that calls a helper reading another module's value via getattr;
#: hands = a macro that passes another module as an argument, which the receiving helper reads via getattr; documented = a macro that reads another module's constant and writes
#: a function's __doc__ into the expansion result ("__doc__" appears among the code's string constants — the shape of defhandler); reads-data = a macro whose helper reads and returns
#: a neighboring file (data.txt) from its own module's location (__file__); slot-reads = a macro reading a field of a class with ``__slots__``; record-reads = a macro reading
#: a field of a defrecord type (slots, and the shared record functions that read ``vars(cls)``); class-reads = a macro whose helper reads a passed class by ``vars(cls)``;
#: vars-reads = a macro whose helper reads its own module's namespace by ``vars()`` with a key it computes.
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
(import pathlib)
(defn data-of [] (.read-text (/ (. (pathlib.Path __file__) parent) "data.txt")))
(defmacro reads-data [] (data-of))
(import {pkg}.slotted :as slotted)
(import {pkg}.records :as recs)
(import {pkg}.inspects :as inspects)
(defn by-vars [] (get (vars) (.join "" ["LIM" "IT"])))
(defmacro slot-reads [] (slotted.left-of))
(defmacro record-reads [] (recs.width-of))
(defmacro class-reads [] (inspects.box-size))
(defmacro vars-reads [] (by-vars))
"""

CONSTS = '"Constants module."\n(setv OFFSET 1)\n(setv HIDDEN 5)\n'

#: A table of globals (Python module) that a macro reads at expansion time. TABLE gets rows added by a top-level statement and by a registration function called at import time.
#: OTHER and unrelated cannot be reached from the closure.
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

#: A copy of an extension module (made from Python source but with __file__ pointing at a .so — to the record it looks like an extension module with no source).
FAKEEXT = """\
import pathlib


class Thing:
    KIND = "a"

    def run(self):
        return 1


__file__ = str(pathlib.Path(__file__).with_name("fakeext.cpython-test.so"))
"""

#: A class with ``__slots__`` (its fields are member descriptors the interpreter makes from the names) and a helper that reads one field.
SLOTTED = """\
class Pair:
    __slots__ = ("left",)

    def __init__(self, left):
        self.left = left


def left_of():
    return Pair(3).left
"""

#: A defrecord type (slots, and ``__setattr__`` / ``__delattr__`` from doeff_hy's record runtime) and a helper that reads one field.
RECORDS = """\
(require doeff-hy.record [defrecord])
(defrecord Opts
  #^ int width)
(defn width-of [] (. (Opts :width 4) width))
"""

#: A helper that reads a passed class's namespace by ``vars(cls)`` (the shape of the record functions of _record_runtime).
INSPECTS = '''\
"""Inspects module."""


class Box:
    size = 2


def size_of(cls):
    return vars(cls)["size"]


def box_size():
    return size_of(Box)
'''

CONTEXTS = """\
from contextvars import ContextVar

MODE = ContextVar("mode", default="plain")
LATER = 1
"""

#: Consumer modules (name -> source). user = requires only used by name; shadowed = requires all of the macro module and
#: calls its own function shadow (if a macro of the same name is later added to the macro module, the expansion changes); local = the same shape as shadowed
#: but written with a require inside a function, using the macro in a nested function (Hy expansion looks up a function-local macro table before the module's table).
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
    "datareader": "(require {pkg}.macros [reads-data])\n(setv value (reads-data))\n",
    "slotreader": "(require {pkg}.macros [slot-reads])\n(setv value (slot-reads))\n",
    "recordreader": "(require {pkg}.macros [record-reads])\n(setv value (record-reads))\n",
    "classreader": "(require {pkg}.macros [class-reads])\n(setv value (class-reads))\n",
    "varsreader": "(require {pkg}.macros [vars-reads])\n(setv value (vars-reads))\n",
    "local": "(defn run []\n  (require {pkg}.macros *)\n  (defn shadow [x] x)\n  (defn inner [] (shadow (used)))\n  (inner))\n"
    "(setv value (run))\n",
}


@dataclass(frozen=True)
class Tree:
    """A check package in a temporary dir placed on sys.path (the name differs per check — it does not mix with already-loaded modules)."""

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
        """Remove the check package's modules from sys.modules (so the next load re-reads the macro modules, as the next run would)."""
        for name in [n for n in sys.modules if n == self.package or n.startswith(f"{self.package}.")]:
            del sys.modules[name]

    def record(self, user: str) -> records.MacroRecord:
        """Compile a consumer module through the same entry point as import and return the record carried on the code."""
        path = self.directory / f"{user}.hy"
        loader = importlib.machinery.SourceFileLoader(f"{self.package}.{user}", str(path))
        code = source_to_code_as_import(loader, path.read_bytes(), str(path))
        record = records.record_of(code)
        assert record is not None, "the compiled code has no record"
        self.forget()
        return record

    def is_current(self, record: records.MacroRecord) -> bool:
        """Check the record in the current environment (with the macro modules re-read)."""
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
    (directory / "slotted.py").write_text(SLOTTED, encoding="utf-8")
    (directory / "records.hy").write_text(RECORDS, encoding="utf-8")
    (directory / "inspects.py").write_text(INSPECTS, encoding="utf-8")
    (directory / "data.txt").write_text("one", encoding="utf-8")
    for name, text in USERS.items():
        (directory / f"{name}.hy").write_text(text.format(pkg=package), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    made = Tree(package, directory)
    yield made
    made.forget()


def _replace(path: Path, old: str, new: str) -> None:
    """Replace old with new in a file (also changing its size — the file sha256 memo is keyed by path, mtime, and size)."""
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    assert len(old) != len(new)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---- When it must go cold ------------------------------------------------------------------------------------------------


def test_a_record_is_current_while_nothing_changed(tree: Tree) -> None:
    record = tree.record("user")
    assert tree.is_current(record), "judged stale although nothing changed"


def test_a_changed_body_of_the_used_macro_is_stale(tree: Tree) -> None:
    record = tree.record("user")
    _replace(tree.macros, "(+ (helper 1) consts.OFFSET)", "(+ (helper 20) consts.OFFSET)")
    assert not tree.is_current(record), "not judged stale although the body of a used macro changed"


def test_a_changed_helper_the_used_macro_calls_is_stale(tree: Tree) -> None:
    record = tree.record("user")
    _replace(tree.macros, "(* x FACTOR)", "(* x FACTOR 2)")
    assert not tree.is_current(record), "not judged stale although the body of a helper function called by a used macro changed"


def test_a_changed_constant_the_helper_reads_is_stale(tree: Tree) -> None:
    record = tree.record("user")
    _replace(tree.macros, "(setv FACTOR 3)", "(setv FACTOR 30)")
    assert not tree.is_current(record), "not judged stale although a constant of the macro module read by the helper changed"


def test_a_changed_constant_of_another_module_the_used_macro_reads_is_stale(tree: Tree) -> None:
    record = tree.record("user")
    _replace(tree.consts, "(setv OFFSET 1)", "(setv OFFSET 10)")
    assert not tree.is_current(record), "not judged stale although a constant of another module read by a used macro changed"


def test_a_changed_value_a_helper_reads_by_a_string_key_is_stale(tree: Tree) -> None:
    record = tree.record("reader")
    _replace(tree.macros, "(setv LIMIT 7)", "(setv LIMIT 70)")
    assert not tree.is_current(record), "not judged stale although a value the helper reads by string key changed"


def test_a_changed_value_a_helper_reads_by_getattr_is_stale(tree: Tree) -> None:
    record = tree.record("peeker")
    _replace(tree.consts, "(setv HIDDEN 5)", "(setv HIDDEN 50)")
    assert not tree.is_current(record), "not judged stale although a value of another module the helper reads via getattr changed"


def test_a_changed_value_read_by_getattr_on_a_module_passed_as_an_argument_is_stale(tree: Tree) -> None:
    # The shape where the reading helper and the module reference are in different functions (the module is passed as an argument).
    record = tree.record("hander")
    _replace(tree.consts, "(setv HIDDEN 5)", "(setv HIDDEN 50)")
    assert not tree.is_current(record), "not judged stale although a value read via getattr from a module passed as an argument changed"


def test_a_new_macro_named_like_a_called_function_is_stale(tree: Tree) -> None:
    # If a macro with the same name as a function called by a consumer that requires everything is added, that call turns into a macro expansion.
    record = tree.record("shadowed")
    _replace(tree.macros, "(defmacro unused [] 100)", "(defmacro unused [] 100)\n(defmacro shadow [x] 0)")
    assert not tree.is_current(record), "not judged stale although a macro with the name called by the consumer was added"


def test_a_changed_body_of_a_macro_required_inside_a_function_is_stale(tree: Tree) -> None:
    # Red before the fix: a macro from a require inside a function is looked up in the local table and does not go through the module table, so it was missing from the record.
    record = tree.record("local")
    _replace(tree.macros, "(+ (helper 1) consts.OFFSET)", "(+ (helper 20) consts.OFFSET)")
    assert not tree.is_current(record), "not judged stale although the body of a macro used via a require inside a function changed"


def test_a_new_macro_named_like_a_function_called_under_a_local_require_is_stale(tree: Tree) -> None:
    # A consumer that requires everything inside a function also changes expansion when a macro with the same name as a function it calls is added.
    record = tree.record("local")
    _replace(tree.macros, "(defmacro unused [] 100)", "(defmacro unused [] 100)\n(defmacro shadow [x] 0)")
    assert not tree.is_current(record), "not judged stale although a macro with the name called by a consumer using a require inside a function was added"


# ---- When it need not go cold --------------------------------------------------------------------------------------------


def test_a_changed_unused_macro_keeps_the_record_current(tree: Tree) -> None:
    # Red before the fix: the record was the sha256 of the macro module's file, so changing one line of an unused macro made it stale.
    record = tree.record("user")
    _replace(tree.macros, "(defmacro unused [] 100)", "(defmacro unused [] 1000)")
    assert tree.is_current(record), "judged stale merely because an unused macro changed"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring, which picks up code called during expansion, exists from Python 3.12 — on 3.11 the record cannot pick it up and takes a wide scope (called_code.Uncollectable)")
def test_a_changed_unused_macro_under_a_local_require_keeps_the_record_current(tree: Tree) -> None:
    # Even with a require of everything inside a function, only the macros the expansion used go into the record.
    record = tree.record("local")
    assert [used.name for used in record.macros] == ["used"], record.macros
    _replace(tree.macros, "(defmacro unused [] 100)", "(defmacro unused [] 1000)")
    assert tree.is_current(record), "judged stale merely because an unused macro changed, for a consumer using a require inside a function"


def test_a_line_shift_of_the_macro_module_keeps_the_record_current(tree: Tree) -> None:
    # Red before the fix: merely adding a blank line at the top (shifting only line numbers) made it stale.
    record = tree.record("user")
    tree.macros.write_text("\n" + tree.macros.read_text(encoding="utf-8"), encoding="utf-8")
    assert tree.is_current(record), "judged stale merely because line numbers shifted"


def test_a_function_named_like_an_unrequired_macro_keeps_the_record_current(tree: Tree) -> None:
    # Red before the fix (agora's records_turns.hy and intake_requests.hy were recompiled on every import): a consumer that required by selected names
    # calls a function with the same name as another macro (check) in the provider. The consumer table of a selective require does not include it, whatever
    # the provider has, so the record stays current right after it is made.
    record = tree.record("chooser")
    assert tree.is_current(record), "judged stale by a call to a function named like a macro that was not required selectively"


# ---- Cover untraceable values narrowly, not with the whole file -------------------------------------------------------
# When it must go cold (green even before the fix — what matters is that it stays green after).


def test_a_changed_table_the_macro_reads_is_stale(tree: Tree) -> None:
    record = tree.record("looker")
    _replace(tree.tables, 'TABLE = {"a": 1}', 'TABLE = {"a": 10}')
    assert not tree.is_current(record), "not judged stale although the contents of a global table read by a macro changed"


def test_a_changed_top_level_row_of_the_table_is_stale(tree: Tree) -> None:
    record = tree.record("looker")
    _replace(tree.tables, 'TABLE["b"] = 2', 'TABLE["b"] = 20')
    assert not tree.is_current(record), "not judged stale although a row added to the table by a top-level statement changed"


def test_a_changed_row_added_by_a_registering_function_is_stale(tree: Tree) -> None:
    # A row added to the table inside a function the closure cannot reach (a registration called at import time).
    record = tree.record("looker")
    _replace(tree.tables, 'TABLE["c"] = 3', 'TABLE["c"] = 30')
    assert not tree.is_current(record), "not judged stale although a row added to the table by a function called at import time changed"


def test_a_changed_hy_table_the_macro_reads_is_stale(tree: Tree) -> None:
    record = tree.record("hylooker")
    _replace(tree.macros, '(setv HY-TABLE {"a" 1})', '(setv HY-TABLE {"a" 10})')
    assert not tree.is_current(record), "not judged stale although the contents of a Hy global table read by a macro changed"


def test_a_changed_top_level_row_of_the_hy_table_is_stale(tree: Tree) -> None:
    record = tree.record("hylooker")
    _replace(tree.macros, '(setv (get HY-TABLE "b") 2)', '(setv (get HY-TABLE "b") 20)')
    assert not tree.is_current(record), "not judged stale although a row added to a Hy table by a top-level statement changed"


def test_a_new_public_attribute_of_an_extension_class_is_stale(tree: Tree) -> None:
    # Red before the fix: a class from an extension module was covered by the .so file's sha256, so it was judged current even if its public shape changed as long as the .so was the same
    # (here we add a class attribute without changing the contents of the .so copy).
    record = tree.record("thinger")
    _replace(tree.fakeext, '    KIND = "a"\n', '    KIND = "a"\n    EXTRA = 1\n')
    assert not tree.is_current(record), "not judged stale although the public attributes of an extension class increased"


def test_a_removed_public_attribute_of_an_extension_class_is_stale(tree: Tree) -> None:
    # Red before the fix (same reason as above — the contents of the .so copy do not change).
    record = tree.record("thinger")
    _replace(tree.fakeext, "    def run(self):\n        return 1\n", "    pass\n")
    assert not tree.is_current(record), "not judged stale although the public attributes of an extension class decreased"


def test_a_changed_default_of_a_context_variable_is_stale(tree: Tree) -> None:
    # Red before the fix: a ContextVar was covered by the file of the reading function's module (macros.hy), so changing the default in the defining module did not make it stale.
    record = tree.record("moder")
    _replace(tree.contexts, 'default="plain"', 'default="fancy!"')
    assert not tree.is_current(record), "not judged stale although the default of a ContextVar changed"


def test_a_changed_value_set_into_a_nested_row_is_stale(tree: Tree) -> None:
    # Red before the fix (a regression found in verification): rewriting a value inside a subscript result (assignment to a subscript of a subscript) was counted as a mere read.
    record = tree.record("looker")
    _replace(tree.tables, 'TABLE["n"]["x"] = 5', 'TABLE["n"]["x"] = 50')
    assert not tree.is_current(record), "not judged stale although a value that a function called at import time rewrites in a table row changed"


def test_a_changed_value_appended_to_a_nested_row_is_stale(tree: Tree) -> None:
    # Red before the fix: .append on a subscript result was counted as a mere read.
    record = tree.record("looker")
    _replace(tree.tables, 'TABLE["l"].append(7)', 'TABLE["l"].append(70)')
    assert not tree.is_current(record), "not judged stale although a value that a function called at import time appends to a list in the table changed"


def test_a_changed_value_set_through_get_is_stale(tree: Tree) -> None:
    # Red before the fix: assignment to a subscript on the result of a read-only method (.get) was counted as a mere read.
    record = tree.record("looker")
    _replace(tree.tables, 'TABLE.get("n")["y"] = 6', 'TABLE.get("n")["y"] = 60')
    assert not tree.is_current(record), "not judged stale although a value rewritten on the result of .get changed"


def test_a_changed_value_set_into_iterated_rows_is_stale(tree: Tree) -> None:
    # Red before the fix: elements of the iteration source of a for loop (.values()) are bound and may be rewritten.
    record = tree.record("looker")
    _replace(tree.tables, 'row["z"] = 8', 'row["z"] = 80')
    assert not tree.is_current(record), "not judged stale although a value rewritten while looping over table rows changed"


def test_a_changed_value_set_through_an_alias_is_stale(tree: Tree) -> None:
    # Red before the fix: a rewrite through a top-level alias (ALIAS = TABLE) was not counted as a rewrite of the table name.
    record = tree.record("looker")
    _replace(tree.tables, 'ALIAS["d"] = 4', 'ALIAS["d"] = 40')
    assert not tree.is_current(record), "not judged stale although a value rewriting the table through an alias changed"


def test_a_changed_value_set_through_a_hy_alias_is_stale(tree: Tree) -> None:
    # A Hy alias (a defn after (setv HY-ALIAS HY-TABLE) rewrites HY-ALIAS).
    record = tree.record("hylooker")
    _replace(tree.macros, '(setv (get HY-ALIAS "c") 3)', '(setv (get HY-ALIAS "c") 30)')
    assert not tree.is_current(record), "not judged stale although a value rewriting the table through a Hy alias changed"


# When it need not go cold (red before the fix).


def test_a_changed_unrelated_function_beside_the_table_keeps_the_record_current(tree: Tree) -> None:
    # Red before the fix: following a table (dict) covered it with the sha256 of that module's (tables.py) file.
    record = tree.record("looker")
    _replace(tree.tables, "    return OTHER\n", "    return OTHER, 1\n")
    assert tree.is_current(record), "judged stale merely because a function the closure cannot reach changed"


def test_a_changed_unrelated_global_beside_the_table_keeps_the_record_current(tree: Tree) -> None:
    record = tree.record("looker")
    _replace(tree.tables, 'OTHER = {"z": 0}', 'OTHER = {"z": 100}')
    assert tree.is_current(record), "judged stale merely because another global the closure cannot reach changed"


def test_a_changed_unused_macro_beside_a_hy_table_keeps_the_record_current(tree: Tree) -> None:
    # Red before the fix: following a Hy table (HY-TABLE in macros.hy) covered it with the sha256 of the macros.hy file.
    record = tree.record("hylooker")
    _replace(tree.macros, "(defmacro unused [] 100)", "(defmacro unused [] 1000)")
    assert tree.is_current(record), "judged stale merely because another line of the macro module that looks up the table by name changed"


def test_a_changed_unrelated_hy_global_beside_a_hy_table_keeps_the_record_current(tree: Tree) -> None:
    record = tree.record("hylooker")
    _replace(tree.macros, '(setv HY-OTHER {"z" 0})', '(setv HY-OTHER {"z" 100})')
    assert tree.is_current(record), "judged stale merely because another global of the macro module that looks up the table by name changed"


def test_a_changed_binary_of_an_extension_with_the_same_shape_keeps_the_record_current(tree: Tree) -> None:
    # Red before the fix: a class from an extension module was covered by the .so file's sha256.
    record = tree.record("thinger")
    tree.fakeext_binary.write_bytes(b"binary two, rebuilt")
    assert tree.is_current(record), "judged stale merely because only the contents of an extension .so with the same public shape changed"


def test_a_changed_unrelated_line_beside_a_context_variable_keeps_the_record_current(tree: Tree) -> None:
    record = tree.record("moder")
    _replace(tree.contexts, "LATER = 1", "LATER = 100")
    assert tree.is_current(record), "judged stale merely because another line of the ContextVar module changed"


def test_a_changed_unused_macro_beside_a_context_variable_keeps_the_record_current(tree: Tree) -> None:
    # Red before the fix: a ContextVar was covered by the file of the reading function's module (macros.hy).
    record = tree.record("moder")
    _replace(tree.macros, "(defmacro unused [] 100)", "(defmacro unused [] 1000)")
    assert tree.is_current(record), "judged stale merely because another line of the macro module that reads the ContextVar changed"


# ---- Module docstrings ----------------------------------------------------------------------


def test_a_changed_docstring_of_a_module_the_macro_reads_keeps_the_record_current(tree: Tree) -> None:
    # Red before the fix: the string constant "__doc__" in the macro's code (the docstring written on the expansion's function) was taken as a name candidate, and the __doc__ of the
    # module the macro references (consts) went into the digest. A module docstring has no effect on the expansion result.
    record = tree.record("documenter")
    _replace(tree.consts, '"Constants module."', '"Constants module (docstring rewritten)."')
    assert tree.is_current(record), "judged stale merely because the docstring of a module referenced by a macro changed"


def test_a_changed_constant_of_a_module_the_documenting_macro_reads_is_stale(tree: Tree) -> None:
    # When it must go cold: if a constant of the same module read by the same macro changes, it is stale (even without __doc__, reading a module attribute is still covered).
    record = tree.record("documenter")
    _replace(tree.consts, "(setv OFFSET 1)", "(setv OFFSET 10)")
    assert not tree.is_current(record), "not judged stale although a constant of the module read by a macro changed"


# ---- Code that reads a module's location as a value -----------------


def test_a_changed_file_a_macro_reads_beside_its_module_is_stale(tree: Tree) -> None:
    # Red before the fix: when a helper reads a neighboring file from its own module's __file__ to decide the expansion result, the walk does not cover that file's contents
    # (after dropping the location path from the digest, this holds even across different trees). A digest of code that reads the location as a value becomes a mark that cannot be checked.
    record = tree.record("datareader")
    _replace(tree.directory / "data.txt", "one", "two-two")
    assert not tree.is_current(record), "not judged stale although the neighboring file a macro reads from its location changed"


# ---- Slot fields and vars(cls) — covered by name and contents, not by the owner module's file ------------------------------


def test_an_unrelated_function_beside_a_slotted_class_keeps_the_record_current(tree: Tree) -> None:
    # Red before the fix: a slot field (member_descriptor) was taken for a C function and covered by its owner module's file — a function added anywhere in
    # doeff_hy's _clause_endings.hy (ClauseOptions, MatchArm) made every defhandler user stale.
    record = tree.record("slotreader")
    _append(tree.directory / "slotted.py", UNREACHED_PY)
    assert tree.is_current(record), "judged stale merely because a function was added beside a slotted class a macro reads"


def test_an_added_slot_field_is_stale(tree: Tree) -> None:
    # When it must go cold: adding a field to the slotted class changes its __slots__ row and its members.
    record = tree.record("slotreader")
    _replace(tree.directory / "slotted.py", '__slots__ = ("left",)', '__slots__ = ("left", "right")')
    assert not tree.is_current(record), "not judged stale although a field was added to the slotted class a macro reads"


def test_an_unrelated_function_beside_a_defrecord_type_keeps_the_record_current(tree: Tree) -> None:
    # Red before the fix: the fields of a defrecord type are slots, covered by the file of the module that defines the type.
    record = tree.record("recordreader")
    _append(tree.directory / "records.hy", UNREACHED_HY)
    assert tree.is_current(record), "judged stale merely because a function was added beside a defrecord type a macro reads"


def test_an_added_defrecord_field_is_stale(tree: Tree) -> None:
    # When it must go cold: adding a field to the defrecord type a macro reads makes it stale.
    record = tree.record("recordreader")
    _replace(tree.directory / "records.hy", "#^ int width)", "#^ int width\n  #^ int height)")
    assert not tree.is_current(record), "not judged stale although a field was added to the defrecord type a macro reads"


def test_a_rewritten_docstring_beside_a_helper_reading_vars_of_a_class_keeps_the_record_current(tree: Tree) -> None:
    # Red before the fix: vars(cls) was read as a module namespace read (like vars()) and covered the helper's module by its file — a docstring rewrite in
    # doeff_hy's _record_runtime (record_setattr reads vars(cls)) made every defhandler user stale.
    record = tree.record("classreader")
    _replace(tree.directory / "inspects.py", '"""Inspects module."""', '"""Inspects module (docstring rewritten)."""')
    _append(tree.directory / "inspects.py", UNREACHED_PY)
    assert tree.is_current(record), "judged stale merely because the module of a helper reading vars(cls) changed elsewhere"


def test_a_changed_value_of_a_class_read_by_vars_is_stale(tree: Tree) -> None:
    # When it must go cold: the class read through vars(cls) is followed by its contents.
    record = tree.record("classreader")
    _replace(tree.directory / "inspects.py", "size = 2", "size = 20")
    assert not tree.is_current(record), "not judged stale although a value of the class read by vars(cls) changed"


def test_a_changed_global_read_by_vars_without_an_argument_is_stale(tree: Tree) -> None:
    # When it must go cold: vars() with no argument stays a module namespace read — a global read by a computed key is covered by the module's file.
    record = tree.record("varsreader")
    _replace(tree.macros, "(setv LIMIT 7)", "(setv LIMIT 70)")
    assert not tree.is_current(record), "not judged stale although a global read by vars() with a computed key changed"


def _vars_of_a_name(cls):
    return vars(cls)


def _vars_of_an_attribute(self):
    return vars(self.owner.kind)


def _vars_without_an_argument():
    return vars()


def _vars_of_a_call(cls):
    return vars(type(cls))


def _vars_of_a_star(args):
    return vars(*args)


def _vars_as_a_value(values):
    return list(map(vars, values))


def _vars_in_a_nested_function(cls):
    def inner():
        return vars()

    return vars(cls), inner


@pytest.mark.parametrize(
    ("function", "read"),
    [
        (_vars_of_a_name, "object"),
        (_vars_of_an_attribute, "object"),
        (_vars_without_an_argument, "module"),
        (_vars_of_a_call, "module"),
        (_vars_of_a_star, "module"),
        (_vars_as_a_value, "module"),
        (_vars_in_a_nested_function, "module"),
    ],
)
def test_only_vars_with_one_plain_argument_is_an_object_read(function: object, read: str) -> None:
    # vars(x) with one argument read as a plain value is x.__dict__ (OBJECT); vars() and every shape whose argument cannot be read from the instructions stay MODULE.
    from types import FunctionType

    from doeff_hy_bytecode_guard._macro_use import _code_facts

    assert isinstance(function, FunctionType)
    assert _code_facts(function.__code__).reads.value == read


# ---- Real doeff_hy macros (copy doeff_hy to a temp dir, then make and check records in a separate process) ------------------------------

#: A module that uses only defk, val, <-, defrecord, and defhandler (the shape of agora's controllers modules).
REAL_USER = """\
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defrecord])
(require doeff-hy.handle [defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Fetch [EffectBase]
  #^ str source)

(defrecord Row
  "row"
  {:tags {:context "macro-use-probe" :role "type"}}
  (#^ int value))

(val base 1)

(defk step [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "macro-use-probe" :role "judgment"}}
  "Add 1."
  (+ n base))

(defk job [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "macro-use-probe" :role "entry"}}
  "Bundle."
  (<- a (step n))
  a)

(defhandler fetch-handler
  (Fetch [source]
    (resume source)))
"""

#: Child process: with the copied doeff_hy, compile a consumer through the same entry point as import and write the record as JSON (record), or check a record already written
#: (check). It also reports where the loaded doeff_hy is and the names of macro rows that have no digest (compared by file rows).
REAL_CHILD = """\
import importlib.machinery, json, sys
from pathlib import Path
root, mode, stored = sys.argv[1], sys.argv[2], Path(sys.argv[3])
sys.path.insert(0, root)
import hy
import doeff_hy
from doeff_hy_bytecode_guard import _records as records
from doeff_hy_bytecode_guard._loader_hooks import record_from_json, record_is_current_here, record_to_json, source_to_code_as_import
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
    """The doeff_hy copied to a temp dir (lib/doeff_hy), the root of the module using it, and the record file."""

    lib: Path
    root: Path
    stored: Path

    @property
    def macros(self) -> Path:
        return self.lib / "doeff_hy" / "macros.hy"

    def run(self, mode: str) -> dict[str, object]:
        """The answer from running once in a child process (also confirms the copied doeff_hy was loaded)."""
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
    # Red before the fix (record of agora's records_turns.hy): midway through the closure of defk and others, imports inside functions (outcome_forms,
    # declarations, and doeff_hy.macros that defhandler reads) were covered per whole module by file, and macros.hy was included. The record's file rows
    # (files of modules the closure depended on — macros.hy included) are compared only when some rows lack a digest, so confirm that every row has a digest
    # (made in the fully loaded state). The next test confirms that changing another line of macros.hy does not make it stale.
    by_files = real_tree.run("record")["by_files"]
    assert by_files == [], by_files


def test_a_changed_deftest_keeps_the_record_of_a_defk_user_current(real_tree: RealTree) -> None:
    # Red before the fix: changing one line of the body of deftest in macros.hy (a macro the consumer does not use) does not make it stale.
    real_tree.run("record")
    _replace(
        real_tree.macros,
        "Define an effectful test that expands to a pytest-compatible function.",
        "Define an effectful test (changed).",
    )
    assert real_tree.run("check")["current"] is True, "judged stale merely because an unused deftest changed"


def test_a_changed_defk_makes_the_record_of_a_defk_user_stale(real_tree: RealTree) -> None:
    # When it must go cold: if the body of a used defk (a docstring constant) changes, it is stale.
    real_tree.run("record")
    _replace(
        real_tree.macros,
        '"Define a kleisli function (@do decorator)',
        '"Define a kleisli function (@do decorator, changed)',
    )
    assert real_tree.run("check")["current"] is False, "not judged stale although the used defk changed"


#: A module that uses only defk, val, and <-.
REAL_DEFK_USER = """\
(require doeff-hy.macros [defk <- val])

(val base 1)

(defk step [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "macro-use-probe" :role "judgment"}}
  "Add 1."
  (+ n base))

(defk job [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "macro-use-probe" :role "entry"}}
  "Bundle."
  (<- a (step n))
  a)
"""

#: A function that the closure cannot reach and that does not affect expansion (appended to the end of the file).
UNREACHED_PY = "\n\ndef _unreached_probe() -> int:\n    return 1\n"
UNREACHED_HY = "\n(defn _unreached-probe [] 1)\n"


def _append(path: Path, text: str) -> None:
    """Append a statement to the end of the file."""
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def test_an_unreached_function_in_binding_forms_and_static_view_keeps_a_defk_user_current(
    real_tree: RealTree,
) -> None:
    # Red before the fix (nearly all of agora's 414 Hy modules were rebuilt on every pin bump): following the globals dict of binding_forms,
    # the read of dataclass field-name vars, the kwdefaults dict, and static_view's ContextVar covered them with the sha256 of
    # those modules' files.
    (real_tree.root / "macro_use_probe" / "user.hy").write_text(REAL_DEFK_USER, encoding="utf-8")
    real_tree.run("record")
    _append(real_tree.lib / "doeff_hy" / "_binding_forms.py", UNREACHED_PY)
    _append(real_tree.lib / "doeff_hy" / "_static_view.py", UNREACHED_PY)
    assert real_tree.run("check")["current"] is True, (
        "judged a defk/val consumer stale merely because a function not reached from binding_forms and static_view was added"
    )


def test_an_unreached_function_in_clause_endings_keeps_a_defhandler_user_current(
    real_tree: RealTree,
) -> None:
    # Red before the fix: following the table (NAMED-RESUMPTION) of clause_endings.hy that defhandler reads covered it with the clause_endings.hy file.
    real_tree.run("record")
    _append(real_tree.lib / "doeff_hy" / "_clause_endings.hy", UNREACHED_HY)
    _append(real_tree.lib / "doeff_hy" / "_binding_forms.py", UNREACHED_PY)
    _append(real_tree.lib / "doeff_hy" / "_static_view.py", UNREACHED_PY)
    assert real_tree.run("check")["current"] is True, (
        "judged a defhandler consumer stale merely because a function not reached from clause_endings, binding_forms, and static_view was added"
    )


#: A module that uses only defhandler.
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

#: Modules the closure of defhandler walks that have a module docstring (file), and the start of that docstring.
DOCUMENTED_MODULES = {
    "__init__.py": '"""doeff-hy — Standard Hy macros for doeff effect composition.',
    "_record_runtime.py": '"""Parts that the expansions of defrecord and defeffect read at load time.',
    "_type_forms.py": '"""Map a type expression to a form that can be passed as the second argument of ``isinstance`` at run time',
}


def test_changed_module_docstrings_keep_a_defhandler_user_current(real_tree: RealTree) -> None:
    # Red before the fix (doeff b44c49c -> 0f2fa6f: 540 defhandler consumer files dropped out of the Hy expansion cache): taking the string constant "__doc__" of the defhandler expansion code as a name candidate,
    # the __doc__ of modules walked in the closure went into the digest. The only difference in the digest's inputs was
    # one line of the record_runtime docstring.
    (real_tree.root / "macro_use_probe" / "user.hy").write_text(REAL_HANDLER_USER, encoding="utf-8")
    real_tree.run("record")
    for name, head in DOCUMENTED_MODULES.items():
        _replace(real_tree.lib / "doeff_hy" / name, head, f"{head} (docstring rewritten)")
    assert real_tree.run("check")["current"] is True, (
        "judged a defhandler consumer stale merely because a docstring of a module in defhandler's closure changed"
    )


def test_a_changed_table_in_binding_forms_makes_a_defk_user_stale(real_tree: RealTree) -> None:
    # When it must go cold: if a row of the binding_forms table (_KINDS) read by defk and val changes, it is stale.
    (real_tree.root / "macro_use_probe" / "user.hy").write_text(REAL_DEFK_USER, encoding="utf-8")
    real_tree.run("record")
    _replace(
        real_tree.lib / "doeff_hy" / "_binding_forms.py",
        '_KINDS: Mapping[str, Mutability] = {"val": Mutability.VAL, "var": Mutability.VAR}',
        '_KINDS: Mapping[str, Mutability] = {"val": Mutability.VAR, "var": Mutability.VAR, "v": Mutability.VAL}',
    )
    assert real_tree.run("check")["current"] is False, "not judged stale although _KINDS of binding_forms changed"


#: A module that uses defk, val, var, and var rewriting (:=) — expansion passes through _Walker._assign of binding_forms.
REAL_ASSIGN_USER = """\
(require doeff-hy.macros [defk <- val var])

(val base 1)

(defk step [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "macro-use-probe" :role "judgment"}}
  "Add 1."
  (var total n)
  (:= total (+ total base))
  total)
"""

#: A replacement that changes only the code of _Walker._assign of binding_forms (rewriting by :=) (the constant of the error message).
ASSIGN_MESSAGE = "can be written only in the body of a defk, deftest or defhandler clause (not inside ({where.head} …))."

#: Child process: with the copied doeff_hy, record in one process a consumer that does not use := (user.hy) and one that does (assigning.hy),
#: and check the two written records in one process (check) — so the copied doeff_hy is compiled only once.
REAL_PAIR_CHILD = """\
import importlib.machinery, json, sys
from pathlib import Path
root, mode, stored = sys.argv[1], sys.argv[2], Path(sys.argv[3])
sys.path.insert(0, root)
import doeff_hy
from doeff_hy_bytecode_guard import _records as records
from doeff_hy_bytecode_guard._loader_hooks import record_from_json, record_is_current_here, record_to_json, source_to_code_as_import
users = ("user", "assigning")
if mode == "record":
    made = {}
    for user in users:
        path = f"{root}/macro_use_probe/{user}.hy"
        loader = importlib.machinery.SourceFileLoader(f"macro_use_probe.{user}", path)
        made[user] = record_to_json(records.record_of(source_to_code_as_import(loader, open(path, "rb").read(), path)))
    stored.write_text(json.dumps(made))
    print(json.dumps({"doeff_hy": doeff_hy.__file__}))
else:
    made = json.loads(stored.read_text())
    current = {user: record_is_current_here(record_from_json(made[user])) for user in users}
    print(json.dumps({"current": current, "doeff_hy": doeff_hy.__file__}))
"""


def _run_pair(real_tree: RealTree, mode: str) -> dict[str, object]:
    """:data:`REAL_PAIR_CHILD` answer from running once in a child process (also confirms the copied doeff_hy was loaded)."""
    import json
    import subprocess

    done = subprocess.run(
        [sys.executable, "-c", REAL_PAIR_CHILD, str(real_tree.root), mode, str(real_tree.stored)],
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout.strip().splitlines()[-1])
    assert Path(str(answer["doeff_hy"])).is_relative_to(real_tree.lib), answer
    return answer


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring, which picks up code called during expansion, exists from Python 3.12 — on 3.11 the record cannot pick it up and takes a wide scope (called_code.Uncollectable)")
def test_a_changed_method_only_the_assigning_expansion_calls_makes_only_its_record_stale(
    real_tree: RealTree,
) -> None:
    # Red before the fix (child of #3769; on the daily run 10-12 04:00 1,197 Hy modules with identical contents were rebuilt): the closure walk of defk walked every method of the class reachable from defk
    # (binding_forms._Walker), and a change to the code of _assign, which expansion does not call, made even the record of a consumer not using := stale
    # (the only difference in the digest inputs from doeff 167d38ec2 to 1db4cb7bf was the hash of _assign's code).
    # Contrast (green before and after): a consumer using := passes through _assign in expansion, so the same change makes it stale.
    probe = real_tree.root / "macro_use_probe"
    (probe / "user.hy").write_text(REAL_DEFK_USER, encoding="utf-8")
    (probe / "assigning.hy").write_text(REAL_ASSIGN_USER, encoding="utf-8")
    _run_pair(real_tree, "record")
    _replace(
        real_tree.lib / "doeff_hy" / "_binding_forms.py",
        ASSIGN_MESSAGE,
        ASSIGN_MESSAGE.replace("can be written only", "can be written only (changed)"),
    )
    current = _run_pair(real_tree, "check")["current"]
    assert current == {"user": True, "assigning": False}, (
        "when only binding_forms._Walker._assign changes, the consumer without := (which expansion does not call through) should match and the consumer with := (which expansion passes through) should be stale, "
        f"but got {current}"
    )


# ---- Digest independent of import order (when a module in the closure cannot be looked up in the middle of a circular import) -------------------------------------
#
# The closure of deff looks up doeff_hy._outcome_forms with an import inside a function. outcome_forms requires defrecord from doeff_hy.record,
# and record.hy uses deff. In a process that reads record from a .pyc, the check of record.hy's record runs before the body of record executes,
# so outcome_forms cannot be imported (record's macro table is still empty). Before the fix, the digest at that time (names only) stayed in the process's memo,
# and every check and record of deff consumers in that process took that value, so processes with different import orders kept rewriting each other's entries in the store
# (the daily run of 10-11 16:03).

#: A module that uses only deff.
REAL_DEFF_USER = """\
(require doeff-hy.macros [deff])

(deff add-one [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "macro-use-probe" :role "foundation"}}
  "Add 1."
  (+ n 1))
"""

#: Child process (changes import order): compute in order the digests of the named macros (module:name) (digests); make a consumer's record
#: (record); compute the named macros' digests first and then check the record (check); import modules in order and list the Hy
#: files that finished compiling (import — a compile in which require failed in the middle of a circular import produces nothing, so it is not counted).
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
from doeff_hy_bytecode_guard import _loader_hooks as loader_hooks, _macro_use as macro_use, _records as records
from doeff_hy_bytecode_guard._loader_hooks import record_from_json, record_is_current_here, record_to_json, source_to_code_as_import
def digest(spec):
    module, name = spec.split(":")
    key = macro_use.UsedMacroKey(records.MACRO_TABLE, module, hy.mangle(name))
    macro = vars(importlib.import_module(module))[records.MACRO_TABLE][key.name]
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


@pytest.fixture
def writing_tree(real_tree: RealTree, monkeypatch: pytest.MonkeyPatch) -> RealTree:
    """The real_tree in which the child process writes .pyc (written under PYTHONPYCACHEPREFIX in the check's temp dir — placed by real_tree). The instability occurs only in a process
    that reads record from a .pyc (the check of record.hy's record runs before the body executes), so the ``PYTHONDONTWRITEBYTECODE`` that the check suite passes to the child is removed only during this check."""
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    return real_tree


def _order_child(real_tree: RealTree, mode: str, *names: str) -> dict[str, object]:
    """The answer from running ORDER_CHILD once in a child process (the child inherits the check's environment — also confirms the copied doeff_hy was loaded)."""
    import json
    import subprocess

    done = subprocess.run(
        [sys.executable, "-c", ORDER_CHILD, str(real_tree.root), str(real_tree.stored), mode, *names],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout.strip().splitlines()[-1])
    assert Path(str(answer["doeff_hy"])).is_relative_to(real_tree.lib), answer
    return answer


def _warm_bytecode(real_tree: RealTree) -> None:
    """Import record and wire once to make .pyc files (the shape from the second daily run on). Also confirm they were made — without .pyc, record
    is compiled every time and the check is green in a shape where the instability cannot occur (that was the case when the check suite's PYTHONDONTWRITEBYTECODE was passed to the child)."""
    _order_child(real_tree, "import", "doeff_hy.record", "doeff_hy._wire")
    pycache = real_tree.stored.parent / "pycache"
    assert any(pycache.rglob("record.*.pyc")), f"no .pyc of record.hy in {pycache} — the check's precondition broke"


def test_the_digest_of_deff_does_not_depend_on_computing_defrecord_and_defwire_first(
    writing_tree: RealTree,
) -> None:
    real_tree = writing_tree
    # Red before the fix: the digest of deff in a process that first computed the digests of defrecord and defwire (reading record from a .pyc and unable to
    # import outcome_forms midway through the check of record.hy) differed from the digest in a standalone process (959a4c15 / f24dde8b).
    _warm_bytecode(real_tree)
    alone = _order_child(real_tree, "digests", DEFF)["digests"]
    after = _order_child(real_tree, "digests", DEFRECORD, DEFWIRE, DEFF)["digests"]
    assert isinstance(alone, list), alone
    assert isinstance(after, list), after
    assert alone[0] is not None, "cannot make the digest of deff in a standalone process"
    assert after[-1] == alone[0], (
        f"the digest of deff {after[-1]} in a process that first computed the digests of defrecord and defwire differs from the standalone {alone[0]}"
    )


def test_a_deff_user_stays_current_in_a_process_that_computed_defrecord_and_defwire_first(
    writing_tree: RealTree,
) -> None:
    real_tree = writing_tree
    # Red before the fix: a record of a deff consumer that matches in a standalone process was judged stale (deff) in a process that first computed the digests of defrecord and defwire
    # — the source of processes with different import orders rewriting each other's store entries.
    (real_tree.root / "macro_use_probe" / "user.hy").write_text(REAL_DEFF_USER, encoding="utf-8")
    _warm_bytecode(real_tree)
    _order_child(real_tree, "record")
    assert _order_child(real_tree, "check")["current"] is True, "the record of the deff consumer is stale in a standalone process"
    assert _order_child(real_tree, "check", DEFRECORD, DEFWIRE)["current"] is True, (
        "in a process that first computed the digests of defrecord and defwire, the record of the deff consumer was judged stale"
    )


#: The shapes of two processes with different import orders — reading record first (outcome_forms cannot be imported midway through the check of record.hy),
#: and reading a deff consumer first (outcome_forms is midway through import during the check of record.hy).
RECORD_FIRST = ("doeff_hy.record", "doeff_hy._wire", "doeff_hy._clause_endings")
DEFF_USER_FIRST = ("doeff_hy.macros", "doeff_hy._clause_endings", "doeff_hy._wire", "doeff_hy.record")


@dataclass(frozen=True)
class ImportRound:
    """One import — the first module imported, and the Hy files that finished compiling."""

    first: str
    compiled: object


def _import_round(
    real_tree: RealTree,
    monkeypatch: pytest.MonkeyPatch,
    store: Path,
    pycache: Path,
    order: tuple[str, ...],
) -> ImportRound:
    """One import in the order given, with the shared store and a pycache empty to this process only."""
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(store))
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(pycache))
    return ImportRound(order[0], _order_child(real_tree, "import", *order)["compiled"])


def test_processes_importing_in_different_orders_share_the_store_without_recompiling(
    writing_tree: RealTree, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Red before the fix: when one shared store was used alternately by processes with different import orders (pycache empty every time — the shape of the daily pytest stage),
    # even from the second round it rebuilt record.hy, outcome_forms.hy, wire.hy, and clause_endings.hy every time and rewrote the store.
    store = tmp_path / "store"
    store.mkdir()
    rounds = tuple(
        _import_round(writing_tree, monkeypatch, store, tmp_path / f"pycache-{index}", order)
        for index, order in enumerate((RECORD_FIRST, DEFF_USER_FIRST) * 2)
    )
    assert rounds[0].compiled, "nothing was compiled in round 1 (cold store) — the check's precondition broke"
    again = [(round_.first, round_.compiled) for round_ in rounds[2:] if round_.compiled]
    assert not again, f"Hy modules were rebuilt even from round 2: {again} (all rounds: {rounds})"


# ---- Memo across processes ----------------------------------------------------------
#
# Most of the cost of a new process's check was reading the source statements that cover globals the closure walk cannot trace (bound_source.binding_of — parsing the source and
# walking the syntax tree). Pure reads determined by contents and name are memoized in the store (DOEFF_HY_CODE_STORE) so the next process does not re-parse.
# The digest itself is not memoized across processes — a digest depends on live values (macro files, plain values coming from other modules, values determined by environment variables).
# The three below (clauses 1 to 3) turn red in the naive shape that memoizes a digest by the state (path, mtime, size) of the files it traced and has the next process trust it
# (what it depended on differs from when it was memoized, yet the previous process's digest is judged to match now, and a stale expansion is used).

#: Macro modules (macros per clause). body = a macro with only a body (clause 1 — contents of the file defining the macro); scaled = a macro returning a plain value imported from another module
#: (clause 2 — the value is determined by the contents of helper.hy, but the only file the macro's closure traces is macros.hy); moded = a macro returning a value assembled at load time from
#: an environment variable (clause 3 — a value determined outside files); looks and rows = macros reading untraceable global tables (Hy and Python
#: covered by source statements — through binding_of); limited = a macro returning a plain value imported from a top-level package separate from the macro (another shape of clause 2 —
#: none of the modules the record names is in that package); placed = a macro whose helper writes __file__ onto a module it made (__file__ appears in the code's name table,
#: but the location is not read as a value — the shape of doeff_hy._static_check's project; the consumer is placed.hy).
CROSS_MACROS = """\
(import os)
(import types)
(import {pkg}.helper [SCALE])
(import macro_use_cross_values.limits [LIMIT])
(import {pkg}.tables :as tables)
(setv TABLE {{"a" 1}})
(setv MODE (os.environ.get "MACRO_USE_PROBE_MODE" "plain"))
(defn place-of [module] (setv module.__file__ "placed") module)
(defmacro body [] 1)
(defmacro scaled [] SCALE)
(defmacro moded [] MODE)
(defmacro looks [] (get TABLE "a"))
(defmacro rows [] (get tables.ROWS "a"))
(defmacro limited [] LIMIT)
(defmacro placed [] (place-of (types.ModuleType "placed")) 1)
"""

CROSS_FILES = {
    "__init__.py": "",
    "macros.hy": CROSS_MACROS,
    "helper.hy": "(setv SCALE 3)\n",
    "tables.py": 'ROWS = {{"a": 1}}\nROWS["b"] = 2\n',
    "user.hy": "(require {pkg}.macros [body scaled moded looks rows limited])\n"
    "(setv value [(body) (scaled) (moded) (looks) (rows) (limited)])\n",
    "placed.hy": "(require {pkg}.macros [placed])\n(setv value (placed))\n",
}

#: A top-level package separate from the macro (the owner of LIMIT returned by limited).
CROSS_VALUES_PACKAGE = "macro_use_cross_values"
CROSS_VALUES_FILES = {"__init__.py": "", "limits.hy": "(setv LIMIT 3)\n"}

#: Child process: make the record of a consumer (default user.hy; named by the fifth argument) and also check it in the same process (record — goes through both the record-making walk and
#: the check), or check a record (check).
#: The answer includes the number of times source was parsed during the check (parsed — bound_source parsing), the number of times code constants were read to make code readings (facts —
#: macro_use's _constant_text), and whether hy.core.hy_repr is loaded after the check (hy_repr).
CROSS_CHILD = """\
import importlib.machinery, json, sys
from pathlib import Path
root, stored, mode, package = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
user = sys.argv[5] if len(sys.argv) > 5 else "user"
sys.path.insert(0, root)
import hy
import doeff_hy
from doeff_hy_bytecode_guard import _bound_source as bound_source, _macro_use as macro_use, _records as records
from doeff_hy_bytecode_guard._loader_hooks import record_from_json, record_is_current_here, record_to_json, source_to_code_as_import
counts = {"parsed": 0, "facts": 0}
def counting(name, original):
    def counted(*args, **kwargs):
        counts[name] += 1
        return original(*args, **kwargs)
    return counted
bound_source._read = counting("parsed", bound_source._read)
macro_use._constant_text = counting("facts", macro_use._constant_text)
if mode == "record":
    path = f"{root}/{package}/{user}.hy"
    loader = importlib.machinery.SourceFileLoader(f"{package}.{user}", path)
    record = records.record_of(source_to_code_as_import(loader, open(path, "rb").read(), path))
    stored.write_text(json.dumps(record_to_json(record)))
    current = record_is_current_here(record)
else:
    current = record_is_current_here(record_from_json(json.loads(stored.read_text())))
print(json.dumps({"current": current, **counts, "hy_repr": "hy.core.hy_repr" in sys.modules}))
"""

CROSS_PACKAGE = "macro_use_cross"


@dataclass(frozen=True)
class CrossTree:
    """The root of the check package read in a child process (standing in for one work tree), and the store and record file shared across processes."""

    root: Path
    store: Path
    stored: Path

    @property
    def package(self) -> Path:
        return self.root / CROSS_PACKAGE

    @property
    def values(self) -> Path:
        return self.root / CROSS_VALUES_PACKAGE

    def run(self, mode: str, user: str = "user") -> dict[str, object]:
        """The answer from running once in a child process (user = file name of the consumer; the child inherits the check's environment — the store and pycache are set by the fixture with
        monkeypatch)."""
        import json
        import subprocess

        done = subprocess.run(
            [sys.executable, "-c", CROSS_CHILD, str(self.root), str(self.stored), mode, CROSS_PACKAGE, user],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip().splitlines()[-1])

    def elsewhere(self, name: str) -> "CrossTree":
        """Copy a package with the same contents to another root (another work tree) — using the same store and record file."""
        import shutil

        root = self.root.parent / name
        for package in (CROSS_PACKAGE, CROSS_VALUES_PACKAGE):
            shutil.copytree(
                self.root / package, root / package, ignore=shutil.ignore_patterns("__pycache__")
            )
        return CrossTree(root, self.store, self.stored)


@pytest.fixture
def cross_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CrossTree:
    """One root of the check package, and the environment the child process inherits (shared store, the check's temp-dir pycache, and no clause 3 environment variable)."""
    root = tmp_path / "tree-a"
    for package, files in ((CROSS_PACKAGE, CROSS_FILES), (CROSS_VALUES_PACKAGE, CROSS_VALUES_FILES)):
        (root / package).mkdir(parents=True)
        for name, text in files.items():
            (root / package / name).write_text(text.format(pkg=package), encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(store))
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pycache"))
    monkeypatch.delenv("MACRO_USE_PROBE_MODE", raising=False)
    return CrossTree(root, store, tmp_path / "record.json")


def test_a_second_process_does_not_parse_the_sources_the_first_one_read(cross_tree: CrossTree) -> None:
    # Red before the fix: the check of a new process re-parsed the same-content source as the previous process (TABLE in macros.hy, ROWS in tables.py) every time
    # (about 2/3 of the check walk when loading test_emulated_screen.hy).
    assert cross_tree.run("record")["current"] is True, "the process that made the record judged the record it made stale"
    answer = cross_tree.run("check")
    assert answer["current"] is True, "the second process judged stale although nothing changed"
    assert answer["parsed"] == 0, f"the second process re-parsed the same-content source {answer['parsed']} times"


def test_a_macro_changed_in_another_tree_is_stale_in_a_second_process(cross_tree: CrossTree) -> None:
    # Clause 1 (contents of the file defining the macro): macros.hy in another work tree has a different body for body. The file whose digest the previous process memoized
    # (macros.hy of the previous tree) has not changed, so the shape that trusts the memo by file state uses the previous digest and judges a stale expansion to match now.
    cross_tree.run("record")
    other = cross_tree.elsewhere("tree-b")
    _replace(other.package / "macros.hy", "(defmacro body [] 1)", "(defmacro body [] 20)")
    assert other.run("check")["current"] is False, "the second process did not judge stale although the macro body differs in another tree"


def test_a_macro_naming_the_place_of_its_module_is_current_in_another_tree(cross_tree: CrossTree) -> None:
    # Red before the fix: when __file__ appears in the name table of the code of a helper a macro calls (writing __file__ onto a module it made),
    # the module's location path went into the digest, so merely placing the same contents in another work tree made it stale (the daily
    # full verification places doeff in a different dir every run, and the type-check expansion cache missed entirely every time).
    cross_tree.run("record", "placed")
    other = cross_tree.elsewhere("tree-b")
    assert other.run("check", "placed")["current"] is True, "judged stale merely because a macro with the same contents was placed in another tree"


def test_a_changed_macro_naming_the_place_of_its_module_is_stale_in_another_tree(cross_tree: CrossTree) -> None:
    # When it must go cold: even without including the location path, a tree where the same macro's body was changed is stale.
    cross_tree.run("record", "placed")
    other = cross_tree.elsewhere("tree-b")
    _replace(
        other.package / "macros.hy",
        '(place-of (types.ModuleType "placed")) 1)',
        '(place-of (types.ModuleType "placed")) 20)',
    )
    assert other.run("check", "placed")["current"] is False, "not judged stale although the macro body differs in another tree"


def test_a_changed_value_from_another_module_is_stale_in_a_second_process(cross_tree: CrossTree) -> None:
    # Clause 2 (contents of the file of the module a traced value came from): SCALE is determined by the contents of helper.hy, but the only file scaled's closure traces is macros.hy
    # (a plain value does not carry which module it came from). The shape that trusts the memo by file state uses the previous digest even if helper.hy is changed.
    cross_tree.run("record")
    _replace(cross_tree.package / "helper.hy", "(setv SCALE 3)", "(setv SCALE 30)")
    assert cross_tree.run("check")["current"] is False, "the second process did not judge stale although the value of another module returned by a macro changed"


def test_a_changed_environment_value_is_stale_in_a_second_process(
    cross_tree: CrossTree, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Clause 3 (a value determined outside files): MODE is assembled from an environment variable at load time. No file changes, so the shape that trusts the memo by file state
    # uses the previous digest.
    cross_tree.run("record")
    monkeypatch.setenv("MACRO_USE_PROBE_MODE", "strict")  # inherited only by the second process
    answer = cross_tree.run("check")
    assert answer["current"] is False, "the second process did not judge stale although the environment variable value returned by a macro differs"


def test_a_changed_value_from_another_top_package_is_stale_after_an_unchanged_check(
    cross_tree: CrossTree,
) -> None:
    # Another shape of clause 2: LIMIT is determined by a file of a top-level package separate from the macro, and none of the modules the record names is in that package.
    # A shape that memoizes the check answer (match / stale) in the store keyed by the files of the packages the record names and the environment, and has the next process trust it,
    # memoized the unchanged check's answer (match), and after LIMIT was changed still judged it to match and used a stale expansion.
    cross_tree.run("record")
    assert cross_tree.run("check")["current"] is True, "judged stale although nothing changed"
    _replace(cross_tree.values / "limits.hy", "(setv LIMIT 3)", "(setv LIMIT 30)")
    assert cross_tree.run("check")["current"] is False, (
        "the next process did not judge stale although the value of a separate top-level package returned by a macro changed"
    )


# ---- Source suffixes ----------------------------------------------------------
#
# The judgment that "a class of a module without source is covered only by its public shape" (#3938) held its own copy of Hy's source suffixes. If a suffix is added to the definition point
# (code_store.HY_SOURCE_SUFFIXES) while the copy stays old, a class of a module with that suffix is covered as an extension module's class
# by name and public shape only, and the digest does not change even if a method body changes (a stale expansion is judged to match now).


def test_a_class_in_a_source_with_an_unlisted_extension_is_covered_by_its_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Red before the fix: a class of a module with .hym (neither an extension module suffix nor a copied source suffix) did not include the method body in the digest, and
    # 3 and 30 gave the same digest.
    import types

    from doeff_hy_bytecode_guard import _loader_hooks as loader_hooks, _macro_use as macro_use

    name = f"macro_use_ext_{uuid.uuid4().hex[:8]}"  # the same name in the two versions (the name also goes into the digest)

    def digest(factor: int) -> str | None:
        """Re-place a module whose versions differ only in a constant in a method body, and make the macro's digest (functions are separate objects per version — the memo is not used)."""
        module = types.ModuleType(name)
        module.__file__ = str(tmp_path / "macros.hym")
        exec(  # the body of the check's module (standing in for a module that differs only in source suffix)
            f"class Scaler:\n    def factor(self):\n        return {factor}\n\n\n"
            "def scaled():\n    return Scaler().factor()\n",
            vars(module),
        )
        monkeypatch.setitem(sys.modules, name, module)
        key = macro_use.UsedMacroKey(records.MACRO_TABLE, name, "scaled")
        return macro_use.macro_digest(key, vars(module)["scaled"], loader_hooks.file_sha256).digest

    assert digest(3) != digest(30), "the digest does not change even when the method body of a class in a module with suffix .hym changes"


# ---- Fixed cost of the check ------------------------------------------------------------------
#
# Breakdown of the difference (0.385 seconds when loading test_emulated_screen.hy) from a process that always treats the check as "matches now": reading hy.core.hy_repr only inside the check
# (hy.repr is looked up to make the store key — Hy imports it lazily), rebuilding the code readings (_code_facts — reading instructions in
# Python) in every process, and the verification of memoized digests stat-ing files on every check (9,700 times for 797 checks).


def test_a_steady_check_process_does_not_import_hy_repr(cross_tree: CrossTree) -> None:
    # Red before the fix: even when the store had the readings, hy.repr was looked up to make the key and Hy loaded hy.core.hy_repr (a process that always treats the check as
    # matching now does not load it — 0.055 to 0.094 seconds).
    cross_tree.run("record")
    cross_tree.run("check")
    answer = cross_tree.run("check")
    assert answer["current"] is True, "the check process judged stale although nothing changed"
    assert answer["parsed"] == 0, f"the check process re-parsed the same-content source {answer['parsed']} times"
    assert answer["hy_repr"] is False, "the check loaded hy.core.hy_repr although the store had the readings"


def test_a_steady_check_process_does_not_read_code_facts_again(cross_tree: CrossTree) -> None:
    # Red before the fix: code readings (name table, string constants, imports, digest shape) are determined by the contents of the code, but were memoized only within a process, and
    # the check process re-read the code of the macro's closure every time.
    cross_tree.run("record")
    cross_tree.run("check")
    answer = cross_tree.run("check")
    assert answer["current"] is True, "the check process judged stale although nothing changed"
    assert answer["facts"] == 0, f"the check process rebuilt the code readings (constant readings: {answer['facts']} times)"


#: A package for one outermost load (importing top — importing six consumers inside). All consumers use the same macro.
SCOPE_USERS = tuple(f"user_{index}" for index in range(6))
SCOPE_FILES = {
    "__init__.py": "",
    "macros.hy": CROSS_MACROS,
    "helper.hy": "(setv SCALE 3)\n",
    "tables.py": 'ROWS = {{"a": 1}}\nROWS["b"] = 2\n',
    **{f"{user}.hy": "(require {pkg}.macros [body scaled looks rows])\n(setv value [(body) (scaled) (looks) (rows)])\n" for user in SCOPE_USERS},
    "top.hy": "".join(f"(import {{pkg}}.{user})\n" for user in SCOPE_USERS),
}

#: Child process: imports the named modules in order (one outermost load per name). Between them you can insert directives to rewrite a file (``edit:<file>:<before>:<after>``),
#: to re-read a module (``reload:<module>``), and to make a reader started inside a load import (``<server module>:<thread or
#: context>:<module>`` — ``requests``, ``answers``, and ``CONTEXT`` placed by the server module). The answer = the number of times the verification of memoized digests
#: stat-ed files (per file name — macro_use._file_state), the Hy files compiled, and the imported module's value ``value`` (if present
#: — the expansion result).
SCOPE_CHILD = """\
import importlib, importlib.machinery, json, sys
from pathlib import Path
root, package, *steps = sys.argv[1], sys.argv[2], *sys.argv[3:]
sys.path.insert(0, root)
import hy
compiled = []
previous = importlib.machinery.SourceFileLoader.source_to_code
def counting(self, data, path, *args, **kwargs):
    code = previous(self, data, path, *args, **kwargs)
    if isinstance(path, str) and path.endswith(".hy"):
        compiled.append(Path(path).name)
    return code
importlib.machinery.SourceFileLoader.source_to_code = counting
import doeff_hy
from doeff_hy_bytecode_guard import _macro_use as macro_use
stats = {}
stating = macro_use._file_state
def counted(path):
    stats[Path(path).name] = stats.get(Path(path).name, 0) + 1
    return stating(path)
macro_use._file_state = counted
values = {}
for step in steps:
    if step.startswith("edit:"):
        _, name, old, new = step.split(":")
        target = Path(root) / package / name
        target.write_text(target.read_text().replace(old, new))
        importlib.invalidate_caches()
    elif step.startswith("reload:"):
        importlib.reload(importlib.import_module(f"{package}.{step.split(':')[1]}"))
    elif step.count(":") == 2:
        server, reader, name = step.split(":")
        server = sys.modules[f"{package}.{server}"]
        if reader == "thread":
            server.requests.put(f"{package}.{name}")
            module = server.answers.get(timeout=30)
        else:
            module = server.CONTEXT.run(importlib.import_module, f"{package}.{name}")
        values[name] = module.value
    else:
        module = importlib.import_module(f"{package}.{step}")
        if hasattr(module, "value"):
            values[step] = module.value
print(json.dumps({"stats": stats, "compiled": compiled, "values": values}))
"""

SCOPE_PACKAGE = "macro_use_scope"


@dataclass(frozen=True)
class ScopeTree:
    """The root of the check package read in a child process (.pyc is written to the check's temp-dir pycache)."""

    root: Path
    package: str = SCOPE_PACKAGE

    def run(self, *steps: str) -> dict[str, object]:
        """The answer from running steps once in a child process."""
        import json
        import subprocess

        done = subprocess.run(
            [sys.executable, "-c", SCOPE_CHILD, str(self.root), self.package, *steps],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip().splitlines()[-1])


def _scope_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, package: str, files: dict[str, str]
) -> ScopeTree:
    """A root with one check package (files — {pkg} replaced by the package name) and a separate top-level package, and the environment the child process inherits (shared
    store, the check's temp-dir pycache, writing .pyc)."""
    root = tmp_path / "tree"
    (root / package).mkdir(parents=True)
    for name, text in files.items():
        (root / package / name).write_text(text.format(pkg=package), encoding="utf-8")
    (root / CROSS_VALUES_PACKAGE).mkdir()
    for name, text in CROSS_VALUES_FILES.items():
        (root / CROSS_VALUES_PACKAGE / name).write_text(text, encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(store))
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pycache"))
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    return ScopeTree(root, package)


@pytest.fixture
def scope_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ScopeTree:
    return _scope_tree(tmp_path, monkeypatch, SCOPE_PACKAGE, SCOPE_FILES)


def test_one_import_stats_each_macro_file_once(scope_tree: ScopeTree) -> None:
    # Red before the fix: each time the .pyc records of six consumers were checked, the verification of memoized digests re-stat-ed the macro files (once within a single check
    # — in the check's load, 797 checks and 9,700 stats). Within one outermost load, file state is read only once.
    first = scope_tree.run("top")
    assert {f"{user}.hy" for user in SCOPE_USERS} <= set(first["compiled"]), first
    answer = scope_tree.run("top")
    assert answer["compiled"] == [], f"the second process that only reads .pyc compiled: {answer['compiled']}"
    assert answer["stats"].get("macros.hy") == 1, f"number of times the state of macros.hy was read within one outermost load: {answer['stats']}"


def test_a_file_changed_between_two_imports_is_noticed_by_the_second(scope_tree: ScopeTree) -> None:
    # Keep green: file state is read only once only within one outermost load. If the ROWS statement of tables.py changes between loads,
    # the next load's check does not use the memoized digest but traces again, judges it stale, and recompiles the consumer.
    scope_tree.run("top")
    answer = scope_tree.run("user_0", 'edit:tables.py:ROWS["b"] = 2:ROWS["b"] = 20', "user_1")
    assert answer["compiled"] == ["user_1.hy"], f"did not notice tables.py changed between loads: {answer}"


def test_the_digest_is_the_sha256_of_the_length_prefixed_parts() -> None:
    # Keep green: the spelling of the record's digest (the sha256 of a sequence in which, for each string the walk fed in, the length of its UTF-8 (surrogatepass) bytes as an 8-byte little endian
    # is placed before it). If the spelling changes, all .pyc files and the store's code records are judged stale and all Hy modules are recompiled
    # (evidence that the change making the walk's string feeding faster did not change the spelling).
    import hashlib

    from doeff_hy_bytecode_guard import _loader_hooks as loader_hooks, _macro_use as macro_use

    walker = macro_use._Walker(loader_hooks.file_sha256, macro_use.CodeReadings({}))
    fed = (("module", "a"), ("€", ""), ("\ud800",))
    for parts in fed:
        walker._feed(*parts)
    expected = hashlib.sha256()
    for part in (part for parts in fed for part in parts):
        data = part.encode("utf-8", "surrogatepass")
        expected.update(len(data).to_bytes(8, "little"))
        expected.update(data)
    # The digest covering the whole function body (rows for values outside the macro and rows for when it cannot be picked up) has the same spelling as before.
    walked = walker.walked()
    assert walked.digest(walked.everything) == expected.hexdigest(), (
        "the spelling of the walk's digest is not the sha256 of the sequence of strings with their length placed before"
    )


# ---- Failure cases from design verification A and B ----------------------------------------------

#: Blind A package — macro rows returns row "b" of the table in the loaded value module tables.py (the walk covers the table with the tables.py statement).
LIVE_FILES = {
    "__init__.py": "",
    "tables.py": 'ROWS = {{"a": 1}}\nROWS["b"] = 2\n',
    "macros.hy": '(import {pkg}.tables :as tables)\n(defmacro rows [] (get tables.ROWS "b"))\n',
    "user_0.hy": "(require {pkg}.macros [rows])\n(setv value (rows))\n",
    "user_1.hy": "(require {pkg}.macros [rows])\n(setv value (rows))\n",
}


def test_a_record_made_after_the_file_of_a_loaded_module_changed_is_stale_in_a_new_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Red before the fix (blind A — present since baseline cf7ea9cc0): after a resident process loaded tables (ROWS["b"] = 2), tables.py was
    # rewritten (20), and when the same process recompiled user_1, the expansion used the old table in the process (2), yet the record's digest was made from the current
    # file's statement (20). A new process (table is 20) judged that record to match now, and used 2 where re-expansion would give 20.
    tree = _scope_tree(tmp_path, monkeypatch, "macro_use_live", LIVE_FILES)
    tree.run("user_0", "user_1")
    edited = tree.run("user_0", 'edit:tables.py:ROWS["b"] = 2:ROWS["b"] = 20', "user_1")
    assert edited["compiled"] == ["user_1.hy"], f"did not notice tables.py changed between loads: {edited}"
    answer = tree.run("user_1")
    assert answer["values"] == {"user_1": 20}, (
        f"a new process judged to match now the record made by a process after the loaded table was rewritten: {answer}"
    )


#: Blind B package — the helper that macro body calls at expansion time is a function that the macro defhelper of another file makes inside macros.hy.
HELPER_FILES = {
    "__init__.py": "",
    "defs.hy": "(defmacro defhelper [name value] `(defn ~name [] ~value))\n",
    "macros.hy": "(require {pkg}.defs [defhelper])\n(defhelper helper 1)\n(defmacro body [] (helper))\n",
    "user.hy": "(require {pkg}.macros [body])\n(setv value (body))\n",
}


def test_a_helper_built_by_a_changed_macro_of_another_file_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keep green (blind B): the code of helper depends not only on the contents of macros.hy but also on the expansion of defhelper. A shape that keys the memo of code readings in the store by
    # the source file's contents and the function's location passed all the checks presented, yet even after defs.hy was changed and macros.hy was recompiled
    # it used the previous process's reading of helper and judged the consumer to match now (value stayed 1). Code readings are looked up by the contents of the code.
    tree = _scope_tree(tmp_path, monkeypatch, "macro_use_helper", HELPER_FILES)
    assert tree.run("user")["values"] == {"user": 1}
    assert tree.run("user")["compiled"] == [], "the second process compiled — the check's precondition broke"
    _replace(tree.root / "macro_use_helper" / "defs.hy", "[] ~value)", "[] (* ~value 10))")
    answer = tree.run("user")
    assert "user.hy" in answer["compiled"] and answer["values"] == {"user": 10}, (
        f"judged the consumer to match now although the macro making helper changed: {answer}"
    )


#: Blind A addendum package — server.py checks user_0 inside the outermost load (making the memo and a copy of file state), starts a
#: thread inside the load (on free-threaded 3.14 it inherits the starting side's context — sys.flags.thread_inherit_context), and copies the context.
SERVED_FILES = {
    "__init__.py": "",
    "tables.py": 'ROWS = {{}}\nROWS["a"] = 1\n',
    "macros.hy": '(import {pkg}.tables :as tables)\n(defmacro rows [] (get tables.ROWS "a"))\n',
    "user_0.hy": "(require {pkg}.macros [rows])\n(setv value (rows))\n",
    "user_1.hy": "(require {pkg}.macros [rows])\n(setv value (rows))\n",
    "server.py": (
        "import contextvars, importlib, queue, threading\n"
        "import {pkg}.user_0\n"
        "requests, answers = queue.Queue(), queue.Queue()\n"
        "def serve():\n"
        "    while (name := requests.get()) is not None:\n"
        "        answers.put(importlib.import_module(name))\n"
        "threading.Thread(target=serve, daemon=True).start()\n"
        "CONTEXT = contextvars.copy_context()\n"
    ),
    # While the outermost load body has not finished (standing in for the body running the app), rewrite the table, reload, and have the started thread read user_1.
    "serving.py": (
        "import importlib, pathlib, queue, threading\n"
        "import {pkg}.tables, {pkg}.user_0\n"
        "requests, answers = queue.Queue(), queue.Queue()\n"
        "def serve():\n"
        "    while (name := requests.get()) is not None:\n"
        "        answers.put(importlib.import_module(name))\n"
        "threading.Thread(target=serve, daemon=True).start()\n"
        "table = pathlib.Path({pkg}.tables.__file__)\n"
        "table.write_text(table.read_text().replace('ROWS[\"a\"] = 1', 'ROWS[\"a\"] = 100'))\n"
        "importlib.invalidate_caches()\n"
        "importlib.reload({pkg}.tables)\n"
        'requests.put("{pkg}.user_1")\n'
        "value = answers.get(timeout=30).value\n"
    ),
}


@pytest.mark.parametrize("reader", ["thread", "context"])
def test_an_import_after_the_outer_import_ended_does_not_use_its_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    # Red before the fix (blind A addendum; baseline cf7ea9cc0 is green): the thread started and the context copied inside the outermost load
    # kept holding the memo (the copy of file state and the verified digests) after the load finished. After rewriting tables.py and
    # reloading (table is 100), reading user_1 verified the memo against the old copy, judged it to match now (value stayed 1, no compile).
    tree = _scope_tree(tmp_path, monkeypatch, "macro_use_served", SERVED_FILES)
    tree.run("user_0", "user_1")
    answer = tree.run(
        "server", 'edit:tables.py:ROWS["a"] = 1:ROWS["a"] = 100', "reload:tables", f"server:{reader}:user_1"
    )
    assert "user_1.hy" in answer["compiled"] and answer["values"] == {"user_1": 100}, (
        f"a {reader} started inside the load judged the table reloaded after the load to match now by using the old copy: {answer}"
    )


def test_a_thread_started_inside_a_running_import_does_not_share_its_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keep green (blind A addendum): the memo is used only by the thread that set it. If a thread started while the outermost load body had not finished (a worker of a process
    # running the app in the body) shared the memo, that thread would also judge a table reloaded inside the body to match now by the old copy.
    tree = _scope_tree(tmp_path, monkeypatch, "macro_use_served", SERVED_FILES)
    tree.run("user_0", "user_1")
    answer = tree.run("serving")
    assert "user_1.hy" in answer["compiled"] and answer["values"] == {"serving": 100}, (
        f"a thread started inside the load body judged the table reloaded inside the body to match now by the old copy: {answer}"
    )


def _scope_variable_uses(source: Path) -> tuple[str, ...]:
    """Uses of IMPORT_SCOPE in a source (excluding receivers of set and reset calls), and aliased imports."""
    import ast

    tree = ast.parse(source.read_text(encoding="utf-8"))
    receivers = {
        id(node.func.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("set", "reset")
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "IMPORT_SCOPE"
    }
    names = tuple(
        f"{source.name}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "IMPORT_SCOPE" and id(node) not in receivers
    )
    aliases = tuple(
        f"{source.name}:{node.lineno} (alias {node.asname})"
        for node in ast.walk(tree)
        if isinstance(node, ast.alias) and node.name == "IMPORT_SCOPE" and node.asname not in (None, "IMPORT_SCOPE")
    )
    return names + aliases


def test_the_import_scope_variable_is_read_only_through_current_scope() -> None:
    # Keep green (blind A addendum): the load memo's ContextVar leaves its value in threads started inside the load and in copied contexts,
    # so merely having a value does not establish it as the current load's memo — only import_scope.current_scope() reads it (inside the thread that set it, only before
    # closing). Outside import_scope.py, it is used only for the set and reset calls at the entry that places the memo (carrying it out under an alias cannot constrain reads).
    import doeff_hy_bytecode_guard

    package = Path(doeff_hy_bytecode_guard.__file__).parent
    uses = tuple(
        use
        for source in sorted(package.glob("*.py"))
        if source.name != "_import_scope.py"
        for use in _scope_variable_uses(source)
    )
    assert not uses, f"IMPORT_SCOPE is used outside import_scope.py other than in set and reset calls — reads must go through current_scope(): {uses}"


def test_the_code_reading_entry_is_keyed_by_the_hy_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Red before the fix (blind A addendum): called_modules in the code readings makes hy.R module names with Hy's mangle and slashes2dots, yet
    # the store entry key had no Hy version — even when Hy was upgraded and mangle changed, it looked up the previous Hy's readings (the store spans work trees and versions).
    import hy

    from doeff_hy_bytecode_guard import _macro_use as macro_use

    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(tmp_path))
    key = macro_use.UsedMacroKey("macros", "doeff_hy.macros", "defk")
    entry = macro_use._facts_entry(key)
    assert entry is not None
    monkeypatch.setattr(hy, "__version__", f"{hy.__version__}+other")
    assert macro_use._facts_entry(key) != entry, "the code-readings entry is the same although the Hy version differs"


def test_every_function_the_code_reading_calls_is_in_the_module_its_key_covers() -> None:
    # Keep green (blind A addendum): the key of the code-readings entry covers the reading code with the sha256 of this module's file (_own_digest).
    # If a function the reading calls is moved to another file and imported, fixing that file does not cool the store entry and the reading by the previous method is looked up. Confirm that
    # every function the reading calls by global name is in this module (Hy's functions are covered by the Hy version in the key — import inside the function).
    from types import CodeType, FunctionType

    from doeff_hy_bytecode_guard import _macro_use as macro_use

    def names(code: CodeType) -> set[str]:
        inner = (constant for constant in code.co_consts if isinstance(constant, CodeType))
        return {*code.co_names, *(name for constant in inner for name in names(constant))}

    seen: set[str] = set()
    pending = ["_code_facts"]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        value = vars(macro_use)[name]
        assert value.__module__ == macro_use.__name__, (
            f"{name} called by the code reading is in {value.__module__} — _own_digest does not cover that file"
        )
        # A class (``_Instructions``) reads the code of its methods.
        functions = [v for v in vars(value).values() if isinstance(v, FunctionType)] if isinstance(value, type) else [value]
        # The interpreter's built-in types (``CodeType``, ``FunctionType`` — module is builtins) are covered by the Python version, not by a file.
        for function in functions:
            pending.extend(
                called
                for called in names(function.__code__)
                if isinstance(vars(macro_use).get(called), (FunctionType, type))
                and vars(macro_use)[called].__module__ != "builtins"
            )
    assert {"_code_facts", "_constant_text", "_called_modules", "_reference_module", "_mangle"} <= seen, seen


# ---- Macros that read values determined by environment variables ---
#
# The walk includes standard-library modules by name only (covered by the Python version), but os.environ is process state and neither version nor file covers it. A global of plain values assembled from environment variables
# (MODE in clause 3) enters by value and so is judged stale, but a table assembled from environment variables is covered by the source statement binding its name, and the statement does not carry the environment variable's
# value. A shape where a macro reads it directly at expansion time and a shape where a helper function reads it also include only the code's contents and the name os.

#: Names of environment variables (inherited only by the check's child process — set with monkeypatch).
ENV_MODE = "MACRO_USE_ENV_MODE"

#: Macros that read values determined by environment variables, per shape. table = a dict a statement assembles by reading the environment variable directly (the shape of addendum 3); listed = likewise a list;
#: aliased = a dict assembled from environ imported from os under an alias; got = a dict assembled from getenv imported from os; built = a dict a helper function assembles at load
#: time; py-table = a dict a Python module statement assembles; direct = a macro reads directly at expansion time; helper = a helper
#: function reads at expansion time; local = read through an import inside a function (the module's globals have no os). body and plain are a macro that does not read environment variables and
#: a macro that returns a plain value assembled from environment variables (clause 3 — enters by value). Failure-case shapes: hy-import = reads a table of another module bound by name through a Hy
#: import; default = holds a table assembled from environment variables as the default of a helper function; cell = holds a table assembled at load time
#: in a closure cell (neither is a value that can be looked up by global name).
ENV_FORMS = (
    "table", "listed", "aliased", "got", "built", "py-table", "direct", "helper", "local", "hy-import", "default", "cell",
)
#: Macro module per shape (macros.hy for shapes not listed).
ENV_FORM_MODULES = {
    "local": "local_macros",
    "hy-import": "hy_import_macros",
    "default": "held_macros",
    "cell": "held_macros",
    "fixed": "hy_import_macros",
}
ENV_FILES = {
    "__init__.py": "",
    "tables.py": f'import os\nROWS = {{{{"a": os.environ.get("{ENV_MODE}", "plain")}}}}\nFIXED = {{{{"a": 1}}}}\n',
    "local_macros.hy": f'(defmacro local [] (import os) (os.environ.get "{ENV_MODE}" "plain"))\n',
    "hy_import_macros.hy": (
        '(import {pkg}.tables [ROWS FIXED])\n(defmacro hy-import [] (get ROWS "a"))\n(defmacro fixed [] (get FIXED "a"))\n'
    ),
    "held_macros.hy": (
        "(import os)\n"
        f'(defn by-default [[rows {{{{"a" (os.environ.get "{ENV_MODE}" "plain")}}}}]] (get rows "a"))\n'
        "(defmacro default [] (by-default))\n"
        f'(defn make-reader [] (setv rows {{{{"a" (os.environ.get "{ENV_MODE}" "plain")}}}}) (fn [] (get rows "a")))\n'
        "(setv reader (make-reader))\n"
        "(defmacro cell [] (reader))\n"
    ),
    "macros.hy": (
        "(import os)\n"
        "(import os [environ :as env getenv])\n"
        "(import {pkg}.tables :as tables)\n"
        f'(setv MODE (os.environ.get "{ENV_MODE}" "plain"))\n'
        f'(setv MODES {{{{"a" (os.environ.get "{ENV_MODE}" "plain")}}}})\n'
        f'(setv LISTED [(os.environ.get "{ENV_MODE}" "plain")])\n'
        f'(setv ALIASED {{{{"a" (env.get "{ENV_MODE}" "plain")}}}})\n'
        f'(setv GOT {{{{"a" (getenv "{ENV_MODE}" "plain")}}}})\n'
        f'(defn mode-now [] (os.environ.get "{ENV_MODE}" "plain"))\n'
        '(setv BUILT {{"a" (mode-now)}})\n'
        "(defmacro body [] 1)\n"
        "(defmacro plain [] MODE)\n"
        '(defmacro table [] (get MODES "a"))\n'
        "(defmacro listed [] (get LISTED 0))\n"
        '(defmacro aliased [] (get ALIASED "a"))\n'
        '(defmacro got [] (get GOT "a"))\n'
        '(defmacro built [] (get BUILT "a"))\n'
        '(defmacro py-table [] (get tables.ROWS "a"))\n'
        f'(defmacro direct [] (os.environ.get "{ENV_MODE}" "plain"))\n'
        "(defmacro helper [] (mode-now))\n"
    ),
    **{
        f"u_{form.replace('-', '_')}.hy": (
            f"(require {{pkg}}.{ENV_FORM_MODULES.get(form, 'macros')} [{form}])\n(setv value ({form}))\n"
        )
        for form in (*ENV_FORMS, "body", "plain", "fixed")
    },
}


@pytest.mark.parametrize("form", ENV_FORMS)
def test_a_macro_reading_a_value_decided_by_the_environment_is_stale_when_it_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    # Red before the fix (addendum 3 — present since baseline cf7ea9cc0): a new process with only the environment variable changed judged the consumer's .pyc record to match now and
    # used the previous process's expansion ("plain") where re-expansion would give "fancy". The B1 clause (a value determined outside files, such as an environment variable, makes it stale if different).
    monkeypatch.delenv(ENV_MODE, raising=False)
    tree = _scope_tree(tmp_path, monkeypatch, "macro_use_env", ENV_FILES)
    user = f"u_{form.replace('-', '_')}"
    assert tree.run(user)["values"] == {user: "plain"}
    monkeypatch.setenv(ENV_MODE, "fancy")  # inherited only by the second process
    answer = tree.run(user)
    assert f"{user}.hy" in answer["compiled"], f"judged the consumer to match now although the environment variable value read by the macro ({form}) differs: {answer}"
    assert answer["values"] == {user: "fancy"}, f"the value of the re-expanded consumer does not match the current environment variable: {answer}"


def test_macros_that_do_not_read_the_environment_stay_checkable_beside_ones_that_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keep green: reading an environment variable changes the check of only the macro that hit it. A macro in the same module that does not read environment variables (body),
    # a macro that enters a plain value assembled from environment variables by value (plain — clause 3), and a macro that reads a table imported in the same Hy import form
    # alongside a table assembled from environment variables (fixed — names that are siblings in the import form are not traced) do not compile in the next process
    # with the same environment, and when the environment variable changes, only plain is re-expanded.
    monkeypatch.delenv(ENV_MODE, raising=False)
    tree = _scope_tree(tmp_path, monkeypatch, "macro_use_env", ENV_FILES)
    tree.run("u_body", "u_plain", "u_fixed")
    assert tree.run("u_body", "u_plain", "u_fixed")["compiled"] == [], "the second process compiled although nothing changed"
    monkeypatch.setenv(ENV_MODE, "fancy")
    answer = tree.run("u_body", "u_plain", "u_fixed")
    assert answer["compiled"] == ["u_plain.hy"], (
        f"the process with the changed environment variable either compiled even consumers of macros that do not read environment variables, or did not notice the change in the plain value: {answer}"
    )
    assert answer["values"] == {"u_body": 1, "u_plain": "fancy", "u_fixed": 1}, answer


def test_a_row_of_an_environment_read_is_stale_even_when_the_walk_now_compares_files() -> None:
    # Keep green: a record row that hit an environment variable read is stale even when the current walk hits a module midway through import and compares by file rows (digest is
    # None) — a file does not reflect a difference in environment variables. Even when the current walk hit an environment variable read, it is stale even if it has the same mark as the record.
    from doeff_hy_bytecode_guard import _macro_use as macro_use

    for recorded, now in (
        (records.ENVIRONMENT_READ, None),
        (records.ENVIRONMENT_READ, records.ENVIRONMENT_READ),
        (None, records.ENVIRONMENT_READ),
        ("0" * 64, records.ENVIRONMENT_READ),
    ):
        check = macro_use._row_check(recorded, macro_use.ClosureDigest(now, ()))
        assert check is macro_use.RowCheck.STALE, f"judged the row with record {recorded!r} and now {now!r} to be {check}"


def test_an_environment_reading_user_is_compiled_again_after_its_macro_changed_in_another_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keep green: the record of a consumer of a macro that reads an environment variable cannot be checked
    # even in a process with the same environment variables — even on the path that looks up the shared store. A load entry that checks with the mark rows removed from the record and reuses a store entry keyed by a fingerprint of environment variables
    # judged the previous expansion ("plain") to match now merely because the environment variables were the same, even after the macro body was changed in another work tree.
    import shutil

    monkeypatch.delenv(ENV_MODE, raising=False)
    tree = _scope_tree(tmp_path, monkeypatch, "macro_use_env", ENV_FILES)
    assert tree.run("u_direct")["values"] == {"u_direct": "plain"}
    other = ScopeTree(tmp_path / "tree-b", tree.package)
    shutil.copytree(tree.root, other.root, ignore=shutil.ignore_patterns("__pycache__"))
    _replace(
        other.root / tree.package / "macros.hy",
        f'(defmacro direct [] (os.environ.get "{ENV_MODE}" "plain"))',
        f'(defmacro direct [] (.upper (os.environ.get "{ENV_MODE}" "plain")))',
    )
    answer = other.run("u_direct")
    assert "u_direct.hy" in answer["compiled"], f"judged the consumer in the tree where the macro was changed to match now by the store's previous expansion: {answer}"
    assert answer["values"] == {"u_direct": "PLAIN"}, answer


def test_the_environment_mark_is_read_only_by_the_walk_and_the_check() -> None:
    # Keep green (blind B): the meaning of the mark ENVIRONMENT_READ (instead of the whole closure digest — cannot be checked) is held only by the definition point (records.py) and by the
    # walk that makes the mark and the check that checks the mark (macro_use.py). If the load entry or similar discerned mark rows and rewrote the record, the check's decision would
    # move to that entry (the shape of checking a record with mark rows removed).
    import ast

    import doeff_hy_bytecode_guard

    package = Path(doeff_hy_bytecode_guard.__file__).parent
    uses = tuple(
        f"{source.name}:{getattr(node, 'lineno', 0)}"
        for source in sorted(package.glob("*.py"))
        if source.name not in ("_records.py", "_macro_use.py")
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
        if (isinstance(node, ast.Name) and node.id == "ENVIRONMENT_READ")
        or (isinstance(node, ast.Attribute) and node.attr == "ENVIRONMENT_READ")
        or (isinstance(node, ast.alias) and node.name == "ENVIRONMENT_READ")
    )
    assert not uses, f"the mark ENVIRONMENT_READ is read outside records.py and macro_use.py — the decision about the mark belongs to the check (macro_use): {uses}"


# ---- A function-local require table goes only into the record of the module being expanded ----


def _expand_on_its_own(name: str, source: str, filename: str, entered, before) -> records.MacroRecord:
    """Expand a source in the same shape as the analyzer's expansion (hy_compile inside macro_recording) and return the record — entered is a notice that the expansion's wrapper was entered,
    before is a wait before compile (to fix the thread order)."""
    import types

    import hy
    import hy.compiler
    from doeff_hy_bytecode_guard._loader_hooks import macro_recording

    scratch = types.ModuleType(name)
    scratch.__file__ = filename
    with macro_recording(scratch) as recording:
        entered()
        before()
        hy.compiler.hy_compile(hy.read_many(source, filename=filename), scratch, filename=filename, source=source)
    return recording.record(source)


def test_a_local_macro_of_a_module_compiled_inside_another_expansion_stays_out_of_its_record(tree: Tree) -> None:
    # Blind B: a shape that wraps the entry that remembers the local table only for the duration of the expansion's with (holding the record table in a closure) put the local table of a module compiled inside an outer expansion
    # into the outer record, and it went cold from a change to a macro the outer consumer does not use. The local table goes only into the record of the module being compiled.
    (tree.directory / "inner.hy").write_text(
        f"(defmacro wrap [x] x)\n(defn f []\n  (require {tree.package}.macros [used])\n  (used))\n", encoding="utf-8"
    )
    (tree.directory / "outer.hy").write_text(
        f"(require {tree.package}.inner [wrap])\n(setv value (wrap 1))\n", encoding="utf-8"
    )
    record = tree.record("outer")
    assert [(used.module, used.name) for used in record.macros] == [(f"{tree.package}.inner", "wrap")], record.macros
    _replace(tree.macros, "(+ (helper 1) consts.OFFSET)", "(+ (helper 20) consts.OFFSET)")
    assert tree.is_current(record), "judged stale merely because a local macro of a nested-compiled module, which the outer consumer does not use, changed"


def test_a_local_require_expanded_while_another_expansion_ends_on_another_thread_is_recorded(tree: Tree) -> None:
    # Blind B: in a shape that puts the wrapper on and off per expansion, if the expansion of another module that started earlier ends in the middle of a later expansion, it also removes the wrapper
    # remembering the later expansion's local table, and the macro of a require inside a function leaked out of the record (the shape where the analyzer's expansions run side by side on free-threaded Python).
    import threading

    quiet_entered, local_entered, quiet_exited = threading.Event(), threading.Event(), threading.Event()
    made: dict[str, records.MacroRecord] = {}
    local = f"(defn f []\n  (require {tree.package}.macros [used])\n  (used))\n(setv value (f))\n"

    def run_quiet() -> None:
        try:
            made["quiet"] = _expand_on_its_own(
                f"{tree.package}.quiet", "(setv value 1)\n", str(tree.directory / "quiet.hy"),
                quiet_entered.set, lambda: local_entered.wait(10),
            )
        finally:
            quiet_exited.set()

    def run_local() -> None:
        quiet_entered.wait(10)
        made["local"] = _expand_on_its_own(
            f"{tree.package}.local_thread", local, str(tree.directory / "local_thread.hy"),
            local_entered.set, lambda: quiet_exited.wait(10),
        )

    workers = [threading.Thread(target=run_quiet), threading.Thread(target=run_local)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(60)
    assert [used.name for used in made["local"].macros] == ["used"], made["local"].macros
    assert made["quiet"].macros == (), made["quiet"].macros
