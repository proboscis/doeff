"""The macro-expanded tree of a Hy module is cached on disk under its exact inputs.

Expansion is most of an analysis' time and every process used to redo it for each
Hy module it read. The tree is kept under the source's content (never its path), the
module name, every macro the expansion used, the Hy / Python versions and the code of
the expansion (``hy_expansion``); what the reader derives from it is kept apart under
the same key and the reader's own source — so a changed source, macro or expansion is
expanded again, a changed reader only builds what it derives again (ki-81f1c868203f),
the same source in another checkout is not expanded again, and an unreadable cache
file only costs one expansion.
"""

import ast
import contextlib
import importlib
import inspect
import pickle
import re
import shutil
import sys
import types
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

from doeff_effect_analyzer import hy_expansion  # noqa: E402
from doeff_effect_analyzer import program_effects as pe  # noqa: E402

SOURCE = """
(require doeff-hy.macros [defk <-])
(setv answer 42)
"""


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "trees"
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", str(directory))
    return directory


@pytest.fixture(autouse=True)
def import_leaves_no_tree(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests count the analyzer's own expansions and entries, so a module they import must not also leave
    its tree for the analysis (``doeff_hy_bytecode_guard.expansion_trees``); the tests that ask for
    ``import_trees_on`` are the ones about that."""
    if "import_trees_on" not in request.fixturenames:
        from doeff_hy_bytecode_guard import expansion_trees

        monkeypatch.setattr(expansion_trees, "add_import_tree", lambda *args, **kwargs: None)


@pytest.fixture
def import_trees_on() -> None:
    """Asked for by a test about the trees an import leaves (see ``import_leaves_no_tree``)."""


def expansions(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Count how often the real expansion runs (the cache must spare the second one)."""
    seen: list[str] = []
    real = hy_expansion._expand_hy

    def counting(source: str, filename: str, module_name: str) -> hy_expansion._Expanded:
        seen.append(module_name)
        return real(source, filename, module_name)

    monkeypatch.setattr(hy_expansion, "_expand_hy", counting)
    return seen


def entries(cache_dir: Path) -> list[Path]:
    """Every entry of what the reader derives: one directory per source and reader, one entry in it
    per set of macros used."""
    return sorted(cache_dir.glob("*/*.pickle"))


def expansion_entries(cache_dir: Path) -> list[Path]:
    """Every entry of the expanded trees: one directory per source, one entry in it per set of macros used."""
    return sorted((cache_dir / "expansions").glob("*/*.pickle"))


def test_the_second_read_of_the_same_source_comes_from_the_cache(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = expansions(monkeypatch)
    first = pe._compile_hy(SOURCE, "/src/m.hy", "m")
    second = pe._compile_hy(SOURCE, "/src/m.hy", "m")
    assert seen == ["m"]
    assert ast.dump(first.tree) == ast.dump(second.tree)
    assert len(entries(cache_dir)) == 1


def test_a_changed_source_is_expanded_again(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = expansions(monkeypatch)
    pe._compile_hy(SOURCE, "/src/m.hy", "m")
    pe._compile_hy(SOURCE.replace("42", "43"), "/src/m.hy", "m")
    assert seen == ["m", "m"]


@dataclass(frozen=True)
class TwoCheckouts:
    """One module file written into two directories — the same file in two checkouts of one repo."""

    first: Path
    second: Path


def written_file(directory: Path, name: str, text: str) -> Path:
    """``directory/name`` holding ``text`` (the directory is made here)."""
    directory.mkdir()
    (directory / name).write_text(text, encoding="utf-8")
    return directory / name


def two_checkouts(tmp_path: Path, name: str, first: str, second: str) -> TwoCheckouts:
    """Write ``first`` as ``checkout_a/<name>`` and ``second`` as ``checkout_b/<name>``."""
    return TwoCheckouts(
        first=written_file(tmp_path / "checkout_a", name, first),
        second=written_file(tmp_path / "checkout_b", name, second),
    )


def compile_file(path: Path) -> "pe._WholeTree | pe._ChunkedTree":
    """Expand the file as the reader does (``_module_source`` reads the text and passes its path)."""
    return pe._compile_hy(path.read_text(encoding="utf-8"), str(path), "m")


def test_the_same_source_in_another_directory_is_read_from_the_cache(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agora-redesign #3598: the key named the file's absolute path, so every new worktree expanded
    every Hy module again although its source was the same (a closure test of the screen job: 120.7 s
    cold, 5.9 s warm, 71 % of the cold samples in ``_expand_hy``). The tree depends on the source,
    not on where it lies: the same source in another directory is read from the cache."""
    seen = expansions(monkeypatch)
    files = two_checkouts(tmp_path, "m.hy", SOURCE, SOURCE)
    compile_file(files.first)
    a_new_process()
    read = compile_file(files.second)
    assert seen == ["m"]
    assert isinstance(read.tree, ast.Module)
    assert len(entries(cache_dir)) == 1


def test_one_changed_character_in_another_directory_is_expanded_again(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = expansions(monkeypatch)
    files = two_checkouts(tmp_path, "m.hy", SOURCE, SOURCE.replace("42", "43"))
    compile_file(files.first)
    a_new_process()
    compile_file(files.second)
    assert seen == ["m", "m"]
    assert len(entries(cache_dir)) == 2


ASKING = """
(require doeff-hy.macros [defk <-])
(import doeff_core_effects.effects [Ask])

(defk asks [conv]
  {:pre [(: conv str)] :post [(: % str)]}
  (<- who str (Ask "worker"))
  (+ conv who))
"""


def analyzed_from(file: Path, monkeypatch: pytest.MonkeyPatch) -> pe.ProgramEffects:
    """Import the module of ``file`` from its directory, as a process of that checkout does, and
    analyze its ``asks`` in a new process's memory (only the disk cache is shared)."""
    name = file.stem
    monkeypatch.syspath_prepend(str(file.parent))
    importlib.invalidate_caches()
    try:
        module = importlib.import_module(name)
        assert module.__file__ == str(file)
        a_new_process()
        return pe.analyze_program(f"{name}:asks")
    finally:
        sys.modules.pop(name, None)
        sys.path.remove(str(file.parent))


def test_a_tree_read_from_the_cache_reports_the_path_it_was_read_for(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tree expanded for one checkout and read for another reports the other checkout's path: the
    cached tree and its body facts hold no path, and the report takes the path of the module it
    reads (``_ModuleSource.filename``)."""
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")  # each checkout compiles its own module
    seen = expansions(monkeypatch)
    name = f"tree_cache_{uuid.uuid4().hex[:8]}"
    files = two_checkouts(tmp_path, f"{name}.hy", ASKING, ASKING)
    first = analyzed_from(files.first, monkeypatch)
    second = analyzed_from(files.second, monkeypatch)
    assert seen == [name]
    assert [use.effect.__name__ for use in first.effects] == ["Ask"]
    assert [use.effect.__name__ for use in second.effects] == ["Ask"]
    assert {use.location.file for use in first.effects} == {str(files.first)}
    assert {use.location.file for use in second.effects} == {str(files.second)}
    assert str(files.first.parent) not in repr(second)


TWICE = "(defmacro twice [x] `(* 2 ~x))\n"
ANSWER = "(require {pkg}.b [twice])\n(defmacro answer [] (twice 21))\n"
USES_ANSWER = "(require {pkg}.a [answer])\n(setv value (answer))\n"


def macro_package(root: Path, package: str, twice: str) -> Path:
    """A package in ``root``: ``m`` requires ``a``'s macro, which is written with ``b``'s macro."""
    directory = root / package
    directory.mkdir(parents=True)
    (directory / "__init__.py").write_text("", encoding="utf-8")
    (directory / "b.hy").write_text(twice, encoding="utf-8")
    (directory / "a.hy").write_text(ANSWER.replace("{pkg}", package), encoding="utf-8")
    (directory / "m.hy").write_text(USES_ANSWER.replace("{pkg}", package), encoding="utf-8")
    return root


def expanded_value(root: Path, package: str, monkeypatch: pytest.MonkeyPatch) -> object:
    """Import ``m`` from ``root`` (and so its macro modules), expand it in a new process's memory,
    and return the value ``value`` is set to in its tree."""
    monkeypatch.syspath_prepend(str(root))
    importlib.invalidate_caches()
    try:
        module = importlib.import_module(f"{package}.m")
        path = Path(str(module.__file__))
        a_new_process()
        tree = pe._compile_hy(path.read_text(encoding="utf-8"), str(path), module.__name__).tree
        return next(
            node.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        )
    finally:
        for name in [name for name in sys.modules if name == package or name.startswith(f"{package}.")]:
            sys.modules.pop(name)
        sys.path.remove(str(root))


def test_a_changed_macro_module_that_a_required_macro_module_requires_is_expanded_again(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agora-redesign #3598: without the path in the key, checkouts of different doeff versions share
    entries, so the key must name every macro file the expansion used — not only the modules the
    source requires itself. ``m`` requires ``a``; ``a``'s macro is written with ``b``'s; the two
    checkouts differ only in one character of ``b``, and ``m`` is expanded again with it."""
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")  # each checkout compiles its own modules
    seen = expansions(monkeypatch)
    package = f"tree_macros_{uuid.uuid4().hex[:8]}"
    first = macro_package(tmp_path / "checkout_a", package, TWICE)
    second = macro_package(tmp_path / "checkout_b", package, TWICE.replace("2", "3"))
    values = [expanded_value(root, package, monkeypatch) for root in (first, second)]
    assert seen == [f"{package}.m", f"{package}.m"]
    assert values == [42, 63]


USED_AND_UNUSED = "(defmacro used [] 42)\n(defmacro unused [] 100)\n"
USES_USED = "(require {pkg}.a [used])\n(setv value (used))\n"


def used_and_unused(root: Path, package: str) -> Path:
    """A package in ``root``: ``a`` has two macros, ``used`` and ``unused``; ``m`` requires only ``used``."""
    directory = root / package
    directory.mkdir(parents=True)
    (directory / "__init__.py").write_text("", encoding="utf-8")
    (directory / "a.hy").write_text(USED_AND_UNUSED, encoding="utf-8")
    (directory / "m.hy").write_text(USES_USED.replace("{pkg}", package), encoding="utf-8")
    return root


def edited(path: Path, old: str, new: str) -> None:
    """Replace ``old`` by ``new`` in ``path`` (a change of size, so a remembered digest is not reused)."""
    text = path.read_text(encoding="utf-8")
    assert old in text and len(old) != len(new)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_a_changed_body_of_the_used_macro_is_expanded_again(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record names the macros an expansion used: a change of the body of one of them misses."""
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    seen = expansions(monkeypatch)
    package = f"tree_used_{uuid.uuid4().hex[:8]}"
    root = used_and_unused(tmp_path / "checkout", package)
    assert expanded_value(root, package, monkeypatch) == 42
    edited(root / package / "a.hy", "(defmacro used [] 42)", "(defmacro used [] 4242)")
    assert expanded_value(root, package, monkeypatch) == 4242
    assert seen == [f"{package}.m", f"{package}.m"]


def test_a_changed_unused_macro_is_read_from_the_cache(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A change of a macro the expansion did not use keeps the entry (red before the record named
    the macros used: it named the file of ``a``, so any line of it missed)."""
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    seen = expansions(monkeypatch)
    package = f"tree_unused_{uuid.uuid4().hex[:8]}"
    root = used_and_unused(tmp_path / "checkout", package)
    assert expanded_value(root, package, monkeypatch) == 42
    edited(root / package / "a.hy", "(defmacro unused [] 100)", "(defmacro unused [] 1000)")
    assert expanded_value(root, package, monkeypatch) == 42
    assert seen == [f"{package}.m"], "a change of an unused macro expanded the module again"


def test_an_expansion_records_the_macros_it_used_by_their_digest() -> None:
    """The source requires ``defk`` and ``<-`` of ``doeff-hy.macros`` and uses neither: the record an
    entry is named by and read under (the record the import side keeps) names no macro, and no whole
    require (the names are chosen, so a macro added to the provider never enters the table). A
    source that calls ``defk``
    names it with the digest of its code's closure."""
    record = hy_expansion._expand_hy(SOURCE, "/src/m.hy", "m").macros
    assert record.macros == ()
    assert record.providers == ()  # names chosen in the require: only a whole require is kept
    used = hy_expansion._expand_hy(DEFS, "/src/m.hy", "m").macros
    assert [(row.module, row.name) for row in used.macros] == [("doeff_hy.macros", "defk")]
    assert len(used.macros[0].digest) == 64


def test_off_turns_the_cache_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", "off")
    seen = expansions(monkeypatch)
    pe._compile_hy(SOURCE, "/src/m.hy", "m")
    pe._compile_hy(SOURCE, "/src/m.hy", "m")
    assert seen == ["m", "m"]


def test_an_unreadable_cache_file_is_expanded_again_and_rewritten(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = expansions(monkeypatch)
    pe._compile_hy(SOURCE, "/src/m.hy", "m")
    (cached,) = entries(cache_dir)
    (stored,) = expansion_entries(cache_dir)
    cached.write_bytes(b"not a pickle")
    stored.write_bytes(b"not a pickle")
    tree = pe._compile_hy(SOURCE, "/src/m.hy", "m")
    assert seen == ["m", "m"]
    assert isinstance(tree.tree, ast.Module)
    assert pe._read_cached_tree(cached) is not None
    assert hy_expansion._read_expansion(stored) is not None


def test_an_unreadable_derived_entry_is_built_again_from_the_stored_tree(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = expansions(monkeypatch)
    pe._compile_hy(SOURCE, "/src/m.hy", "m")
    (cached,) = entries(cache_dir)
    cached.write_bytes(b"not a pickle")
    tree = pe._compile_hy(SOURCE, "/src/m.hy", "m")
    assert seen == ["m"]
    assert isinstance(tree.tree, ast.Module)
    assert pe._read_cached_tree(cached) is not None


def test_an_observer_sees_only_the_expansions_that_missed_the_cache(cache_dir: Path) -> None:
    import contextlib

    entered: list[str] = []

    @contextlib.contextmanager
    def watching():
        entered.append("expansion")
        yield

    stop = pe.observe_expansions(watching)
    try:
        pe._compile_hy(SOURCE, "/src/m.hy", "m")
        pe._compile_hy(SOURCE, "/src/m.hy", "m")
    finally:
        stop()
    pe._compile_hy(SOURCE.replace("42", "44"), "/src/m.hy", "m")
    assert entered == ["expansion"]


DEFS = """
(require doeff-hy.macros [defk <-])
(import os.path [join])
(defk outer [x] {:pre [(: x int)] :post [(: % int)]}
  (setv y (+ x 1))
  (defk inner [z] {:pre [(: z int)] :post [(: % int)]} (+ z y))
  y)
"""


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def function_at(qualname: str, *, line: int, params: tuple[str, ...]) -> SimpleNamespace:
    """A stand-in for the function a def compiles to — ``find`` reads its qualname, its first
    line and its positional parameter names, nothing else."""
    return SimpleNamespace(
        __qualname__=qualname,
        __code__=SimpleNamespace(co_firstlineno=line, co_varnames=params, co_argcount=len(params)),
    )


def functions_named(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """Every def and lambda of ``tree`` with the qualname its function would carry."""
    return [
        (".".join(path), node)
        for path, nodes in pe._definitions_by_path(tree).items()
        for node in nodes
        if isinstance(node, FUNCTIONS)
    ]


def nested_in(qualnames: Iterable[str], outer: str, inner: str) -> str:
    """The qualname of the def ``inner`` written in the body of ``outer``. doeff-hy writes a
    definition in a function body inside a function it calls at once (card ki-964823cd7feb),
    so the qualname has that function's name between the two."""
    [qualname] = [q for q in qualnames if q.startswith(f"{outer}.") and q.endswith(f".{inner}")]
    return qualname


def a_new_process() -> None:
    """Forget everything derived in memory, as a new process starts."""
    pe._MODULE_CACHE.clear()
    pe._DEFINITION_INDEX.clear()
    pe._BODY_FACTS.clear()
    pe._BODY_NODES.clear()


def test_a_cached_tree_finds_the_same_def_with_its_body_facts_without_walking(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agora-redesign #1586 / #1590 / #1591: the definition index and the body nodes and facts
    of every def are stored with the tree, so a process that reads the cached tree does not
    walk the module and every def again — and chooses the same def, with the same facts, as
    ``_find_function`` does on the whole tree (nested defs included; the line and the
    parameters decide between candidates)."""
    written = pe._compile_hy(DEFS, "/src/defs.hy", "defs").tree
    named = functions_named(written)
    qualnames = {qualname for qualname, _ in named}
    assert "outer" in qualnames and nested_in(qualnames, "outer", "inner")
    asked = []
    for qualname, node in named:
        for line in (node.lineno, node.lineno + 1):
            for params in (pe._definition_params(node), ("other",)):
                function = function_at(qualname, line=line, params=params)
                expected = pe._find_function(written, function)
                assert expected is not None
                facts = pe._body_facts(expected)
                asked.append((function, ast.dump(expected), sorted(facts.names), sorted(facts.imports)))
    a_new_process()
    walked: list[str] = []
    monkeypatch.setattr(pe, "_read_body_facts", lambda f: walked.append("facts") or (_ for _ in ()).throw(AssertionError("rebuilt")))
    monkeypatch.setattr(pe, "_direct_definitions", lambda m: walked.append("index") or iter(()))
    read = pe._compile_hy(DEFS, "/src/defs.hy", "defs")
    for function, node, names, imports in asked:
        found = read.find(function)
        assert found is not None and ast.dump(found) == node
        facts = pe._body_facts(found)
        assert (sorted(facts.names), sorted(facts.imports)) == (names, imports)
    assert walked == [], walked
    assert ast.dump(read.tree) == ast.dump(written)


TWO = """
(require doeff-hy.macros [defk <-])
(defk first-one [x] {:pre [(: x int)] :post [(: % int)]} (+ x 1))
(setv unrelated 1)
(defk second-one [y] {:pre [(: y int)] :post [(: % int)]}
  (defk inside [z] {:pre [(: z int)] :post [(: % int)]} (+ z y))
  y)
"""


def test_a_cached_tree_builds_only_the_definition_it_is_asked_for(cache_dir: Path) -> None:
    """agora-redesign #1591: building the whole cached tree was most of what was left of an
    analysis (0.41–0.45 s of 1.17 s for one closure test), although the reader follows a few
    defs of each module. Finding a def builds only its top-level definition (a nested def
    comes with it, as the same nodes); the whole tree is built only when asked for."""
    written = pe._compile_hy(TWO, "/src/two.hy", "two").tree
    named = dict(functions_named(written))
    inside_name = nested_in(named, "second_one", "inside")
    inside, outer = named[inside_name], named["second_one"]
    a_new_process()
    read = pe._compile_hy(TWO, "/src/two.hy", "two")
    found_inside = read.find(function_at(inside_name, line=inside.lineno, params=pe._definition_params(inside)))
    assert found_inside is not None and ast.dump(found_inside) == ast.dump(inside)
    assert set(read._loaded) == {1}
    found_outer = read.find(function_at("second_one", line=outer.lineno, params=pe._definition_params(outer)))
    assert found_outer is not None and any(node is found_inside for node in ast.walk(found_outer))
    assert set(read._loaded) == {1} and read._tree is None


REWRAPPED = """
(defn clause [effect wrap]
  (setv prog effect.program)
  (setv prog (wrap prog))
  prog)
"""


def as_the_older_reader_wrote(raw: bytes) -> bytes:
    """A pickled chunk with the body facts in the shape the reader before #2973 stored them:
    ``rewraps`` maps a local to its one first value (now a tuple of first values)."""
    chunk = pickle.loads(raw)
    older_facts = tuple(
        (function, replace(facts, rewraps={name: seeds[0] for name, seeds in facts.rewraps.items()}))
        for function, facts in chunk.body_facts
    )
    return pickle.dumps(replace(chunk, body_facts=older_facts), protocol=pickle.HIGHEST_PROTOCOL)


@dataclass(frozen=True)
class StoredEntry:
    """What one entry file holds, in its order: the record of the macro files, then the tree."""

    macros: object
    tree: object


def read_entry(path: Path) -> StoredEntry:
    with path.open("rb") as handle:
        return StoredEntry(macros=pickle.load(handle), tree=pickle.load(handle))


def write_entry(path: Path, entry: StoredEntry) -> None:
    with path.open("wb") as handle:
        pickle.dump(entry.macros, handle, protocol=pickle.HIGHEST_PROTOCOL)
        pickle.dump(entry.tree, handle, protocol=pickle.HIGHEST_PROTOCOL)


def test_an_entry_written_by_another_reader_is_not_read(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """agora-redesign #2973: the body facts stored with a tree are built by the reader's code.
    #2973 changed ``_BodyFacts.rewraps`` from one first value per local to a tuple of them and
    kept the key, so the newer reader read the older reader's entries and raised
    ``TypeError: 'Attribute' object is not iterable`` on ``prog = effect.program`` … ``prog =
    wrap(prog)``. The key of what the reader derives names the reader's source, so an entry the
    older reader wrote (the older shape, stored under its own digest) is not read: the tree is read
    from the expansion's store (not expanded again — ki-81f1c868203f) and its facts are this
    reader's."""
    seen = expansions(monkeypatch)
    reader = pe._reader_digest
    monkeypatch.setattr(pe, "_reader_digest", lambda: "reader=an older reader")
    pe._compile_hy(REWRAPPED, "/src/r.hy", "r")
    (entry,) = entries(cache_dir)
    stored = read_entry(entry)
    assert isinstance(stored.tree, pe._CachedTree)
    older_chunks = tuple(as_the_older_reader_wrote(raw) for raw in stored.tree.chunks)
    write_entry(entry, replace(stored, tree=replace(stored.tree, chunks=older_chunks)))
    a_new_process()
    monkeypatch.setattr(pe, "_reader_digest", reader)
    pe._compile_hy(REWRAPPED, "/src/r.hy", "r")
    assert seen == ["r"]
    place = pe._hy_cache_place(REWRAPPED, "r")
    assert place is not None
    assert place != entry.parent
    (path,) = sorted(place.glob("*.pickle"))
    written = read_entry(path).tree
    assert isinstance(written, pe._CachedTree)
    rewraps = [facts.rewraps for raw in written.chunks for _, facts in pickle.loads(raw).body_facts]
    assert rewraps == [{"prog": rewraps[0]["prog"]}], rewraps
    assert isinstance(rewraps[0]["prog"], tuple), rewraps


def test_writing_the_cache_entry_is_inside_the_observed_miss(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """agora-redesign #1590: building and writing what is cached with the tree (the derived values)
    is part of the miss the budget subtracts — outside it, a cold cache would count against the test."""
    import contextlib

    inside: list[bool] = []
    state = {"observing": False}

    @contextlib.contextmanager
    def watching():
        state["observing"] = True
        try:
            yield
        finally:
            state["observing"] = False

    real = pe._write_cached_tree
    monkeypatch.setattr(pe, "_write_cached_tree", lambda path, tree: inside.append(state["observing"]) or real(path, tree))
    stop = pe.observe_expansions(watching)
    try:
        pe._compile_hy(SOURCE, "/src/w.hy", "w")
    finally:
        stop()
    assert inside == [True]


ChangeFile = Callable[..., None]


@pytest.fixture
def changed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ChangeFile]:
    """Make the analyzer read as the next commit would have it, for the digests of its own files a
    cache key names (``_reader_digest`` — every file of the package, ``_expander_digest`` — the
    expansion's file): the package's files are copied, ``changed`` (by default ``module``'s file; None
    = none) gets one more line, and ``module`` reads the copy as its file. Nothing else changes."""

    def change(module: ModuleType, changed: str | None = "") -> None:
        source = Path(str(module.__file__))
        copy = tmp_path / "changed"
        shutil.copytree(source.parent, copy, ignore=shutil.ignore_patterns("__pycache__"))
        if changed is not None:
            edited_file = copy / (changed or source.name)
            edited_file.write_text(edited_file.read_text(encoding="utf-8") + "# one more line\n", encoding="utf-8")
        monkeypatch.setattr(module, "__file__", str(copy / source.name))
        forget_digests()

    yield change
    forget_digests()  # the next call reads the module's own file again (monkeypatch restores it after)


def forget_digests() -> None:
    """Forget the digests of the analyzer's own files, so the next key reads them again."""
    pe._reader_digest.cache_clear()
    hy_expansion._expander_digest.cache_clear()


def the_answer(report: pe.ProgramEffects) -> str:
    """The answer of an analysis, without the addresses of the objects it names."""
    return re.sub(r"0x[0-9a-f]+", "0x", repr(report))


def test_a_changed_reader_expands_nothing_again_and_answers_the_same(
    cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed_file: ChangeFile
) -> None:
    """ki-81f1c868203f: the tree was kept only with what the reader derives from it, under a key
    naming the reader's source, so any change of program_effects.py expanded every Hy module again —
    the screen job's closure test took 257.3 s in the daily run after one (212 s of 244 s expansion
    when measured by hand). The tree does not depend on the reader: after a change of
    program_effects.py alone, the first analysis expands nothing, builds what this reader derives,
    and answers as before."""
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")
    seen = expansions(monkeypatch)
    name = f"tree_reader_{uuid.uuid4().hex[:8]}"
    file = written_file(tmp_path / "checkout", f"{name}.hy", ASKING)
    before = analyzed_from(file, monkeypatch)
    changed_file(pe)
    after = analyzed_from(file, monkeypatch)
    assert seen == [name], "a change of the reader expanded the module again"
    assert the_answer(after) == the_answer(before)
    assert [use.effect.__name__ for use in after.effects] == ["Ask"]
    assert len(entries(cache_dir)) == 2  # one entry of what is derived per reader
    assert len(expansion_entries(cache_dir)) == 1


def test_building_what_a_changed_reader_derives_is_inside_the_observed_miss(
    cache_dir: Path, changed_file: ChangeFile
) -> None:
    """The tree read from the expansion's store and the derived values built for the changed reader
    are paid once per change, like an expansion: the budget subtracts them."""
    watching = mock.Mock(side_effect=contextlib.nullcontext)
    pe._compile_hy(SOURCE, "/src/m.hy", "m")
    changed_file(pe)
    a_new_process()
    stop = pe.observe_expansions(watching)
    try:
        pe._compile_hy(SOURCE, "/src/m.hy", "m")
        pe._compile_hy(SOURCE, "/src/m.hy", "m")
    finally:
        stop()
    assert watching.call_count == 1


def test_a_changed_expansion_is_expanded_again(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, changed_file: ChangeFile
) -> None:
    """How a module is expanded is the code of ``hy_expansion``: its source is in the key, so a tree
    another version of it made is not read, and the reader's entries name it too."""
    seen = expansions(monkeypatch)
    pe._compile_hy(SOURCE, "/src/m.hy", "m")
    changed_file(hy_expansion)
    a_new_process()
    pe._compile_hy(SOURCE, "/src/m.hy", "m")
    assert seen == ["m", "m"]
    assert len(expansion_entries(cache_dir)) == 2


def test_a_changed_module_of_the_reader_builds_what_is_derived_again(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, changed_file: ChangeFile
) -> None:
    """Design check lt-DMXJNAWWMCFNV7F1QX3GJZ574H, blind A: the reader's digest named
    program_effects.py alone, so a helper of the body facts split into a module of its own and
    changed there read the facts the older rule had built (``rewraps`` empty where the new rule finds
    ``prog``). Every file of the package is the reader: a change of another one builds what is
    derived again — and still expands nothing."""
    seen = expansions(monkeypatch)
    pe._compile_hy(SOURCE, "/src/m.hy", "m")
    changed_file(pe, "handler_effects.py")
    a_new_process()
    pe._compile_hy(SOURCE, "/src/m.hy", "m")
    assert seen == ["m"]
    assert len(entries(cache_dir)) == 2


def test_the_same_files_of_the_reader_elsewhere_read_the_same_entry(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, changed_file: ChangeFile
) -> None:
    """The reader's digest is made of the files' names and contents, not of where they lie: another
    checkout of the same version reads what this one derived."""
    pe._compile_hy(SOURCE, "/src/m.hy", "m")
    changed_file(pe, None)
    a_new_process()
    read = pe._compile_hy(SOURCE, "/src/m.hy", "m")
    assert isinstance(read, pe._ChunkedTree)
    assert len(entries(cache_dir)) == 1


PACKAGE = pe.__name__.split(".")[0]


def code_names(code: types.CodeType) -> frozenset[str]:
    """The global names a code object and the code nested in it read."""
    nested = (const for const in code.co_consts if isinstance(const, types.CodeType))
    return frozenset(code.co_names).union(*(code_names(inner) for inner in nested))


def module_of(value: object) -> str | None:
    """The module a function, class or module value is defined in (None for plain data)."""
    if isinstance(value, types.ModuleType):
        return value.__name__
    if isinstance(value, (types.FunctionType, types.BuiltinFunctionType, type)) or callable(getattr(value, "__wrapped__", None)):
        return getattr(value, "__module__", None)
    return None


def from_package(value: object) -> bool:
    return (module_of(value) or "").split(".")[0] == PACKAGE


def uses_of(value: object) -> tuple[tuple[str, object], ...]:
    """What ``value`` reaches in one step, each with the name it is reached by: the globals the code
    of a function of this package reads, and the functions of a class of this package."""
    function = inspect.unwrap(value) if isinstance(value, types.FunctionType) or hasattr(value, "__wrapped__") else value
    if isinstance(function, types.FunctionType) and from_package(function):
        return tuple(
            (f"{function.__module__}.{name}", function.__globals__[name])
            for name in sorted(code_names(function.__code__))
            if name in function.__globals__
        )
    if isinstance(value, type) and from_package(value):
        return tuple(
            (f"{value.__module__}.{value.__qualname__}.{name}", member)
            for name, member in vars(value).items()
            if isinstance(member, types.FunctionType)
        )
    return ()


def reached(root: object) -> tuple[tuple[str, object], ...]:
    """Everything reached from ``root`` by the global names the code of this package reads."""
    found: tuple[tuple[str, object], ...] = ()
    seen: frozenset[int] = frozenset()
    frontier = uses_of(root)
    while frontier:
        new = tuple({id(value): (name, value) for name, value in frontier if id(value) not in seen}.values())
        seen = seen | {id(value) for _, value in new}
        found = found + new
        frontier = tuple(use for _, value in new for use in uses_of(value))
    return found


def imported_from_outside() -> frozenset[str]:
    """``module.name`` for every name a module of this package binds by importing it from outside
    the package and the standard library (data such a module holds has no module of its own)."""
    package_dir = Path(str(pe.__file__)).parent
    return frozenset(
        f"{PACKAGE}.{file.stem}.{alias.asname or alias.name.split('.')[0]}"
        for file in sorted(package_dir.glob("*.py"))
        for node in ast.parse(file.read_text(encoding="utf-8")).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if not outside_allowed(node.module if isinstance(node, ast.ImportFrom) else alias.name)
    )


def outside_allowed(module: str | None) -> bool:
    top = (module or "").split(".")[0]
    return top in (PACKAGE, "builtins") or top in sys.stdlib_module_names


def foreign_uses(root: object) -> list[str]:
    """The names through which what ``root`` builds depends on code outside this package and the
    standard library — code the reader's digest does not name."""
    outside = imported_from_outside()
    return sorted(
        f"{name} → {module_of(value) or type(value).__name__}"
        for name, value in reached(root)
        if name in outside or (module_of(value) is not None and not outside_allowed(module_of(value)))
    )


def test_what_is_derived_is_built_by_code_of_this_package() -> None:
    """Blind A: the derived values are keyed on the reader's digest — every file of this package —
    so the code that builds them (``_derived`` and all it reaches) may lie in any module of the
    package but nowhere else; a helper outside it could change and leave the entries as they were."""
    assert len(reached(pe._derived)) > 20  # the walk reaches the reader's code (not a vacuous pass)
    assert foreign_uses(pe._derived) == []


def a_helper_outside_the_package(node: ast.AST) -> tuple[int, ...]:
    return (getattr(node, "lineno", 0),)


def test_a_helper_outside_the_package_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """The negative case of the walk above: a helper of ``_derived`` from another module is named."""
    monkeypatch.setattr(pe, "_definition_lines", a_helper_outside_the_package)
    assert foreign_uses(pe._derived) == [f"{pe.__name__}._definition_lines → {__name__}"]


EXPANSION_STORE = frozenset({"_store_expansion", "_stored_expansion", "_read_expansion"})


def names_used(source: str) -> frozenset[str]:
    """Every name a source reads, assigns, imports or reaches as an attribute."""
    return frozenset(
        name
        for node in ast.walk(ast.parse(source))
        for name in (
            (node.id,) if isinstance(node, ast.Name)
            else (node.attr,) if isinstance(node, ast.Attribute)
            else tuple(alias.name for alias in node.names) if isinstance(node, (ast.Import, ast.ImportFrom))
            else ()
        )
    )


def analyzer_sources() -> dict[str, str]:
    package_dir = Path(str(hy_expansion.__file__)).parent
    return {file.name: file.read_text(encoding="utf-8") for file in sorted(package_dir.glob("*.py"))}


def test_only_the_expansion_module_reads_or_writes_its_store() -> None:
    """Design check lt-DMXJNAWWMCFNV7F1QX3GJZ574H, blind B: the reader called the store's writer, so a
    reader that reshaped a tree (renumbered its gensyms) could store it under a key without the
    reader, and every later reader — a reverted one too — read that shape. A caller asks for a tree
    (``_expansion``); no module but ``hy_expansion`` names the store."""
    used = {name: names_used(text) & EXPANSION_STORE for name, text in analyzer_sources().items() if name != "hy_expansion.py"}
    assert {name: names for name, names in used.items() if names} == {}
    assert names_used(
        "from doeff_effect_analyzer.hy_expansion import _store_expansion\n"
        "def _expanded(place, expanded):\n    _store_expansion(place, _canonical_gensyms(expanded))\n"
    ) & EXPANSION_STORE == {"_store_expansion"}  # the negative case: blind B's reader is named


def analyzer_imports(source: str) -> frozenset[str]:
    """The modules of the analyzer a source imports, at its top or inside a function."""
    return frozenset(
        module
        for node in ast.walk(ast.parse(source))
        for module in (
            (node.module or "",) if isinstance(node, ast.ImportFrom) else tuple(alias.name for alias in node.names) if isinstance(node, ast.Import) else ()
        )
        if module.split(".")[0] == PACKAGE
    )


def test_the_expansion_module_imports_no_other_module_of_the_analyzer() -> None:
    """The tree is keyed on ``hy_expansion``'s own file: code it took from the reader would decide
    trees without being in their key."""
    assert analyzer_imports(analyzer_sources()["hy_expansion.py"]) == frozenset()
    assert analyzer_imports(
        "def _expand_hy(source):\n    from doeff_effect_analyzer.program_effects import _canonical_gensyms\n"
    ) == {"doeff_effect_analyzer.program_effects"}  # the negative case


def test_a_type_check_expansion_is_neither_read_from_nor_written_to_a_store(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blind A, another candidate: inside ``doeff_hy.static_view`` doeff-hy's macros write another
    tree (for type checking). The import side keeps no record of such code; the analyzer neither
    stores such a tree under a plain key nor gives a stored plain tree to such an expansion."""
    from doeff_hy_bytecode_guard import TYPE_CHECK_EXPANSION

    seen = expansions(monkeypatch)
    pe._compile_hy(SOURCE, "/src/m.hy", "m")  # the positive case: a plain expansion is stored
    assert (len(entries(cache_dir)), len(expansion_entries(cache_dir))) == (1, 1)
    token = TYPE_CHECK_EXPANSION.set(True)
    try:
        a_new_process()
        pe._compile_hy(SOURCE, "/src/m.hy", "m")
        pe._compile_hy(SOURCE.replace("42", "43"), "/src/m.hy", "m")
    finally:
        TYPE_CHECK_EXPANSION.reset(token)
    assert seen == ["m", "m", "m"]
    assert (len(entries(cache_dir)), len(expansion_entries(cache_dir))) == (1, 1)


def import_compiling(root: Path, package: str, files: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write ``files`` (name -> text) as the package ``package`` in ``root`` and import its ``m`` — the import's
    compile expands it (a fresh package: no bytecode, no stored code); returns ``m``'s file."""
    directory = root / package
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "__init__.py").write_text("", encoding="utf-8")
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    monkeypatch.syspath_prepend(str(root))
    importlib.invalidate_caches()
    try:
        importlib.import_module(f"{package}.m")
    finally:
        for name in [name for name in sys.modules if name == package or name.startswith(f"{package}.")]:
            sys.modules.pop(name)
    return directory / "m.hy"


def test_a_module_the_import_compiled_is_not_expanded_again(
    import_trees_on: None, cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ki-e053a847c515: a changed module was expanded by the session's import and again by the analysis
    (58 % of the closure tests' seconds in the daily run). The import's compile leaves its tree and the macros
    it used; the analysis reads that tree instead of expanding the same source again."""
    seen = expansions(monkeypatch)
    package = f"imported_{uuid.uuid4().hex[:8]}"
    path = import_compiling(tmp_path, package, {"m.hy": SOURCE}, monkeypatch)
    a_new_process()
    read = pe._compile_hy(SOURCE, str(path), f"{package}.m")
    assert seen == []
    assert ast.dump(read.tree) == ast.dump(hy_expansion._expand_hy(SOURCE, str(path), f"{package}.m").tree)


def test_a_changed_macro_after_the_import_expands_the_module_again(
    import_trees_on: None, cache_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tree the import left is read only while the macros it used are this environment's."""
    seen = expansions(monkeypatch)
    package = f"imported_{uuid.uuid4().hex[:8]}"
    uses = f"(require {package}.a [answer])\n(setv value (answer))\n"
    path = import_compiling(
        tmp_path, package, {"a.hy": "(defmacro answer [] 1)\n", "m.hy": uses}, monkeypatch
    )
    (path.parent / "a.hy").write_text("(defmacro answer [] 2)\n", encoding="utf-8")
    importlib.invalidate_caches()
    a_new_process()
    pe._compile_hy(uses, str(path), f"{package}.m")
    assert seen == [f"{package}.m"]


def test_an_import_leaves_no_tree_when_the_cache_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", "off")
    package = f"imported_{uuid.uuid4().hex[:8]}"
    import_compiling(tmp_path, package, {"m.hy": SOURCE}, monkeypatch)
    assert not list(tmp_path.rglob("import-trees"))
