"""Failure cases for the saved expansions of doeff-hy-check (doeff_hy/_static_cache.py) -- whether to reuse or re-expand.

A saved expansion is reused only when what the expansion depended on is the same as in the current environment. What it depended on = the source contents, module name,
path relative to the root, Hy version, and the files the expansion passed through (modules of required macros, helper modules the macros call, transitive requires,
modules of the post-processing of the type-checking expansion). Changing a doeff_hy file the expansion does not pass through still lets the saved expansion be reused (previously the fingerprint of the
whole doeff_hy package was part of the key, so one unrelated commit forced every expansion to be rebuilt).

To make reloading macro modules the same as in the next run (a separate process), the checks remove the macro modules from sys.modules before
pulling the second time. Checks that change doeff_hy's own files copy doeff_hy to a temporary dir and run it in a separate process (so as not to change the
already loaded doeff_hy).
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import doeff_hy
import pytest
from doeff_hy import _static_cache as static_cache, _static_check as static_check
from doeff_hy._static_check import Projection, project_cached

PACKAGE = "probe_cache"

#: Macro modules. said = the answer of a function in the helper module; said-late = the answer of a helper imported only inside the macro body;
#: said-deep = the answer of a macro from another macro module that this module requires, expanded at this module's compile time.
#: unused = a macro that user.hy does not use.
MACROS = f"""\
(import {PACKAGE}.words [word])
(require {PACKAGE}.deeper [deep])
(defmacro said [] (word))
(defmacro said-late [] (import {PACKAGE}.late) ({PACKAGE}.late.word))
(defmacro said-deep [] (deep))
(defmacro unused [] "unused")
"""

USER = f"""\
(require {PACKAGE}.macros [said said-late said-deep])
(setv a (said) b (said-late) c (said-deep))
"""


@dataclass(frozen=True)
class Tree:
    """A macro package under the root (probe_cache) and the user.hy that requires it."""

    root: Path
    cache: Path

    @property
    def package(self) -> Path:
        return self.root / PACKAGE

    @property
    def user(self) -> Path:
        return self.package / "user.hy"


def _write_tree(root: Path, words: str = "old") -> Tree:
    package = root / PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.hy").write_text("", encoding="utf-8")
    (package / "macros.hy").write_text(MACROS, encoding="utf-8")
    (package / "words.py").write_text(
        f'def word() -> str:\n    return "{words}"\n', encoding="utf-8"
    )
    (package / "late.py").write_text('def word() -> str:\n    return "late"\n', encoding="utf-8")
    (package / "deeper.hy").write_text('(defmacro deep [] "deep")\n', encoding="utf-8")
    (package / "user.hy").write_text(USER, encoding="utf-8")
    return Tree(root, root.parent / ".cache")


def _forget(package: str) -> None:
    """Remove the loaded package's modules (as in the next run, so that the next expansion reloads the macro modules)."""
    for name in [n for n in sys.modules if n == package or n.startswith(f"{package}.")]:
        del sys.modules[name]


@pytest.fixture
def expansions(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """The list of expanded sources (does not grow when pulled from the cache)."""
    seen: list[Path] = []
    real = static_check.project

    def counting(root: Path, roots: list[Path], source: Path) -> object:
        seen.append(source)
        return real(root, roots, source)

    monkeypatch.setattr(static_check, "project", counting)
    return seen


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Tree]:
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")
    made = _write_tree(tmp_path / "tree")
    monkeypatch.syspath_prepend(str(made.root))
    yield made
    _forget(PACKAGE)


def _project(tree: Tree) -> Projection:
    result = project_cached(tree.root, [tree.root], tree.user, tree.cache)
    assert isinstance(result, Projection), result
    _forget(PACKAGE)
    return result


def _rewrite(path: Path, text: str) -> None:
    """Change the contents (also change the size -- because the memo of a file's sha256 is keyed by path, mtime, and size)."""
    assert len(text) != len(path.read_text(encoding="utf-8"))
    path.write_text(text, encoding="utf-8")


def test_an_unchanged_tree_reads_the_stored_expansion(tree: Tree, expansions: list[Path]) -> None:
    first = _project(tree)
    assert all(word in first.text for word in ("'old'", "'late'", "'deep'")), first.text
    assert _project(tree).text == first.text
    assert expansions == [tree.user], "re-expanded although nothing changed"


