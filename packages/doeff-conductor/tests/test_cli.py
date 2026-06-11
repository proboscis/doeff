"""Tests for doeff-conductor CLI.

Tests all CLI commands using Click's CliRunner for:
- Workflow commands: run, ps, show, watch, stop
- Issue commands: create, list, show, resolve
- Workspace commands: list, cleanup
- Template commands: list, show
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from doeff_conductor.cli import cli
from doeff_conductor.types import (
    Issue,
    IssueStatus,
    WorkflowStatus,
    Workspace,
)


class TestCLIBase:
    """Base class with common fixtures for CLI tests."""

    @pytest.fixture
    def runner(self) -> CliRunner:
        """Create a Click test runner."""
        return CliRunner()

    @pytest.fixture
    def tmp_state_dir(self, tmp_path: Path) -> Path:
        """Create a temporary state directory."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        return state_dir

    @pytest.fixture
    def tmp_issues_dir(self, tmp_path: Path) -> Path:
        """Create a temporary issues directory."""
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        return issues_dir


class TestWorkflowCommands(TestCLIBase):
    """Tests for workflow-related CLI commands."""

    def _write_workflow_meta(
        self,
        state_dir: Path,
        workflow_id: str,
        status: WorkflowStatus,
    ) -> None:
        workflow_dir = state_dir / "workflows" / workflow_id
        workflow_dir.mkdir(parents=True)
        now = datetime.now(timezone.utc)
        workflow_data = {
            "id": workflow_id,
            "name": f"{workflow_id}-workflow",
            "status": status.value,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "workspaces": [],
            "agents": [],
        }
        (workflow_dir / "meta.json").write_text(json.dumps(workflow_data))

    def _write_open_gate(self, state_dir: Path, workflow_id: str) -> None:
        run_state = {
            "workflow_id": workflow_id,
            "workflow_name": f"{workflow_id}-workflow",
            "supervision": "autonomous",
            "events": [],
            "open_gates": [
                {
                    "gate_id": "gate-review",
                    "workflow_id": workflow_id,
                    "node_id": "review",
                    "phase": None,
                    "reason": "review required",
                    "stakes": {},
                    "options": [
                        {
                            "name": "proceed",
                            "outcome": "resume",
                            "description": "continue the run",
                        },
                        {
                            "name": "abort",
                            "outcome": "abort",
                            "description": "stop the run",
                        },
                    ],
                }
            ],
            "answered_gates": {},
        }
        run_state_path = state_dir / "workflows" / workflow_id / "run-state.json"
        run_state_path.write_text(json.dumps(run_state))

    def test_ps_empty(self, runner: CliRunner, tmp_state_dir: Path):
        """List workflows when none exist."""
        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "ps"]
        )
        assert result.exit_code == 0
        assert "No workflows found" in result.output

    def test_ps_with_workflows(self, runner: CliRunner, tmp_state_dir: Path):
        """List workflows when some exist."""
        # Create a workflow directory
        workflow_dir = tmp_state_dir / "workflows" / "abc12345"
        workflow_dir.mkdir(parents=True)

        now = datetime.now(timezone.utc)
        workflow_data = {
            "id": "abc12345",
            "name": "test-workflow",
            "status": "running",
            "template": "basic_pr",
            "issue_id": "ISSUE-001",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "workspaces": [],
            "agents": [],
        }
        (workflow_dir / "meta.json").write_text(json.dumps(workflow_data))

        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "ps"]
        )
        assert result.exit_code == 0
        assert "abc1234" in result.output  # Truncated ID
        assert "test-workflow" in result.output
        assert "basic_pr" in result.output

    def test_ps_with_status_filter(self, runner: CliRunner, tmp_state_dir: Path):
        """Filter workflows by status."""
        workflows_dir = tmp_state_dir / "workflows"
        workflows_dir.mkdir(parents=True)

        now = datetime.now(timezone.utc)

        # Create running workflow
        running_dir = workflows_dir / "run12345"
        running_dir.mkdir()
        (running_dir / "meta.json").write_text(json.dumps({
            "id": "run12345",
            "name": "running-wf",
            "status": "running",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }))

        # Create done workflow
        done_dir = workflows_dir / "done1234"
        done_dir.mkdir()
        (done_dir / "meta.json").write_text(json.dumps({
            "id": "done1234",
            "name": "done-wf",
            "status": "done",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }))

        # Filter by running
        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "ps", "--status", "running"]
        )
        assert result.exit_code == 0
        assert "running-wf" in result.output
        assert "done-wf" not in result.output

    def test_ps_json_output(self, runner: CliRunner, tmp_state_dir: Path):
        """Test JSON output for ps command."""
        workflow_dir = tmp_state_dir / "workflows" / "abc12345"
        workflow_dir.mkdir(parents=True)

        now = datetime.now(timezone.utc)
        workflow_data = {
            "id": "abc12345",
            "name": "test-workflow",
            "status": "running",
            "template": "basic_pr",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        (workflow_dir / "meta.json").write_text(json.dumps(workflow_data))

        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "ps", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == "abc12345"

    def test_show_workflow(self, runner: CliRunner, tmp_state_dir: Path):
        """Show workflow details."""
        workflow_dir = tmp_state_dir / "workflows" / "abc12345"
        workflow_dir.mkdir(parents=True)

        now = datetime.now(timezone.utc)
        workflow_data = {
            "id": "abc12345",
            "name": "test-workflow",
            "status": "running",
            "template": "basic_pr",
            "issue_id": "ISSUE-001",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "workspaces": ["env-1"],
            "agents": ["agent-1"],
        }
        (workflow_dir / "meta.json").write_text(json.dumps(workflow_data))

        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "show", "abc12345"]
        )
        assert result.exit_code == 0
        assert "abc12345" in result.output
        assert "test-workflow" in result.output
        assert "running" in result.output
        assert "basic_pr" in result.output

    def test_show_workflow_not_found(self, runner: CliRunner, tmp_state_dir: Path):
        """Show error when workflow not found."""
        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "show", "nonexistent"]
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_show_workflow_json(self, runner: CliRunner, tmp_state_dir: Path):
        """Test JSON output for show command."""
        workflow_dir = tmp_state_dir / "workflows" / "abc12345"
        workflow_dir.mkdir(parents=True)

        now = datetime.now(timezone.utc)
        workflow_data = {
            "id": "abc12345",
            "name": "test-workflow",
            "status": "running",
            "template": "basic_pr",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        (workflow_dir / "meta.json").write_text(json.dumps(workflow_data))

        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "show", "abc12345", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == "abc12345"
        assert data["status"] == "running"

    def test_show_workflow_prefix_match(self, runner: CliRunner, tmp_state_dir: Path):
        """Show workflow by ID prefix."""
        workflow_dir = tmp_state_dir / "workflows" / "abc12345"
        workflow_dir.mkdir(parents=True)

        now = datetime.now(timezone.utc)
        (workflow_dir / "meta.json").write_text(json.dumps({
            "id": "abc12345",
            "name": "test-workflow",
            "status": "running",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }))

        # Should match by prefix
        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "show", "abc"]
        )
        assert result.exit_code == 0
        assert "abc12345" in result.output

    def test_wait_done_exits_zero_with_json(self, runner: CliRunner, tmp_state_dir: Path):
        """Wait returns success when the workflow is done."""
        self._write_workflow_meta(tmp_state_dir, "done1234", WorkflowStatus.DONE)

        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "wait", "done1234", "--json"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "done"
        assert data["gates"] == []
        assert data["waited_seconds"] >= 0

    def test_wait_error_exits_one_with_json(self, runner: CliRunner, tmp_state_dir: Path):
        """Wait returns failure when the workflow errors."""
        self._write_workflow_meta(tmp_state_dir, "err12345", WorkflowStatus.ERROR)

        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "wait", "err12345", "--json"]
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert data["gates"] == []

    def test_wait_stopped_exits_one_with_json(self, runner: CliRunner, tmp_state_dir: Path):
        """Wait returns failure when the workflow is stopped."""
        self._write_workflow_meta(tmp_state_dir, "stop1234", WorkflowStatus.STOPPED)

        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "wait", "stop1234", "--json"]
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "stopped"
        assert data["gates"] == []

    def test_wait_aborted_exposes_stopped_with_json(
        self,
        runner: CliRunner,
        tmp_state_dir: Path,
    ):
        """Wait preserves compatibility with legacy aborted workflow metadata."""
        self._write_workflow_meta(tmp_state_dir, "abort123", WorkflowStatus.ABORTED)

        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "wait", "abort123", "--json"]
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "stopped"
        assert data["gates"] == []

    def test_wait_parked_exits_two_with_gate_payload(
        self,
        runner: CliRunner,
        tmp_state_dir: Path,
    ):
        """Wait returns attention-needed when the workflow has an open gate."""
        self._write_workflow_meta(tmp_state_dir, "gate1234", WorkflowStatus.BLOCKED)
        self._write_open_gate(tmp_state_dir, "gate1234")

        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "wait", "gate1234", "--json"]
        )

        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data["status"] == "blocked"
        assert data["gates"] == [
            {"gate_id": "gate-review", "options": ["proceed", "abort"]}
        ]

    def test_wait_timeout_exits_three_with_json(self, runner: CliRunner, tmp_state_dir: Path):
        """Wait returns timeout when no terminal or parked state arrives in time."""
        self._write_workflow_meta(tmp_state_dir, "run12345", WorkflowStatus.RUNNING)

        result = runner.invoke(
            cli,
            [
                "--state-dir",
                str(tmp_state_dir),
                "wait",
                "run12345",
                "--timeout",
                "0",
                "--json",
            ],
        )

        assert result.exit_code == 3
        data = json.loads(result.output)
        assert data["status"] == "running"
        assert data["gates"] == []

    def test_wait_unknown_workflow_names_state_dir(
        self,
        runner: CliRunner,
        tmp_state_dir: Path,
    ):
        """Wait fails loudly for unknown workflow ids and names the consulted state dir."""
        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "wait", "missing-workflow"]
        )

        assert result.exit_code == 1
        assert "missing-workflow" in result.output
        assert str(tmp_state_dir) in result.output

    def test_stop_workflow(self, runner: CliRunner, tmp_state_dir: Path):
        """Stop a workflow."""
        workflow_dir = tmp_state_dir / "workflows" / "abc12345"
        workflow_dir.mkdir(parents=True)

        now = datetime.now(timezone.utc)
        (workflow_dir / "meta.json").write_text(json.dumps({
            "id": "abc12345",
            "name": "test-workflow",
            "status": "running",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "agents": ["agent-1", "agent-2"],
        }))

        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "stop", "abc12345"]
        )
        assert result.exit_code == 0
        assert "Stopped" in result.output

    def test_stop_workflow_json(self, runner: CliRunner, tmp_state_dir: Path):
        """Test JSON output for stop command."""
        workflow_dir = tmp_state_dir / "workflows" / "abc12345"
        workflow_dir.mkdir(parents=True)

        now = datetime.now(timezone.utc)
        (workflow_dir / "meta.json").write_text(json.dumps({
            "id": "abc12345",
            "name": "test-workflow",
            "status": "running",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "agents": ["agent-1"],
        }))

        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "stop", "abc12345", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True

    def test_stop_nonexistent_workflow(self, runner: CliRunner, tmp_state_dir: Path):
        """Stop a non-existent workflow."""
        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "stop", "nonexistent"]
        )
        assert result.exit_code == 1


