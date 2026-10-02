"""The macro-expanded tree of a Hy module is cached on disk under its exact inputs.

Expansion is most of an analysis' time and every process used to redo it for each
Hy module it read. The cache key is the source, the module name and path, the
macro modules the source requires, the Hy / Python versions and the reader's own
source — so a changed source, macro module or reader is expanded again, and an
unreadable cache file only costs one expansion.
"""

import ast
import pickle
from dataclasses import replace
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

    def counting(source: str, filename: str, module_name: str) -> ast.Module:
        seen.append(module_name)
        return real(source, filename, module_name)

    monkeypatch.setattr(pe, "_expand_hy", counting)
    return seen


def test_the_second_read_of_the_same_source_comes_from_the_cache(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = expansions(monkeypatch)
    first = pe._compile_hy(SOURCE, "/src/m.hy", "m")
    second = pe._compile_hy(SOURCE, "/src/m.hy", "m")
    assert seen == ["m"]
    assert ast.dump(first.tree) == ast.dump(second.tree)
    assert len(list(cache_dir.glob("*.pickle"))) == 1


def test_a_changed_source_is_expanded_again(cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = expansions(monkeypatch)
    pe._compile_hy(SOURCE, "/src/m.hy", "m")
    pe._compile_hy(SOURCE.replace("42", "43"), "/src/m.hy", "m")
    assert seen == ["m", "m"]


def test_the_key_names_the_required_macro_module_by_its_digest() -> None:
    digests = pe._required_macro_digests(SOURCE)
    assert len(digests) == 1 and digests[0].startswith("doeff-hy.macros=")
    assert not digests[0].endswith("=?")


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
    (cached,) = cache_dir.glob("*.pickle")
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
    (entry,) = cache_dir.glob("*.pickle")
    stored = pickle.loads(entry.read_bytes())
    assert isinstance(stored, pe._CachedTree)
    older_chunks = tuple(as_the_older_reader_wrote(raw) for raw in stored.chunks)
    entry.write_bytes(pickle.dumps(replace(stored, chunks=older_chunks), protocol=pickle.HIGHEST_PROTOCOL))
    a_new_process()
    monkeypatch.setattr(pe, "_reader_digest", reader)
    pe._compile_hy(REWRAPPED, "/src/r.hy", "r")
    assert seen == ["r", "r"]
    path = pe._hy_cache_path(REWRAPPED, "/src/r.hy", "r")
    assert path is not None
    assert path != entry
    written = pickle.loads(path.read_bytes())
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
