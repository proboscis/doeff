"""Counterexamples showing that the shortcut that builds items from records (``indexed_pytest``) builds the same items as pytest's normal path.

Every test runs the first time (no records, so modules are imported during collection = pytest's normal Module collection) and the second and later times (collected from records =
the shortcut) in separate processes, and checks that the order of nodeids, the number of results, and the values checked inside the tests are the same. Fixture values and
``request.fixturenames`` are checked inside the tests, so if fixture resolution leaks from a sibling item, the second run turns red.

The cache and pyc locations are under tmp (the same as test_lazy_collection).
"""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

PRELUDE = "(require doeff-hy.macros [deftest val])\n(import pytest)\n"

CONFTEST = """
import pytest


@pytest.fixture
def doeff_interpreter():
    def run_program(program, *, env=None):
        from doeff import run

        return run(program)

    return run_program


@pytest.fixture
def answer():
    return 42


@pytest.fixture
def auto_seen():
    return "auto"


@pytest.fixture(autouse=True)
def autoused(auto_seen):
    return auto_seen


@pytest.fixture
def ini_used():
    return "ini"


@pytest.fixture(params=[10, 20], ids=["ten", "twenty"])
def base(request):
    return request.param


"""

REGISTRATION_CONFTEST = """
import pytest
class _Registered:
    @pytest.fixture(name="answer")
    def registered_answer(self):
        return 99


def pytest_collectstart(collector):
    # Register a fixture additionally in just one module before collection (a counterexample for additional fixture registration).
    if isinstance(collector, pytest.Module) and collector.path.name == "test_registered.hy":
        collector.session._fixturemanager.parsefactories(holder=_Registered(), node=collector)
"""

FILES = {
    "pkg/__init__.py": "",
    # Two files under the same conftest — only one overrides answer with 41 through a module fixture
    "pkg/registered/__init__.py": "",
    "pkg/registered/conftest.py": REGISTRATION_CONFTEST,
    "pkg/test_fixture_a.hy": PRELUDE
    + """
(defn local-answer [] 41)
(val answer ((pytest.fixture :name "answer") local-answer))
(deftest test-local-answer [answer] (assert (= answer 41)))
(deftest test-local-answer-again [answer] (assert (= answer 41)))
(deftest test-local-answer-with-request [answer request] (assert (= answer 41)) (assert (in "autoused" request.fixturenames)))
""",
    "pkg/test_fixture_b.hy": PRELUDE
    + """
(deftest test-conftest-answer [answer] (assert (= answer 42)))
(deftest test-conftest-answer-again [answer] (assert (= answer 42)))
""",
    # Override in a lower conftest
    "pkg/sub/__init__.py": "",
    "pkg/sub/conftest.py": "import pytest\n\n\n@pytest.fixture\ndef answer():\n    return 43\n",
    "pkg/sub/test_fixture_c.hy": PRELUDE
    + """
(deftest test-sub-answer [answer] (assert (= answer 43)))
(deftest test-sub-answer-again [answer] (assert (= answer 43)))
""",
    # A fixture a plugin registered additionally on the module before collection
    "pkg/registered/test_registered.hy": PRELUDE
    + """
(deftest test-registered-answer [answer] (assert (= answer 99)))
(deftest test-registered-answer-again [answer] (assert (= answer 99)))
""",
    # autouse, ini usefixtures, module usefixtures (a call of module usefixtures cannot be recorded, so it takes the import path)
    "pkg/test_used.hy": PRELUDE
    + """
(deftest test-autouse-and-ini [request]
  (assert (in "autoused" request.fixturenames))
  (assert (in "auto_seen" request.fixturenames))
  (assert (in "ini_used" request.fixturenames)))
(deftest test-autouse-and-ini-again [request]
  (assert (in "ini_used" request.fixturenames)))
(deftest test-bare-usefixtures [request] {:marks ["usefixtures"]} (assert (in "ini_used" request.fixturenames)))
(deftest test-bare-usefixtures-again [request] {:marks ["usefixtures"]} (assert (in "ini_used" request.fixturenames)))
""",
    "pkg/test_module_usefixtures.hy": PRELUDE
    + """
(val pytestmark (pytest.mark.usefixtures "auto_seen"))
(deftest test-module-usefixtures [request] (assert (in "auto_seen" request.fixturenames)))
""",
}

