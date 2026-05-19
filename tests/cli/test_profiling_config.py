from __future__ import annotations

import json

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

pytestmark = pytest.mark.cli


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


def test_cli_launcher_injects_disable_profile_env(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DOEFF_DISABLE_PROFILE", "1")

    exit_code = cli.main([
        "run",
        "-c",
        "from doeff import Ask; v = yield Ask('value'); return v",
        "--interpreter",
        "tests.cli.test_cli_main._env_interpreter",
        "--set",
        "value=7",
        "--format",
        "json",
        "--no-runbox",
    ])

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert payload["result"] == "7"
    assert "[DOEFF][PROFILE]" not in captured.err


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
