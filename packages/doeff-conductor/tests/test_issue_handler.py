"""Tests for issue handler."""

from pathlib import Path

import pytest
from doeff_conductor.effects.issue import CreateIssue, GetIssue, ListIssues, ResolveIssue
from doeff_conductor.exceptions import IssueNotFoundError
from doeff_conductor.handlers.issue_handler import IssueHandler
from doeff_conductor.types import IssueStatus


class TestIssueHandler:
    """Tests for IssueHandler."""

    @pytest.fixture
    def handler(self, tmp_path: Path) -> IssueHandler:
        """Create handler with temp directory."""
        return IssueHandler(issues_dir=tmp_path)

    def test_create_issue(self, handler: IssueHandler):
        """Test creating an issue."""
        effect = CreateIssue(
            title="Test Issue",
            body="This is the issue body",
            labels=("feature", "test"),
        )

        issue = handler.handle_create_issue(effect)

        assert issue.id.startswith("ISSUE-")
        assert issue.title == "Test Issue"
        assert issue.body == "This is the issue body"
        assert issue.status == IssueStatus.OPEN
        assert "feature" in issue.labels

    def test_create_issue_writes_file(self, handler: IssueHandler):
        """Test that creating an issue writes a file."""
        effect = CreateIssue(title="File Test", body="Body")
        issue = handler.handle_create_issue(effect)

        file_path = handler.issues_dir / f"{issue.id}.md"
        assert file_path.exists()

        content = file_path.read_text()
        assert "File Test" in content
        assert "Body" in content

    def test_get_issue(self, handler: IssueHandler):
        """Test getting an issue by ID."""
        # Create an issue first
        create_effect = CreateIssue(title="Get Test", body="Body")
        created = handler.handle_create_issue(create_effect)

        # Get it back
        get_effect = GetIssue(id=created.id)
        retrieved = handler.handle_get_issue(get_effect)

        assert retrieved.id == created.id
        assert retrieved.title == "Get Test"

    def test_get_issue_not_found(self, handler: IssueHandler):
        """Test getting a non-existent issue."""
        effect = GetIssue(id="ISSUE-NONEXISTENT")

        with pytest.raises(IssueNotFoundError, match="ISSUE-NONEXISTENT"):
            handler.handle_get_issue(effect)

    def test_list_issues_empty(self, handler: IssueHandler):
        """Test listing issues when empty."""
        effect = ListIssues()
        issues = handler.handle_list_issues(effect)

        assert issues == []

    def test_list_issues(self, handler: IssueHandler):
        """Test listing issues."""
        # Create some issues
        handler.handle_create_issue(CreateIssue(title="Issue 1", body="Body 1"))
        handler.handle_create_issue(CreateIssue(title="Issue 2", body="Body 2"))
        handler.handle_create_issue(CreateIssue(title="Issue 3", body="Body 3"))

        effect = ListIssues()
        issues = handler.handle_list_issues(effect)

        assert len(issues) == 3

    def test_list_issues_with_limit(self, handler: IssueHandler):
        """Test listing issues with limit."""
        handler.handle_create_issue(CreateIssue(title="Issue 1", body="Body"))
        handler.handle_create_issue(CreateIssue(title="Issue 2", body="Body"))
        handler.handle_create_issue(CreateIssue(title="Issue 3", body="Body"))

        effect = ListIssues(limit=2)
        issues = handler.handle_list_issues(effect)

        assert len(issues) == 2

    def test_list_issues_with_labels(self, handler: IssueHandler):
        """Test listing issues filtered by labels."""
        handler.handle_create_issue(
            CreateIssue(title="Feature", body="Body", labels=("feature",))
        )
        handler.handle_create_issue(
            CreateIssue(title="Bug", body="Body", labels=("bug",))
        )
        handler.handle_create_issue(
            CreateIssue(title="Both", body="Body", labels=("feature", "bug"))
        )

        effect = ListIssues(labels=("bug",))
        issues = handler.handle_list_issues(effect)

        # Should match "Bug" and "Both"
        assert len(issues) == 2
        for issue in issues:
            assert "bug" in issue.labels

    def test_resolve_issue(self, handler: IssueHandler):
        """Test resolving an issue."""
        # Create an issue
        created = handler.handle_create_issue(
            CreateIssue(title="To Resolve", body="Body")
        )

        # Resolve it
        effect = ResolveIssue(
            issue=created, pr_url="https://github.com/user/repo/pull/123"
        )
        resolved = handler.handle_resolve_issue(effect)

        assert resolved.status == IssueStatus.RESOLVED
        assert resolved.pr_url == "https://github.com/user/repo/pull/123"
        assert resolved.resolved_at is not None

    def test_resolve_issue_updates_file(self, handler: IssueHandler):
        """Test that resolving an issue updates the file."""
        created = handler.handle_create_issue(
            CreateIssue(title="Resolve File Test", body="Body")
        )

        handler.handle_resolve_issue(
            ResolveIssue(issue=created, pr_url="https://example.com/pr")
        )

        file_path = handler.issues_dir / f"{created.id}.md"
        content = file_path.read_text()

        assert "resolved" in content.lower()
        assert "https://example.com/pr" in content
