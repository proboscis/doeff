"""Collect Hy test files without importing them, and import only at item setup (parent #1211).

Run pytest in a separate process (the pytester subprocess). The first run has no record, so it imports during collection (the Hy
plugin writes the record to the cache at that point); the second run collects from the cache. The nodeids, selection and results
of the two runs are the same, and the second collection is checked not to import the test modules. The cache and pyc locations are under tmp (ini doeff_adr_items_cache and
PYTHONPYCACHEPREFIX) -- nothing is written into the checkout or the user cache.

Each test module appends one line with its name to the ``IMPORT_LOG`` file when imported (to count imports from outside).
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

PRELUDE = """
(require doeff-hy.macros [deftest val])
(import os pytest)
(with [log (open (get os.environ "IMPORT_LOG") "a")] (.write log (+ __name__ "\\n")))
"""

FILES = {
    "pkg/__init__.py": "",
    "pkg/tests/__init__.py": "",
    "pkg/tests/test_alpha.hy": PRELUDE
    + """
(val pytestmark pytest.mark.real-world)
(deftest test-plain (assert True))
(deftest test-slow {:marks ["slow"]} (assert True))
(deftest test-params [x y] {:params {"x" ["a" 1 2.5 True None] "y" [[1 2] {"k" 1}]}}
  (assert (in y [[1 2] {"k" 1}])))
(deftest test-skipped {:skip-if (= 1 1) :skip-reason "always"} (assert False))
(deftest test-kept {:skip-if (= 1 2) :skip-reason "never"} (assert True))
""",
    # import another module in the parent package by name
    "pkg/tests/test_beta.hy": PRELUDE
    + """
(import pkg.tests.test-alpha :as alpha)
(deftest test-uses-alpha (assert (callable alpha.test-plain)))
""",
    "pkg/tests/test_dynamic.hy": PRELUDE
    + """
(val VALUES [1 2 3])
(deftest test-dynamic [v] {:params {"v" VALUES}} (assert (in v VALUES)))
""",
    # use our own macro provider (pkg.helpers) -- if the provider changes, the old record is not read (acceptance 4 of #1291)
    "pkg/helpers.hy": "(defmacro answer [] 42)\n",
    "pkg/tests/test_gamma.hy": PRELUDE
    + """
(require pkg.helpers [answer])
(deftest test-answer (assert (= (answer) 42)))
""",
    # module-level skipif pytestmark -- collected from the record; the condition is evaluated at setup from the real mark
    "pkg/tests/test_env_gated.hy": PRELUDE
    + """
(val pytestmark (pytest.mark.skipif (not (os.getenv "I1211_NEVER_SET")) :reason "env is not set"))
(deftest test-gated (assert False))
""",
    # skip the whole module at its top level (importing it yields 0 items)
    "pkg/tests/test_skipped_module.hy": PRELUDE
    + """
