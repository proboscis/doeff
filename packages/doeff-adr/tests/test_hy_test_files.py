"""One point of the Hy test-file collector (ini option doeff_hy_test_files).

In a project with no conftest.py, a .hy that defines tests is collected as one item per function, a .hy that defines none (a "script" run top to bottom)
is collected as a single item per file, and a script exception turns red. Files in the skip list (doeff_hy_test_skips)
are not imported and become a skip with a reason. In a repo with no ini (the downstream default), nothing is collected.

pytest runs in a separate process (pytester subprocess). The record cache and pyc are written under tmp.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

_LOG = """(import os)
(with [log (open (get os.environ "RUN_LOG") "a")] (.write log (+ __name__ "\\n")))
"""

FILES = {
    "tests/test_functions.hy": "(defn test-one [] (assert True))\n(defn test-two [] (assert True))\n",
    "tests/test_script_ok.hy": _LOG + '(print "script ran")\n(assert (= (+ 1 1) 2))\n',
    "tests/test_skipped.hy": '(raise (RuntimeError "skipped, so this should not run"))\n',
    "tests/helper.hy": '(raise (RuntimeError "files not starting with test_ are not collected"))\n',
}
SKIP_REASON = "handed to the owner -- example reason"


@dataclass(frozen=True)
class Run:
    """One pytest run: exit code, the full output (stdout and stderr), and the names of script modules executed in that run."""

    ret: int
    out: str
    ran: list[str]


def _project(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ini: str
) -> None:
    """Place the tree of FILES and the ini, and point the cache, pyc and execution-record locations under tmp."""
    for name, text in FILES.items():
        path = pytester.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    pytester.makeini(
        f"[pytest]\ndoeff_adr_wiring = off\ndoeff_adr_items_cache = {tmp_path / 'items'}\n" + ini
    )
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pyc"))
    monkeypatch.setenv("RUN_LOG", str(tmp_path / "run.log"))


def _run(pytester: pytest.Pytester, tmp_path: Path, *args: str) -> Run:
    """Run pytest in a separate process, read the names of scripts executed in that run, and empty the record."""
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-rs", *args, timeout=120)
    log = tmp_path / "run.log"
    ran = log.read_text(encoding="utf-8").split() if log.exists() else []
    log.write_text("", encoding="utf-8")
    return Run(result.ret, result.stdout.str() + "\n" + result.stderr.str(), ran)


_DECLARED = (
    "doeff_hy_test_files = test_*.hy\n"
    f"doeff_hy_test_skips =\n    tests/test_skipped.hy | {SKIP_REASON}\n"
)


def test_one_collector_takes_test_modules_and_scripts_without_any_conftest(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test functions are per item, a script is one per file, and a skipped file is not imported but skipped with a reason."""
    _project(pytester, tmp_path, monkeypatch, _DECLARED)

    cold = _run(pytester, tmp_path, "-v")
    assert "tests/test_functions.hy::test_one PASSED" in cold.out, cold.out
    assert "tests/test_functions.hy::test_two PASSED" in cold.out, cold.out
    assert "tests/test_script_ok.hy::script PASSED" in cold.out, cold.out
    assert SKIP_REASON in cold.out, cold.out
    assert "skipped, so this should not run" not in cold.out, cold.out
    assert "helper.hy" not in cold.out, cold.out
    assert cold.ret == 0, cold.out
    assert cold.ran == ["tests.test_script_ok"], cold.ran

    # The 2nd run collects from the record -- collection does not run the script; it runs exactly once, in the script item's execution.
    collected = _run(pytester, tmp_path, "--collect-only", "-q")
    assert "tests/test_script_ok.hy::script" in collected.out, collected.out
    assert collected.ran == [], f"collected from the record but the script ran during collection: {collected.ran}"
    warm = _run(pytester, tmp_path, "-v", "tests/test_script_ok.hy")
    assert "tests/test_script_ok.hy::script PASSED" in warm.out, warm.out
    assert warm.ran == ["tests.test_script_ok"], warm.ran


def test_a_script_that_raises_is_red(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A script exception is red -- the 1st run has no record so it runs during collection, making the file's collection red."""
    _project(pytester, tmp_path, monkeypatch, _DECLARED)
    (pytester.path / "tests" / "test_script_red.hy").write_text(
        '(raise (RuntimeError "script is red"))\n', encoding="utf-8"
    )
    red = _run(pytester, tmp_path, "tests/test_script_red.hy")
    assert red.ret != 0, red.out
    assert "ERROR collecting tests/test_script_red.hy" in red.out, red.out
    assert "script is red" in red.out, red.out


def test_a_script_that_fails_after_the_first_run_is_red_on_the_item(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A script collected from the record also turns its item red if execution raises (a green record is not carried over)."""
    _project(pytester, tmp_path, monkeypatch, _DECLARED)
    assert _run(pytester, tmp_path, "tests/test_script_ok.hy").ret == 0
    monkeypatch.setenv("RUN_LOG", str(tmp_path / "no-such-dir" / "run.log"))
    red = _run(pytester, tmp_path, "-v", "tests/test_script_ok.hy")
    assert red.ret != 0, red.out
    assert "FileNotFoundError" in red.out, red.out


def test_a_repo_without_the_ini_collects_no_hy_test_files(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repo without doeff_hy_test_files (the downstream default) does not collect test_*.hy -- no double collection with its own conftest."""
    _project(pytester, tmp_path, monkeypatch, "")
    result = _run(pytester, tmp_path, "--collect-only", "-q")
    assert ".hy" not in result.out, result.out


def test_a_skip_row_without_a_reason_stops_the_run(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exclusion without a reason is stopped as a configuration error (never skipped silently)."""
    _project(
        pytester,
        tmp_path,
        monkeypatch,
        "doeff_hy_test_files = test_*.hy\ndoeff_hy_test_skips =\n    tests/test_skipped.hy\n",
    )
    result = _run(pytester, tmp_path, "--collect-only", "-q")
    assert result.ret != 0, result.out
    assert "doeff_hy_test_skips" in result.out, result.out


def test_a_listed_adr_file_is_skipped_with_its_reason_and_an_unlisted_one_is_collected(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The skip list also applies to the ADR collector (files matching doeff_adr_hy_files) -- one list applies to every collection (agora-redesign 2026-10-07:
    even when two ADR volumes of agora-controllers were put in the list, the ADR collector did not read the list and stayed red in the daily run). Volumes not listed are still collected as before."""
    _project(
        pytester,
        tmp_path,
        monkeypatch,
        "doeff_adr_hy_files = adr/defadr_*.hy\n"
        f"doeff_hy_test_skips =\n    adr/defadr_paused.hy | {SKIP_REASON}\n",
    )
    adr = pytester.path / "adr"
    adr.mkdir()
    (adr / "defadr_paused.hy").write_text('(raise (RuntimeError "skipped, so this should not run"))\n', encoding="utf-8")
    (adr / "defadr_live.hy").write_text("(defn test-live [] (assert True))\n", encoding="utf-8")

    run = _run(pytester, tmp_path, "-v")
    assert SKIP_REASON in run.out, run.out
    assert "skipped, so this should not run" not in run.out, run.out
    assert "adr/defadr_live.hy::test_live PASSED" in run.out, run.out
    assert run.ret == 0, run.out
