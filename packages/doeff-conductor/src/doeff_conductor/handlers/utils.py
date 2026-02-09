"""Handler utilities for doeff-conductor.

Utilities in this module build handler-protocol callables for conductor effect types.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from doeff import Resume

if TYPE_CHECKING:
    from .agent_handler import AgentHandler
    from .git_handler import GitHandler
    from .issue_handler import IssueHandler
    from .worktree_handler import WorktreeHandler

SimpleHandler = Callable[[Any], Any]


def make_scheduled_handler(handler: SimpleHandler) -> Callable[..., Any]:
    """Create a handler-protocol callable from a pure effect handler."""

    def scheduled_handler(effect: Any, k):
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
    worktree_handler: WorktreeHandler | None = None,
    issue_handler: IssueHandler | None = None,
    agent_handler: AgentHandler | None = None,
    git_handler: GitHandler | None = None,
) -> dict[type, Callable[..., Any]]:
    """Build a complete handler map for all conductor effects.

    Args:
        worktree_handler: Custom WorktreeHandler, or None to create default
        issue_handler: Custom IssueHandler, or None to create default
        agent_handler: Custom AgentHandler, or None to create default
        git_handler: Custom GitHandler, or None to create default

    Returns:
        Dict mapping effect types to handler-protocol callables.
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
    from .agent_handler import AgentHandler
    from .git_handler import GitHandler
    from .issue_handler import IssueHandler
    from .worktree_handler import WorktreeHandler

    # Create default handlers if not provided
    wt = worktree_handler or WorktreeHandler()
    iss = issue_handler or IssueHandler()
    agent = agent_handler or AgentHandler()
    git = git_handler or GitHandler()

    return {
        # Worktree effects (blocking - subprocess)
        CreateWorktree: make_blocking_scheduled_handler(wt.handle_create_worktree),
        MergeBranches: make_blocking_scheduled_handler(wt.handle_merge_branches),
        DeleteWorktree: make_blocking_scheduled_handler(wt.handle_delete_worktree),
        # Issue effects (blocking - file I/O)
        CreateIssue: make_blocking_scheduled_handler(iss.handle_create_issue),
        ListIssues: make_blocking_scheduled_handler(iss.handle_list_issues),
        GetIssue: make_blocking_scheduled_handler(iss.handle_get_issue),
        ResolveIssue: make_blocking_scheduled_handler(iss.handle_resolve_issue),
        # Agent effects (blocking - subprocess/network)
        RunAgent: make_blocking_scheduled_handler(agent.handle_run_agent),
        SpawnAgent: make_blocking_scheduled_handler(agent.handle_spawn_agent),
        SendMessage: make_blocking_scheduled_handler(agent.handle_send_message),
        WaitForStatus: make_blocking_scheduled_handler(agent.handle_wait_for_status),
        CaptureOutput: make_blocking_scheduled_handler(agent.handle_capture_output),
        # Git effects (blocking - subprocess)
        Commit: make_blocking_scheduled_handler(git.handle_commit),
        Push: make_blocking_scheduled_handler(git.handle_push),
        CreatePR: make_blocking_scheduled_handler(git.handle_create_pr),
        MergePR: make_blocking_scheduled_handler(git.handle_merge_pr),
    }


__all__ = [
    "default_scheduled_handlers",
    "make_async_scheduled_handler",
    "make_blocking_scheduled_handler",
    "make_blocking_scheduled_handler_with_store",
    "make_scheduled_handler",
    "make_scheduled_handler_with_store",
]
