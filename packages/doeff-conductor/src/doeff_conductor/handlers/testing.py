"""Testing helpers and mock handlers for doeff-conductor effects."""

from __future__ import annotations

import hashlib
import inspect
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doeff import Effect, Pass, do
from doeff.do import make_doeff_generator
from doeff_conductor.effects.agent import (
    CaptureOutput,
    RunAgent,
    SendMessage,
    SpawnAgent,
    WaitForStatus,
)
from doeff_conductor.effects.git import Commit, CreatePR, MergePR, Push
from doeff_conductor.effects.issue import CreateIssue, GetIssue, ListIssues, ResolveIssue
from doeff_conductor.effects.worktree import CreateWorktree, DeleteWorktree, MergeBranches
from doeff_conductor.exceptions import IssueNotFoundError
from doeff_conductor.types import (
    AgentRef,
    Issue,
    IssueStatus,
    PRHandle,
    WorktreeEnv,
)

from .utils import make_scheduled_handler

ScheduledHandler = Callable[..., Any]


def _supports_continuation(handler: Callable[..., Any]) -> bool:
    """Return True if the callable appears to accept (effect, continuation)."""
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return False

    params = tuple(signature.parameters.values())
    if any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in params):
        return True

    positional = [
        param
        for param in params
        if param.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    return len(positional) >= 2


def _is_lazy_program_value(value: object) -> bool:
    return bool(getattr(value, "__doeff_do_expr_base__", False) or getattr(
        value, "__doeff_effect_base__", False
    ))


def _done_status() -> Any:
    """Return AgenticSessionStatus.DONE when available, otherwise string fallback."""
    try:
        from doeff_agentic import AgenticSessionStatus

        return AgenticSessionStatus.DONE
    except Exception:
        return "done"


class MockConductorRuntime:
    """In-memory + filesystem-backed mock runtime for conductor tests."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        workflow_id: str = "mock-workflow",
    ) -> None:
        self._owned_root = root is None
        base_root = root or Path(tempfile.mkdtemp(prefix="doeff-conductor-mock-"))
        self.root = base_root
        self.root.mkdir(parents=True, exist_ok=True)

        self.workflow_id = workflow_id

        self.worktree_base = self.root / "worktrees"
        self.worktree_base.mkdir(parents=True, exist_ok=True)

        self.issues_dir = self.root / "issues"
        self.issues_dir.mkdir(parents=True, exist_ok=True)

        self._issues: dict[str, Issue] = {}
        self._worktrees: dict[str, WorktreeEnv] = {}
        self._agents: dict[str, AgentRef] = {}
        self._agent_statuses: dict[str, Any] = {}
        self._agent_messages: dict[str, list[tuple[str, str]]] = {}
        self._prs: dict[int, PRHandle] = {}

        self._issue_counter = 0
        self._worktree_counter = 0
        self._merge_counter = 0
        self._agent_counter = 0
        self._pr_counter = 0
        self.pushed_branches: list[str] = []

    def close(self) -> None:
        """Cleanup temporary resources owned by this runtime."""
        if self._owned_root:
            shutil.rmtree(self.root, ignore_errors=True)

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
        worktree_path.mkdir(parents=True, exist_ok=True)
        (worktree_path / ".git").mkdir(exist_ok=True)

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

    def _agent_response(self, prompt: str) -> str:
        prompt_lower = prompt.lower()
        if "fix" in prompt_lower:
            return "Applied fixes and validated updates."
        if "review" in prompt_lower:
            return "Review complete: identified and documented findings."
        if "test" in prompt_lower:
            return "All tests passed successfully."
        if "implement" in prompt_lower:
            return "Implementation complete with requested changes."
        return f"Mock response: {prompt[:80]}"

    def handle_create_issue(self, effect: CreateIssue) -> Issue:
        self._issue_counter += 1
        issue_id = f"ISSUE-{self._issue_counter:03d}"
        now = datetime.now(timezone.utc)
        issue = Issue(
            id=issue_id,
            title=effect.title,
            body=effect.body,
            status=IssueStatus.OPEN,
            labels=effect.labels,
            metadata=effect.metadata or {},
            created_at=now,
        )
        self._issues[issue_id] = issue
        self._write_issue_file(issue)
        return issue

    def handle_list_issues(self, effect: ListIssues) -> list[Issue]:
        issues = list(self._issues.values())
        if effect.status is not None:
            issues = [issue for issue in issues if issue.status == effect.status]
        if effect.labels:
            issues = [
                issue for issue in issues if any(label in issue.labels for label in effect.labels)
            ]
        issues.sort(key=lambda issue: issue.created_at, reverse=True)
        if effect.limit is not None:
            issues = issues[: effect.limit]
        return issues

    def handle_get_issue(self, effect: GetIssue) -> Issue:
        issue = self._issues.get(effect.id)
        if issue is None:
            raise IssueNotFoundError(effect.id)
        return issue

    def handle_resolve_issue(self, effect: ResolveIssue) -> Issue:
        source = self._issues.get(effect.issue.id, effect.issue)
        now = datetime.now(timezone.utc)
        metadata = dict(source.metadata)
        if effect.result is not None:
            metadata["result"] = effect.result

        resolved = replace(
            source,
            status=IssueStatus.RESOLVED,
            pr_url=effect.pr_url or source.pr_url,
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

    def handle_merge_branches(self, effect: MergeBranches) -> WorktreeEnv:
        if not effect.envs:
            raise ValueError("No environments to merge")

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

    def handle_delete_worktree(self, effect: DeleteWorktree) -> bool:
        shutil.rmtree(effect.env.path, ignore_errors=True)
        self._worktrees.pop(effect.env.id, None)
        return True

    def handle_run_agent(self, effect: RunAgent) -> str:
        return self._agent_response(effect.prompt)

    def handle_spawn_agent(self, effect: SpawnAgent) -> AgentRef:
        self._agent_counter += 1
        session_id = f"session-{self._agent_counter:03d}"
        name = effect.name or f"agent-{self._agent_counter:03d}"
        output = self._agent_response(effect.prompt)

        agent_ref = AgentRef(
            id=session_id,
            name=name,
            workflow_id=self.workflow_id,
            env_id=effect.env.id,
            agent_type=effect.agent_type,
        )
        self._agents[session_id] = agent_ref
        self._agent_statuses[session_id] = _done_status()
        self._agent_messages[session_id] = [("user", effect.prompt), ("assistant", output)]
        return agent_ref

    def handle_send_message(self, effect: SendMessage) -> None:
        session_id = effect.agent_ref.id
        response = self._agent_response(effect.message)
        messages = self._agent_messages.setdefault(session_id, [])
        messages.append(("user", effect.message))
        messages.append(("assistant", response))
        self._agent_statuses[session_id] = _done_status()

    def handle_wait_for_status(self, effect: WaitForStatus) -> Any:
        return self._agent_statuses.get(effect.agent_ref.id, _done_status())

    def handle_capture_output(self, effect: CaptureOutput) -> str:
        messages = self._agent_messages.get(effect.agent_ref.id, [])
        if effect.lines <= 0:
            return ""
        limited_messages = messages[-effect.lines :]
        return "\n\n".join(f"[{role}] {content}" for role, content in limited_messages)

    def handle_commit(self, effect: Commit) -> str:
        payload = f"{effect.env.id}:{effect.env.branch}:{effect.message}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def handle_push(self, effect: Push) -> None:
        self.pushed_branches.append(effect.env.branch)

    def handle_create_pr(self, effect: CreatePR) -> PRHandle:
        self._pr_counter += 1
        pr = PRHandle(
            url=f"https://github.com/mock/repo/pull/{self._pr_counter}",
            number=self._pr_counter,
            title=effect.title,
            branch=effect.env.branch,
            target=effect.target,
            status="open",
            created_at=datetime.now(timezone.utc),
        )
        self._prs[self._pr_counter] = pr
        return pr

    def handle_merge_pr(self, effect: MergePR) -> None:
        pr = self._prs.get(effect.pr.number)
        if pr is None:
            return
        self._prs[effect.pr.number] = PRHandle(
            url=pr.url,
            number=pr.number,
            title=pr.title,
            branch=pr.branch,
            target=pr.target,
            status="merged",
            created_at=pr.created_at,
        )


def mock_handlers(
    runtime: MockConductorRuntime | None = None,
    *,
    overrides: Mapping[type[Any], Callable[..., Any]] | None = None,
    root: Path | None = None,
    workflow_id: str = "mock-workflow",
) -> ScheduledHandler:
    """Build a complete mock protocol handler for all conductor effects.

    Args:
        runtime: Optional runtime instance to keep state between invocations.
        overrides: Optional per-effect handlers to replace defaults.
        root: Optional root directory for created mock runtime state.
        workflow_id: Workflow ID for mock agent references.
    """
    active_runtime = runtime or MockConductorRuntime(root=root, workflow_id=workflow_id)

    handlers: list[tuple[type[Any], ScheduledHandler]] = [
        (CreateWorktree, make_scheduled_handler(active_runtime.handle_create_worktree)),
        (MergeBranches, make_scheduled_handler(active_runtime.handle_merge_branches)),
        (DeleteWorktree, make_scheduled_handler(active_runtime.handle_delete_worktree)),
        (CreateIssue, make_scheduled_handler(active_runtime.handle_create_issue)),
        (ListIssues, make_scheduled_handler(active_runtime.handle_list_issues)),
        (GetIssue, make_scheduled_handler(active_runtime.handle_get_issue)),
        (ResolveIssue, make_scheduled_handler(active_runtime.handle_resolve_issue)),
        (RunAgent, make_scheduled_handler(active_runtime.handle_run_agent)),
        (SpawnAgent, make_scheduled_handler(active_runtime.handle_spawn_agent)),
        (SendMessage, make_scheduled_handler(active_runtime.handle_send_message)),
        (WaitForStatus, make_scheduled_handler(active_runtime.handle_wait_for_status)),
        (CaptureOutput, make_scheduled_handler(active_runtime.handle_capture_output)),
        (Commit, make_scheduled_handler(active_runtime.handle_commit)),
        (Push, make_scheduled_handler(active_runtime.handle_push)),
        (CreatePR, make_scheduled_handler(active_runtime.handle_create_pr)),
        (MergePR, make_scheduled_handler(active_runtime.handle_merge_pr)),
    ]

    for effect_type, override_handler in (overrides or {}).items():
        normalized = (
            override_handler
            if _supports_continuation(override_handler)
            else make_scheduled_handler(override_handler)  # type: ignore[arg-type]
        )
        handlers.insert(0, (effect_type, normalized))

    @do
    def handler(effect: Effect, k: Any):
        for effect_type, effect_handler in handlers:
            if isinstance(effect, effect_type):
                result = effect_handler(effect, k)
                if inspect.isgenerator(result):
                    return (yield make_doeff_generator(result))
                if _is_lazy_program_value(result):
                    return (yield result)
                return result
        yield Pass()

    return handler


__all__ = [
    "MockConductorRuntime",
    "mock_handlers",
]
