"""Testing helpers and mock handlers for doeff-conductor effects."""

import hashlib
import inspect
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doeff_agents.result_validation import validate_result_payload

from doeff import Effect, Gather, Pass, Resume, Spawn, do
from doeff import handler as _install_raw_handler
from doeff_conductor.effects.agent import (
    AgentAttemptExhaustedError,
    AgentEffect,
    AgentValidationErrorKind,
    AgentValidationFailure,
)
from doeff_conductor.effects.dsl import RandomCall, TimeCall
from doeff_conductor.effects.exec import Exec
from doeff_conductor.effects.git import Commit, CreatePR, MergePR, Push
from doeff_conductor.effects.issue import CreateIssue, GetIssue, ListIssues, ResolveIssue
from doeff_conductor.effects.workspace import CreateWorkspace, DeleteWorkspace, MergeWorkspaces
from doeff_conductor.exceptions import IssueNotFoundError
from doeff_conductor.handlers.exec_handler import ExecHandler
from doeff_conductor.types import (
    Issue,
    IssueStatus,
    MergeStatus,
    MergeWorkspacesResult,
    PRHandle,
    Workspace,
)
from doeff_conductor.workflow_effect_journal import JournaledWorkflowEffectHandler

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

        self.workspace_base = self.root / "workspaces"
        self.workspace_base.mkdir(parents=True, exist_ok=True)

        self.issues_dir = self.root / "issues"
        self.issues_dir.mkdir(parents=True, exist_ok=True)

        self._issues: dict[str, Issue] = {}
        # Workspace identity is scoped per repo, mirroring the git handler.
        self._workspaces: dict[tuple[str, str], Workspace] = {}
        self._workspace_paths: dict[tuple[str, str], Path] = {}
        self._agent_scripts: dict[str, list[Any]] = {}
        self._agent_script_indices: dict[str, int] = {}
        self._agent_invocation_counts: dict[str, int] = {}
        self._agent_follow_ups: dict[str, list[str]] = {}
        self._agent_prompts: dict[str, list[str]] = {}
        self._prs: dict[int, PRHandle] = {}

        self._issue_counter = 0
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

    def _ensure_workspace(
        self,
        workspace_id: str,
        *,
        repo: str = "default",
        base_ref: str = "main",
        issue_id: str | None = None,
    ) -> Workspace:
        """Idempotently bind a workspace identity, mirroring the git handler."""
        if not workspace_id:
            raise ValueError("workspace effects require a non-empty workspace_id")
        existing = self._workspaces.get((repo, workspace_id))
        if existing is not None:
            return existing

        workspace_path = self.workspace_base / repo / workspace_id
        workspace_path.mkdir(parents=True, exist_ok=True)
        (workspace_path / ".git").mkdir(exist_ok=True)

        workspace = Workspace(
            id=workspace_id,
            repo=repo,
            ref=f"conductor/{workspace_id}",
            base_ref=base_ref,
            issue_id=issue_id,
            created_at=datetime.now(timezone.utc),
        )
        self._workspaces[(repo, workspace_id)] = workspace
        self._workspace_paths[(repo, workspace_id)] = workspace_path
        return workspace

    def resolve_path(self, workspace: Workspace) -> Path:
        path = self._workspace_paths.get((workspace.repo, workspace.id))
        if path is None:
            raise ValueError(f"Workspace is not materialized: {workspace.id}")
        return path

    def configure_agent_script(self, session_id: str, script: list[Any]) -> None:
        """Configure deterministic artifacts for the schema-validated Agent effect."""
        self._agent_scripts[session_id] = list(script)
        self._agent_script_indices[session_id] = 0

    def agent_follow_ups(self, session_id: str) -> list[str]:
        """Return validation follow-up messages sent for a scripted agent."""
        return list(self._agent_follow_ups.get(session_id, []))

    def agent_prompts(self, session_id: str) -> list[str]:
        """Return worker prompts observed for a scripted agent session."""
        return list(self._agent_prompts.get(session_id, []))

    def agent_invocation_count(self, session_id: str) -> int:
        """Return how many times the stub AgentEffect handler ran."""
        return self._agent_invocation_counts.get(session_id, 0)

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

    def handle_create_workspace(self, effect: CreateWorkspace) -> Workspace:
        return self._ensure_workspace(
            effect.workspace_id,
            repo=effect.repo,
            base_ref=effect.from_ref or "main",
            issue_id=effect.issue.id if effect.issue else None,
        )

    def handle_merge_workspaces(self, effect: MergeWorkspaces) -> MergeWorkspacesResult:
        if not effect.workspaces:
            raise ValueError("No workspaces to merge")

        merged_workspace = self._ensure_workspace(
            effect.workspace_id,
            repo=effect.workspaces[0].repo,
            base_ref=effect.workspaces[0].ref,
        )
        merged_path = self.resolve_path(merged_workspace)

        for workspace in effect.workspaces:
            for source in self.resolve_path(workspace).iterdir():
                if source.name == ".git":
                    continue

                target = merged_path / source.name
                if source.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    target.write_bytes(source.read_bytes())

        return MergeWorkspacesResult(status=MergeStatus.MERGED, workspace=merged_workspace)

    def handle_delete_workspace(self, effect: DeleteWorkspace) -> bool:
        workspace_key = (effect.workspace.repo, effect.workspace.id)
        path = self._workspace_paths.pop(workspace_key, None)
        if path is None:
            return False
        shutil.rmtree(path, ignore_errors=True)
        self._workspaces.pop(workspace_key, None)
        return True

    def handle_exec(self, effect: Exec):
        handler = ExecHandler(
            workspace_resolver=self.resolve_path,
            log_dir=self.root / "exec-logs",
        )
        return handler.handle_exec(effect)

    def handle_agent(self, effect: AgentEffect) -> object:
        session_id = effect.task.session_id
        self._agent_prompts.setdefault(session_id, []).append(effect.task.worker_prompt)
        self._agent_invocation_counts[session_id] = (
            self._agent_invocation_counts.get(session_id, 0) + 1
        )
        attempts = 0
        while attempts <= effect.task.max_retries:
            payload = self._next_agent_payload(session_id)
            if payload is None:
                failure = AgentValidationFailure(
                    kind=AgentValidationErrorKind.ABSENT,
                    message="result artifact is absent",
                )
            else:
                validation_error = validate_result_payload(payload, effect.task.result_schema)
                if validation_error is None:
                    return payload
                failure = AgentValidationFailure(
                    kind=AgentValidationErrorKind.INVALID,
                    message=validation_error,
                )

            if attempts >= effect.task.max_retries:
                raise AgentAttemptExhaustedError(
                    session_id=session_id,
                    attempts=attempts + 1,
                    last_error=failure,
                )

            self._agent_follow_ups.setdefault(session_id, []).append(
                self._agent_retry_message(failure)
            )
            attempts += 1

        raise AssertionError("unreachable agent retry state")

    def _next_agent_payload(self, session_id: str) -> object | None:
        script = self._agent_scripts.get(session_id)
        if not script:
            return {"summary": "mock artifact"}
        index = self._agent_script_indices.get(session_id, 0)
        if index >= len(script):
            return script[-1]
        self._agent_script_indices[session_id] = index + 1
        return script[index]

    def _agent_retry_message(self, failure: AgentValidationFailure) -> str:
        if failure.kind == AgentValidationErrorKind.ABSENT:
            return "No result artifact was produced. Return the required result artifact as JSON."
        return (
            f"The result artifact was invalid: {failure.message}. "
            "Return a corrected result artifact that satisfies the schema."
        )

    def handle_commit(self, effect: Commit) -> str:
        payload = f"{effect.workspace.id}:{effect.workspace.ref}:{effect.message}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def handle_push(self, effect: Push) -> None:
        self.pushed_branches.append(effect.workspace.ref)

    def handle_create_pr(self, effect: CreatePR) -> PRHandle:
        self._pr_counter += 1
        pr = PRHandle(
            url=f"https://github.com/mock/repo/pull/{self._pr_counter}",
            number=self._pr_counter,
            title=effect.title,
            branch=effect.workspace.ref,
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
    """Build a complete mock protocol handler for all conductor effects."""
    active_runtime = runtime or MockConductorRuntime(root=root, workflow_id=workflow_id)
    workflow_effect_handler = JournaledWorkflowEffectHandler(
        state_dir=active_runtime.root,
    )

    handlers: list[tuple[type[Any], ScheduledHandler]] = [
        (CreateWorkspace, make_scheduled_handler(active_runtime.handle_create_workspace)),
        (MergeWorkspaces, make_scheduled_handler(active_runtime.handle_merge_workspaces)),
        (DeleteWorkspace, make_scheduled_handler(active_runtime.handle_delete_workspace)),
        (Exec, make_scheduled_handler(active_runtime.handle_exec)),
        (CreateIssue, make_scheduled_handler(active_runtime.handle_create_issue)),
        (ListIssues, make_scheduled_handler(active_runtime.handle_list_issues)),
        (GetIssue, make_scheduled_handler(active_runtime.handle_get_issue)),
        (ResolveIssue, make_scheduled_handler(active_runtime.handle_resolve_issue)),
        (AgentEffect, make_scheduled_handler(active_runtime.handle_agent)),
        (TimeCall, make_scheduled_handler(workflow_effect_handler.handle_time)),
        (RandomCall, make_scheduled_handler(workflow_effect_handler.handle_random)),
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

    spawn_results: dict[str, Any] = {}
    spawn_counter = 0

    @do
    def handler(effect: Effect, k: Any):
        nonlocal spawn_counter
        if isinstance(effect, Spawn):
            spawn_counter += 1
            task_id = f"mock-spawn-{spawn_counter}"
            result = yield _install_raw_handler(handler)(effect.program)
            spawn_results[task_id] = result
            return (yield Resume(k, task_id))

        if isinstance(effect, Gather):
            results = tuple(spawn_results[task] for task in effect.tasks)
            return (yield Resume(k, results))

        for effect_type, effect_handler in handlers:
            if isinstance(effect, effect_type):
                return (yield effect_handler(effect, k))
        yield Pass(effect, k)

    return _install_raw_handler(handler)


__all__ = [
    "MockConductorRuntime",
    "mock_handlers",
]
