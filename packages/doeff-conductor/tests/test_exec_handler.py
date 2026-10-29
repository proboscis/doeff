"""Tests for deterministic Exec gate handling."""

from pathlib import Path

from doeff_conductor import Exec
from doeff_conductor import exec_handler
from doeff_conductor import Workspace

from doeff import stacked_handlers
from tests.effect_runner import answering, workspace_paths


def test_exec_tees_full_output_to_log(tmp_path: Path) -> None:
    handler = answering(exec_handler(log_dir=tmp_path / "logs"))

    result = handler(
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
    handler = answering(exec_handler(log_dir=tmp_path / "logs"))

    result = handler(Exec(cmd="echo failing && exit 7", workdir=tmp_path))

    assert result.exit_code == 7
    assert not result.passed
    assert "failing" in Path(result.log_path).read_text()


def test_exec_timeout_is_structured_result(tmp_path: Path) -> None:
    handler = answering(exec_handler(log_dir=tmp_path / "logs"))

    result = handler(Exec(cmd="sleep 1", workdir=tmp_path, timeout=0.01))

    assert result.exit_code == 124
    assert result.timed_out
    assert "timed out" in Path(result.log_path).read_text()


def test_exec_runs_in_the_workspace_the_workspace_handler_locates(tmp_path: Path) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    (workspace_path / "input.txt").write_text("workspace-data\n")
    workspace = Workspace(id="ws-1", repo="default", ref="feature", base_ref="main")
    handler = answering(
        stacked_handlers(
            workspace_paths({workspace.id: workspace_path}),
            exec_handler(log_dir=tmp_path / "logs"),
        )
    )

    result = handler(Exec(cmd="cat input.txt", workspace=workspace))

    assert result.passed
    assert Path(result.log_path).read_text() == "workspace-data\n"

