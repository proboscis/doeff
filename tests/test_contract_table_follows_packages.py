"""Test that goes red when the contract-test table (`[[tool.doeff.contract-tests]]` in the root pyproject.toml) drifts from the addition or removal of packages
(hole 4 remaining from #2605, "the contract table is maintained by hand").

The table is maintained by hand. If a package (a uv workspace member — `packages/<name>/`) is added but the table has no entry for it, checks such as that package's stub
matching are silently never selected by the pre-registration entry point (`make test-changed`). This file does the following:

1. In the real tree, every package has in the table either a contract entry or an exclusion with a reason (a `reason` line), and every test file named in the table exists
   (the reader read_contract_table stops if a listed test file is missing — that stop is verified daily).
2. Failure cases — in a model repo holding a copy of the real pyproject.toml: adding one package without fixing the table is named; deleting a test file from the table
   stops the reader; deleting a package that has an entry stops the reader; an exclusion without a reason (no reason key, empty, or whitespace only) and
   an exclusion of a prefix that is not a package stops the reader.
3. A test that walks a tree is selected by changes to what it walks: the doeff-traverse examples test globs examples/ and runs them in child processes, and imports neither the examples nor
   the macros, so it is not selected by reverse dependencies. A diff that changes an example, its expected output, the doeff-hy macro module the examples require, or
   the runtime half of the macros (doeff_core_effects._outcomes, which holds how the `<-` and `!` binding is opened) selects the examples test from the real table
   (while the table lacked it, changes that broke examples were not measured before registration and first went red in the next day's daily run.
   The runtime half was found by blind test B of design verification — it was not selected by the doeff-hy row alone).
4. Tests that measure against the Hy version in uv.lock are selected by changes to uv.lock and to the uv.lock version reader (locked_version in scripts/semgrep_locked.py):
   the version is read from a data file, so reverse dependencies do not select them. The measuring tests are counted from source (
   test_bytecode_guard.py reads the version from uv.lock but was not in the table, so it was not selected when Hy was upgraded and ran only in the daily run).
5. Changes to the doeff-hy modules that build the shape of handle and defhandler clauses (handle.hy, clause_ops.hy, expansion_names.py) select the doeff-effect-analyzer
   tests: the analyzer expands Hy source with the real macros and reads it but does not import doeff-hy, so reverse dependencies do not select them
   (when 4f284efa9 changed the local names of clauses, 12 analyzer tests and the agora-controllers closure tests landed on main while red).

This runs from both the daily population (tests/) and the repo-wide row of the table (prefix = "") — the change-based test selection runs this file as the first
entry for any change. It does not call the entry point's main; it calls the reader's pure definitions (read_contract_table, workspace_packages, table_gaps) directly.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "run_changed_tests.py"
#: Name of the package added to the model (a name not in the real tree).
ADDED = "packages/doeff-added-without-row/"
#: The examples test, the examples dir it walks (the example `.hy` files and the expected output in expected/), and the macro module the examples require.
EXAMPLES_CHECK = "packages/doeff-traverse/tests/test_examples_run.py"
EXAMPLES_DIR = "packages/doeff-traverse/examples"
EXAMPLE_MACROS = "packages/doeff-hy/src/doeff_hy/macros.hy"
#: The module of the runtime half of the macros and its file. doeff-hy's outcome_forms.hy says "the runtime meaning is held by doeff_core_effects._outcomes
#: and this only builds the shape", and the binding of the defk body calls open_bind of this module, which macros.hy puts in globals.
EXAMPLE_MACRO_RUNTIME_MODULE = "doeff_core_effects._outcomes"
EXAMPLE_MACRO_RUNTIME = "packages/doeff-core-effects/doeff_core_effects/_outcomes.py"
#: uv.lock, and the one place in doeff that reads the version from uv.lock.
LOCK = "uv.lock"
LOCK_READER = "scripts/semgrep_locked.py"
#: The doeff-hy modules that build the shape of handle and defhandler clauses (the arguments of the function that runs the clause, and the effect and continuation names read by clause operations) (
#: the 3 files changed by 4f284efa9 — the macro definition, the clause operations, and the definition of the name spellings), and the analyzer test files that expand and read them with the real macros.
HANDLER_EXPANSION_MODULES = (
    "packages/doeff-hy/src/doeff_hy/handle.hy",
    "packages/doeff-hy/src/doeff_hy/_clause_ops.hy",
    "packages/doeff-hy/src/doeff_hy/_expansion_names.py",
)
ANALYZER_EXPANSION_CHECKS = (
    "packages/doeff-effect-analyzer/tests/python/test_program_effects.py",
    "packages/doeff-effect-analyzer/tests/python/test_guarded_clauses.py",
)
#: The line where an analyzer test requires the real defhandler in Hy source (the marker that it goes through expansion — from doeff-hy.macros or doeff-hy.handle).
HANDLER_MACRO_REQUIRE = re.compile(r"\(require doeff-hy\.(?:macros|handle) \[[^\]]*\bdefhandler\b")
#: The line where a test's source calls the reader for the Hy version (a call to Python's locked_version or Hy's .locked-version that passes the name hy
#: as the package).
LOCKED_HY_READ = re.compile(r'locked[-_]version\b[^\n]*"hy"')
#: Dirs not descended into when counting measuring tests (venv, git, build output — they hold no test source).
NOT_WALKED = re.compile(r"^(\..*|node_modules|target|build|dist|__pycache__)$")


@dataclass(frozen=True)
class Copy:
    """A model repo holding a copy of the real table, and the table as copied (prefixes of entry rows and test paths)."""

    root: Path
    contract_prefixes: tuple[str, ...]
    test_paths: tuple[str, ...]


@pytest.fixture(scope="module")
def runner() -> Iterator[ModuleType]:
    """Load the entry script as a module (a dataclass looks up its defining module in sys.modules — registered only during the test)."""
    spec = importlib.util.spec_from_file_location("run_changed_tests", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        yield module


def _touch(path: Path) -> None:
    """Place an empty file in the model tree (the reader only looks at whether it exists)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _copy_of_real_table(runner: ModuleType, tmp_path: Path) -> Copy:
    """Build a model repo that copies the real pyproject.toml as is and places, as empty files, only the tree shape named by the table and workspace (package pyproject.toml files, entry-prefix dirs,
    and test files) — to verify by moving only the tree side without rewriting the table."""
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copy2(REPO_ROOT / "pyproject.toml", root / "pyproject.toml")
    units = runner.workspace_packages(REPO_ROOT)
    table = runner.read_contract_table(REPO_ROOT)
    test_paths = tuple(sorted({test.split("::", 1)[0] for row in table.rows for test in row.tests}))
    for prefix in units.prefixes:
        _touch(root / prefix / "pyproject.toml")
    # Rows for dirs inside a package (e.g. src of doeff-hy) — the reader requires that a prefix dir (ending with `/`) or file exists in the tree.
    for row in table.rows:
        if row.prefix.endswith("/"):
            (root / row.prefix).mkdir(parents=True, exist_ok=True)
        elif row.prefix:
            _touch(root / row.prefix)
    for path in test_paths:
        _touch(root / path)
    return Copy(
        root=root,
        contract_prefixes=tuple(row.prefix for row in table.rows),
        test_paths=test_paths,
    )


