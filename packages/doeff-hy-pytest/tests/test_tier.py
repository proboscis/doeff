"""Tests for the test tiers (doeff_hy_pytest/_tier.py) and deftest's ``:tier``.

Every test gets one tier (small, medium, large): its own tier mark, else the tier its marks are mapped to by
``doeff_tier_by_marker``, else ``doeff_tier_default``. ``--doeff-tier`` keeps only the named tiers. A small test whose call
phase starts a child process, uses a network socket or runs over ``doeff_tier_small_seconds`` is listed at the end (report only).
"""

from __future__ import annotations

import sys

import pytest
from doeff_hy_pytest._tier import (
    SettingError,
    _AuditWatcher,
    finding_reasons,
    parse_tier_by_marker,
    parse_tier_selection,
    tier_of,
)

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def _own_stores(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each inner run compiles its .hy into an empty store (as in test_budget.py), so the runner's shared stores do not leak in."""
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", str(tmp_path_factory.mktemp("code-store")))
    monkeypatch.delenv("DOEFF_ADR_ITEMS_CACHE", raising=False)


CONFTEST = """\
import pytest


@pytest.fixture
def doeff_interpreter():
    def run_program(program, *, env=None):
        from doeff import run

        return run(program)

    return run_program
"""

TIERED = """\
(require doeff-hy.macros [deftest])
(deftest test-plain (assert True))
(deftest test-declared-medium {:tier "medium"} (assert True))
(deftest test-declared-large {:tier "large"} (assert True))
(deftest test-real-world {:marks ["real_world"]} (assert True))
(deftest test-e2e {:marks ["e2e"]} (assert True))
(deftest test-real-world-declared-small {:marks ["real_world"] :tier "small"} (assert True))
"""

PYTHON_TIERED = """\
import pytest


def test_python_plain():
    pass


@pytest.mark.tier_large
def test_python_large():
    pass
"""

MAPPING_INI = (
    'markers = ["real_world: real I/O", "e2e: the outside world"]\n'
    'doeff_tier_by_marker = ["real_world=medium", "e2e=large"]\n'
)


def _project(pytester: pytest.Pytester, ini: str, hy_files: dict[str, str], py_files: dict[str, str] | None = None) -> None:
    pytester.makeconftest(CONFTEST)
    pytester.makepyprojecttoml('[tool.pytest.ini_options]\ndoeff_adr_hy_files = ["test_*.hy"]\n' + ini)
    pytester.makefile(".hy", **hy_files)
    if py_files:
        pytester.makepyfile(**py_files)


def _passed(recorder: pytest.HookRecorder) -> set[str]:
    """The function names of the tests that passed in an inline run."""
    return {report.nodeid.split("::")[-1] for report in recorder.getreports("pytest_runtest_logreport") if report.when == "call" and report.passed}


def _findings(result: pytest.RunResult) -> dict[str, str]:
    """The listed small tests of the "doeff test tiers" section: nodeid -> reasons (other sections, such as warnings, are not read)."""
    lines = result.stdout.lines
    starts = [i for i, line in enumerate(lines) if "doeff test tiers" in line]
    if not starts:
        return {}
    found: dict[str, str] = {}
    for line in lines[starts[0] + 1 :]:
        if line.startswith("="):
            break
        if line.startswith("  ") and ": " in line:
            nodeid, _, reasons = line.strip().partition(": ")
            found[nodeid] = reasons
    return found


def test_a_tier_mark_wins_then_the_marker_mapping_then_the_default() -> None:
    """Own tier mark (the largest of several) > the largest mapped tier of the marks > the default."""
    mapping = {"real_world": "medium", "e2e": "large"}
    assert tier_of([], mapping, "small") == "small"
    assert tier_of(["real_world"], mapping, "small") == "medium"
    assert tier_of(["real_world", "e2e"], mapping, "small") == "large"
    assert tier_of(["e2e", "tier_small"], mapping, "small") == "small"
    assert tier_of(["tier_small", "tier_medium"], mapping, "small") == "medium"
    assert tier_of(["slow"], mapping, "medium") == "medium"


def test_unreadable_settings_are_errors_not_defaults() -> None:
    assert parse_tier_by_marker(["real_world=medium", "", "e2e = large"]) == {"real_world": "medium", "e2e": "large"}
    assert isinstance(parse_tier_by_marker(["real_world=huge"]), SettingError)
    assert isinstance(parse_tier_by_marker(["real_world"]), SettingError)
    assert isinstance(parse_tier_by_marker(["e2e=large", "e2e=medium"]), SettingError)
    assert parse_tier_selection("small, medium") == frozenset({"small", "medium"})
    assert isinstance(parse_tier_selection("small,tiny"), SettingError)


def test_finding_reasons_name_time_child_processes_and_sockets() -> None:
    assert finding_reasons({}, 1.0, 60.0) == ()
    (slow,) = finding_reasons({}, 61.0, 60.0)
    assert "61.0 s" in slow
    reasons = finding_reasons({"subprocess.Popen": "'git'", "socket.connect": "<socket>"}, 1.0, 60.0)
    assert reasons == ("started a child process (subprocess.Popen 'git')", "used a network socket (socket.connect <socket>)")


def test_each_tier_option_runs_only_its_tests(pytester: pytest.Pytester) -> None:
    """With the marker mapping, existing marks decide the tier of a test without :tier, and --doeff-tier picks one tier."""
    _project(pytester, MAPPING_INI, {"test_tiered": TIERED}, {"test_python_tiered": PYTHON_TIERED})
    expected = {
        "small": {"test_plain", "test_real_world_declared_small", "test_python_plain"},
        "medium": {"test_declared_medium", "test_real_world"},
        "large": {"test_declared_large", "test_e2e", "test_python_large"},
    }
    for tier, names in expected.items():
        result = pytester.inline_run("-q", "--doeff-tier", tier)
        assert _passed(result) == names, tier
    result = pytester.runpytest("-q", "--doeff-tier", "small,large")
    result.assert_outcomes(passed=6, deselected=2)
    result.stdout.fnmatch_lines(
        ["*doeff test tiers*", "selected tiers: small,large; tests per tier (selected / collected): small 3 / 3, medium 0 / 2, large 3 / 3"]
    )


def test_without_the_option_every_tier_runs_and_nothing_is_printed(pytester: pytest.Pytester) -> None:
    _project(pytester, MAPPING_INI, {"test_tiered": TIERED})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=6)
    assert "doeff test tiers" not in result.stdout.str()


