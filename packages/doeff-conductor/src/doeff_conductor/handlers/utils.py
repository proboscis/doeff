"""Handler utilities for doeff-conductor."""

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from doeff_core_effects.scheduler import CreateExternalPromise, Wait

from doeff import Effect, Pass, Resume, do
from doeff import handler as _install_raw_handler

if TYPE_CHECKING:
    from doeff_conductor.handlers.agent_handler import AgentHandler
    from doeff_conductor.handlers.exec_handler import ExecHandler
    from doeff_conductor.handlers.git_handler import GitHandler
    from doeff_conductor.handlers.issue_handler import IssueHandler
    from doeff_conductor.handlers.journaled_agent import JournaledAgentHandler
    from doeff_conductor.handlers.workspace_handler import WorkspaceHandler
    from doeff_conductor.workflow_effect_journal import JournaledWorkflowEffectHandler

SimpleHandler = Callable[[Any], Any]


def make_scheduled_handler(handler: SimpleHandler) -> Callable[..., Any]:
    """Create a handler-protocol callable from a pure effect handler."""

    @do
    def scheduled_handler(effect: Effect, k: Any):
        return (yield Resume(k, handler(effect)))

    return scheduled_handler


def make_blocking_scheduled_handler(handler: SimpleHandler) -> Callable[..., Any]:
    """Alias for sync blocking handlers."""
    return make_scheduled_handler(handler)


def make_offloaded_scheduled_handler(handler: SimpleHandler) -> Callable[..., Any]:
    """Bridge a blocking handler through the scheduler's ExternalPromise.

    The handler executes in a daemon thread while the cooperative
    scheduler is free to dispatch sibling tasks.  This is the
    **agent-await bridge** required by L-K4-1: an effect handler must
    not perform unbounded blocking I/O synchronously; unbounded waits
    enter only via the scheduler's Await / external-completion path.

    Cancellation: the daemon thread is bounded by the server-side
    timeout (agentd's ``await_budget + RPC_TIMEOUT_MARGIN_SECONDS``).
    If the scheduler cancels the waiting task (e.g. Gather fail-fast),
    the thread runs to completion and the promise completion is a
    harmless no-op on an already-resolved promise.
    """

    @do
    def scheduled_handler(effect: Effect, k: Any):
        promise = yield CreateExternalPromise()

        def _offload(ep=promise, eff=effect):
            try:
                ep.complete(handler(eff))
            except Exception as exc:
                ep.fail(exc)

        threading.Thread(
            target=_offload,
            daemon=True,
            name="agent-await-bridge",
        ).start()

        result = yield Wait(promise.future)
        return (yield Resume(k, result))

    return scheduled_handler


def make_scheduled_handler_with_store(_handler: Callable[..., Any]) -> Callable[..., Any]:
    """Store-aware wrappers were removed in direct handler-protocol migration."""
    raise NotImplementedError("Store-aware scheduled handlers are not supported")


def make_blocking_scheduled_handler_with_store(_handler: Callable[..., Any]) -> Callable[..., Any]:
    """Store-aware wrappers were removed in direct handler-protocol migration."""
    raise NotImplementedError("Store-aware scheduled handlers are not supported")


def make_async_scheduled_handler(
    handler: Callable[[Any], Any],
) -> Callable[..., Any]:
    """Create a sync handler-protocol callable from an async-oriented handler."""
    import warnings

    warnings.warn(
        "make_async_scheduled_handler is deprecated; use Await effects in programs.",
        DeprecationWarning,
        stacklevel=2,
    )
    return make_scheduled_handler(handler)