PARAM_FILES = {
    "pkg/__init__.py": "",
    # Cartesian product of literals, and siblings with the same parametrize shape (a direct parametrize rewrite must not leak into the next item)
    "pkg/test_product.hy": PRELUDE
    + """
(deftest test-product [left right] {:params {"left" [1 2] "right" [3 4]}} (assert (in (+ left right) [4 5 6])))
(deftest test-product-again [left right] {:params {"left" [5] "right" [6]}} (assert (= (+ left right) 11)))
(deftest test-plain [answer] (assert (= answer 42)))
""",
    # Dynamic values and explicit ids (the records are filled in with real values after import and saved)
    "pkg/test_recorded_params.hy": PRELUDE
    + """
(val VALUES [(pytest.param 21 :id "half") (pytest.param 42 :id "whole")])
(deftest test-recorded-param-values [value] {:params {"value" VALUES}} (assert (in value [21 42])))
""",
    # Values with an individual mark (skip) — the records cannot explain them, so they are imported every time
    "pkg/test_marked_params.hy": PRELUDE
    + """
(val VALUES [(pytest.param 21 :id "half") (pytest.param 42 :id "whole") (pytest.param 0 :id "skip" :marks pytest.mark.skip)])
(deftest test-param-values [value] {:params {"value" VALUES}} (assert (in value [21 42])))
""",
    # Indirect parametrize (a conftest fixture with params), combined with direct parametrize
    "pkg/test_indirect.hy": PRELUDE
    + """
(deftest test-indirect [base] (assert (in base [10 20])))
(deftest test-indirect-again [base] (assert (in base [10 20])))
(deftest test-indirect-and-direct [base value] {:params {"value" [1 2]}} (assert (in (+ base value) [11 12 21 22])))
""",
}

PLUGIN_FILES = {
    "pkg/__init__.py": "",
    "pkg/test_plain.hy": PRELUDE + "(deftest test-plain [answer] (assert (= answer 42)))\n",
    "pkg/custom/__init__.py": "",
    # Unknown plugin: pytest_generate_tests that asks for the type of FunctionDefinition and adds values, and a wrapper of
    # pytest_pycollect_makeitem that attaches information to an item and adds an item to the module. Write the names passed to makeitem into a file (generic collection also passes names that are not functions)
    "pkg/custom/conftest.py": """
import os

import pytest
from _pytest.python import FunctionDefinition


def pytest_generate_tests(metafunc):
    assert isinstance(metafunc.definition, FunctionDefinition)
    if "extra" in metafunc.fixturenames:
        metafunc.parametrize("extra", [3, 5], ids=["three", "five"])


class ExtraItem(pytest.Item):
    def runtest(self):
        pass


@pytest.hookimpl(wrapper=True)
def pytest_pycollect_makeitem(collector, name, obj):
    with open(os.environ["MAKEITEM_LOG"], "a") as log:
        log.write(name + "\\n")
    result = yield
    if isinstance(result, list):
        for item in result:
            if isinstance(item, pytest.Function):
                item.user_properties.append(("plugin_seen", True))
        if name == "test_generated":
            result = [*result, ExtraItem.from_parent(collector, name="extra_item")]
    return result


def pytest_runtest_setup(item):
    if isinstance(item, pytest.Function):
        assert ("plugin_seen", True) in item.user_properties
""",
    "pkg/custom/test_plugin.hy": PRELUDE
    + """
(defn local-answer [] 7)
(val answer ((pytest.fixture :name "answer") local-answer))
(deftest test-generated [extra] (assert (in extra [3 5])))
(deftest test-local [answer] (assert (= answer 7)))
""",
}

# Two deftests with the same name — as in a Python module, the last definition stays at the first position (one item)
DUPLICATE_FILES = {
    "pkg/__init__.py": "",
    "pkg/test_duplicate.hy": PRELUDE
    + """
(deftest test-dup (assert False))
(deftest test-between (assert True))
(deftest test-dup [answer] (assert (= answer 42)))
""",
}


def _write_project(
    pytester: pytest.Pytester,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    files: dict[str, str],
) -> None:
    """Place files, conftest and ini. pyc and records are written to the place under tmp."""
    for name, text in files.items():
        path = pytester.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    pytester.makeconftest(CONFTEST)
    pytester.makeini(
        f"""
        [pytest]
        doeff_adr_hy_files = pkg/test_*.hy
            pkg/sub/test_*.hy
            pkg/custom/test_*.hy
            pkg/registered/test_*.hy
        doeff_adr_wiring = off
        doeff_adr_items_cache = {tmp_path / "items"}
        usefixtures = ini_used
        """
    )
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pyc"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "")
    monkeypatch.setenv("MAKEITEM_LOG", str(tmp_path / "makeitem.log"))


@dataclass(frozen=True)
class Run:
    """Result of one run: the order of collected nodeids, the number of results, and the full report text."""

    nodeids: list[str]
    outcomes: dict[str, int]
    out: str


_VERBOSE_OUTCOMES = frozenset({"PASSED", "FAILED", "SKIPPED", "ERROR", "XFAIL", "XPASS"})


