"""Test that the trees expanded by import compilation remain under the bytecode store (code_store -- environment variable DOEFF_HY_CODE_STORE).

Real case (2026-10-19, daily run): trees were placed only by the compile of a process whose DOEFF_EFFECT_ANALYZER_CACHE pointed at a dir. The bytecode store is shared by all jobs on a worker
through DOEFF_HY_CODE_STORE, but the analyzer-store variable is not in the allow list the worker passes to a job's children. When the verification job in the merge queue compiled the changed modules
first, the bytecode store was warm and no tree was left, so the daily run hit bytecode without compiling, and the closing check's
analyzer re-expanded the same source (the sum of the daily run's two closing checks was 557.5 seconds; the reproduction with split stores was 151.9 seconds).

The tree store is one place inside the bytecode store -- the process that places trees and the analyzer that pulls them look only at the same explicitly named store. They do not write to the unnamed default dir
(the user's cache) (tree writes are not added to an ordinary import compile). Trees are several times larger than bytecode, so they are stored compressed, and the number of entries per key
is capped (entries unused for 7 days are removed by the worker's cleanup -- each hit on a pull sets the time to now).
"""

from __future__ import annotations

import ast
import os
import pickle
import time
from pathlib import Path

import pytest
from doeff_hy_bytecode_guard import _code_store as code_store
from doeff_hy_bytecode_guard import _expansion_trees as expansion_trees
from doeff_hy_bytecode_guard._records import MacroRecord

SOURCE = "(setv answer 42)\n"
TREE = ast.parse("answer = 42\n")


def record(label: str = "") -> MacroRecord:
    """Stand-in for the record of macros the expansion used (no macros -- a different ``label`` gives a different record fingerprint = a writer with a different macro environment)."""
    return MacroRecord(hy_version=f"probe{label}", macros=(), absent=(), providers=(), values=(), files=())


@pytest.fixture(autouse=True)
def records_are_current(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check reads the macro files of the current environment, so here every record is treated as "matches the current environment" (the case where it does not match is tested separately)."""
    monkeypatch.setattr(expansion_trees, "record_is_current_here", lambda _macros: True)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A named bytecode store (no analyzer-store variable -- the shape of the verification job)."""
    directory = tmp_path / "code-store"
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(directory))
    monkeypatch.delenv("DOEFF_EFFECT_ANALYZER_CACHE", raising=False)
    return directory


def trees_under(root: Path) -> list[Path]:
    """The tree entries under root."""
    return sorted(root.rglob(f"*{code_store.TREE_SUFFIX}"))


def test_a_tree_is_left_in_the_named_code_store_and_read_from_there(store: Path) -> None:
    """A tree placed by a process without the analyzer-store variable can be pulled by a process naming the same store."""
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), TREE)
    assert len(trees_under(store)) == 1
    found = expansion_trees.read_import_tree("pkg.m", SOURCE)
    assert found is not None
    assert ast.dump(found.tree) == ast.dump(TREE)
    assert found.macros == record()


@pytest.mark.parametrize("name", ["module", "source"])
def test_another_module_or_source_does_not_find_the_tree(store: Path, name: str) -> None:
    """The key is the module name plus the source content -- a difference in either means no hit."""
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), TREE)
    other = ("pkg.n", SOURCE) if name == "module" else ("pkg.m", SOURCE + ";; changed\n")
    assert expansion_trees.read_import_tree(*other) is None


@pytest.mark.parametrize("setting", [None, "", "off"])
def test_a_store_that_is_not_named_gets_no_tree(
    setting: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is placed in or pulled from an unnamed (unset, empty, off) store -- nothing is written to the default dir (the user's cache) either."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DOEFF_EFFECT_ANALYZER_CACHE", raising=False)
    if setting is None:
        monkeypatch.delenv("DOEFF_HY_CODE_STORE", raising=False)
    else:
        monkeypatch.setenv("DOEFF_HY_CODE_STORE", setting)
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), TREE)
    assert trees_under(tmp_path) == []
    assert expansion_trees.read_import_tree("pkg.m", SOURCE) is None


