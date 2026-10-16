from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from doeff import __main__ as cli
from doeff.cli.discovery import StandardSymbolLoader
from doeff.cli.profiling import (
    ProfilingConfig,
    is_profiling_enabled,
    print_profiling_status,
    profile,
    use_profiling_config,
)
from doeff.cli.run_services import import_symbol
from tests.cli.cli_child import with_settings

pytestmark = pytest.mark.cli

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHILD_DEADLINE_SECONDS = 60
HY_RUN_ARGS = ["run", "--no-runbox", "--hy", "(import doeff [Pure]) (Pure 42)"]


def _run_cli_in_process(argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    exit_code = cli.main(argv)
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert captured.out == "42\n"
    return captured.err


def test_profiling_is_off_when_no_config_is_injected(capsys) -> None:
    assert not ProfilingConfig().enabled
    assert not is_profiling_enabled()
    with profile("library operation"):
        pass
    print_profiling_status()

    assert "[DOEFF][PROFILE]" not in capsys.readouterr().err


def test_profiling_can_switch_in_process_without_environ_or_reload(capsys) -> None:
    with use_profiling_config(ProfilingConfig(enabled=False)):
        assert not is_profiling_enabled()
        with profile("disabled operation"):
            pass
        print_profiling_status()

    disabled = capsys.readouterr()
    assert "[DOEFF][PROFILE]" not in disabled.err

    with use_profiling_config(ProfilingConfig(enabled=True)):
        assert is_profiling_enabled()
        with profile("enabled operation"):
            pass
        print_profiling_status()

    enabled = capsys.readouterr()
    assert "[DOEFF][PROFILE] enabled operation:" in enabled.err
    assert "Profiling enabled" in enabled.err


def test_doeff_command_prints_no_profile_lines_by_default(deadline_scale_factor: float) -> None:
    completed = subprocess.run(
        with_settings([sys.executable, "-m", "doeff", *HY_RUN_ARGS], {"DOEFF_PROFILE": None}),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "42\n"
    assert "[DOEFF][PROFILE]" not in completed.stderr


@pytest.mark.parametrize("value", ["", "0"])
def test_cli_profile_env_off_values_print_nothing(monkeypatch, capsys, value: str) -> None:
    monkeypatch.setenv("DOEFF_PROFILE", value)

    assert "[DOEFF][PROFILE]" not in _run_cli_in_process(HY_RUN_ARGS, capsys)


def test_cli_profile_env_prints_profile_lines(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DOEFF_PROFILE", "1")

    err = _run_cli_in_process(HY_RUN_ARGS, capsys)
    assert "[DOEFF][PROFILE] Profiling enabled" in err
    assert "[DOEFF][PROFILE]   Import doeff.runners.local:" in err


def test_cli_profile_flag_prints_profile_lines(monkeypatch, capsys) -> None:
    monkeypatch.delenv("DOEFF_PROFILE", raising=False)

    err = _run_cli_in_process([*HY_RUN_ARGS, "--profile"], capsys)
    assert "[DOEFF][PROFILE] Profiling enabled" in err
    assert "[DOEFF][PROFILE]   Import doeff.runners.local:" in err


def test_injected_config_reaches_discovery_and_run_service_call_sites(capsys) -> None:
    with use_profiling_config(ProfilingConfig(enabled=False)):
        StandardSymbolLoader().load_symbol("tests.cli.test_cli_main._TEST_SYMBOL")
        import_symbol("tests.cli.test_cli_main._TEST_SYMBOL")

    disabled = capsys.readouterr()
    assert "[DOEFF][PROFILE]" not in disabled.err

    with use_profiling_config(ProfilingConfig(enabled=True)):
        StandardSymbolLoader().load_symbol("tests.cli.test_cli_main._TEST_SYMBOL")
        import_symbol("tests.cli.test_cli_main._TEST_SYMBOL")

    enabled = capsys.readouterr()
    assert "[DOEFF][PROFILE]     Load symbol tests.cli.test_cli_main._TEST_SYMBOL:" in enabled.err
    assert "[DOEFF][PROFILE]   Import tests.cli.test_cli_main:" in enabled.err