def _append_row(root: Path, row: str) -> None:
    """Append one table row to the end of the model pyproject.toml (the key lines after `[[tool.doeff.contract-tests]]`)."""
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(f"{text}\n[[tool.doeff.contract-tests]]\n{row}\n", encoding="utf-8")


def test_every_package_has_a_contract_row_or_a_reasoned_exemption(runner: ModuleType) -> None:
    """Real tree: the table can be read (all listed test files exist), and every package has a contract entry or an exclusion with a reason."""
    table = runner.read_contract_table(REPO_ROOT)
    units = runner.workspace_packages(REPO_ROOT)
    assert units.prefixes, "0 uv workspace members — the unit reading is broken"
    gaps = runner.table_gaps(table, units)
    assert gaps.uncovered == (), (
        "packages with neither a contract entry nor an exclusion with a reason: "
        f"{list(gaps.uncovered)} — in [[tool.doeff.contract-tests]] of the root pyproject.toml, add an entry for a test that verifies the package's public shape"
        " as a whole (stub matching, public names, population), or write why it is not needed in prefix and reason rows"
    )
    assert all(row.reason for row in table.exemptions), table.exemptions


def test_changing_an_example_or_its_macros_selects_the_examples_check(runner: ModuleType) -> None:
    """Real table: a diff that changes any of one example, one expected output, the doeff-hy macro module the examples require, or the runtime-half module of the macros
    has the examples test selected by the changed-test selection (select_targets, the same as the entry point)."""
    table = runner.read_contract_table(REPO_ROOT)
    patterns = runner.collector_patterns(REPO_ROOT)
    examples = REPO_ROOT / EXAMPLES_DIR
    changed = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in (*examples.glob("*.hy"), *(examples / "expected").glob("*.txt"))
    )
    assert any(p.endswith(".hy") for p in changed), f"{EXAMPLES_DIR} has no example — the test's premise is broken"
    assert any(p.endswith(".txt") for p in changed), f"{EXAMPLES_DIR}/expected has no expected output"
    assert (REPO_ROOT / EXAMPLE_MACROS).is_file(), (
        f"{EXAMPLE_MACROS} does not exist — fix the module the examples require"
    )
    # If the macros move the runtime half to another module, fix the module name and file here (do not stay green with the old name).
    assert f'"{EXAMPLE_MACRO_RUNTIME_MODULE}"' in (REPO_ROOT / EXAMPLE_MACROS).read_text(
        encoding="utf-8"
    ), (
        f"{EXAMPLE_MACROS} does not name the runtime module {EXAMPLE_MACRO_RUNTIME_MODULE} — write the module it moved to"
    )
    assert EXAMPLE_MACRO_RUNTIME.endswith(EXAMPLE_MACRO_RUNTIME_MODULE.replace(".", "/") + ".py"), (
        EXAMPLE_MACRO_RUNTIME
    )
    assert (REPO_ROOT / EXAMPLE_MACRO_RUNTIME).is_file(), f"{EXAMPLE_MACRO_RUNTIME} does not exist"
    unselected = [
        path
        for path in (*changed, EXAMPLE_MACROS, EXAMPLE_MACRO_RUNTIME)
        if EXAMPLES_CHECK
        not in {
            target.arg
            for target in runner.select_targets(
                table, runner.ChangeSet(base="", files=(path,)), patterns, REPO_ROOT
            ).targets
        }
    ]
    assert unselected == [], (
        f"changes that do not select the examples test {EXAMPLES_CHECK}: {unselected} — in [[tool.doeff.contract-tests]] of the root pyproject.toml"
        ", add the examples test to the tests of the row matching that path (the examples test imports neither examples nor macros, so reverse dependencies do not select it)"
    )