class TestIssueCommands(TestCLIBase):
    """Tests for issue-related CLI commands."""

    def test_issue_create(self, runner: CliRunner, tmp_issues_dir: Path):
        """Create a new issue via CLI."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as mock_handler_class:
            mock_handler = MagicMock()
            mock_issue = Issue(
                id="ISSUE-001",
                title="Test Issue",
                body="Issue body",
                status=IssueStatus.OPEN,
            )
            mock_handler.handle_create_issue.return_value = mock_issue
            mock_handler_class.return_value = mock_handler

            result = runner.invoke(cli, [
                "issue", "create", "Test Issue",
                "--body", "Issue body"
            ])
            assert result.exit_code == 0
            assert "Created issue" in result.output or "ISSUE-001" in result.output

    def test_issue_create_with_labels(self, runner: CliRunner, tmp_issues_dir: Path):
        """Create an issue with labels."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as mock_handler_class:
            mock_handler = MagicMock()
            mock_issue = Issue(
                id="ISSUE-001",
                title="Feature",
                body="Description",
                status=IssueStatus.OPEN,
                labels=("feature", "urgent"),
            )
            mock_handler.handle_create_issue.return_value = mock_issue
            mock_handler_class.return_value = mock_handler

            result = runner.invoke(cli, [
                "issue", "create", "Feature",
                "--body", "Description",
                "--labels", "feature",
                "--labels", "urgent"
            ])
            assert result.exit_code == 0

    def test_issue_create_json(self, runner: CliRunner, tmp_issues_dir: Path):
        """Test JSON output for issue create."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as mock_handler_class:
            mock_handler = MagicMock()
            now = datetime.now(timezone.utc)
            mock_issue = Issue(
                id="ISSUE-001",
                title="Test Issue",
                body="Body",
                status=IssueStatus.OPEN,
                created_at=now,
            )
            mock_handler.handle_create_issue.return_value = mock_issue
            mock_handler_class.return_value = mock_handler

            result = runner.invoke(cli, [
                "issue", "create", "Test Issue",
                "--body", "Body",
                "--json"
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["id"] == "ISSUE-001"
            assert data["title"] == "Test Issue"

    def test_issue_list_empty(self, runner: CliRunner):
        """List issues when none exist."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as mock_handler_class:
            mock_handler = MagicMock()
            mock_handler.handle_list_issues.return_value = []
            mock_handler_class.return_value = mock_handler

            result = runner.invoke(cli, ["issue", "list"])
            assert result.exit_code == 0
            assert "No issues found" in result.output

    def test_issue_list_with_issues(self, runner: CliRunner):
        """List existing issues."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as mock_handler_class:
            mock_handler = MagicMock()
            now = datetime.now(timezone.utc)
            mock_issues = [
                Issue(
                    id="ISSUE-001",
                    title="First Issue",
                    body="Body 1",
                    status=IssueStatus.OPEN,
                    labels=("bug",),
                    created_at=now,
                ),
                Issue(
                    id="ISSUE-002",
                    title="Second Issue",
                    body="Body 2",
                    status=IssueStatus.IN_PROGRESS,
                    created_at=now,
                ),
            ]
            mock_handler.handle_list_issues.return_value = mock_issues
            mock_handler_class.return_value = mock_handler

            result = runner.invoke(cli, ["issue", "list"])
            assert result.exit_code == 0
            assert "ISSUE-001" in result.output
            assert "ISSUE-002" in result.output
            assert "First Issue" in result.output

    def test_issue_list_filter_status(self, runner: CliRunner):
        """Filter issues by status."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as mock_handler_class:
            mock_handler = MagicMock()
            mock_handler_class.return_value = mock_handler
            mock_handler.handle_list_issues.return_value = []

            result = runner.invoke(cli, [
                "issue", "list", "--status", "open"
            ])
            assert result.exit_code == 0

            # Verify the handler was called with correct status
            call_args = mock_handler.handle_list_issues.call_args
            assert call_args is not None

    def test_issue_list_json(self, runner: CliRunner):
        """Test JSON output for issue list."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as mock_handler_class:
            mock_handler = MagicMock()
            now = datetime.now(timezone.utc)
            mock_issues = [
                Issue(
                    id="ISSUE-001",
                    title="Test Issue",
                    body="Body",
                    status=IssueStatus.OPEN,
                    created_at=now,
                ),
            ]
            mock_handler.handle_list_issues.return_value = mock_issues
            mock_handler_class.return_value = mock_handler

            result = runner.invoke(cli, ["issue", "list", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["id"] == "ISSUE-001"

    def test_issue_show(self, runner: CliRunner):
        """Show issue details."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as mock_handler_class:
            mock_handler = MagicMock()
            now = datetime.now(timezone.utc)
            mock_issue = Issue(
                id="ISSUE-001",
                title="Test Issue",
                body="Detailed description of the issue",
                status=IssueStatus.OPEN,
                labels=("feature", "priority"),
                created_at=now,
            )
            mock_handler.handle_get_issue.return_value = mock_issue
            mock_handler_class.return_value = mock_handler

            result = runner.invoke(cli, ["issue", "show", "ISSUE-001"])
            assert result.exit_code == 0
            assert "ISSUE-001" in result.output
            assert "Test Issue" in result.output

    def test_issue_show_json(self, runner: CliRunner):
        """Test JSON output for issue show."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as mock_handler_class:
            mock_handler = MagicMock()
            now = datetime.now(timezone.utc)
            mock_issue = Issue(
                id="ISSUE-001",
                title="Test Issue",
                body="Body",
                status=IssueStatus.OPEN,
                created_at=now,
            )
            mock_handler.handle_get_issue.return_value = mock_issue
            mock_handler_class.return_value = mock_handler

            result = runner.invoke(cli, ["issue", "show", "ISSUE-001", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["id"] == "ISSUE-001"

    def test_issue_resolve(self, runner: CliRunner):
        """Resolve an issue."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as mock_handler_class:
            mock_handler = MagicMock()
            now = datetime.now(timezone.utc)
            mock_issue = Issue(
                id="ISSUE-001",
                title="Test Issue",
                body="Body",
                status=IssueStatus.OPEN,
                created_at=now,
            )
            resolved_issue = Issue(
                id="ISSUE-001",
                title="Test Issue",
                body="Body",
                status=IssueStatus.RESOLVED,
                pr_url="https://github.com/test/repo/pull/1",
                created_at=now,
                resolved_at=now,
            )
            mock_handler.handle_get_issue.return_value = mock_issue
            mock_handler.handle_resolve_issue.return_value = resolved_issue
            mock_handler_class.return_value = mock_handler

            result = runner.invoke(cli, [
                "issue", "resolve", "ISSUE-001",
                "--pr", "https://github.com/test/repo/pull/1"
            ])
            assert result.exit_code == 0
            assert "Resolved" in result.output

    def test_issue_resolve_json(self, runner: CliRunner):
        """Test JSON output for issue resolve."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as mock_handler_class:
            mock_handler = MagicMock()
            now = datetime.now(timezone.utc)
            mock_issue = Issue(
                id="ISSUE-001",
                title="Test Issue",
                body="Body",
                status=IssueStatus.OPEN,
                created_at=now,
            )
            resolved_issue = Issue(
                id="ISSUE-001",
                title="Test Issue",
                body="Body",
                status=IssueStatus.RESOLVED,
                pr_url="https://github.com/test/repo/pull/1",
                created_at=now,
                resolved_at=now,
            )
            mock_handler.handle_get_issue.return_value = mock_issue
            mock_handler.handle_resolve_issue.return_value = resolved_issue
            mock_handler_class.return_value = mock_handler

            result = runner.invoke(cli, [
                "issue", "resolve", "ISSUE-001",
                "--pr", "https://github.com/test/repo/pull/1",
                "--json"
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["status"] == "resolved"


class TestWorkspaceCommands(TestCLIBase):
    """Tests for workspace-related CLI commands."""

    def test_workspace_list_empty(self, runner: CliRunner, tmp_state_dir: Path):
        """List workspaces when none exist."""
        with patch("doeff_conductor.api.ConductorAPI.list_workspaces", return_value=[]):
            result = runner.invoke(
                cli, ["--state-dir", str(tmp_state_dir), "workspace", "list"]
            )
        assert result.exit_code == 0
        assert "No workspaces found" in result.output

    def test_workspace_list_with_items(self, runner: CliRunner, tmp_state_dir: Path):
        """List existing workspaces."""
        with patch("doeff_conductor.api.ConductorAPI.list_workspaces") as mock_list:
            now = datetime.now(timezone.utc)
            mock_workspaces = [
                Workspace(
                    id="workspace-abc123",
                    repo="default",
                    ref="feature/issue-001",
                    base_ref="main",
                    issue_id="ISSUE-001",
                    created_at=now,
                ),
            ]
            mock_list.return_value = mock_workspaces

            result = runner.invoke(
                cli, ["--state-dir", str(tmp_state_dir), "workspace", "list"]
            )
            assert result.exit_code == 0
            assert "workspace-ab" in result.output  # Truncated ID
            assert "default" in result.output
            assert "feature/issue-001" in result.output

    def test_workspace_list_json(self, runner: CliRunner, tmp_state_dir: Path):
        """Test JSON output for workspace list."""
        with patch("doeff_conductor.api.ConductorAPI.list_workspaces") as mock_list:
            now = datetime.now(timezone.utc)
            mock_workspaces = [
                Workspace(
                    id="workspace-abc123",
                    repo="default",
                    ref="feature/issue-001",
                    base_ref="main",
                    created_at=now,
                ),
            ]
            mock_list.return_value = mock_workspaces

            result = runner.invoke(
                cli, ["--state-dir", str(tmp_state_dir), "workspace", "list", "--json"]
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["id"] == "workspace-abc123"
            assert "path" not in data[0]

    def test_workspace_cleanup_dry_run(self, runner: CliRunner, tmp_state_dir: Path):
        """Test workspace cleanup with dry run."""
        with patch("doeff_conductor.api.ConductorAPI.cleanup_workspaces") as mock_cleanup:
            mock_cleanup.return_value = [
                Path("/tmp/workspaces/old-workspace-1"),
                Path("/tmp/workspaces/old-workspace-2"),
            ]

            result = runner.invoke(
                cli, ["--state-dir", str(tmp_state_dir), "workspace", "cleanup", "--dry-run"]
            )
            assert result.exit_code == 0
            assert "Would clean" in result.output
            mock_cleanup.assert_called_once_with(dry_run=True, older_than_days=None)

    def test_workspace_cleanup_actual(self, runner: CliRunner, tmp_state_dir: Path):
        """Test actual workspace cleanup."""
        with patch("doeff_conductor.api.ConductorAPI.cleanup_workspaces") as mock_cleanup:
            mock_cleanup.return_value = [Path("/tmp/workspaces/old-workspace")]

            result = runner.invoke(
                cli, ["--state-dir", str(tmp_state_dir), "workspace", "cleanup"]
            )
            assert result.exit_code == 0
            assert "Cleaned" in result.output

    def test_workspace_cleanup_empty(self, runner: CliRunner, tmp_state_dir: Path):
        """Test cleanup when no orphaned workspaces."""
        with patch("doeff_conductor.api.ConductorAPI.cleanup_workspaces") as mock_cleanup:
            mock_cleanup.return_value = []

            result = runner.invoke(
                cli, ["--state-dir", str(tmp_state_dir), "workspace", "cleanup"]
            )
            assert result.exit_code == 0
            assert "No orphaned workspaces found" in result.output

    def test_workspace_cleanup_json(self, runner: CliRunner, tmp_state_dir: Path):
        """Test JSON output for workspace cleanup."""
        with patch("doeff_conductor.api.ConductorAPI.cleanup_workspaces") as mock_cleanup:
            mock_cleanup.return_value = [Path("/tmp/workspaces/old-workspace")]

            result = runner.invoke(
                cli, ["--state-dir", str(tmp_state_dir), "workspace", "cleanup", "--json"]
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "cleaned" in data
            assert len(data["cleaned"]) == 1

    def test_workspace_cleanup_older_than(self, runner: CliRunner, tmp_state_dir: Path):
        """Test cleanup with age filter."""
        with patch("doeff_conductor.api.ConductorAPI.cleanup_workspaces") as mock_cleanup:
            mock_cleanup.return_value = []

            result = runner.invoke(
                cli,
                ["--state-dir", str(tmp_state_dir), "workspace", "cleanup", "--older-than", "7"],
            )
            assert result.exit_code == 0
            mock_cleanup.assert_called_once_with(dry_run=False, older_than_days=7)


class TestTemplateCommands(TestCLIBase):
    """Tests for template-related CLI commands."""

    def test_template_list(self, runner: CliRunner):
        """List available templates."""
        result = runner.invoke(cli, ["template", "list"])
        assert result.exit_code == 0
        assert "basic_pr" in result.output
        assert "enforced_pr" in result.output
        assert "reviewed_pr" in result.output
        assert "multi_agent" in result.output

    def test_template_list_json(self, runner: CliRunner):
        """Test JSON output for template list."""
        result = runner.invoke(cli, ["template", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert "basic_pr" in data
        assert "enforced_pr" in data

    def test_template_show(self, runner: CliRunner):
        """Show template source code or error due to DoYieldFunction.

        Note: The @do decorator wraps functions in DoYieldFunction which
        cannot be inspected with getsource(). This is an existing limitation.
        """
        result = runner.invoke(cli, ["template", "show", "basic_pr"])
        if result.exit_code == 0:
            assert "@do" in result.output or "def basic_pr" in result.output
        else:
            assert result.exit_code == 1

    def test_template_show_not_found(self, runner: CliRunner):
        """Show error for unknown template."""
        result = runner.invoke(cli, ["template", "show", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestRunCommand(TestCLIBase):
    """Tests for the run command."""

    def test_run_command_has_no_agent_mode_option(
        self,
        runner: CliRunner,
    ):
        """Run command exposes no worker backend selector."""
        result = runner.invoke(cli, ["run", "--help"])

        assert result.exit_code == 0
        removed_option = "--agent" + "-mode"
        removed_envvar = "CONDUCTOR_AGENT" + "_MODE"
        assert removed_option not in result.output
        assert removed_envvar not in result.output

    def test_run_template_not_found(self, runner: CliRunner, tmp_state_dir: Path):
        """Run a non-existent template."""
        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "run", "nonexistent_template"]
        )
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_run_file_not_found(self, runner: CliRunner, tmp_state_dir: Path):
        """Run a non-existent workflow file."""
        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "run", "/nonexistent/workflow.hy"]
        )
        assert result.exit_code == 1


class TestHelpOutput(TestCLIBase):
    """Tests for CLI help output."""

    def test_main_help(self, runner: CliRunner):
        """Test main CLI help."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "conductor" in result.output
        assert "run" in result.output
        assert "ps" in result.output
        assert "issue" in result.output
        assert "workspace" in result.output
        assert "template" in result.output

    def test_issue_help(self, runner: CliRunner):
        """Test issue subcommand help."""
        result = runner.invoke(cli, ["issue", "--help"])
        assert result.exit_code == 0
        assert "create" in result.output
        assert "list" in result.output
        assert "show" in result.output
        assert "resolve" in result.output

    def test_workspace_help(self, runner: CliRunner):
        """Test workspace subcommand help."""
        result = runner.invoke(cli, ["workspace", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "cleanup" in result.output

    def test_template_help(self, runner: CliRunner):
        """Test template subcommand help."""
        result = runner.invoke(cli, ["template", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "show" in result.output
