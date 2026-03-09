"""Handler utilities for doeff-conductor.

Utilities in this module build handler-protocol callables for conductor effect types.
"""

import asyncio

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from doeff import Await, Effect, Pass, Resume, do

if TYPE_CHECKING:
    from .agent_handler import AgentHandler
    from .git_handler import GitHandler
    from .issue_handler import IssueHandler
    from .worktree_handler import WorktreeHandler

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
    worktree_handler: "WorktreeHandler | None" = None,
    issue_handler: "IssueHandler | None" = None,
    agent_handler: "AgentHandler | None" = None,
    git_handler: "GitHandler | None" = None,
    agentic_handler: Callable[..., Any] | None = None,
) -> Callable[..., Any]:
    """Build a complete protocol handler for all conductor effects.

    Args:
        worktree_handler: Custom WorktreeHandler, or None to create default
        issue_handler: Custom IssueHandler, or None to create default
        agent_handler: Custom AgentHandler, or None to create default
        git_handler: Custom GitHandler, or None to create default

    Returns:
        Handler-protocol callable for all conductor effects.
    """
    # Import effect types
    from ..effects.agent import (
        CaptureOutput,
        RunAgent,
        SendMessage,
        SpawnAgent,
        WaitForStatus,
    )
    from ..effects.git import Commit, CreatePR, MergePR, Push
    from ..effects.issue import CreateIssue, GetIssue, ListIssues, ResolveIssue
    from ..effects.worktree import CreateWorktree, DeleteWorktree, MergeBranches
    from doeff_agentic import (
        AgenticCreateEnvironment,
        AgenticCreateSession,
        AgenticEnvironmentType,
        AgenticGetMessages,
        AgenticGetSessionStatus,
        AgenticSendMessage,
    )
    from doeff_agentic.handlers import production_handlers as agentic_production_handlers
    from .agent_handler import AgentHandler
    from .git_handler import GitHandler
    from .issue_handler import IssueHandler
    from .worktree_handler import WorktreeHandler

    # Create default handlers if not provided
    wt = worktree_handler or WorktreeHandler()
    iss = issue_handler or IssueHandler()
    agent = agent_handler or AgentHandler()
    git = git_handler or GitHandler()
    active_agentic_handler = agentic_handler or agentic_production_handlers()

    handlers: tuple[tuple[type[Any], Callable[..., Any]], ...] = (
        (CreateWorktree, make_blocking_scheduled_handler(wt.handle_create_worktree)),
        (MergeBranches, make_blocking_scheduled_handler(wt.handle_merge_branches)),
        (DeleteWorktree, make_blocking_scheduled_handler(wt.handle_delete_worktree)),
        (CreateIssue, make_blocking_scheduled_handler(iss.handle_create_issue)),
        (ListIssues, make_blocking_scheduled_handler(iss.handle_list_issues)),
        (GetIssue, make_blocking_scheduled_handler(iss.handle_get_issue)),
        (ResolveIssue, make_blocking_scheduled_handler(iss.handle_resolve_issue)),
        (RunAgent, make_blocking_scheduled_handler(agent.handle_run_agent)),
        (SpawnAgent, make_blocking_scheduled_handler(agent.handle_spawn_agent)),
        (SendMessage, make_blocking_scheduled_handler(agent.handle_send_message)),
        (WaitForStatus, make_blocking_scheduled_handler(agent.handle_wait_for_status)),
        (CaptureOutput, make_blocking_scheduled_handler(agent.handle_capture_output)),
        (Commit, make_blocking_scheduled_handler(git.handle_commit)),
        (Push, make_blocking_scheduled_handler(git.handle_push)),
        (CreatePR, make_blocking_scheduled_handler(git.handle_create_pr)),
        (MergePR, make_blocking_scheduled_handler(git.handle_merge_pr)),
    )

    @do
    def handler(effect: Effect, k: Any):
        if agent_handler is None and isinstance(
            effect,
            (
                AgenticCreateEnvironment,
                AgenticCreateSession,
                AgenticSendMessage,
                AgenticGetMessages,
                AgenticGetSessionStatus,
            ),
        ):
            return (yield active_agentic_handler(effect, k))
        if agent_handler is None and isinstance(effect, RunAgent):
            import secrets

            session_name = effect.name or f"agent-{secrets.token_hex(3)}"
            env_handle = yield AgenticCreateEnvironment(
                env_type=AgenticEnvironmentType.SHARED,
                working_dir=str(effect.env.path),
            )
            session = yield AgenticCreateSession(
                name=session_name,
                environment_id=env_handle.id,
                agent=effect.agent_type,
            )
            _ = yield AgenticSendMessage(
                session_id=session.id,
                content=effect.prompt,
                wait=True,
            )
            messages = yield AgenticGetMessages(session_id=session.id)
            value = ""
            for msg in reversed(messages):
                if msg.role == "assistant":
                    value = msg.content
                    break
            return (yield Resume(k, value))
        if agent_handler is None and isinstance(effect, SpawnAgent):
            import secrets

            from ..types import AgentRef

            session_name = effect.name or f"agent-{secrets.token_hex(3)}"
            env_handle = yield AgenticCreateEnvironment(
                env_type=AgenticEnvironmentType.SHARED,
                working_dir=str(effect.env.path),
            )
            session = yield AgenticCreateSession(
                name=session_name,
                environment_id=env_handle.id,
                agent=effect.agent_type,
            )
            _ = yield AgenticSendMessage(
                session_id=session.id,
                content=effect.prompt,
                wait=False,
            )
            value = AgentRef(
                id=session.id,
                name=session_name,
                workflow_id=agent.workflow_id,
                env_id=effect.env.id,
                agent_type=effect.agent_type,
            )
            agent._sessions[session_name] = value
            return (yield Resume(k, value))
        if agent_handler is None and isinstance(effect, SendMessage):
            yield AgenticSendMessage(
                session_id=effect.agent_ref.id,
                content=effect.message,
                wait=effect.wait,
            )
            value = None
            return (yield Resume(k, value))
        if agent_handler is None and isinstance(effect, WaitForStatus):
            import time

            from doeff_agentic import AgenticSessionStatus

            targets = effect.target
            if isinstance(targets, AgenticSessionStatus):
                targets = (targets,)

            deadline = None
            if effect.timeout:
                deadline = time.time() + effect.timeout

            while True:
                status = yield AgenticGetSessionStatus(session_id=effect.agent_ref.id)

                if status in targets:
                    value = status
                    break

                if status.is_terminal() and status not in targets:
                    value = status
                    break

                if deadline and time.time() > deadline:
                    from ..exceptions import AgentTimeoutError

                    raise AgentTimeoutError(
                        agent_id=effect.agent_ref.id,
                        timeout=effect.timeout or 0.0,
                        last_status=str(status),
                    )

                yield Await(asyncio.sleep(effect.poll_interval))
            return (yield Resume(k, value))
        if agent_handler is None and isinstance(effect, CaptureOutput):
            messages = yield AgenticGetMessages(
                session_id=effect.agent_ref.id,
                limit=effect.lines,
            )
            value = "\n\n".join(f"[{msg.role}] {msg.content}" for msg in messages)
            return (yield Resume(k, value))
        for effect_type, effect_handler in handlers:
            if isinstance(effect, effect_type):
                return (yield effect_handler(effect, k))
        yield Pass()

    return handler


__all__ = [
    "default_scheduled_handlers",
    "make_async_scheduled_handler",
    "make_blocking_scheduled_handler",
    "make_blocking_scheduled_handler_with_store",
    "make_scheduled_handler",
    "make_scheduled_handler_with_store",
]
