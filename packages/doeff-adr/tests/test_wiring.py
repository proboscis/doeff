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
        "assert [path.name for path in verdict.executable_adrs] == ['defadr_gate_green.hy']",
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


def test_hy_file_under_a_workspace_package_dir_imports_from_its_package_base(
    pytester: pytest.Pytester,
) -> None:
    """``packages/doeff-x/tests/test_y.hy`` cannot be named from the rootdir (``doeff-x``
    is not an identifier). It is imported the way pytest's ``prepend`` mode imports a
    Python test: from the first ancestor that is not a package, so ``tests/__init__.py``
    makes it ``tests.test_y`` and its sibling module ``tests.helper`` importable."""
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        doeff_adr_hy_files = ["packages/*/tests/test_*.hy"]
        """
    )
    pytester.mkdir("packages")
    pytester.mkdir("packages/doeff-x")
    pytester.mkdir("packages/doeff-x/tests")
    pytester.makefile(".py", **{"packages/doeff-x/tests/__init__": ""})
    pytester.makefile(".hy", **{"packages/doeff-x/tests/helper": "(setv VALUE 42)\n"})
    pytester.makefile(
        ".hy",
        **{
            "packages/doeff-x/tests/test_y": """\
                (import tests.helper [VALUE])
                (defn test-sibling-import [] (assert (= VALUE 42)))
                (defn test-module-name [] (assert (= __name__ "tests.test_y")))
                """,
        },
    )

    result: pytest.RunResult = pytester.runpytest("-q", "packages/doeff-x/tests")

    result.assert_outcomes(passed=2)


def test_parametrize_marks_on_hy_tests_expand_into_items(
    pytester: pytest.Pytester,
) -> None:
    """deftest の ``:interpreters`` / ``:params`` は ``pytest.mark.parametrize`` を付ける。Hy の file の収集がそれを
    展開せずに関数を 1 つだけ作ると、parametrize の fixture は既定の値のまま 1 本だけ走る(ADR-DOE-HY-002 R2 の違反・
    2026-09-25)。展開されれば値ごとに 1 本ずつ走り、値は関数の引数に届く。"""
    pytester.makepyprojecttoml(
        """\
        [tool.pytest.ini_options]
        doeff_adr_hy_files = ["tests/test_*.hy"]
        """
    )
    pytester.mkdir("tests")
    pytester.makeconftest(
        """\
        import pytest

        @pytest.fixture
        def flavor():
            return "default"
        """
    )
    pytester.makefile(
        ".hy",
        **{
            "tests/test_params": """\
                (import pytest)
                (defn [(pytest.mark.parametrize "flavor" ["a" "b" "c"])] test-flavor [flavor]
                  (assert (in flavor ["a" "b" "c"])))
                """,
        },
    )

    result: pytest.RunResult = pytester.runpytest("-v", "tests")

    result.assert_outcomes(passed=3)
    result.stdout.fnmatch_lines(["*test_flavor?a?*", "*test_flavor?b?*", "*test_flavor?c?*"])