(pytest.skip "skip the whole module" :allow-module-level True)
(deftest test-never (assert False))
""",
}


@pytest.fixture
def project(pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    """A project with three test files. The pyc and records are written to locations under tmp."""
    for name, text in FILES.items():
        path = pytester.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    pytester.makeconftest(
        """
        import pytest


        @pytest.fixture
        def doeff_interpreter():
            def run_program(program, *, env=None):
                from doeff import run

                return run(program)

            return run_program
        """
    )
    pytester.makeini(
        f"""
        [pytest]
        doeff_adr_hy_files = pkg/tests/test_*.hy
        doeff_adr_wiring = off
        doeff_adr_items_cache = {tmp_path / "items"}
        markers =
            slow: slow
            real_world: real world
        """
    )
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pyc"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "")
    monkeypatch.setenv("IMPORT_LOG", str(tmp_path / "imports.log"))
    return pytester


def _imports(tmp_path: Path) -> list[str]:
    """Names of test modules imported so far (one entry per import). Cleared after reading."""
    log = tmp_path / "imports.log"
    names = log.read_text().split() if log.exists() else []
    log.write_text("")
    return names


@dataclass(frozen=True)
class Collected:
    """Result of --collect-only: the collected nodeids and the full text of the report."""

    nodeids: list[str]
    out: str


def _collect(project: pytest.Pytester, *args: str) -> Collected:
    """Run --collect-only in a separate process."""
    result = project.runpytest_subprocess("--collect-only", "-q", "-p", "no:cacheprovider", *args)
    nodeids = [line for line in result.stdout.lines if "::" in line and not line.startswith(" ")]
    return Collected(nodeids, result.stdout.str())


def test_warm_collection_matches_cold_and_imports_nothing(project: pytest.Pytester, tmp_path: Path) -> None:
    """On the second run, dynamic values are also collected from the record, and only files that skip per module are imported."""
    cold = _collect(project).nodeids
    assert sorted(_imports(tmp_path)) == sorted(
        [
            "pkg.tests.test_alpha",
            "pkg.tests.test_beta",
            "pkg.tests.test_dynamic",
            "pkg.tests.test_env_gated",
            "pkg.tests.test_gamma",
            "pkg.tests.test_skipped_module",
        ]
    )
    warm = _collect(project)
    out = warm.out
    assert warm.nodeids == cold
    assert "collected from record: 5 files; imported during collection: 1 files" in out
    assert "import: pkg/tests/test_skipped_module.hy — no record" in out
    assert _imports(tmp_path) == ["pkg.tests.test_skipped_module"]


def test_selection_by_k_and_m_is_the_same_and_imports_only_the_chosen_file(
    project: pytest.Pytester, tmp_path: Path
) -> None:
    """``-k`` / ``-m`` selection works the same when collecting from the record, and only the files of the items that ran are imported."""
    _collect(project)
    _imports(tmp_path)
    result = project.runpytest_subprocess("-p", "no:cacheprovider", "-k", "test_slow")
    result.assert_outcomes(passed=1, deselected=19, skipped=1)
    assert _imports(tmp_path) == [
        "pkg.tests.test_skipped_module",
        "pkg.tests.test_alpha",
    ]
    result = project.runpytest_subprocess("-p", "no:cacheprovider", "-m", "slow")
    result.assert_outcomes(passed=1, deselected=19, skipped=1)
    result = project.runpytest_subprocess("-p", "no:cacheprovider", "-m", "not real_world")
    result.assert_outcomes(passed=5, deselected=14, skipped=2)


def test_params_and_skip_if_run_as_when_imported(project: pytest.Pytester) -> None:
    """Param values run as real values (including values whose content is irrelevant), and skip-if is evaluated as the real expression."""
    cold = project.runpytest_subprocess("-p", "no:cacheprovider")
    cold.assert_outcomes(passed=18, skipped=3)
    warm = project.runpytest_subprocess("-p", "no:cacheprovider", "-rs")
    warm.assert_outcomes(passed=18, skipped=3)
    warm.stdout.fnmatch_lines(["*always*", "*env is not set*"])


def test_a_named_hy_file_imported_by_another_named_file_is_read_by_hy(project: pytest.Pytester) -> None:
    """A .hy file named on the command line is read by the Hy loader even when another earlier-named file imports it.

    pytest's assert rewriting re-reads files named on the command line as Python (``ast.parse``). If a .hy
    is handed there it becomes a SyntaxError (the pair where test_emulated_delivery_unwritten imports
    test_emulated_delivery_to_turn). It passes the same whether the record is cold or warm.
    """
    named = ("pkg/tests/test_beta.hy", "pkg/tests/test_alpha.hy")
    cold = project.runpytest_subprocess("-p", "no:cacheprovider", *named)
    cold.assert_outcomes(passed=14, skipped=1)
    warm = project.runpytest_subprocess("-p", "no:cacheprovider", *named)
    warm.assert_outcomes(passed=14, skipped=1)


def test_a_record_that_disagrees_fails_the_item_and_is_forgotten(project: pytest.Pytester, tmp_path: Path) -> None:
    """If the record is hand-edited so it disagrees with the real thing, that item goes red, the record is deleted, and the next collection imports again."""
    _collect(project)
    entry = _cache_entry(project, tmp_path, "pkg/tests/test_alpha.hy")
    data = json.loads(entry.read_text())
    texts = [json.loads(text) for text in data["records"]]
    for record in texts:
        if record.get("function") == "test_slow":
            record["decorators"] = [{"mark": "fast"}]
    data["records"] = [json.dumps(record) for record in texts]
    entry.write_text(json.dumps(data))
    _imports(tmp_path)
    result = project.runpytest_subprocess("-p", "no:cacheprovider", "-k", "test_slow or test_fast")
    result.assert_outcomes(errors=1, deselected=19, skipped=1)
    result.stdout.fnmatch_lines(["*record and real thing disagree*"])
    out = _collect(project).out
    assert "import: pkg/tests/test_alpha.hy — no record" in out


MARKS_CONFTEST = """
import json
import os