def _walked_files() -> Iterator[Path]:
    """Files of the repo (without descending into NOT_WALKED dirs)."""
    for current, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = sorted(name for name in dirs if not NOT_WALKED.match(name))
        yield from (Path(current, name) for name in files)


def _locked_hy_checks(runner: ModuleType) -> tuple[str, ...]:
    """Test files that measure against the Hy version in uv.lock — among files matching the collector's naming rule, those whose source calls the reader for the Hy version."""
    patterns = runner.collector_patterns(REPO_ROOT)
    return tuple(
        sorted(
            rel
            for path in _walked_files()
            for rel in (path.relative_to(REPO_ROOT).as_posix(),)
            if runner.is_test_file(rel, patterns)
            and LOCKED_HY_READ.search(path.read_text(encoding="utf-8"))
        )
    )


def _selected_files(runner: ModuleType, changed: str) -> frozenset[str]:
    """The test files selected by the changed-test selection (select_targets, the same as the entry point) for a diff that changes one changed file."""
    selection = runner.select_targets(
        runner.read_contract_table(REPO_ROOT),
        runner.ChangeSet(base="", files=(changed,)),
        runner.collector_patterns(REPO_ROOT),
        REPO_ROOT,
    )
    return frozenset(target.arg.split("::", 1)[0] for target in selection.targets)


