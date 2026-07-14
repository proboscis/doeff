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