def test_an_entry_of_the_current_store_version_is_read_back(
    tree: Tree, expansions: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Red before the fix: reading an entry compared the cache-format version against the literal 3.
    # A version with CACHE_VERSION raised could not read a single entry it had written itself, and re-expanded everything every time.
    monkeypatch.setattr(static_cache, "CACHE_VERSION", static_cache.CACHE_VERSION + 1)
    first = _project(tree)
    assert _project(tree).text == first.text
    assert expansions == [tree.user], "could not read the entry written by the current version and re-expanded"


#: sha256 of the source of the rules for renaming gensym names used by the post-processing of the type-checking expansion (with_canonical_gensyms in static_check) -> the version
#: of the cache format (CACHE_VERSION) written with that rule. The rules live in doeff_hy_bytecode_guard (a package where the closure walk only includes names), and the record's digest does not
#: cover them (changing the rules still pulled saved expansions with the old names). When the rules change,
#: raise CACHE_VERSION and add a new row to this table (the version differs per row -- adding only a hash under the same version is rejected by the check below).
#: The hash of the version-3 row is that of the source with the import inside gensym_renaming moved to _records (the rules are the same so the version was
#: not raised; the previous hash 5b4612a4... was replaced). After that only the docstring was rewritten (the moved module's name and the tracker id were
#: removed), so the hash 75e5ff36... was replaced with the rules unchanged. Version 4 changed the cache format of the position mapping, and the rules are the same as version 3, so
#: the row of the same hash was set to version 4 (the table compares the version of the row of the current rules with CACHE_VERSION).
GENSYM_RULE_VERSIONS = {
    "7981e4ad3c2b2d0f589750610b29f6d53a2dcbac75e1180c84876ed63736f0b5": 4,
}


def _gensym_rule_sha256() -> str:
    """sha256 of the source of the renaming rules (the entry point gensym_renaming, gensym_renames that builds the mapping, name matching, the replacement, and the two regexes for the name form)."""
    import hashlib
    import inspect

    from doeff_hy_bytecode_guard import _loader_hooks as loader_hooks, _records as records

    parts = (
        inspect.getsource(loader_hooks.gensym_renaming),
        inspect.getsource(records.gensym_renames),
        inspect.getsource(records._gensym_match),
        inspect.getsource(records.renamed),
        records.GENSYM_NAME.pattern,
        records.RENAMED_BINDING.pattern,
    )
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def test_a_changed_gensym_renaming_rule_comes_with_a_new_store_version() -> None:
    # Guard: if the source of the renaming rules is not in the table, the rules were changed without raising the cache-format
    # version (saved expansions would be pulled with names from the old rules). The table's version differs per row, and the version of the row of the current rules is CACHE_VERSION.
    assert len(set(GENSYM_RULE_VERSIONS.values())) == len(GENSYM_RULE_VERSIONS), "two rule rows share the same version"
    rule = _gensym_rule_sha256()
    assert GENSYM_RULE_VERSIONS.get(rule) == static_cache.CACHE_VERSION, (
        f"When the gensym renaming rules ({rule}) change, raise static_cache.CACHE_VERSION and add"
        " a new rule row to GENSYM_RULE_VERSIONS"
    )


#: Lines in doeff_hy sources that may touch the module location (__file__, __cached__) (file name, line content) -- just one line of static_check's project
#: that only writes it into the module it creates.
PLACE_LINES = {("static_check.py", "    module.__file__ = str(source)")}


def test_doeff_hy_sources_do_not_read_their_place() -> None:
    # Guard: doeff_hy's macros and the type-check post-processing do not read neighboring files from their own location.
    # A function reading a plain-string global built from the location (HERE = os.path.dirname(__file__)) has only the value (path) included by the closure walk, which does not cover the contents of the file
    # at that path, so changing a neighboring file still pulls an old expansion (code or binding statements that read the location directly are marked as something the walk cannot compare).
    package = Path(doeff_hy.__file__).parent
    touching = sorted(
        (path.relative_to(package).as_posix(), line)
        for path in package.rglob("*")
        if path.suffix in (".py", ".hy", ".pyi")
        for line in path.read_text(encoding="utf-8").splitlines()
        if ("__file__" in line or "__cached__" in line) and not line.lstrip().startswith(("#", ";"))
    )
    assert [row for row in touching if row not in PLACE_LINES] == [], (
        "a doeff_hy source reads the module location (the contents of a neighboring file are not in the record of the saved expansion)"
    )


def test_a_changed_macro_module_expands_again(tree: Tree, expansions: list[Path]) -> None:
    # Failure case 2: rebuild when the source of a required macro module changes.
    _project(tree)
    _rewrite(
        tree.package / "macros.hy",
        MACROS.replace("(defmacro said [] (word))", '(defmacro said [] "newer")'),
    )
    assert "'newer'" in _project(tree).text
    assert len(expansions) == 2


def test_a_changed_helper_a_macro_calls_expands_again(tree: Tree, expansions: list[Path]) -> None:
    # Failure case 3: rebuild when a helper Python module (non-macro code) that a macro calls during expansion changes.
    # The old key included only the .hy at the end of the require, so changing words.py still hit the old expansion.
    _project(tree)
    _rewrite(tree.package / "words.py", 'def word() -> str:\n    return "newer"\n')
    assert "'newer'" in _project(tree).text
    assert len(expansions) == 2


def test_a_changed_helper_imported_in_a_macro_body_expands_again(
    tree: Tree, expansions: list[Path]
) -> None:
    # Failure case 4: rebuild when a helper module imported only inside a macro body changes.
    _project(tree)
    _rewrite(tree.package / "late.py", 'def word() -> str:\n    return "later"\n')
    assert "'later'" in _project(tree).text
    assert len(expansions) == 2


def test_a_changed_macro_of_a_transitive_require_expands_again(
    tree: Tree, expansions: list[Path]
) -> None:
    # Failure case 8: rebuild when a macro that a macro module requires changes.
    _project(tree)
    _rewrite(tree.package / "deeper.hy", '(defmacro deep [] "deeper")\n')
    assert "'deeper'" in _project(tree).text
    assert len(expansions) == 2


def test_a_relative_require_follows_the_macro_module_of_its_package(
    tree: Tree, expansions: list[Path]
) -> None:
    # The relative require form (`.macros`) of failure case 2.
    tree.user.write_text("(require .deeper [deep])\n(setv c (deep))\n", encoding="utf-8")
    _project(tree)
    _rewrite(tree.package / "deeper.hy", '(defmacro deep [] "deeper")\n')
    assert "'deeper'" in _project(tree).text
    assert len(expansions) == 2


def test_the_hy_version_is_part_of_what_the_expansion_used(
    tree: Tree, expansions: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Failure case 7: rebuild when the Hy version changes.
    import hy

    _project(tree)
    monkeypatch.setattr(hy, "__version__", f"{hy.__version__}.probe")
    _project(tree)
    assert len(expansions) == 2


def test_another_worktree_checks_its_own_macro_files(
    tmp_path: Path, expansions: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Failure case 9: a cache made in a different worktree is compared by re-resolving the origin files from the current environment's module names. Even with the same source contents,
    # the same module name, and the same relative path, rebuild if the helpers in the current tree differ (the cache location is the same for both trees).
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")
    cache = tmp_path / ".cache"
    first = _write_tree(tmp_path / "first")
    second = _write_tree(tmp_path / "second", words="other")
    try:
        monkeypatch.syspath_prepend(str(first.root))
        assert "'old'" in _project(Tree(first.root, cache)).text
        sys.path.remove(str(first.root))
        monkeypatch.syspath_prepend(str(second.root))
        assert "'other'" in _project(Tree(second.root, cache)).text
        assert len(expansions) == 2
    finally:
        _forget(PACKAGE)


OUTSIDE_PACKAGE = "probe_outside_macros"


@pytest.fixture
def outside_macros(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A macro module of another package placed outside the root (proj) (outside/probe_outside_macros/macros.hy -- resolvable from sys.path), and
    proj/probe.hy under the root that requires it."""
    outside = tmp_path / "outside"
    monkeypatch.syspath_prepend(str(outside))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")
    package = outside / OUTSIDE_PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.hy").write_text("", encoding="utf-8")
    macros = package / "macros.hy"
    macros.write_text('(defmacro said [] "old")\n', encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "probe.hy").write_text(
        f"(require {OUTSIDE_PACKAGE}.macros [said])\n(setv word (said))\n", encoding="utf-8"
    )
    yield macros
    _forget(OUTSIDE_PACKAGE)


def test_a_changed_macro_of_another_package_outside_the_roots_expands_again(
    tmp_path: Path, outside_macros: Path, expansions: list[Path]
) -> None:
    # Failure case 5: rebuild when a macro of another package outside the root (e.g. doeff-adr.macros) changes.
    project = tmp_path / "proj"
    cache = tmp_path / ".cache"
    first = project_cached(project, [project], project / "probe.hy", cache)
    assert isinstance(first, Projection), first
    assert "'old'" in first.text
    _forget(OUTSIDE_PACKAGE)
    _rewrite(outside_macros, '(defmacro said [] "newer")\n')
    second = project_cached(project, [project], project / "probe.hy", cache)
    assert isinstance(second, Projection), second
    assert "'newer'" in second.text
    assert len(expansions) == 2


# ---- Records per macro used by the expansion -----------------------------------------------------------------------------


def test_a_changed_body_of_a_used_macro_expands_again(tree: Tree, expansions: list[Path]) -> None:
    # When it should go cold: rebuild when the body of a macro user.hy uses (said-deep) changes.
    _project(tree)
    _rewrite(
        tree.package / "macros.hy",
        MACROS.replace("(defmacro said-deep [] (deep))", '(defmacro said-deep [] "used-now")'),
    )
    assert "'used-now'" in _project(tree).text
    assert len(expansions) == 2


def test_a_changed_unused_macro_keeps_the_stored_expansion(tree: Tree, expansions: list[Path]) -> None:
    # When it need not go cold (red before the fix): pull from the cache even if the body of a macro user.hy does not use changes.
    _project(tree)
    _rewrite(
        tree.package / "macros.hy",
        MACROS.replace('(defmacro unused [] "unused")', '(defmacro unused [] "unused-now")'),
    )
    _project(tree)
    assert expansions == [tree.user], "re-expanded just because an unused macro changed"


# ---- Checks that change doeff_hy's own files (copy doeff_hy and run in a separate process) ---------------------------------

#: Run project_cached once with the copied doeff_hy and output the number of expansions and the location of the doeff_hy that was read as JSON.
CHILD = """\
import json, sys
from pathlib import Path
from doeff_hy import static_check
seen = []
real = static_check.project
def counting(root, roots, source):
    seen.append(str(source))
    return real(root, roots, source)
static_check.project = counting
root, cache = Path(sys.argv[1]), Path(sys.argv[2])
result = static_check.project_cached(root, [root], root / "probe.hy", cache)
print(json.dumps({"expanded": len(seen), "projected": isinstance(result, static_check.Projection),
                  "doeff_hy": static_check.__file__}))
"""

PROBE = """\
(require doeff-hy.macros [defk])

(defk twice [x]
  {:pre [(: x int)] :post [(: % int)]}
  (* 2 x))
"""


@dataclass(frozen=True)
class CopiedDoeffHy:
    """doeff_hy copied to a temporary dir (lib/doeff_hy), and the root and cache location of a child process that uses it. The child process inherits the environment in which the fixture gave
    PYTHONPATH (so the copy is read first) and PYTHONPYCACHEPREFIX (bytecode into a temporary dir)."""

    lib: Path
    root: Path
    cache: Path

    @property
    def package(self) -> Path:
        return self.lib / "doeff_hy"

    def expanded(self) -> int:
        """Run once in a child process and return the number of expansions (0 if pulled from the cache)."""
        done = subprocess.run(
            [sys.executable, "-c", CHILD, str(self.root), str(self.cache)],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        answer = json.loads(done.stdout.strip().splitlines()[-1])
        assert answer["projected"], done.stderr
        assert Path(answer["doeff_hy"]).is_relative_to(self.package), (
            answer
        )  # the copied doeff_hy was read
        return int(answer["expanded"])


@pytest.fixture
def copied_doeff_hy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CopiedDoeffHy:
    lib = tmp_path / "lib"
    monkeypatch.setenv("PYTHONPATH", str(lib))
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pycache"))
    shutil.copytree(
        Path(doeff_hy.__file__).parent,
        lib / "doeff_hy",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    root = tmp_path / "proj"
    root.mkdir()
    (root / "probe.hy").write_text(PROBE, encoding="utf-8")
    return CopiedDoeffHy(lib, root, tmp_path / ".cache")


def _append_comment(path: Path) -> None:
    comment = "\n;; changed\n" if path.suffix == ".hy" else "\n# changed\n"
    path.write_text(path.read_text(encoding="utf-8") + comment, encoding="utf-8")


def test_a_doeff_hy_file_the_expansion_does_not_go_through_keeps_the_stored_expansion(
    copied_doeff_hy: CopiedDoeffHy,
) -> None:
    # Failure case 1: changing a doeff_hy file the expansion does not pass through (the HTTP part http.hy) still pulls from the cache. Previously the fingerprint of the whole doeff_hy
    # package was part of the key, so one unrelated commit forced every expansion to be rebuilt (red before the fix).
    assert copied_doeff_hy.expanded() == 1
    _append_comment(copied_doeff_hy.package / "http.hy")
    assert copied_doeff_hy.expanded() == 0


def test_a_changed_type_check_post_processing_expands_again(copied_doeff_hy: CopiedDoeffHy) -> None:
    # Failure case 6: the post-processing of the type-checking expansion (static_check's removal of bookkeeping and addition of helper imports) cannot be reached from macros.
    # Rebuild when it changes (the record is the digest of the closure of functions and classes defined in static_check -- add one function).
    assert copied_doeff_hy.expanded() == 1
    static_check_file = copied_doeff_hy.package / "static_check.py"
    static_check_file.write_text(
        static_check_file.read_text(encoding="utf-8") + "\n\ndef _changed() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    assert copied_doeff_hy.expanded() == 1


def test_a_changed_doeff_hy_macro_expands_again(copied_doeff_hy: CopiedDoeffHy) -> None:
    # The doeff_hy form of failure case 2: rebuild when a doeff_hy macro the expansion used (defk in macros.hy) changes. The record is per used macro,
    # so merely adding a comment to macros.hy does not rebuild (the check below) -- change a constant (docstring) in defk's code.
    assert copied_doeff_hy.expanded() == 1
    macros = copied_doeff_hy.package / "macros.hy"
    old = '"Define a kleisli function (@do decorator)'
    text = macros.read_text(encoding="utf-8")
    assert old in text
    macros.write_text(text.replace(old, '"Define a kleisli function (@do decorator, changed)', 1), encoding="utf-8")
    assert copied_doeff_hy.expanded() == 1


def test_a_comment_in_doeff_hy_macros_keeps_the_stored_expansion(copied_doeff_hy: CopiedDoeffHy) -> None:
    # When it need not go cold (red before the fix): pull from the cache even if a comment is added to macros.hy (the code of the used macro does not change).
    assert copied_doeff_hy.expanded() == 1
    _append_comment(copied_doeff_hy.package / "macros.hy")
    assert copied_doeff_hy.expanded() == 0


def test_the_same_doeff_hy_in_another_place_keeps_the_stored_expansion(
    copied_doeff_hy: CopiedDoeffHy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When it need not go cold (red before the fix): pull from the cache even if doeff_hy with the same contents is placed in a different dir.
    # The daily full verification places doeff in a different dir each run (a worktree under the task's working dir). The closure digest of the post-processing (POST_PROCESSING) included
    # the value of static_check's __file__ (the location path -- the project's `module.__file__ = …` puts __file__ in the code's name table),
    # and the saved expansions all missed every run (agora-controllers' hy-type-trend expanded about 2,450 .hy files cold each time).
    assert copied_doeff_hy.expanded() == 1
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(
        copied_doeff_hy.package, elsewhere / "doeff_hy", ignore=shutil.ignore_patterns("__pycache__")
    )
    monkeypatch.setenv("PYTHONPATH", str(elsewhere))
    moved = CopiedDoeffHy(elsewhere, copied_doeff_hy.root, copied_doeff_hy.cache)
    assert moved.expanded() == 0, "re-expanded just because doeff_hy with the same contents was placed in a different dir"


def test_a_changed_file_the_post_processing_reads_beside_its_module_expands_again(
    copied_doeff_hy: CopiedDoeffHy,
) -> None:
    # Red before the fix: if the post-processing reads a neighboring file from its own module location
    # (__file__) and changes the expansion result, the walk does not cover that file's contents. After removing the location path from the digest,
    # trees in different dirs, and even the same tree, pulled an old expansion after the file was changed. The digest of code that reads the location as a value
    # becomes a mark meaning it cannot be compared, and the user re-expands every time.
    static_check_file = copied_doeff_hy.package / "static_check.py"
    text = static_check_file.read_text(encoding="utf-8")
    assert text.count("    rendered = ast.unparse(tree)\n") == 1
    static_check_file.write_text(
        text.replace(
            "    rendered = ast.unparse(tree)\n", "    rendered = ast.unparse(tree) + _probe_note()\n"
        )
        + '\n\ndef _probe_note() -> str:\n    return "\\n# " + Path(__file__).with_name("probe-note.txt")'
        '.read_text(encoding="utf-8")\n',
        encoding="utf-8",
    )
    note = copied_doeff_hy.package / "probe-note.txt"
    note.write_text("one", encoding="utf-8")
    assert copied_doeff_hy.expanded() == 1
    note.write_text("two-two", encoding="utf-8")
    assert copied_doeff_hy.expanded() == 1, "pulled the old saved expansion although the neighboring file read by the post-processing was changed"


# ---- Cache location --------------------------------------------------------------------------


def test_doeff_hy_check_cache_names_the_place_of_the_stored_expansions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Failure case (#3863): the daily verification task replaces HOME with an empty dir per run, so the cache location under the XDG cache was empty every time and
    # all .hy were re-expanded. Make the dir named by DOEFF_HY_CHECK_CACHE the cache location so that it can be pointed to a dir that persists across runs
    # (the same form as DOEFF_HY_CODE_STORE on the test-run side and DOEFF_EFFECT_ANALYZER_CACHE of doeff-effect-analyzer).
    from doeff_hy._static_cache import default_cache_dir

    # This checks the form with no explicit naming, so remove the naming that the running side (the daily task / worker) passes (the red of daily 10-11 08:05 JST -- with the naming
    # present, the XDG tier is not reached).
    monkeypatch.delenv("DOEFF_HY_CHECK_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert default_cache_dir() == tmp_path / "xdg" / "doeff-hy-check"
    monkeypatch.setenv("DOEFF_HY_CHECK_CACHE", str(tmp_path / "kept"))
    assert default_cache_dir() == tmp_path / "kept"


# ---- Two writers (two workers share the cache location) --------------------------------------------

#: Child processes write to the same entry many times (with large contents, so that another writer's write overlaps in the middle of a write).
WRITER = """\
import sys
from pathlib import Path
from doeff_hy._static_cache import CachedProjection, store
from doeff_hy_bytecode_guard._records import MacroRecord
cache, mark, rounds = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
used = MacroRecord("probe", (), (), (), (), ())
for _ in range(rounds):
    store(cache, "a" * 64, CachedProjection(mark * 400_000, (), (), used))
"""


def test_two_writers_of_one_entry_do_not_mix(tmp_path: Path) -> None:
    # Failure case ((b) of #3863): the temporary file name of a cache write was fixed as <entry>.tmp, so when two processes (two workers sharing the
    # cache location) wrote the same entry at the same time, they wrote into the same temporary file and mixed contents could be renamed. Write to a per-writer
    # temporary file and then rename. The final entry is exactly one writer's contents (readable JSON), and no temporary file remains.
    import json
    import subprocess

    from doeff_hy import _static_cache as static_cache

    cache = tmp_path / ".cache"
    writers = [
        subprocess.Popen([sys.executable, "-c", WRITER, str(cache), mark, "40"]) for mark in ("x", "y")
    ]
    assert [writer.wait(timeout=25) for writer in writers] == [0, 0]
    entries = list(cache.rglob("*.json"))
    assert len(entries) == 1, entries
    text = json.loads(entries[0].read_text(encoding="utf-8"))["text"]
    assert text in {"x" * 400_000, "y" * 400_000}, "the two writers' contents were mixed"
    assert not [p for p in cache.rglob("*") if p.is_file() and p.suffix != ".json"], "a temporary file remained"
    assert static_cache.CACHE_VERSION == 4


def test_each_writer_uses_its_own_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Failure case ((b) of #3863): two writes do not use the same temporary file (before the fix both used <entry>.tmp -- red).
    from doeff_hy import _static_cache as static_cache
    from doeff_hy_bytecode_guard._records import MacroRecord

    written: list[str] = []
    real = Path.write_text

    def recording(path: Path, data: str, *args: object, **kwargs: object) -> int:
        written.append(path.name)
        return real(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", recording)
    used = MacroRecord("probe", (), (), (), (), ())
    for _ in range(2):
        static_cache.store(tmp_path, "b" * 64, static_cache.CachedProjection("t", (), (), used))
    assert len(written) == 2
    assert written[0] != written[1], written


# ---- Lookup speed (checking 10 files cost 100,000 stats and 10 seconds of CPU) -----------------------------------------------


def test_entry_keeps_imports_and_old_entry_reads_as_unknown(tmp_path: Path) -> None:
    # The entry holds the import names (the expansion text is not re-read with ast on every lookup). An old entry without names reads as None.
    from doeff_hy import _static_cache as static_cache
    from doeff_hy_bytecode_guard._records import MacroRecord

    used = MacroRecord("probe", (), (), (), (), ())
    static_cache.store(tmp_path, "c" * 64, static_cache.CachedProjection("t", (), (), used, ("a", "a.b")))
    static_cache.store(tmp_path, "d" * 64, static_cache.CachedProjection("t", (), (), used))
    [with_imports] = (tmp_path / "cc" / ("c" * 64)).glob("*.json")
    [without] = (tmp_path / "dd" / ("d" * 64)).glob("*.json")
    kept = static_cache._read_entry(with_imports)
    old = static_cache._read_entry(without)
    assert isinstance(kept, static_cache.CachedProjection) and kept.imports == ("a", "a.b")
    assert isinstance(old, static_cache.CachedProjection) and old.imports is None


def test_candidates_are_looked_up_once_per_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Candidate existence is looked up once per dir (no stat per candidate). A missing dir is empty, and names that are not files (dirs) are not counted.
    from doeff_hy import _static_check as static_check

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.hy").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "sub.hy").mkdir()
    listing = static_check._listing(tmp_path / "pkg")
    assert listing.files == {"a.hy"} and listing.dirs == {"sub.hy"}
    assert static_check._listing(tmp_path / "missing") == static_check._Listing(frozenset(), frozenset())
    # Do not look beyond a missing dir; find <name>.hy and <name>/__init__.hy under a package
    (tmp_path / "pkg" / "inner").mkdir()
    (tmp_path / "pkg" / "inner" / "__init__.hy").write_text("", encoding="utf-8")
    asked: list[Path] = []

    def listing_of(directory: Path) -> object:
        asked.append(directory)
        return static_check._listing(directory)

    found = static_check._present_candidates((tmp_path,), "pkg.inner", listing_of)  # type: ignore[arg-type]
    assert found == (tmp_path / "pkg" / "inner" / "__init__.hy",)
    asked.clear()
    assert static_check._present_candidates((tmp_path,), "nope.deeper.mod", listing_of) == ()  # type: ignore[arg-type]
    assert asked == [tmp_path]


def test_shared_scope_stats_each_file_once_across_checks(tmp_path: Path) -> None:
    # Do not discard the memo per comparison; while in SharedScope, a file's state is read only once. After it closes, no memo remains.
    from doeff_hy_bytecode_guard._import_scope import SharedScope
    from doeff_hy_bytecode_guard._import_scope import current_scope
    from doeff_hy_bytecode_guard import _macro_use as macro_use

    probe = tmp_path / "x.py"
    probe.write_text("1", encoding="utf-8")
    assert current_scope() is None
    with SharedScope() as scope:
        assert current_scope() is scope
        first = macro_use._state_now(str(probe))
        probe.write_text("22", encoding="utf-8")  # treated as unchanged during the run
        assert macro_use._state_now(str(probe)) == first
        with SharedScope() as inner:  # a nested scope uses the outer memo as is
            assert inner is scope
    assert current_scope() is None
    assert macro_use._state_now(str(probe)) != first


# --- Per-closure bundle-------------------------------------------------------------------------


def _closure(tree: Tree) -> static_check.Closure:
    closure = static_check.project_closure(tree.root, [tree.root], [tree.user], tree.cache, 1)
    _forget(PACKAGE)
    return closure


def _opened_during(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Paths of files under the root and cache opened while looking up a closure (counts source reads and entry reads)."""
    import builtins
    import io

    opened: list[str] = []
    real = io.open

    def counting(file: object, *args: object, **kwargs: object) -> object:
        text = str(file)
        if text.startswith(str(tree.root.parent)):
            opened.append(text)
        return real(file, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(io, "open", counting)
    monkeypatch.setattr(builtins, "open", counting)
    try:
        _closure(tree)
    finally:
        monkeypatch.undo()
    return opened


def _age(tree: Tree) -> None:
    """Make every source's mtime sufficiently old (the bundle compares by reading the contents of files updated right after it was written)."""
    for path in tree.root.rglob("*"):
        if path.is_file():
            os.utime(path, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))


def test_a_warm_closure_opens_one_bundle_not_every_entry(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    _age(tree)
    first = _closure(tree)
    second = _closure(tree)  # the bundle is written on the second run (the first write does not trust just-updated files)
    opened = _opened_during(tree, monkeypatch)
    sources = [p for p in opened if p.endswith(".hy")]
    entries = [p for p in opened if p.endswith(".json")]
    bundles = [p for p in opened if p.endswith(".jsonl")]
    assert sources == [], f"a warm closure re-read the sources: {sources}"
    assert (entries, len(bundles)) == ([], 1), f"a warm closure read entries instead of one bundle: {opened}"
    assert {p: q.text for p, q in second.projections.items()} == {p: q.text for p, q in first.projections.items()}


def test_a_bundle_gives_the_same_projections_and_findings_as_expanding(tree: Tree) -> None:
    _age(tree)
    fresh = static_check.project_closure(tree.root, [tree.root], [tree.user], None, 1)
    _forget(PACKAGE)
    _closure(tree)
    bundled = _closure(tree)
    assert list(bundled.projections) == list(fresh.projections)
    for path, projection in fresh.projections.items():
        other = bundled.projections[path]
        assert (other.text, tuple(other.spans), other.findings) == (
            projection.text,
            tuple(projection.spans),
            projection.findings,
        )


def test_a_changed_source_is_not_served_from_the_bundle(tree: Tree, expansions: list[Path]) -> None:
    _age(tree)
    _closure(tree)
    _closure(tree)
    before = len(expansions)
    _rewrite(tree.user, USER + "(setv d 1)\n")
    changed = _closure(tree)
    assert tree.user in expansions[before:], "pulled the changed source from the bundle"
    assert "d = 1" in changed.projections[tree.user].text


def test_a_touched_but_unchanged_source_keeps_the_bundle(tree: Tree, expansions: list[Path]) -> None:
    _age(tree)
    _closure(tree)
    _closure(tree)
    before = len(expansions)
    os.utime(tree.user, None)  # only refresh the mtime
    _closure(tree)
    assert expansions[before:] == [], "re-expanded although the contents are the same"


def test_a_changed_macro_expands_again_even_from_a_bundle(tree: Tree, expansions: list[Path]) -> None:
    _age(tree)
    _closure(tree)
    _closure(tree)
    before = len(expansions)
    _rewrite(tree.package / "words.py", 'def word() -> str:\n    return "newer"\n')
    changed = _closure(tree)
    assert tree.user in expansions[before:], "pulled the bundle's expansion although the helper the macro calls was changed"
    assert "'newer'" in changed.projections[tree.user].text


def test_a_source_changed_right_after_the_bundle_was_written_is_read_again(
    tree: Tree, expansions: list[Path]
) -> None:
    # A file right after the bundle was written (new mtime) is compared by contents, not trusting size and mtime (so as not to miss a rewrite within the same tick).
    _closure(tree)
    _closure(tree)
    before = len(expansions)
    stat = tree.user.stat()
    tree.user.write_text(USER.replace(" b ", " z "), encoding="utf-8")  # same size
    os.utime(tree.user, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    changed = _closure(tree)
    assert "z =" in changed.projections[tree.user].text


def test_a_bundle_follows_a_changed_macro_to_the_next_run(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    # A row re-expanded after changing a macro is also rewritten into the bundle (if the bundle's row record stays stale, it falls through to entries every time and opens do not decrease).
    _age(tree)
    _closure(tree)
    _closure(tree)
    _rewrite(tree.package / "words.py", 'def word() -> str:\n    return "newer"\n')
    os.utime(tree.package / "words.py", ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    _closure(tree)  # re-expand and rewrite the bundle
    opened = _opened_during(tree, monkeypatch)
    assert [p for p in opened if p.endswith(".hy")] == []
    assert [p for p in opened if p.endswith(".json")] == [], opened
    assert len([p for p in opened if p.endswith(".jsonl")]) == 1, opened


# ---- Hold the position mapping compactly (memory the parent holds while pyright runs)--------------------------------------------


def test_packed_spans_read_like_the_tuple_they_pack() -> None:
    # The position mapping is held as one integer array per file (as individual lists or Spans, the parent's RSS stayed at 10GB with the daily 8.74 million of them).
    # Reads are the same as a tuple -- repr goes into the carried digest, so unless the spelling is the same as before packing, all carried diagnostics would be re-matched.
    import pickle

    from doeff_hy._static_cache import PackedSpans, Span

    spans = (Span((0, 0), (0, 5), 1, 1), Span((2, 4), (70000, 3), 90000, 12), Span((3, 0), (3, 1), 4, 2))
    packed = PackedSpans.of(spans)
    assert (len(packed), tuple(packed), repr(packed)) == (3, spans, repr(spans))
    assert (packed == spans, packed[1], packed[-1], packed[0:2]) == (True, spans[1], spans[2], spans[0:2])
    stored = PackedSpans.from_text(packed.payload())
    assert stored == packed and tuple(stored) == spans
    assert pickle.loads(pickle.dumps(packed)) == packed
    assert PackedSpans.of(()) == () and repr(PackedSpans.from_text(PackedSpans.of(()).payload())) == "()"


def test_an_entry_with_unreadable_spans_is_a_miss(tmp_path: Path) -> None:
    # A broken position mapping is skipped as an unreadable entry at read time (delaying it until use would make the whole check fail after pyright).
    from doeff_hy._static_cache import CacheMiss, _entry_from_json
    from doeff_hy_bytecode_guard._loader_hooks import record_to_json
    from doeff_hy_bytecode_guard._records import MacroRecord
    from doeff_hy import _static_cache as static_cache

    used = record_to_json(MacroRecord("probe", (), (), (), (), ()))
    entry = {"version": static_cache.CACHE_VERSION, "used": used, "text": "t", "spans": "not base64!", "findings": []}
    assert isinstance(_entry_from_json(entry), CacheMiss)