def test_changing_the_lock_or_its_reader_selects_every_check_measuring_the_locked_hy(
    runner: ModuleType,
) -> None:
    """Real table: both a diff that changes uv.lock and a diff that changes the uv.lock version reader select all test files that measure against the Hy version in uv.lock
    (selecting some of the tests in a file is enough). Measuring tests are counted from source — no copied list is kept."""
    checks = _locked_hy_checks(runner)
    assert checks, (
        f"0 tests call {LOCK_READER} to read the Hy version — if the reader's name or calling style changed, fix LOCKED_HY_READ"
    )
    selected = {changed: _selected_files(runner, changed) for changed in (LOCK, LOCK_READER)}
    unselected = [
        (changed, check)
        for changed, files in selected.items()
        for check in checks
        if check not in files
    ]
    assert unselected == [], (
        f"changes that do not select tests measuring against the Hy version in uv.lock #(changed file, test): {unselected} — in [[tool.doeff.contract-tests]] of the root pyproject.toml"
        ", add the test to the tests of the row matching that path (the version is read from a data file, so "
        "reverse dependencies do not select it)"
    )


def test_changing_the_handler_expansion_selects_the_analyzer_checks(runner: ModuleType) -> None:
    """Real table: a diff that changes any of the doeff-hy modules that build the shape of handle and defhandler clauses (the 3 files changed by 4f284efa9)
    has the changed-test selection (select_targets, the same as the entry point) select the doeff-effect-analyzer test files. The analyzer tests write
    `(require doeff-hy.macros [defhandler …])` in Hy source, expand it with the real macros and then read it, and do not import doeff-hy, so reverse dependencies do not select them
    (when 4f284efa9 changed the local names of clauses, 12 analyzer tests and the agora-controllers closure tests landed on main while red
    and first showed up in the daily run). Removing the row turns this test red."""
    for module in HANDLER_EXPANSION_MODULES:
        assert (REPO_ROOT / module).is_file(), f"{module} does not exist — write where it moved or its new name"
    for check in ANALYZER_EXPANSION_CHECKS:
        source = (REPO_ROOT / check).read_text(encoding="utf-8")
        # If a test stops going through the real macros, this selection protects nothing (do not stay green on an outdated premise).
        assert HANDLER_MACRO_REQUIRE.search(source), (
            f"{check} does not require the real defhandler — it no longer reads real expanded clauses. Fix the test being selected"
        )
    unselected = [
        (module, check)
        for module in HANDLER_EXPANSION_MODULES
        for check in ANALYZER_EXPANSION_CHECKS
        if check not in _selected_files(runner, module)
    ]
    assert unselected == [], (
        f"changes that do not select the analyzer tests #(changed file, test): {unselected} — in [[tool.doeff.contract-tests]] of the root pyproject.toml, "
        "add the analyzer tests to the tests of the doeff-hy src row (the analyzer does not import doeff-hy, so reverse dependencies do not select them)"
    )


@dataclass(frozen=True)
class Uncovered:
    """Prefixes of packages in one tree that have neither an entry nor an exclusion with a reason (in name order)."""

    prefixes: tuple[str, ...]


def _gaps(runner: ModuleType, root: Path) -> Uncovered:
    """Read the table of tree root and return packages with neither an entry nor an exclusion with a reason — to compare before and after the model change (the reader stops if it cannot read)."""
    gaps = runner.table_gaps(runner.read_contract_table(root), runner.workspace_packages(root))
    return Uncovered(prefixes=gaps.uncovered)


def test_the_copy_of_the_real_table_reads_like_the_real_tree(
    runner: ModuleType, tmp_path: Path
) -> None:
    """Check that the model copies the real table (the foundation of the failure cases below): the copy is readable, and its entry rows and drift match the real tree.
    The failure cases below look only at the difference before and after the copy — drift in the real tree turns only the test above red (the same red is not counted twice)."""
    copy = _copy_of_real_table(runner, tmp_path)
    table = runner.read_contract_table(copy.root)
    assert tuple(row.prefix for row in table.rows) == copy.contract_prefixes
    assert _gaps(runner, copy.root) == _gaps(runner, REPO_ROOT)


