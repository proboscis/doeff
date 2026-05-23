"""Focused tests for remote runner file-exchange configuration."""

import hy  # noqa: F401
import pytest
from pathlib import Path

from doeff import Pure, run
from doeff_ml_nexus.runner import p_run, runner_interpreter
from doeff_ml_nexus.runner_env import (
    DEFAULT_RUNNER_INPUT_PATH,
    DEFAULT_RUNNER_OUTPUT_PATH,
    RUNNER_INPUT_PATH_KEY,
    RUNNER_OUTPUT_PATH_KEY,
    default_runner_env,
    resolve_runner_env,
    runner_env_from_process,
)
from doeff_ml_nexus.serializer import default_serializer


def _write_pickled_program(path, program):
    try:
        path.write_bytes(default_serializer.dumps(program))
    except TypeError:
        pytest.skip("doeff-vm pickle not supported on this Python version")


def _load_pickled_result(path):
    return default_serializer.loads(path.read_bytes())


def test_p_run_uses_injected_file_exchange_paths(tmp_path, monkeypatch):
    injected_input = tmp_path / "injected-program.pkl"
    injected_output = tmp_path / "injected-result.pkl"
    wrong_input = tmp_path / "wrong-program.pkl"
    wrong_output = tmp_path / "wrong-result.pkl"
    _write_pickled_program(injected_input, Pure({"ok": True}))
    _write_pickled_program(wrong_input, Pure({"ok": False}))
    monkeypatch.setenv("DOEFF_INPUT", str(wrong_input))
    monkeypatch.setenv("DOEFF_OUTPUT", str(wrong_output))

    result_path = runner_interpreter(
        p_run,
        env={
            RUNNER_INPUT_PATH_KEY: str(injected_input),
            RUNNER_OUTPUT_PATH_KEY: str(injected_output),
        },
    )

    assert result_path == str(injected_output)
    assert _load_pickled_result(injected_output) == {"ok": True}
    assert not wrong_output.exists()


def test_runner_interpreter_adapts_process_env_to_program_env(tmp_path, monkeypatch):
    process_input = tmp_path / "process-program.pkl"
    process_output = tmp_path / "process-result.pkl"
    _write_pickled_program(process_input, Pure("from-process-env"))
    monkeypatch.setenv("DOEFF_INPUT", str(process_input))
    monkeypatch.setenv("DOEFF_OUTPUT", str(process_output))

    result_path = runner_interpreter(p_run)

    assert result_path == str(process_output)
    assert _load_pickled_result(process_output) == "from-process-env"


def test_runner_env_defaults_are_explicit_injected_config(monkeypatch):
    monkeypatch.delenv("DOEFF_INPUT", raising=False)
    monkeypatch.delenv("DOEFF_OUTPUT", raising=False)

    expected_defaults = {
        RUNNER_INPUT_PATH_KEY: DEFAULT_RUNNER_INPUT_PATH,
        RUNNER_OUTPUT_PATH_KEY: DEFAULT_RUNNER_OUTPUT_PATH,
    }

    assert run(default_runner_env()) == expected_defaults
    assert run(runner_env_from_process()) == expected_defaults


def test_resolve_runner_env_accepts_program_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DOEFF_INPUT", str(tmp_path / "process-program.pkl"))
    monkeypatch.setenv("DOEFF_OUTPUT", str(tmp_path / "process-result.pkl"))
    injected_input = tmp_path / "program-env-program.pkl"
    injected_output = tmp_path / "program-env-result.pkl"

    resolved = run(
        resolve_runner_env(
            Pure(
                {
                    RUNNER_INPUT_PATH_KEY: str(injected_input),
                    RUNNER_OUTPUT_PATH_KEY: str(injected_output),
                }
            )
        )
    )

    assert resolved == {
        RUNNER_INPUT_PATH_KEY: str(injected_input),
        RUNNER_OUTPUT_PATH_KEY: str(injected_output),
    }


def test_docker_handler_preserves_process_env_file_exchange_contract():
    repo_root = Path(__file__).resolve().parents[3]
    docker_handler = (
        repo_root / "packages/doeff-ml-nexus/src/doeff_ml_nexus/handlers/docker.hy"
    ).read_text()

    assert 'f"DOEFF_INPUT={container-exchange}/program.pkl"' in docker_handler
    assert 'f"DOEFF_OUTPUT={container-exchange}/result.pkl"' in docker_handler
    assert '"--program" "doeff_ml_nexus.runner.p_run"' in docker_handler
    assert '"--interpreter" "doeff_ml_nexus.runner.runner_interpreter"' in docker_handler