def test_the_analyzers_variable_does_not_name_a_place_for_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is one tree store (inside the bytecode store) -- the analyzer-store variable alone does not place anything (no second source of definition is created)."""
    monkeypatch.delenv("DOEFF_HY_CODE_STORE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("DOEFF_EFFECT_ANALYZER_CACHE", str(tmp_path / "analyzer"))
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), TREE)
    assert trees_under(tmp_path) == []


def test_a_tree_that_is_not_a_module_is_not_kept(store: Path) -> None:
    """Expansion trees that are not modules (such as the type-check expansion's code) are not placed."""
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), object())
    assert trees_under(store) == []


def test_a_tree_is_kept_compressed(store: Path) -> None:
    """Trees are several times larger than bytecode, so they are stored compressed -- the placed entry is smaller than the tree pickled as-is."""
    big = ast.parse("".join(f"value_{index} = [{index}, 'text_{index}', ({index}, {index + 1})]\n" for index in range(2000)))
    expansion_trees.add_import_tree("pkg.big", SOURCE, record(), big)
    [entry] = trees_under(store)
    assert entry.stat().st_size * 3 < len(pickle.dumps(big, protocol=pickle.HIGHEST_PROTOCOL))
    found = expansion_trees.read_import_tree("pkg.big", SOURCE)
    assert found is not None
    assert ast.dump(found.tree) == ast.dump(big)


def test_a_tree_of_other_macros_is_not_read(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only entries whose macros that the expansion relied on are the current environment's are pulled -- trees from old macros are not used."""
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), TREE)
    monkeypatch.setattr(expansion_trees, "record_is_current_here", lambda _macros: False)
    assert expansion_trees.read_import_tree("pkg.m", SOURCE) is None


def test_the_entry_of_the_current_macros_is_the_one_read(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even when trees from two writers with different macro environments sit side by side, the one matching the current environment is pulled."""
    old, new = ast.parse("answer = 1\n"), ast.parse("answer = 2\n")
    expansion_trees.add_import_tree("pkg.m", SOURCE, record("-old"), old)
    expansion_trees.add_import_tree("pkg.m", SOURCE, record("-new"), new)
    monkeypatch.setattr(expansion_trees, "record_is_current_here", lambda macros: macros.hy_version == "probe-new")
    found = expansion_trees.read_import_tree("pkg.m", SOURCE)
    assert found is not None
    assert ast.dump(found.tree) == ast.dump(new)


def test_a_damaged_entry_is_skipped(store: Path) -> None:
    """A corrupted entry is not used, and the reading side does not crash (the analyzer expands by itself)."""
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), TREE)
    [entry] = trees_under(store)
    entry.write_bytes(entry.read_bytes()[:-5])
    assert expansion_trees.read_import_tree("pkg.m", SOURCE) is None
    entry.write_bytes(b"not a pickle")
    assert expansion_trees.read_import_tree("pkg.m", SOURCE) is None


def test_reading_a_tree_marks_it_used(store: Path) -> None:
    """Each hit on a pull sets the entry's time to now -- the worker's cleanup (removing entries unused for 7 days) does not remove trees in use."""
    expansion_trees.add_import_tree("pkg.m", SOURCE, record(), TREE)
    [entry] = trees_under(store)
    week_ago = time.time() - 6 * 24 * 3600
    os.utime(entry, (week_ago, week_ago))
    assert expansion_trees.read_import_tree("pkg.m", SOURCE) is not None
    assert time.time() - entry.stat().st_mtime < 60


def test_a_key_keeps_at_most_the_limit_of_environments_and_the_used_ones_stay(store: Path) -> None:
    """Tree entries per key are capped -- for environments beyond the cap, entries that were only added and never used are removed first (entries of environments that keep being used are not pushed out)."""
    limit = code_store.TREE_ENTRIES_PER_KEY
    for index in range(limit):
        expansion_trees.add_import_tree("pkg.m", SOURCE, record(f"-{index}"), TREE)
    entries = trees_under(store)
    assert len(entries) == limit
    used = entries[0]
    day_ago = time.time() - 3600
    for entry in entries:
        os.utime(entry, (day_ago, day_ago))
    os.utime(used, None)  # entry of an environment that keeps being used
    for index in range(limit, limit + 3):
        expansion_trees.add_import_tree("pkg.m", SOURCE, record(f"-{index}"), TREE)
    after = trees_under(store)
    assert len(after) == limit
    assert used in after
