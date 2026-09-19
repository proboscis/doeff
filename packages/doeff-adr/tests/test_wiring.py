"""Regression tests for canonical-gate collection wiring (executable ADRs and Python tests)."""

import subprocess
import sys
from collections.abc import Sequence

import pytest

pytest_plugins = ["pytester"]


def _make_executable_adr(pytester: pytest.Pytester, adr_id: str) -> None:
    pytester.mkdir("docs")
    pytester.mkdir("docs/adr")
    pytester.makefile(
        ".hy",
        **{
            f"docs/adr/defadr_{adr_id.lower().replace('-', '_')}": f"""\
                (require doeff-adr.macros [defadr])

                (defadr ADR-{adr_id}
                  :title "wiring fixture"
                  :status "proposed")
                """,
        },
    )


def _make_smoke_test(pytester: pytest.Pytester) -> None:
    pytester.mkdir("tests")
    pytester.makefile(".py", **{"tests/test_smoke": "def test_smoke():\n    assert True\n"})


def _combined_output(result: pytest.RunResult) -> str:
    return f"{result.stdout.str()}\n{result.stderr.str()}"


def test_strict_wiring_fails_when_defadr_is_outside_collection_scope(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests"]
        """
    )
    _make_smoke_test(pytester)
    _make_executable_adr(pytester, "WIRING-RED")

    result: pytest.RunResult = pytester.runpytest("-q", "--doeff-adr-wiring=strict")

    assert result.ret != pytest.ExitCode.OK
    output: str = _combined_output(result)
    assert "doeff-adr wiring verification failed" in output
    assert "docs/adr/defadr_wiring_red.hy" in output


def test_strict_wiring_passes_when_all_defadrs_are_collected(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests", "docs/adr"]
        """
    )
    _make_smoke_test(pytester)
    _make_executable_adr(pytester, "WIRING-GREEN")

    result: pytest.RunResult = pytester.runpytest("-q", "--doeff-adr-wiring=strict")

    result.assert_outcomes(passed=2)


def test_default_wiring_mode_warns_without_failing(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests"]
        """
    )
    _make_smoke_test(pytester)
    _make_executable_adr(pytester, "WIRING-WARN")

    result: pytest.RunResult = pytester.runpytest("-q")

    result.assert_outcomes(passed=1, warnings=1)
    output: str = _combined_output(result)
    assert "doeff-adr wiring verification warning" in output
    assert "docs/adr/defadr_wiring_warn.hy" in output


def test_wiring_discovery_skips_norecursedirs_matched_directories(
    pytester: pytest.Pytester,
) -> None:
    # pytest's default norecursedirs includes ".*" — a defadr copy inside a
    # hidden directory (e.g. .claude/worktrees checkout copies) can never be
    # collected, so wiring verification must not report it as mis-wired.
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["docs/adr"]
        """
    )
    _make_executable_adr(pytester, "WIRING-TREE")
    for part in (".claude", ".claude/worktrees", ".claude/worktrees/wt",
                 ".claude/worktrees/wt/docs", ".claude/worktrees/wt/docs/adr"):
        pytester.mkdir(part)
    pytester.makefile(
        ".hy",
        **{
            ".claude/worktrees/wt/docs/adr/defadr_wiring_copy": """\
                (require doeff-adr.macros [defadr])

                (defadr ADR-WIRING-COPY
                  :title "hidden worktree copy"
                  :status "proposed")
                """,
        },
    )

    result: pytest.RunResult = pytester.runpytest("-q", "--doeff-adr-wiring=strict")

    result.assert_outcomes(passed=1)
    assert "defadr_wiring_copy" not in _combined_output(result)


def _make_orphan_python_test(pytester: pytest.Pytester, tree: str) -> None:
    """A Python test file in a tree of its own — the shape of an unwired package."""
    parts: list[str] = []
    for part in tree.split("/"):
        parts.append(part)
        pytester.mkdir("/".join(parts))
    pytester.makefile(".py", **{f"{tree}/test_orphan": "def test_orphan():\n    assert True\n"})


def test_strict_wiring_fails_when_python_test_file_is_outside_collection_scope(
    pytester: pytest.Pytester,
) -> None:
    # The second file kind. A test tree nobody added to testpaths is silent in
    # exactly the way an unwired defadr is, and the failure mode is worse: the
    # tests look written, so nobody re-writes them (doeff 2026-09-19: 19 package
    # trees, 3,200 tests, five of them rotted against APIs deleted months
    # earlier without a single red).
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests"]
        """
    )
    _make_smoke_test(pytester)
    _make_orphan_python_test(pytester, "packages/pkg/tests")

    result: pytest.RunResult = pytester.runpytest("-q", "--doeff-adr-wiring=strict")

    assert result.ret != pytest.ExitCode.OK
    output: str = _combined_output(result)
    assert "doeff-adr wiring verification failed" in output
    assert "packages/pkg/tests/test_orphan.py" in output


def test_strict_wiring_passes_when_python_test_tree_is_in_testpaths(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests", "packages/pkg/tests"]
        """
    )
    _make_smoke_test(pytester)
    _make_orphan_python_test(pytester, "packages/pkg/tests")

    result: pytest.RunResult = pytester.runpytest("-q", "--doeff-adr-wiring=strict")

    result.assert_outcomes(passed=2)