def _run(pytester: pytest.Pytester, *args: str) -> Run:
    """Run in a separate process, and read the nodeids, the results (the line of -v ``<nodeid> <result> [..%]``) and the number of results."""
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-v", *args, timeout=30)
    rows = [line.split() for line in result.stdout.lines]
    nodeids = [
        f"{row[0]} {row[1]}"
        for row in rows
        if len(row) >= 2 and "::" in row[0] and row[1] in _VERBOSE_OUTCOMES
    ]
    return Run(nodeids, result.parseoutcomes(), result.stdout.str())


def _collect(pytester: pytest.Pytester, *args: str) -> list[str]:
    """Read the order of nodeids (including order) of --collect-only -q in a separate process."""
    result = pytester.runpytest_subprocess(
        "--collect-only", "-q", "-p", "no:cacheprovider", *args, timeout=30
    )
    return [line for line in result.stdout.lines if "::" in line and not line.startswith(" ")]


@dataclass(frozen=True)
class ColdAndWarm:
    """The nodeid order (including order) of each collection: the run collected by import (cold) and the run collected from records (warm)."""

    cold: Run
    warm: Run
    cold_ids: list[str]
    warm_ids: list[str]


def _cold_then_warm(pytester: pytest.Pytester) -> ColdAndWarm:
    """Run the first time (no records, so collect by import) and the second time (collect from records), and take the nodeid order of collection
    in both a place without records (import path) and a place with records (shortcut)."""
    cold = _run(pytester)
    warm = _run(pytester)
    cold_ids = _collect(
        pytester, "-o", "doeff_adr_items_cache=" + str(pytester.path / "no-records")
    )
    warm_ids = _collect(pytester)
    return ColdAndWarm(cold, warm, cold_ids, warm_ids)


