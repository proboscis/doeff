"""The macro-expanded tree of a Hy module is cached on disk under its exact inputs.

Expansion is most of an analysis' time and every process used to redo it for each
Hy module it read. The cache key is the source's content (never its path), the
module name, every macro file the expansion went through (the modules the source
requires and the ones they require in turn), the Hy / Python versions and the
reader's own source — so a changed source, macro file or reader is expanded again,
the same source in another checkout is not, and an unreadable cache file only
costs one expansion.
"""

import ast
import importlib
import pickle
import sys
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

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


def expansions(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Count how often the real expansion runs (the cache must spare the second one)."""
    seen: list[str] = []
    real = pe._expand_hy

    def counting(source: str, filename: str, module_name: str) -> pe._Expanded:
        seen.append(module_name)
        return real(source, filename, module_name)

    monkeypatch.setattr(pe, "_expand_hy", counting)
    return seen


def entries(cache_dir: Path) -> list[Path]:
    """Every entry of the cache: one directory per source, one entry in it per set of macro files."""
    return sorted(cache_dir.glob("*/*.pickle"))


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


def test_an_expansion_records_every_macro_file_it_went_through_by_its_digest() -> None:
    """The source requires ``doeff-hy.macros``, which requires ``doeff-hy.handle`` in turn: both files
    are in the record an entry is named by and read under (the record the import side keeps)."""
    import doeff_hy.handle
    import doeff_hy.macros
    from doeff_hy_bytecode_guard import file_sha256
    from doeff_hy_bytecode_guard.records import MacroDependency

    record = pe._expand_hy(SOURCE, "/src/m.hy", "m").macros
    for module in (doeff_hy.macros, doeff_hy.handle):
        file = str(module.__file__)
        assert MacroDependency(module.__name__, file, str(file_sha256(file))) in record.dependencies


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
    cached.write_bytes(b"not a pickle")
    tree = pe._compile_hy(SOURCE, "/src/m.hy", "m")
    assert seen == ["m", "m"]
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
    assert {qualname for qualname, _ in named} >= {"outer", "outer.inner"}
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
    inside, outer = named["second_one.inside"], named["second_one"]
    a_new_process()
    read = pe._compile_hy(TWO, "/src/two.hy", "two")
    found_inside = read.find(function_at("second_one.inside", line=inside.lineno, params=pe._definition_params(inside)))
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
    wrap(prog)``. The key names the reader's source, so an entry the older reader wrote (the
    older shape, stored under its own digest) is not read: the tree is expanded again and its
    facts are this reader's."""
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
    assert seen == ["r", "r"]
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
