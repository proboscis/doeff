"""End-to-end tests for doeff-conductor workflows."""

import subprocess
from pathlib import Path

import pytest

from doeff import do
from doeff.cesk import run_sync
from doeff_conductor import (
    CreateWorktree,
    DeleteWorktree,
    CreateIssue,
    GetIssue,
    ResolveIssue,
    Commit,
    WorktreeHandler,
    IssueHandler,
    GitHandler,
    IssueStatus,
    make_scheduled_handler,
)


def _is_git_available() -> bool:
    """Check if git is available."""
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _init_test_repo(path: Path) -> None:
    """Initialize a test git repository."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    # Create initial commit
    (path / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=path,
        check=True,
        capture_output=True,
    )


@pytest.mark.e2e
class TestWorkflowE2E:
    """End-to-end workflow tests."""

    @pytest.fixture
    def test_repo(self, tmp_path: Path) -> Path:
        """Create a test git repository."""
        if not _is_git_available():
            pytest.skip("git not available")

        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        _init_test_repo(repo_path)
        return repo_path

    @pytest.fixture
    def issues_dir(self, tmp_path: Path) -> Path:
        """Create a temp issues directory."""
        issues = tmp_path / "issues"
        issues.mkdir()
        return issues

    def test_issue_lifecycle_workflow(self, issues_dir: Path):
        """Test a simple workflow that creates and resolves an issue."""

        @do
        def issue_lifecycle():
            # Step 1: Create an issue
            issue = yield CreateIssue(
                title="Test Feature",
                body="Implement a test feature",
                labels=("feature", "test"),
            )

            # Step 2: Get it back
            retrieved = yield GetIssue(id=issue.id)
            assert retrieved.title == "Test Feature"
            assert retrieved.status == IssueStatus.OPEN

            # Step 3: Resolve it
            resolved = yield ResolveIssue(
                issue=retrieved,
                pr_url="https://github.com/test/repo/pull/1",
            )
            assert resolved.status == IssueStatus.RESOLVED

            return resolved

        # Create handler
        issue_handler = IssueHandler(issues_dir=issues_dir)

        # Use scheduled handlers with Resume for sync operations
        handlers = {
            CreateIssue: make_scheduled_handler(issue_handler.handle_create_issue),
            GetIssue: make_scheduled_handler(issue_handler.handle_get_issue),
            ResolveIssue: make_scheduled_handler(issue_handler.handle_resolve_issue),
        }

        result = run_sync(issue_lifecycle(), scheduled_handlers=handlers)

        # Verify result - result.result is a Result[T], can be Ok or Err
        # Check if it's an Err
        if result.is_err:
            pytest.fail(f"Workflow failed with error: {result.result.error}")

        # For Ok, get the value
        resolved_issue = result.value
        assert resolved_issue.status == IssueStatus.RESOLVED
        assert resolved_issue.pr_url == "https://github.com/test/repo/pull/1"

        # Verify file was written
        files = list(issues_dir.glob("*.md"))
        assert len(files) == 1

    def test_worktree_create_and_delete(self, test_repo: Path, tmp_path: Path):
        """Test creating and deleting a worktree."""
        worktree_base = tmp_path / "worktrees"
        worktree_base.mkdir()

        @do
        def worktree_workflow():
            # Create a worktree
            env = yield CreateWorktree(suffix="test")

            # Verify it exists
            assert env.path.exists()
            assert (env.path / ".git").exists()

            # Delete it
            deleted = yield DeleteWorktree(env=env, force=True)
            assert deleted

            return env.id

        # Create handler pointing to test repo
        worktree_handler = WorktreeHandler(repo_path=test_repo)
        # Override worktree base for testing
        worktree_handler.worktree_base = worktree_base

        handlers = {
            CreateWorktree: make_scheduled_handler(worktree_handler.handle_create_worktree),
            DeleteWorktree: make_scheduled_handler(worktree_handler.handle_delete_worktree),
        }

        result = run_sync(worktree_workflow(), scheduled_handlers=handlers)
        assert result.is_ok

    def test_worktree_with_commit(self, test_repo: Path, tmp_path: Path):
        """Test creating a worktree and making a commit."""
        worktree_base = tmp_path / "worktrees"
        worktree_base.mkdir()

        @do
        def commit_workflow():
            # Create a worktree
            env = yield CreateWorktree(suffix="feature")

            # Make a change in the worktree
            (env.path / "feature.py").write_text("# New feature\n")

            # Commit the change
            sha = yield Commit(env=env, message="feat: add new feature")

            # Verify commit was made
            assert sha is not None
            assert len(sha) == 40  # Git SHA length

            # Cleanup
            yield DeleteWorktree(env=env, force=True)

            return sha

        worktree_handler = WorktreeHandler(repo_path=test_repo)
        worktree_handler.worktree_base = worktree_base
        git_handler = GitHandler()

        handlers = {
            CreateWorktree: make_scheduled_handler(worktree_handler.handle_create_worktree),
            DeleteWorktree: make_scheduled_handler(worktree_handler.handle_delete_worktree),
            Commit: make_scheduled_handler(git_handler.handle_commit),
        }

        result = run_sync(commit_workflow(), scheduled_handlers=handlers)
        assert result.is_ok
        assert len(result.value) == 40  # Valid git SHA

    def test_full_issue_to_commit_workflow(
        self, test_repo: Path, issues_dir: Path, tmp_path: Path
    ):
        """Test a full workflow: issue -> worktree -> change -> commit -> resolve."""
        worktree_base = tmp_path / "worktrees"
        worktree_base.mkdir()

        @do
        def full_workflow():
            # Step 1: Create issue
            issue = yield CreateIssue(
                title="Add greeting module",
                body="Create a hello.py that prints Hello World",
                labels=("feature",),
            )

            # Step 2: Create worktree for the issue
            env = yield CreateWorktree(issue=issue, suffix="impl")

            # Step 3: Make the change (simulating what an agent would do)
            (env.path / "hello.py").write_text('print("Hello World")\n')

            # Step 4: Commit
            sha = yield Commit(env=env, message=f"feat: {issue.title}")

            # Step 5: Resolve issue (in real workflow, this would be after PR)
            resolved = yield ResolveIssue(
                issue=issue,
                pr_url=f"https://github.com/test/repo/pull/1",
                result=f"Implemented in commit {sha[:7]}",
            )

            # Cleanup
            yield DeleteWorktree(env=env, force=True)

            return {
                "issue_id": issue.id,
                "commit_sha": sha,
                "resolved": resolved.status == IssueStatus.RESOLVED,
            }

        # Set up handlers
        worktree_handler = WorktreeHandler(repo_path=test_repo)
        worktree_handler.worktree_base = worktree_base
        issue_handler = IssueHandler(issues_dir=issues_dir)
        git_handler = GitHandler()

        handlers = {
            CreateIssue: make_scheduled_handler(issue_handler.handle_create_issue),
            GetIssue: make_scheduled_handler(issue_handler.handle_get_issue),
            ResolveIssue: make_scheduled_handler(issue_handler.handle_resolve_issue),
            CreateWorktree: make_scheduled_handler(worktree_handler.handle_create_worktree),
            DeleteWorktree: make_scheduled_handler(worktree_handler.handle_delete_worktree),
            Commit: make_scheduled_handler(git_handler.handle_commit),
        }

        result = run_sync(full_workflow(), scheduled_handlers=handlers)

        # Verify result
        assert result.is_ok
        workflow_result = result.value
        assert workflow_result["issue_id"].startswith("ISSUE-")
        assert len(workflow_result["commit_sha"]) == 40
        assert workflow_result["resolved"] is True


@pytest.mark.e2e
class TestTemplateE2E:
    """End-to-end tests for workflow templates."""

    @pytest.fixture
    def issues_dir(self, tmp_path: Path) -> Path:
        """Create a temp issues directory."""
        issues = tmp_path / "issues"
        issues.mkdir()
        return issues

    def test_template_imports(self):
        """Test that all templates can be imported."""
        from doeff_conductor.templates import (
            basic_pr,
            enforced_pr,
            reviewed_pr,
            multi_agent,
            get_available_templates,
            get_template,
            is_template,
        )

        # Verify templates are registered
        templates = get_available_templates()
        assert "basic_pr" in templates
        assert "enforced_pr" in templates
        assert "reviewed_pr" in templates
        assert "multi_agent" in templates

        # Verify template lookup
        assert is_template("basic_pr")
        assert not is_template("nonexistent")

        # Verify get_template
        func = get_template("basic_pr")
        assert callable(func)

    def test_basic_pr_template_structure(self, issues_dir: Path):
        """Test that basic_pr template returns a Program."""
        from doeff_conductor.templates import basic_pr
        from doeff_conductor.types import Issue

        # Create a test issue
        issue = Issue(
            id="TEST-001",
            title="Test Feature",
            body="Implement test feature",
            status=IssueStatus.OPEN,
        )

        # Get the program
        program = basic_pr(issue)

        # Should be a doeff Program (KleisliProgramCall)
        assert program is not None
        assert hasattr(program, "to_generator")  # All Programs have this method