def test_wiring_reads_pytests_own_python_files_patterns(
    pytester: pytest.Pytester,
) -> None:
    # The gate must not hold a second opinion about which .py files are tests:
    # a project that renamed the pattern would otherwise get both false reds
    # (test_*.py that pytest never collects) and false greens (its real tests).
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests"]
        python_files = ["check_*.py"]
        """
    )
    pytester.mkdir("tests")
    pytester.makefile(".py", **{"tests/check_smoke": "def test_smoke():\n    assert True\n"})
    pytester.mkdir("helpers")
    # Matches the project's pattern but sits outside testpaths: uncollected.
    pytester.makefile(".py", **{"helpers/check_orphan": "def test_orphan():\n    assert True\n"})
    # Matches pytest's *default* pattern but not this project's: not a test file
    # here at all, so naming it would be a false red.
    pytester.makefile(".py", **{"helpers/test_not_a_test": "def test_never():\n    assert True\n"})

    result: pytest.RunResult = pytester.runpytest("-q", "--doeff-adr-wiring=strict")

    assert result.ret != pytest.ExitCode.OK
    output: str = _combined_output(result)
    assert "helpers/check_orphan.py" in output
    assert "test_not_a_test.py" not in output


def test_wiring_exclude_declares_files_that_must_never_be_collected(
    pytester: pytest.Pytester,
) -> None:
    # Some test_*.py are inputs, not tests: sample sources another tool's Rust
    # test suite feeds through the linter, example scripts that execute at
    # import. They can never be collected, so the gate needs a declared way to
    # say "dark on purpose" — otherwise the only way to silence it is to turn it
    # off entirely.
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests"]
        doeff_adr_wiring_exclude = ["packages/lint/tests/fixtures/*"]
        """
    )
    _make_smoke_test(pytester)
    _make_orphan_python_test(pytester, "packages/lint/tests/fixtures")

    result: pytest.RunResult = pytester.runpytest("-q", "--doeff-adr-wiring=strict")

    result.assert_outcomes(passed=1)
    assert "test_orphan.py" not in _combined_output(result)


def test_default_scope_wiring_reports_python_test_file_outside_testpaths(
    pytester: pytest.Pytester,
) -> None:
    # The in-session mouth answers for the second file kind too, so doeff's own
    # gate test needs no second collection to see an unwired package tree.
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests"]
        """
    )
    pytester.mkdir("tests")
    _make_orphan_python_test(pytester, "packages/pkg/tests")
    _make_gate_test(
        pytester,
        "assert isinstance(verdict, WiringUncollected), verdict",
        "assert [path.name for path in verdict.uncollected] == ['test_orphan.py']",
    )

    result: pytest.RunResult = pytester.runpytest("-q")

    result.assert_outcomes(passed=1, warnings=1)


def _make_deep_directories(pytester: pytest.Pytester, count: int) -> None:
    path = ""
    for index in range(count):
        path = f"{path}/deep{index}" if path else f"deep{index}"
        pytester.mkdir(path)


def test_wiring_walk_budget_exceeded_warns_loud_in_warn_mode(
    pytester: pytest.Pytester,
) -> None:
    # The wiring walk covers the whole rootdir. When rootdir resolves to a huge
    # tree (observed 2026-09-02: no ini file anchored a docs/adr suite, rootdir
    # became $HOME, and every pytest run silently crawled the home directory for
    # 60+ seconds — minutes under load), the walk must abort loudly instead of
    # hanging the run without output.
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests"]
        doeff_adr_wiring_max_dirs = "3"
        """
    )
    _make_smoke_test(pytester)
    _make_deep_directories(pytester, 8)

    result: pytest.RunResult = pytester.runpytest("-q")

    result.assert_outcomes(passed=1, warnings=1)
    output: str = _combined_output(result)
    assert "doeff-adr wiring verification warning" in output
    assert "aborted after walking" in output
    assert "doeff_adr_wiring_max_dirs" in output


