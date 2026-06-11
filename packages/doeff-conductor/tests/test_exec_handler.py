"""Tests for deterministic Exec gate handling."""

from pathlib import Path

from doeff_conductor.effects.exec import Exec
from doeff_conductor.handlers.exec_handler import ExecHandler
from doeff_conductor.types import Workspace


def test_exec_tees_full_output_to_log(tmp_path: Path) -> None:
    handler = ExecHandler(log_dir=tmp_path / "logs")

    result = handler.handle_exec(
        Exec(
            cmd=(
                "for i in 1 2 3 4 5; do echo line-$i; done; "
                "echo stderr-line >&2"
            ),
            workdir=tmp_path,
        ),
    )

    assert result.exit_code == 0
    log_text = Path(result.log_path).read_text()
    assert "line-1" in log_text
    assert "line-5" in log_text
    assert "stderr-line" in log_text


def test_exec_returns_nonzero_exit_code_without_raising(tmp_path: Path) -> None:
    handler = ExecHandler(log_dir=tmp_path / "logs")

    result = handler.handle_exec(Exec(cmd="echo failing && exit 7", workdir=tmp_path))

    assert result.exit_code == 7
    assert not result.passed
    assert "failing" in Path(result.log_path).read_text()


def test_exec_timeout_is_structured_result(tmp_path: Path) -> None:
    handler = ExecHandler(log_dir=tmp_path / "logs")

    result = handler.handle_exec(Exec(cmd="sleep 1", workdir=tmp_path, timeout=0.01))

    assert result.exit_code == 124
    assert result.timed_out
    assert "timed out" in Path(result.log_path).read_text()


def test_exec_runs_against_workspace_via_resolver(tmp_path: Path) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    (workspace_path / "input.txt").write_text("workspace-data\n")
    workspace = Workspace(id="ws-1", repo="default", ref="feature", base_ref="main")
    handler = ExecHandler(
        workspace_resolver=lambda _candidate: workspace_path,
        log_dir=tmp_path / "logs",
    )

    result = handler.handle_exec(Exec(cmd="cat input.txt", workspace=workspace))

    assert result.passed
    assert Path(result.log_path).read_text() == "workspace-data\n"