def test_fixture_resolution_is_not_shared_across_modules_conftests_or_registrations(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Module fixture override (in one file only), lower conftest override, additional registration before collection, autouse, ini and module
    usefixtures, and a usefixtures mark with no arguments — all collected from records, and resolve to the same fixtures as the imported collection."""
    _write_project(pytester, tmp_path, monkeypatch, FILES)
    runs = _cold_then_warm(pytester)
    cold, warm, cold_ids, warm_ids = runs.cold, runs.warm, runs.cold_ids, runs.warm_ids
    assert cold.outcomes.get("passed") == 14, cold.out
    assert warm.outcomes == cold.outcomes, warm.out
    assert warm.nodeids == cold.nodeids
    assert warm_ids == cold_ids
    assert "collected from record: 4 files; imported during collection: 2 files" in warm.out
    assert "generic:" not in warm.out
    # The warning for usefixtures with no arguments is per item (the shortcut also shares it with siblings and does not reduce it to one)
    assert warm.outcomes.get("warnings") == cold.outcomes.get("warnings")


def test_recollection_in_the_same_process_resolves_fixtures_afresh(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run pytest three times in the same process (import the first time, from records the second and third times). Every run gives the same result —
    fixture resolution and registration of a previous run are not carried over to the next run."""
    _write_project(pytester, tmp_path, monkeypatch, FILES)
    script = pytester.makepyfile(
        rerun="""
        import json
        import sys

        import pytest


        class Recorder:
            def __init__(self):
                self.rows = []

            def pytest_runtest_logreport(self, report):
                if report.when == "call" or report.outcome != "passed":
                    self.rows.append([report.nodeid, report.when, report.outcome])


        runs = []
        for _ in range(3):
            recorder = Recorder()
            code = pytest.main(["-p", "no:cacheprovider", "-q", "pkg"], plugins=[recorder])
            runs.append([int(code), sorted(recorder.rows)])
        print("RUNS=" + json.dumps(runs))
        """
    )
    result = pytester.runpython(script)
    line = next(line for line in result.stdout.lines if line.startswith("RUNS="))
    runs = json.loads(line.removeprefix("RUNS="))
    assert [code for code, _ in runs] == [0, 0, 0], result.stdout.str()
    assert runs[1][1] == runs[0][1]
    assert runs[2][1] == runs[0][1]
    assert len(runs[0][1]) == 14


def test_parametrize_ids_counts_values_and_skips_match_the_imported_collection(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that is imported because it has a Cartesian product of literals, dynamic values and explicit ids, indirect parametrize combined with direct, and individual marks — nodeids,
    counts, values (checked inside the tests) and skips are the same as in the imported collection."""
    _write_project(pytester, tmp_path, monkeypatch, PARAM_FILES)
    runs = _cold_then_warm(pytester)
    cold, warm, cold_ids, warm_ids = runs.cold, runs.warm, runs.cold_ids, runs.warm_ids
    assert cold.outcomes == {"passed": 18, "skipped": 1}, cold.out
    assert warm.outcomes == cold.outcomes, warm.out
    assert warm.nodeids == cold.nodeids
    assert warm_ids == cold_ids
    assert "pkg/test_product.hy::test_product[3-1]" in warm_ids
    assert "pkg/test_recorded_params.hy::test_recorded_param_values[half]" in warm_ids
    assert "pkg/test_marked_params.hy::test_param_values[skip] SKIPPED" in warm.nodeids
    assert sum("test_indirect_and_direct[" in nodeid for nodeid in warm_ids) == 4
    assert "collected from record: 3 files; imported during collection: 1 files" in warm.out
    assert "import: pkg/test_marked_params.hy" in warm.out


def test_unknown_collection_plugins_import_the_real_module_before_collection(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files in a dir that has ``pytest_generate_tests`` which asks for the type of FunctionDefinition and adds values, and a wrapper of ``pytest_pycollect_makeitem`` which
    attaches information to an item and adds an item to the module, import the real module and go to pytest's normal collection — makeitem is
    also called for names that are not functions (names of module fixtures), and the added items are not missing. Files outside it stay on the shortcut."""
    _write_project(pytester, tmp_path, monkeypatch, PLUGIN_FILES)
    runs = _cold_then_warm(pytester)
    cold, warm, cold_ids, warm_ids = runs.cold, runs.warm, runs.cold_ids, runs.warm_ids
    assert cold.outcomes == {"passed": 5}, cold.out
    assert warm.outcomes == cold.outcomes, warm.out
    assert warm.nodeids == cold.nodeids
    assert warm_ids == cold_ids
    assert "pkg/custom/test_plugin.hy::extra_item" in warm_ids
    assert "pkg/custom/test_plugin.hy::test_generated[three]" in warm_ids
    assert "collected from record: 1 files; imported during collection: 1 files" in warm.out
    assert "unknown collection hook" in warm.out
    assert "import: pkg/test_plain.hy" not in warm.out
    log = tmp_path / "makeitem.log"
    log.write_text("")
    _collect(pytester)
    assert "answer" in log.read_text().split()


def test_a_name_recorded_twice_becomes_one_item_at_its_first_position_with_its_last_body(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file with two deftests of the same name has only one item of the last definition at the first position, as in a Python module
    (the first prototype collected the records twice and had one item too many)."""
    _write_project(pytester, tmp_path, monkeypatch, DUPLICATE_FILES)
    runs = _cold_then_warm(pytester)
    cold, warm, cold_ids, warm_ids = runs.cold, runs.warm, runs.cold_ids, runs.warm_ids
    assert cold.outcomes == {"passed": 2}, cold.out
    assert warm.outcomes == cold.outcomes, warm.out
    assert (
        warm_ids
        == cold_ids
        == ["pkg/test_duplicate.hy::test_dup", "pkg/test_duplicate.hy::test_between"]
    )
    assert "collected from record: 1 files" in warm.out


def test_fixture_alias_keeps_the_attribute_used_by_pytest(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {
        "pkg/__init__.py": "",
        "pkg/test_alias.hy": PRELUDE
        + """
(defn original [] 73)
(val exposed (pytest.fixture original))
(deftest test-alias [exposed] (assert (= exposed 73)))
(deftest test-alias-again [exposed] (assert (= exposed 73)))
""",
    }
    _write_project(pytester, tmp_path, monkeypatch, files)
    runs = _cold_then_warm(pytester)
    assert runs.cold.outcomes == {"passed": 2}, runs.cold.out
    assert runs.warm.outcomes == runs.cold.outcomes, runs.warm.out
    assert runs.warm_ids == runs.cold_ids
    assert "collected from record: 1 files; imported during collection: 0 files" in runs.warm.out


def test_item_specific_fixture_registration_does_not_reuse_the_sibling_closure(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {
        "pkg/__init__.py": "",
        "pkg/test_first.hy": PRELUDE + "(deftest test-value [answer] (assert (= answer 42)))\n",
        "pkg/test_second.hy": PRELUDE + "(deftest test-value [answer] (assert (= answer 99)))\n",
    }
    _write_project(pytester, tmp_path, monkeypatch, files)
    conftest = pytester.path / "conftest.py"
    conftest.write_text(
        conftest.read_text()
        + """
class Registered:
    @pytest.fixture(name="answer")
    def answer_for_second(self):
        return 99

@pytest.hookimpl(wrapper=True)
def pytest_collect_file(file_path, parent):
    collectors = yield
    for collector in collectors:
        if isinstance(collector, pytest.Module) and collector.path.name == "test_second.hy":
            collector.session._fixturemanager.parsefactories(holder=Registered(), node=collector)
    return collectors
"""
    )
    runs = _cold_then_warm(pytester)
    assert runs.cold.outcomes == {"passed": 2}, runs.cold.out
    assert runs.warm.outcomes == runs.cold.outcomes, runs.warm.out
    assert runs.warm_ids == runs.cold_ids
    assert "collected from record: 2 files; imported during collection: 0 files" in runs.warm.out