def default_scheduled_handlers(
    workspace_handler: "WorkspaceHandler | None" = None,
    issue_handler: "IssueHandler | None" = None,
    agent_handler: "AgentHandler | JournaledAgentHandler | None" = None,
    git_handler: "GitHandler | None" = None,
    exec_handler: "ExecHandler | None" = None,
    workflow_effect_handler: "JournaledWorkflowEffectHandler | None" = None,
    create_workspace_override: Callable[[Any], Any] | None = None,
) -> Callable[..., Any]:
    """Build a complete protocol handler for all conductor effects.

    Args:
        workspace_handler: Custom WorkspaceHandler, or None to create default
        issue_handler: Custom IssueHandler, or None to create default
        agent_handler: Custom AgentHandler, or None to create default
        git_handler: Custom GitHandler, or None to create default
        exec_handler: Custom ExecHandler, or None to create default
        workflow_effect_handler: Custom time!/random! journal handler, or None for default

    Returns:
        Handler-protocol callable for all conductor effects.
    """
    from doeff_conductor.effects.agent import AgentEffect
    from doeff_conductor.effects.dsl import RandomCall, TimeCall
    from doeff_conductor.effects.exec import Exec
    from doeff_conductor.effects.git import Commit, CreatePR, MergePR, Push
    from doeff_conductor.effects.issue import CreateIssue, GetIssue, ListIssues, ResolveIssue
    from doeff_conductor.effects.workspace import CreateWorkspace, DeleteWorkspace, MergeWorkspaces
    from doeff_conductor.handlers.agent_handler import AgentHandler
    from doeff_conductor.handlers.exec_handler import ExecHandler
    from doeff_conductor.handlers.git_handler import GitHandler
    from doeff_conductor.handlers.issue_handler import IssueHandler
    from doeff_conductor.handlers.workspace_handler import WorkspaceHandler
    from doeff_conductor.workflow_effect_journal import JournaledWorkflowEffectHandler

    workspace = workspace_handler or WorkspaceHandler()
    iss = issue_handler or IssueHandler()
    agent = agent_handler or AgentHandler(workspace_resolver=workspace.resolve_path)
    git = git_handler or GitHandler(workspace_resolver=workspace.resolve_path)
    exec_gate = exec_handler or ExecHandler(workspace_resolver=workspace.resolve_path)
    workflow_effect = workflow_effect_handler or JournaledWorkflowEffectHandler()

    handlers: tuple[tuple[type[Any], Callable[..., Any]], ...] = (
        (CreateWorkspace, make_blocking_scheduled_handler(
            create_workspace_override or workspace.handle_create_workspace,
        )),
        (MergeWorkspaces, make_blocking_scheduled_handler(workspace.handle_merge_workspaces)),
        (DeleteWorkspace, make_blocking_scheduled_handler(workspace.handle_delete_workspace)),
        (Exec, make_blocking_scheduled_handler(exec_gate.handle_exec)),
        (CreateIssue, make_blocking_scheduled_handler(iss.handle_create_issue)),
        (ListIssues, make_blocking_scheduled_handler(iss.handle_list_issues)),
        (GetIssue, make_blocking_scheduled_handler(iss.handle_get_issue)),
        (ResolveIssue, make_blocking_scheduled_handler(iss.handle_resolve_issue)),
        (AgentEffect, make_offloaded_scheduled_handler(agent.handle_agent)),
        (TimeCall, make_blocking_scheduled_handler(workflow_effect.handle_time)),
        (RandomCall, make_blocking_scheduled_handler(workflow_effect.handle_random)),
        (Commit, make_blocking_scheduled_handler(git.handle_commit)),
        (Push, make_blocking_scheduled_handler(git.handle_push)),
        (CreatePR, make_blocking_scheduled_handler(git.handle_create_pr)),
        (MergePR, make_blocking_scheduled_handler(git.handle_merge_pr)),
    )

    @do
    def handler(effect: Effect, k: Any):
        for effect_type, effect_handler in handlers:
            if isinstance(effect, effect_type):
                return (yield effect_handler(effect, k))
        yield Pass(effect, k)

    return _install_raw_handler(handler)


__all__ = [
    "default_scheduled_handlers",
    "make_async_scheduled_handler",
    "make_blocking_scheduled_handler",
    "make_blocking_scheduled_handler_with_store",
    "make_offloaded_scheduled_handler",
    "make_scheduled_handler",
    "make_scheduled_handler_with_store",
]