def test_wiring_walk_budget_exceeded_fails_strict_mode(
    pytester: pytest.Pytester,
) -> None:
    # strict mode promised a verification; an aborted walk cannot verify, so it
    # must fail closed rather than pass silently.
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests"]
        doeff_adr_wiring_max_dirs = "3"
        """
    )
    _make_smoke_test(pytester)
    _make_deep_directories(pytester, 8)

    result: pytest.RunResult = pytester.runpytest("-q", "--doeff-adr-wiring=strict")

    assert result.ret != pytest.ExitCode.OK
    output: str = _combined_output(result)
    assert "doeff-adr wiring verification failed" in output
    assert "aborted after walking" in output


def test_wiring_walk_budget_rejects_non_positive_or_garbage_values(
    pytester: pytest.Pytester,
) -> None:
    # An unparsable or non-positive budget silently becoming "unlimited" would
    # reopen the unbounded-walk hole; the vocabulary is a positive integer only
    # (the intentional opt-out spelling stays doeff_adr_wiring=off).
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests"]
        doeff_adr_wiring_max_dirs = "unbounded"
        """
    )
    _make_smoke_test(pytester)

    result: pytest.RunResult = pytester.runpytest("-q")

    assert result.ret != pytest.ExitCode.OK
    assert "doeff_adr_wiring_max_dirs" in _combined_output(result)


_GATE_TEST_HEAD = """\
from doeff_adr.pytest_plugin import (
    NotDefaultScope,
    WiringUncollected,
    WiringVerified,
    default_scope_wiring,
)


def test_gate(request):
    verdict = default_scope_wiring(request.session)
"""


def _make_gate_test(pytester: pytest.Pytester, *expectations: str) -> None:
    # An in-session gate test: it reads the running session's own collection
    # through the plugin's mouth instead of spawning a second collection.
    body: str = "".join(f"    {line}\n" for line in expectations)
    pytester.makefile(".py", **{"tests/test_gate": _GATE_TEST_HEAD + body})


def test_default_scope_wiring_reports_adr_outside_testpaths(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests"]
        """
    )
    pytester.mkdir("tests")
    _make_executable_adr(pytester, "GATE-RED")
    _make_gate_test(
        pytester,
        "assert isinstance(verdict, WiringUncollected), verdict",
        "assert [path.name for path in verdict.uncollected] == ['defadr_gate_red.hy']",
    )

    result: pytest.RunResult = pytester.runpytest("-q")

    result.assert_outcomes(passed=1, warnings=1)


def test_default_scope_wiring_verifies_from_the_sessions_own_collection(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests", "docs/adr"]
        """
    )
    pytester.mkdir("tests")
    _make_executable_adr(pytester, "GATE-GREEN")
    _make_gate_test(
        pytester,
        "assert isinstance(verdict, WiringVerified), verdict",
        "assert [path.name for path in verdict.wired_files] == ['defadr_gate_green.hy']",
    )

    result: pytest.RunResult = pytester.runpytest("-q")

    result.assert_outcomes(passed=2)


def test_default_scope_wiring_refuses_to_speak_for_explicit_paths(
    pytester: pytest.Pytester,
) -> None:
    # The explicit paths reach every ADR while testpaths does not: reading this
    # session's collection as the default scope's would be a false green.
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests"]
        """
    )
    pytester.mkdir("tests")
    _make_executable_adr(pytester, "GATE-EXPLICIT")
    _make_gate_test(
        pytester,
        "assert verdict == NotDefaultScope(('tests', 'docs/adr')), verdict",
    )

    result: pytest.RunResult = pytester.runpytest("-q", "tests", "docs/adr")

    result.assert_outcomes(passed=2)


def test_wiring_measures_the_collection_scope_not_the_selection(
    pytester: pytest.Pytester,
) -> None:
    # The canonical doeff gate runs with -m 'not e2e': deselection happens after
    # collection, and an ADR it drops was still reached by the scope — neither
    # the strict report nor the in-session gate may call it mis-wired.
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        testpaths = ["tests", "docs/adr"]
        """
    )
    pytester.mkdir("tests")
    _make_executable_adr(pytester, "GATE-DESELECTED")
    _make_gate_test(pytester, "assert isinstance(verdict, WiringVerified), verdict")

    result: pytest.RunResult = pytester.runpytest(
        "-q", "-k", "test_gate", "--doeff-adr-wiring=strict"
    )

    result.assert_outcomes(passed=1, deselected=1)


def test_verify_wiring_cli_runs_strict_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import doeff_adr.cli

    commands: list[Sequence[str]] = []

    def run_command(
        command: Sequence[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert not check
        assert capture_output
        assert text
        commands.append(command)
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(doeff_adr.cli.subprocess, "run", run_command)

    exit_code: int = doeff_adr.cli.main(["verify-wiring", "docs/adr"])

    assert exit_code == 0
    assert commands == [
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "docs/adr",
            "--doeff-adr-wiring=strict",
        ]
    ]
