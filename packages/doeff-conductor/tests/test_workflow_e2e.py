"""Workflow tests for doeff-conductor using WithHandler-based mocks."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doeff_conductor import (
    Commit,
    CreateIssue,
    CreateWorktree,
    DeleteWorktree,
    GetIssue,
    IssueStatus,
    MergeBranches,
    Push,
    ResolveIssue,
)
from doeff_conductor.types import Issue, WorktreeEnv

from doeff import Delegate, Resume, WithHandler, default_handlers, do, run


def _wrap_with_effect_handlers(program: Any, handlers: dict[type, Callable[[Any], Any]]) -> Any:
    wrapped = program
    for effect_type, effect_handler in reversed(list(handlers.items())):

        def typed_handler(effect, k, _effect_type=effect_type, _handler=effect_handler):
            if isinstance(effect, _effect_type):
                return (yield Resume(k, _handler(effect)))
            yield Delegate()

        wrapped = WithHandler(handler=typed_handler, expr=wrapped)
    return wrapped


def _run_with_effect_handlers(program: Any, handlers: dict[type, Callable[[Any], Any]]):
    wrapped = _wrap_with_effect_handlers(program, handlers)
    return run(wrapped, handlers=default_handlers())


class MockConductorRuntime:
    """In-memory + tempdir-backed conductor mock runtime for workflow tests."""

    def __init__(self, root: Path):
        self.worktree_base = root / "worktrees"
        self.worktree_base.mkdir()
        self.issues_dir = root / "issues"
        self.issues_dir.mkdir()

        self._issues: dict[str, Issue] = {}
        self._worktrees: dict[str, WorktreeEnv] = {}
        self._issue_counter = 0
        self._worktree_counter = 0
        self._merge_counter = 0
        self.pushed_branches: list[str] = []

    def _write_issue_file(self, issue: Issue) -> None:
        issue_path = self.issues_dir / f"{issue.id}.md"
        issue_path.write_text(
            "\n".join(
                [
                    f"# {issue.title}",
                    "",
                    "## Status",
                    issue.status.value,
                    "",
                    "## Labels",
                    ", ".join(issue.labels),
                    "",
                    "## Body",
                    issue.body,
                ]
            )
        )

    def _new_worktree(self, branch: str, issue_id: str | None = None) -> WorktreeEnv:
        self._worktree_counter += 1
        env_id = f"env-{self._worktree_counter:03d}"
        worktree_path = self.worktree_base / f"{env_id}-{branch}"
        worktree_path.mkdir(parents=True)
        (worktree_path / ".git").mkdir()

        env = WorktreeEnv(
            id=env_id,
            path=worktree_path,
            branch=branch,
            base_commit="a" * 40,
            issue_id=issue_id,
            created_at=datetime.now(timezone.utc),
        )
        self._worktrees[env_id] = env
        return env

    def handle_create_issue(self, effect: CreateIssue) -> Issue:
        self._issue_counter += 1
        issue_id = f"ISSUE-{self._issue_counter:03d}"
        issue = Issue(
            id=issue_id,
            title=effect.title,
            body=effect.body,
            status=IssueStatus.OPEN,
            labels=effect.labels,
            metadata=effect.metadata or {},
            created_at=datetime.now(timezone.utc),
        )
        self._issues[issue_id] = issue
        self._write_issue_file(issue)
        return issue

    def handle_get_issue(self, effect: GetIssue) -> Issue:
        return self._issues[effect.id]

    def handle_resolve_issue(self, effect: ResolveIssue) -> Issue:
        now = datetime.now(timezone.utc)
        source = self._issues.get(effect.issue.id, effect.issue)
        metadata = dict(source.metadata)
        if effect.result is not None:
            metadata["result"] = effect.result

        resolved = replace(
            source,
            status=IssueStatus.RESOLVED,
            pr_url=effect.pr_url,
            resolved_at=now,
            updated_at=now,
            metadata=metadata,
        )
        self._issues[resolved.id] = resolved
        self._write_issue_file(resolved)
        return resolved

    def handle_create_worktree(self, effect: CreateWorktree) -> WorktreeEnv:
        suffix = effect.name or effect.suffix or f"wt-{self._worktree_counter + 1}"
        branch = effect.name or f"conductor-{suffix}"
        issue_id = effect.issue.id if effect.issue else None
        return self._new_worktree(branch=branch, issue_id=issue_id)

    def handle_delete_worktree(self, effect: DeleteWorktree) -> bool:
        shutil.rmtree(effect.env.path, ignore_errors=True)
        self._worktrees.pop(effect.env.id, None)
        return True

    def handle_commit(self, effect: Commit) -> str:
        payload = f"{effect.env.id}:{effect.env.branch}:{effect.message}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def handle_push(self, effect: Push) -> bool:
        self.pushed_branches.append(effect.env.branch)
        return True

    def handle_merge_branches(self, effect: MergeBranches) -> WorktreeEnv:
        self._merge_counter += 1
        merged_branch = effect.name or f"conductor-merged-{self._merge_counter}"
        merged_env = self._new_worktree(branch=merged_branch)

        for env in effect.envs:
            for source in env.path.iterdir():
                if source.name == ".git":
                    continue

                target = merged_env.path / source.name
                if source.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    target.write_bytes(source.read_bytes())

        return merged_env


class TestWorkflowE2E:
    """Workflow tests that previously required git/OpenCode now use WithHandler mocks."""

    def test_issue_lifecycle_workflow(self, tmp_path: Path):
        runtime = MockConductorRuntime(tmp_path)

        @do
        def issue_lifecycle():
            issue = yield CreateIssue(
                title="Test Feature",
                body="Implement a test feature",
                labels=("feature", "test"),
            )

            retrieved = yield GetIssue(id=issue.id)
            assert retrieved.title == "Test Feature"
            assert retrieved.status == IssueStatus.OPEN

            resolved = yield ResolveIssue(
                issue=retrieved,
                pr_url="https://github.com/test/repo/pull/1",
            )
            assert resolved.status == IssueStatus.RESOLVED

            return resolved

        result = _run_with_effect_handlers(
            issue_lifecycle(),
            {
                CreateIssue: runtime.handle_create_issue,
                GetIssue: runtime.handle_get_issue,
                ResolveIssue: runtime.handle_resolve_issue,
            },
        )

        assert result.is_ok
        resolved_issue = result.value
        assert resolved_issue.status == IssueStatus.RESOLVED
        assert resolved_issue.pr_url == "https://github.com/test/repo/pull/1"
        assert len(list(runtime.issues_dir.glob("*.md"))) == 1

    def test_worktree_create_and_delete(self, tmp_path: Path):
        runtime = MockConductorRuntime(tmp_path)

        @do
        def worktree_workflow():
            env = yield CreateWorktree(suffix="test")
            assert env.path.exists()
            assert (env.path / ".git").exists()

            deleted = yield DeleteWorktree(env=env, force=True)
            assert deleted

            return env.id

        result = _run_with_effect_handlers(
            worktree_workflow(),
            {
                CreateWorktree: runtime.handle_create_worktree,
                DeleteWorktree: runtime.handle_delete_worktree,
            },
        )

        assert result.is_ok
        assert result.value.startswith("env-")

    def test_worktree_with_commit(self, tmp_path: Path):
        runtime = MockConductorRuntime(tmp_path)

        @do
        def commit_workflow():
            env = yield CreateWorktree(suffix="feature")
            (env.path / "feature.py").write_text("# New feature\n")

            sha = yield Commit(env=env, message="feat: add new feature")
            assert len(sha) == 40

            yield DeleteWorktree(env=env, force=True)
            return sha

        result = _run_with_effect_handlers(
            commit_workflow(),
            {
                CreateWorktree: runtime.handle_create_worktree,
                DeleteWorktree: runtime.handle_delete_worktree,
                Commit: runtime.handle_commit,
            },
        )

        assert result.is_ok
        assert len(result.value) == 40

    def test_full_issue_to_commit_workflow(self, tmp_path: Path):
        runtime = MockConductorRuntime(tmp_path)

        @do
        def full_workflow():
            issue = yield CreateIssue(
                title="Add greeting module",
                body="Create a hello.py that prints Hello World",
                labels=("feature",),
            )

            env = yield CreateWorktree(issue=issue, suffix="impl")
            (env.path / "hello.py").write_text('print("Hello World")\n')

            sha = yield Commit(env=env, message=f"feat: {issue.title}")

            resolved = yield ResolveIssue(
                issue=issue,
                pr_url="https://github.com/test/repo/pull/1",
                result=f"Implemented in commit {sha[:7]}",
            )

            yield DeleteWorktree(env=env, force=True)

            return {
                "issue_id": issue.id,
                "commit_sha": sha,
                "resolved": resolved.status == IssueStatus.RESOLVED,
            }

        result = _run_with_effect_handlers(
            full_workflow(),
            {
                CreateIssue: runtime.handle_create_issue,
                GetIssue: runtime.handle_get_issue,
                ResolveIssue: runtime.handle_resolve_issue,
                CreateWorktree: runtime.handle_create_worktree,
                DeleteWorktree: runtime.handle_delete_worktree,
                Commit: runtime.handle_commit,
            },
        )

        assert result.is_ok
        workflow_result = result.value
        assert workflow_result["issue_id"].startswith("ISSUE-")
        assert len(workflow_result["commit_sha"]) == 40
        assert workflow_result["resolved"] is True

    def test_merge_branches_workflow(self, tmp_path: Path):
        runtime = MockConductorRuntime(tmp_path)

        @do
        def merge_workflow():
            env1 = yield CreateWorktree(suffix="feature1")
            (env1.path / "feature1.py").write_text("# Feature 1\n")
            yield Commit(env=env1, message="feat: add feature1")

            env2 = yield CreateWorktree(suffix="feature2")
            (env2.path / "feature2.py").write_text("# Feature 2\n")
            yield Commit(env=env2, message="feat: add feature2")

            merged = yield MergeBranches(envs=[env1, env2])

            assert (merged.path / "feature1.py").exists()
            assert (merged.path / "feature2.py").exists()

            yield DeleteWorktree(env=env1, force=True)
            yield DeleteWorktree(env=env2, force=True)
            yield DeleteWorktree(env=merged, force=True)

            return merged.branch

        result = _run_with_effect_handlers(
            merge_workflow(),
            {
                CreateWorktree: runtime.handle_create_worktree,
                MergeBranches: runtime.handle_merge_branches,
                DeleteWorktree: runtime.handle_delete_worktree,
                Commit: runtime.handle_commit,
            },
        )

        assert result.is_ok
        assert result.value.startswith("conductor-merged-")

    def test_push_to_remote_workflow(self, tmp_path: Path):
        runtime = MockConductorRuntime(tmp_path)

        @do
        def push_workflow():
            env = yield CreateWorktree(suffix="push-test")
            (env.path / "pushed.py").write_text("# Pushed\n")
            yield Commit(env=env, message="feat: push test")
            yield Push(env=env, set_upstream=True)
            yield DeleteWorktree(env=env, force=True)
            return env.branch

        result = _run_with_effect_handlers(
            push_workflow(),
            {
                CreateWorktree: runtime.handle_create_worktree,
                DeleteWorktree: runtime.handle_delete_worktree,
                Commit: runtime.handle_commit,
                Push: runtime.handle_push,
            },
        )

        assert result.is_ok
        assert result.value in runtime.pushed_branches


class TestTemplateE2E:
    """Template loading tests."""

    def test_template_imports(self):
        from doeff_conductor.templates import (
            get_available_templates,
            get_template,
            is_template,
        )

        templates = get_available_templates()
        assert "basic_pr" in templates
        assert "enforced_pr" in templates
        assert "reviewed_pr" in templates
        assert "multi_agent" in templates

        assert is_template("basic_pr")
        assert not is_template("nonexistent")

        func = get_template("basic_pr")
        assert callable(func)

    def test_basic_pr_template_structure(self):
        from doeff_conductor.templates import basic_pr
        from doeff_conductor.types import Issue

        issue = Issue(
            id="TEST-001",
            title="Test Feature",
            body="Implement test feature",
            status=IssueStatus.OPEN,
        )

        program = basic_pr(issue)

        assert program is not None
        assert hasattr(program, "execution_kernel")