import pytest


@pytest.fixture
def doeff_interpreter():
    from doeff import run

    return run


def pytest_collection_modifyitems(config, items):
    # The collection hook adds marks to the item and module nodes (both before and after, with arguments). It is not an
    # unknown makeitem hook that would refuse collecting from the record, so the second run collects from the record.
    modules = []
    for item in items:
        item.add_marker(pytest.mark.plugin_first, append=False)
        item.add_marker(pytest.mark.plugin_mark("arg1", key="v"))
        if item.parent not in modules:
            modules.append(item.parent)
    for module in modules:
        module.add_marker(pytest.mark.plugin_module)


@pytest.hookimpl(trylast=True)
def pytest_runtest_setup(item):
    def seen(node):
        return [
            [repr((m.name, m.args, m.kwargs)), repr(node.keywords.get(m.name))]
            for m in node.own_markers
        ]

    with open(os.environ["MARK_LOG"], "a") as log:
        log.write(json.dumps([item.nodeid, seen(item), seen(item.parent)]) + "\\n")
"""


def test_marks_added_by_a_collection_hook_survive_the_swap_to_the_real_module(
    project: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Marks added by the collection hook remain in place even after setup (replacement with the real thing) of an item collected from the record. The sequence of marks
    (names, arguments) and keywords are the same whether collected by importing or from the record.

    Before the fix, function marks were replaced wholesale with the real marks, so marks added by the hook vanished, and module marks had the real marks
    moved to the end.
    """
    project.makeconftest(MARKS_CONFTEST)
    log = tmp_path / "marks.log"
    monkeypatch.setenv("MARK_LOG", str(log))

    def seen() -> list[list[object]]:
        lines = sorted(json.loads(line) for line in log.read_text().splitlines())
        log.write_text("")
        return lines

    project.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=18, skipped=3)
    cold = seen()
    warm_run = project.runpytest_subprocess("-p", "no:cacheprovider")
    warm_run.assert_outcomes(passed=18, skipped=3)
    warm_run.stdout.fnmatch_lines(["*collected from record: 5 files; imported during collection: 1 files*"])
    assert seen() == cold
    slow = next(entry for entry in cold if entry[0].endswith("::test_slow"))
    assert [shown for shown, _ in slow[1]] == [
        repr(("plugin_first", (), {})),
        repr(("slow", (), {})),
        repr(("plugin_mark", ("arg1",), {"key": "v"})),
    ]
    assert [shown for shown, _ in slow[2]] == [
        repr(("real_world", (), {})),
        repr(("plugin_module", (), {})),
    ]


def _cache_entry(project: pytest.Pytester, tmp_path: Path, relative: str) -> Path:
    """From the current content hash of a test file, derive the path of the cache file."""
    digest = hashlib.sha256((project.path / relative).read_bytes()).hexdigest()
    return tmp_path / "items" / f"{digest}.json"


def test_one_changed_character_reads_no_stale_record(project: pytest.Pytester, tmp_path: Path) -> None:
    """A file with one source character changed does not read the old record; it imports again and rewrites the record (acceptance 4 of #1291)."""
    _collect(project)
    old_entry = _cache_entry(project, tmp_path, "pkg/tests/test_alpha.hy")
    assert old_entry.exists()
    path = project.path / "pkg/tests/test_alpha.hy"
    path.write_text(path.read_text().replace("test-plain (assert True)", "test-plain (assert (not False))"))
    _imports(tmp_path)
    out = _collect(project).out
    assert "import: pkg/tests/test_alpha.hy — no record" in out
    assert "pkg.tests.test_alpha" in _imports(tmp_path)
    assert _cache_entry(project, tmp_path, "pkg/tests/test_alpha.hy").exists()
    assert "collected from record: 5 files" in _collect(project).out