def test_without_the_mapping_only_tier_marks_count(pytester: pytest.Pytester) -> None:
    """A repo with no setting: a test without :tier is small, whatever its marks."""
    _project(pytester, 'markers = ["real_world: real I/O", "e2e: the outside world"]\n', {"test_tiered": TIERED})
    result = pytester.inline_run("-q", "--doeff-tier", "small")
    assert _passed(result) == {"test_plain", "test_real_world", "test_e2e", "test_real_world_declared_small"}


def test_tier_marks_are_registered(pytester: pytest.Pytester) -> None:
    """--strict-markers accepts the tier marks (the plugin registers them)."""
    _project(pytester, "", {"test_tiered": '(require doeff-hy.macros [deftest])\n(deftest test-m {:tier "medium"} (assert True))\n'})
    pytester.runpytest("-q", "--strict-markers").assert_outcomes(passed=1)


def test_an_unknown_tier_name_stops_the_run(pytester: pytest.Pytester) -> None:
    _project(pytester, "", {"test_tiered": TIERED})
    result = pytester.runpytest("-q", "--doeff-tier", "tiny")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--doeff-tier*one of small, medium, large*"])


ACTS_BIG = """\
(require doeff-hy.macros [deftest])
(import subprocess socket sys)
(deftest test-spawns (subprocess.run [sys.executable "-c" "pass"] :check True))
(deftest test-connects
  (setv server (socket.socket))
  (.bind server #("127.0.0.1" 0))
  (.listen server 1)
  (setv client (socket.create-connection (.getsockname server)))
  (.close client)
  (.close server))
(deftest test-quiet (assert True))
(deftest test-medium-spawns {:tier "medium"} (subprocess.run [sys.executable "-c" "pass"] :check True))
"""


def test_small_tests_that_spawn_or_use_sockets_are_listed_but_stay_green(pytester: pytest.Pytester) -> None:
    """The listing names the small tests that started a child process or used a network socket; a medium test is not watched."""
    _project(pytester, "", {"test_acts_big": ACTS_BIG})
    result = pytester.runpytest("-q")
    output = result.stdout.str()
    result.assert_outcomes(passed=4)
    found = _findings(result)
    assert set(found) == {"test_acts_big.hy::test_spawns", "test_acts_big.hy::test_connects"}, output
    assert "started a child process" in found["test_acts_big.hy::test_spawns"]
    assert "subprocess.Popen" in found["test_acts_big.hy::test_spawns"]
    assert "used a network socket" in found["test_acts_big.hy::test_connects"]
    assert "socket.connect" in found["test_acts_big.hy::test_connects"]


def test_a_small_test_over_the_seconds_is_listed(pytester: pytest.Pytester) -> None:
    # Burn CPU for 0.3 s (as test_budget.py does) against a limit of 0.1 s -- a margin so machine jitter does not flip the result.
    slow = (
        "(require doeff-hy.macros [deftest])\n(import time)\n"
        "(deftest test-spins (setv t0 (time.process_time)) (while (< (- (time.process_time) t0) 0.3) (setv _ None)))\n"
    )
    _project(pytester, 'doeff_tier_small_seconds = "0.1"\n', {"test_slow": slow})
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)
    found = _findings(result)
    assert list(found) == ["test_slow.hy::test_spins"], result.stdout.str()
    assert found["test_slow.hy::test_spins"].endswith("s (small is up to 0.1 s)")


def test_nested_equal_watches_keep_the_outer_watch_active() -> None:
    """Closing an empty inner watch must leave the outer watch to receive later events."""
    watcher = _AuditWatcher()
    outer = watcher.open()
    inner = watcher.open()
    watcher.close(inner)
    sys.audit("socket.connect", "after-inner")
    watcher.close(outer)

    assert inner.seen == {}
    assert outer.seen == {"socket.connect": "'after-inner'"}
    sys.audit("socket.bind", "after-outer")
    assert outer.seen == {"socket.connect": "'after-inner'"}
