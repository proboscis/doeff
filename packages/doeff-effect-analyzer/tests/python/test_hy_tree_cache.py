"""The macro-expanded tree of a Hy module is cached on disk under its exact inputs.

Expansion is most of an analysis' time and every process used to redo it for each
Hy module it read. The cache key is the source, the module name and path, the
macro modules the source requires, and the Hy / Python versions — so a changed
source or macro module is expanded again, and an unreadable cache file only costs
one expansion.
"""

import ast
from pathlib import Path

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
    assert ast.dump(first) == ast.dump(second)
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
    assert isinstance(tree, ast.Module)
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


def test_a_cached_tree_brings_its_definition_index_and_body_facts(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agora-redesign #1586 / #1590: what is derived from the tree alone (the definition index,
    the body nodes and facts of every def) is stored with it, so a process that reads the
    cached tree does not walk the module and every def again — and gets the same values."""
    written = pe._compile_hy(DEFS, "/src/defs.hy", "defs")
    built_index = {path: [ast.dump(node) for node in nodes] for path, nodes in pe._definitions_by_path(written).items()}
    functions = [n for n in ast.walk(written) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]
    built_facts = [(sorted(pe._body_facts(f).names), sorted(pe._body_facts(f).imports)) for f in functions]
    # A new process: nothing derived is in memory.
    pe._MODULE_CACHE.clear()
    pe._DEFINITION_INDEX.clear()
    pe._BODY_FACTS.clear()
    pe._BODY_NODES.clear()
    walked: list[str] = []
    monkeypatch.setattr(pe, "_read_body_facts", lambda f: walked.append("facts") or (_ for _ in ()).throw(AssertionError("rebuilt")))
    monkeypatch.setattr(pe, "_direct_definitions", lambda m: walked.append("index") or iter(()))
    read = pe._compile_hy(DEFS, "/src/defs.hy", "defs")
    assert read is not written and ast.dump(read) == ast.dump(written)
    index = pe._definitions_by_path(read)
    assert {path: [ast.dump(node) for node in nodes] for path, nodes in index.items()} == built_index
    read_functions = [n for n in ast.walk(read) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]
    assert [(sorted(pe._body_facts(f).names), sorted(pe._body_facts(f).imports)) for f in read_functions] == built_facts
    assert walked == [], walked
    # The stored values point into the read tree (not copies of other nodes).
    assert all(node in list(ast.walk(read)) for nodes in index.values() for node in nodes)
