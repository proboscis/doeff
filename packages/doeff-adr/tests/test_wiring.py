"""Regression tests for executable ADR collection wiring."""

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