def test_a_package_added_without_a_row_is_named(runner: ModuleType, tmp_path: Path) -> None:
    """Failure case: adding one package without fixing the table names that package (red). A dir without pyproject.toml
    (an untracked remnant of a retired crate) and a dir matching the workspace exclude are not packages, so they are not named."""
    copy = _copy_of_real_table(runner, tmp_path)
    before = _gaps(runner, copy.root)
    _touch(copy.root / ADDED / "pyproject.toml")
    _touch(copy.root / "packages" / "doeff-residue" / "Cargo.lock")
    _touch(copy.root / "packages" / "doeff-agentd" / "pyproject.toml")
    after = _gaps(runner, copy.root)
    assert ADDED not in before.prefixes, before
    assert after.prefixes == tuple(sorted({*before.prefixes, ADDED})), after


def test_a_reasoned_exemption_closes_the_gap(runner: ModuleType, tmp_path: Path) -> None:
    """Writing an exclusion with a reason for the added package closes the drift (not silently dropped; the reason remains in the table)."""
    copy = _copy_of_real_table(runner, tmp_path)
    before = _gaps(runner, copy.root)
    _touch(copy.root / ADDED / "pyproject.toml")
    _append_row(copy.root, f'prefix = "{ADDED}"\nreason = "no test verifies the public shape as a whole (model)"')
    assert _gaps(runner, copy.root) == before
    added = [row for row in runner.read_contract_table(copy.root).exemptions if row.prefix == ADDED]
    assert [row.reason for row in added] == ["no test verifies the public shape as a whole (model)"], added


def test_removing_a_test_file_named_by_the_table_stops_the_reader(
    runner: ModuleType, tmp_path: Path
) -> None:
    """Failure case: deleting a test file listed in the table makes the reader stop, naming it (the entry point returns rc 2; this test is red)."""
    copy = _copy_of_real_table(runner, tmp_path)
    removed = copy.test_paths[0]
    (copy.root / removed).unlink()
    with pytest.raises(runner.StopError, match="listed test file is missing from the tree") as caught:
        runner.read_contract_table(copy.root)
    assert removed in str(caught.value)


def test_removing_a_package_with_a_row_stops_the_reader(runner: ModuleType, tmp_path: Path) -> None:
    """Failure case (shrinking direction): deleting the dir of a package that has an entry without fixing the table makes the reader stop, naming the prefix."""
    copy = _copy_of_real_table(runner, tmp_path)
    removed = next(p for p in copy.contract_prefixes if p.startswith("packages/"))
    shutil.rmtree(copy.root / removed)
    with pytest.raises(runner.StopError, match="is missing from the tree") as caught:
        runner.read_contract_table(copy.root)
    assert removed in str(caught.value)


@pytest.mark.parametrize(
    ("row", "named"),
    [
        (f'prefix = "{ADDED}"', "keys must be"),
        (f'prefix = "{ADDED}"\nreason = ""', "non-empty"),
        (f'prefix = "{ADDED}"\nreason = "   "', "non-empty"),
        (f'prefix = "{ADDED}"\nreason = 1', "non-empty"),
        (
            'prefix = "packages/"\nreason = "a prefix that is not a package"',
            "is not a package (a uv workspace member dir)",
        ),
        (
            'prefix = ""\nreason = "exclude the whole repo"',
            "is not a package (a uv workspace member dir)",
        ),
        (f'prefix = "{ADDED}"\nreason = "both"\ntests = ["tests/test_x.py"]', "keys must be"),
    ],
    ids=[
        "reason-missing",
        "reason-empty",
        "reason-blank",
        "reason-not-text",
        "not-a-package",
        "repo-wide",
        "both",
    ],
)
def test_an_exemption_without_a_reason_stops_the_reader(
    runner: ModuleType, tmp_path: Path, row: str, named: str
) -> None:
    """Failure case: an exclusion without a reason, an exclusion of a prefix that is not a package, and a row mixing an entry and an exclusion make the reader stop (nothing is silently dropped)."""
    copy = _copy_of_real_table(runner, tmp_path)
    _touch(copy.root / ADDED / "pyproject.toml")
    _append_row(copy.root, row)
    with pytest.raises(runner.StopError, match=re.escape(named)):
        runner.read_contract_table(copy.root)
