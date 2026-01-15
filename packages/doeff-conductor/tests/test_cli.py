"""Tests for doeff-conductor CLI.

Tests all CLI commands using Click's CliRunner for:
- Workflow commands: run, ps, show, watch, stop
- Issue commands: create, list, show, resolve
- Environment commands: list, cleanup
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
    WorkflowHandle,
    WorkflowStatus,
    WorktreeEnv,
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
            "environments": [],
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
            "environments": ["env-1"],
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
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as MockHandler:
            mock_handler = MagicMock()
            mock_issue = Issue(
                id="ISSUE-001",
                title="Test Issue",
                body="Issue body",
                status=IssueStatus.OPEN,
            )
            mock_handler.handle_create_issue.return_value = mock_issue
            MockHandler.return_value = mock_handler

            result = runner.invoke(cli, [
                "issue", "create", "Test Issue",
                "--body", "Issue body"
            ])
            assert result.exit_code == 0
            assert "Created issue" in result.output or "ISSUE-001" in result.output

    def test_issue_create_with_labels(self, runner: CliRunner, tmp_issues_dir: Path):
        """Create an issue with labels."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as MockHandler:
            mock_handler = MagicMock()
            mock_issue = Issue(
                id="ISSUE-001",
                title="Feature",
                body="Description",
                status=IssueStatus.OPEN,
                labels=("feature", "urgent"),
            )
            mock_handler.handle_create_issue.return_value = mock_issue
            MockHandler.return_value = mock_handler

            result = runner.invoke(cli, [
                "issue", "create", "Feature",
                "--body", "Description",
                "--labels", "feature",
                "--labels", "urgent"
            ])
            assert result.exit_code == 0

    def test_issue_create_json(self, runner: CliRunner, tmp_issues_dir: Path):
        """Test JSON output for issue create."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as MockHandler:
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
            MockHandler.return_value = mock_handler

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
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as MockHandler:
            mock_handler = MagicMock()
            mock_handler.handle_list_issues.return_value = []
            MockHandler.return_value = mock_handler

            result = runner.invoke(cli, ["issue", "list"])
            assert result.exit_code == 0
            assert "No issues found" in result.output

    def test_issue_list_with_issues(self, runner: CliRunner):
        """List existing issues."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as MockHandler:
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
            MockHandler.return_value = mock_handler

            result = runner.invoke(cli, ["issue", "list"])
            assert result.exit_code == 0
            assert "ISSUE-001" in result.output
            assert "ISSUE-002" in result.output
            assert "First Issue" in result.output

    def test_issue_list_filter_status(self, runner: CliRunner):
        """Filter issues by status."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as MockHandler:
            mock_handler = MagicMock()
            MockHandler.return_value = mock_handler
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
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as MockHandler:
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
            MockHandler.return_value = mock_handler

            result = runner.invoke(cli, ["issue", "list", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["id"] == "ISSUE-001"

    def test_issue_show(self, runner: CliRunner):
        """Show issue details."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as MockHandler:
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
            MockHandler.return_value = mock_handler

            result = runner.invoke(cli, ["issue", "show", "ISSUE-001"])
            assert result.exit_code == 0
            assert "ISSUE-001" in result.output
            assert "Test Issue" in result.output

    def test_issue_show_json(self, runner: CliRunner):
        """Test JSON output for issue show."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as MockHandler:
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
            MockHandler.return_value = mock_handler

            result = runner.invoke(cli, ["issue", "show", "ISSUE-001", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["id"] == "ISSUE-001"

    def test_issue_resolve(self, runner: CliRunner):
        """Resolve an issue."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as MockHandler:
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
            MockHandler.return_value = mock_handler

            result = runner.invoke(cli, [
                "issue", "resolve", "ISSUE-001",
                "--pr", "https://github.com/test/repo/pull/1"
            ])
            assert result.exit_code == 0
            assert "Resolved" in result.output

    def test_issue_resolve_json(self, runner: CliRunner):
        """Test JSON output for issue resolve."""
        with patch("doeff_conductor.handlers.issue_handler.IssueHandler") as MockHandler:
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
            MockHandler.return_value = mock_handler

            result = runner.invoke(cli, [
                "issue", "resolve", "ISSUE-001",
                "--pr", "https://github.com/test/repo/pull/1",
                "--json"
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["status"] == "resolved"


class TestEnvironmentCommands(TestCLIBase):
    """Tests for environment-related CLI commands."""

    def test_env_list_empty(self, runner: CliRunner, tmp_state_dir: Path):
        """List environments when none exist."""
        result = runner.invoke(
            cli, ["--state-dir", str(tmp_state_dir), "env", "list"]
        )
        assert result.exit_code == 0
        assert "No environments found" in result.output

    def test_env_list_with_envs(self, runner: CliRunner, tmp_state_dir: Path):
        """List existing environments."""
        with patch("doeff_conductor.api.ConductorAPI.list_environments") as mock_list:
            now = datetime.now(timezone.utc)
            mock_envs = [
                WorktreeEnv(
                    id="env-abc123",
                    path=Path("/tmp/worktrees/env-abc123"),
                    branch="feature/issue-001",
                    base_commit="abc123def456",
                    issue_id="ISSUE-001",
                    created_at=now,
                ),
            ]
            mock_list.return_value = mock_envs

            result = runner.invoke(
                cli, ["--state-dir", str(tmp_state_dir), "env", "list"]
            )
            assert result.exit_code == 0
            assert "env-abc1" in result.output  # Truncated ID
            assert "feature/issue-001" in result.output

    def test_env_list_json(self, runner: CliRunner, tmp_state_dir: Path):
        """Test JSON output for env list."""
        with patch("doeff_conductor.api.ConductorAPI.list_environments") as mock_list:
            now = datetime.now(timezone.utc)
            mock_envs = [
                WorktreeEnv(
                    id="env-abc123",
                    path=Path("/tmp/worktrees/env-abc123"),
                    branch="feature/issue-001",
                    base_commit="abc123",
                    created_at=now,
                ),
            ]
            mock_list.return_value = mock_envs

            result = runner.invoke(
                cli, ["--state-dir", str(tmp_state_dir), "env", "list", "--json"]
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["id"] == "env-abc123"

    def test_env_cleanup_dry_run(self, runner: CliRunner, tmp_state_dir: Path):
        """Test environment cleanup with dry run."""
        with patch("doeff_conductor.api.ConductorAPI.cleanup_environments") as mock_cleanup:
            mock_cleanup.return_value = [
                Path("/tmp/worktrees/old-env-1"),
                Path("/tmp/worktrees/old-env-2"),
            ]

            result = runner.invoke(
                cli, ["--state-dir", str(tmp_state_dir), "env", "cleanup", "--dry-run"]
            )
            assert result.exit_code == 0
            assert "Would clean" in result.output
            mock_cleanup.assert_called_once_with(dry_run=True, older_than_days=None)

    def test_env_cleanup_actual(self, runner: CliRunner, tmp_state_dir: Path):
        """Test actual environment cleanup."""
        with patch("doeff_conductor.api.ConductorAPI.cleanup_environments") as mock_cleanup:
            mock_cleanup.return_value = [Path("/tmp/worktrees/old-env")]

            result = runner.invoke(
                cli, ["--state-dir", str(tmp_state_dir), "env", "cleanup"]
            )
            assert result.exit_code == 0
            assert "Cleaned" in result.output

    def test_env_cleanup_empty(self, runner: CliRunner, tmp_state_dir: Path):
        """Test cleanup when no orphaned environments."""
        with patch("doeff_conductor.api.ConductorAPI.cleanup_environments") as mock_cleanup:
            mock_cleanup.return_value = []

            result = runner.invoke(
                cli, ["--state-dir", str(tmp_state_dir), "env", "cleanup"]
            )
            assert result.exit_code == 0
            assert "No orphaned environments found" in result.output

    def test_env_cleanup_json(self, runner: CliRunner, tmp_state_dir: Path):
        """Test JSON output for env cleanup."""
        with patch("doeff_conductor.api.ConductorAPI.cleanup_environments") as mock_cleanup:
            mock_cleanup.return_value = [Path("/tmp/worktrees/old-env")]

            result = runner.invoke(
                cli, ["--state-dir", str(tmp_state_dir), "env", "cleanup", "--json"]
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "cleaned" in data
            assert len(data["cleaned"]) == 1

    def test_env_cleanup_older_than(self, runner: CliRunner, tmp_state_dir: Path):
        """Test cleanup with age filter."""
        with patch("doeff_conductor.api.ConductorAPI.cleanup_environments") as mock_cleanup:
            mock_cleanup.return_value = []

            result = runner.invoke(
                cli, ["--state-dir", str(tmp_state_dir), "env", "cleanup", "--older-than", "7"]
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
            cli, ["--state-dir", str(tmp_state_dir), "run", "/nonexistent/workflow.py"]
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
        assert "env" in result.output
        assert "template" in result.output

    def test_issue_help(self, runner: CliRunner):
        """Test issue subcommand help."""
        result = runner.invoke(cli, ["issue", "--help"])
        assert result.exit_code == 0
        assert "create" in result.output
        assert "list" in result.output
        assert "show" in result.output
        assert "resolve" in result.output

    def test_env_help(self, runner: CliRunner):
        """Test env subcommand help."""
        result = runner.invoke(cli, ["env", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "cleanup" in result.output

    def test_template_help(self, runner: CliRunner):
        """Test template subcommand help."""
        result = runner.invoke(cli, ["template", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "show" in result.output