def test_a_changed_macro_provider_reads_no_stale_record(project: pytest.Pytester, tmp_path: Path) -> None:
    """If a macro used by the expansion changes, files using it do not read the old record (files that do not use it stay on the record; acceptance 4 of #1291)."""
    _collect(project)
    helpers = project.path / "pkg/helpers.hy"
    helpers.write_text("(defmacro answer [] (+ 40 2))\n")
    _imports(tmp_path)
    out = _collect(project).out
    assert "import: pkg/tests/test_gamma.hy — the macros used by the expansion changed" in out
    assert "import: pkg/tests/test_alpha.hy" not in out
    assert "pkg.tests.test_gamma" in _imports(tmp_path)


def test_a_macro_the_expansion_does_not_use_keeps_the_record(project: pytest.Pytester, tmp_path: Path) -> None:
    """Adding a macro that the expansion does not use to the macro provider still collects from the record (before the fix, the check compared the provider file's
    sha256, so any line change in doeff_hy's macros.hy made doeff's daily run re-collect every Hy test file by import, and it exceeded the daily
    time limit -- the 10-12 04:00 and 08:00 runs). The record is now checked per macro used by the expansion (the doeff_hy_bytecode_guard record)."""
    _collect(project)
    helpers = project.path / "pkg/helpers.hy"
    helpers.write_text("(defmacro answer [] 42)\n(defmacro unused [] 0)\n")
    _imports(tmp_path)
    out = _collect(project).out
    assert "import: pkg/tests/test_gamma.hy" not in out, out
    assert "pkg.tests.test_gamma" not in _imports(tmp_path)


def test_a_fixture_bound_by_assignment_is_collected_from_records(project: pytest.Pytester, tmp_path: Path) -> None:
    """A file with a module fixture built by assignment ((val name ((pytest.fixture ...) function))) is also collected from the record -- a stand-in module
    gets a stand-in fixture with the same name, scope and arguments, and when it is called it calls the real module's fixture. During that run the real module is
    imported only once (condition 1 of the discussion). Before L713, collecting from the record alone gave
    fixture not found."""
    (project.path / "pkg/tests/test_fixture_by_val.hy").write_text(
        PRELUDE
        + """
(defn make-answer [tmp-path-factory] (if (is-not tmp-path-factory None) 42 0))
(val answer ((pytest.fixture :scope "module" :name "answer") make-answer))
(defn make-counter []
  (yield [1])
  None)
(val counter ((pytest.fixture :name "counter") make-counter))
(deftest test-uses-the-fixture [answer counter] (assert (= answer 42)) (assert (= counter [1])))
(deftest test-uses-it-again [answer] (assert (= answer 42)))
"""
    )
    first = project.runpytest_subprocess("-p", "no:cacheprovider", "-k", "uses_the_fixture or uses_it_again")
    first.assert_outcomes(passed=2, deselected=20, skipped=1)
    _imports(tmp_path)
    out = _collect(project).out
    assert "test_fixture_by_val.hy" not in "\n".join(line for line in out.splitlines() if "import:" in line)
    assert _imports(tmp_path).count("pkg.tests.test_fixture_by_val") == 0
    second = project.runpytest_subprocess("-p", "no:cacheprovider", "-k", "uses_the_fixture or uses_it_again")
    second.assert_outcomes(passed=2, deselected=20, skipped=1)
    assert _imports(tmp_path).count("pkg.tests.test_fixture_by_val") == 1


@pytest.mark.parametrize(
    "fixture_form",
    [
        '((pytest.fixture :params [1 2] :name "answer") make-answer)',
        '((pytest.fixture :autouse True :name "answer") make-answer)',
    ],
)
def test_fixtures_that_shape_collection_keep_their_file_imported(
    project: pytest.Pytester, tmp_path: Path, fixture_form: str
) -> None:
    """A fixture with params / autouse changes the collection result, so that file is still imported during collection as before (condition 1)."""
    (project.path / "pkg/tests/test_shaping_fixture.hy").write_text(
        PRELUDE
        + f"""
(defn make-answer [] 42)
(val answer {fixture_form})
(deftest test-shaping (assert True))
"""
    )
    _collect(project)
    out = _collect(project).out
    assert "import: pkg/tests/test_shaping_fixture.hy — no record" in out
    assert "fixture answer has params / autouse" in out
