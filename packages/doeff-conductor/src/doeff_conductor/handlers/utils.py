"""
Handler utilities for doeff-conductor.

Provides adapter functions to wrap simple handlers into CESK effect handlers
compatible with the doeff CESK runtime.

Handler signature: (effect, task_state, store) -> FrameResult

Migration from old API:
- Resume(value, store) -> ContinueValue(value, env, store, k)
- Schedule(AwaitPayload(coro), store) -> Blocking handlers now run synchronously
  (use Gather at workflow level for parallelism)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, TypeVar

from doeff.cesk.frames import ContinueValue, ContinueError, FrameResult

if TYPE_CHECKING:
    from doeff.cesk.state import TaskState
    from doeff.cesk.types import Store
    from doeff.types import EffectBase

    from .agent_handler import AgentHandler
    from .git_handler import GitHandler
    from .issue_handler import IssueHandler
    from .worktree_handler import WorktreeHandler

E = TypeVar("E", bound="EffectBase")
R = TypeVar("R")

# Type for handlers that return just value (store unchanged)
SimpleHandler = Callable[[Any], Any]
# Type for handlers that return (value, new_store)
StoreAwareHandler = Callable[[Any, dict[str, Any], "Store"], tuple[Any, "Store"]]


def make_cesk_handler(
    handler: SimpleHandler,
) -> Callable[..., "FrameResult"]:
    """Wrap a simple sync handler for the CESK runtime.

    USE FOR: Fast, deterministic operations that complete instantly.

    The handler runs synchronously and returns ContinueValue immediately.
    For blocking operations (file I/O, subprocess), this WILL block the
    event loop. Use Gather at the workflow level for parallelism.

    Args:
        handler: Function that takes an effect and returns a value.

    Returns:
        CESK-compatible handler function.
    """

    def cesk_handler(
        effect: "EffectBase",
        task_state: "TaskState",
        store: "Store",
    ) -> "FrameResult":
        try:
            result = handler(effect)
            return ContinueValue(
                value=result,
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )
        except Exception as ex:
            return ContinueError(
                error=ex,
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )

    return cesk_handler


def make_cesk_handler_with_store(
    handler: StoreAwareHandler,
) -> Callable[..., "FrameResult"]:
    """Wrap a store-aware sync handler for the CESK runtime.

    USE FOR: Operations that need to track state in store.

    Handler receives (effect, env, store) and returns (value, new_store).

    Args:
        handler: Function that takes (effect, env, store) and returns (value, new_store).

    Returns:
        CESK-compatible handler function.
    """

    def cesk_handler(
        effect: "EffectBase",
        task_state: "TaskState",
        store: "Store",
    ) -> "FrameResult":
        try:
            value, new_store = handler(effect, dict(task_state.env), store)
            return ContinueValue(
                value=value,
                env=task_state.env,
                store=new_store,
                k=task_state.kontinuation,
            )
        except Exception as ex:
            return ContinueError(
                error=ex,
                env=task_state.env,
                store=store,
                k=task_state.kontinuation,
            )

    return cesk_handler


# Backwards compatibility aliases
# Note: In the new CESK architecture, all handlers run synchronously.
# For parallelism, use Gather at the workflow level.
make_scheduled_handler = make_cesk_handler
make_scheduled_handler_with_store = make_cesk_handler_with_store
make_blocking_scheduled_handler = make_cesk_handler
make_blocking_scheduled_handler_with_store = make_cesk_handler_with_store


def make_async_scheduled_handler(
    handler: Callable[[Any], Any],
) -> Callable[..., "FrameResult"]:
    """Wrap an async handler for the CESK runtime.

    DEPRECATED: In the new CESK architecture, async operations should be
    expressed as effects (e.g., Await, Delay) rather than handler-level async.
    This function is provided for backwards compatibility and runs the handler
    synchronously.

    For native async operations:
    1. Have your handler return a value
    2. Use Await effect in your program to handle async work

    Args:
        handler: Function that takes an effect and returns a value.

    Returns:
        CESK-compatible handler function.
    """
    import warnings
    warnings.warn(
        "make_async_scheduled_handler is deprecated. "
        "In the CESK architecture, use Await effects for async operations.",
        DeprecationWarning,
        stacklevel=2,
    )
    return make_cesk_handler(handler)


def default_scheduled_handlers(
    worktree_handler: "WorktreeHandler | None" = None,
    issue_handler: "IssueHandler | None" = None,
    agent_handler: "AgentHandler | None" = None,
    git_handler: "GitHandler | None" = None,
) -> dict[type, Callable[..., "FrameResult"]]:
    """Build a complete handler map for all conductor effects.

    Creates CESK-compatible handlers for all conductor effects.
    Note: In the current CESK architecture, all handlers run synchronously.
    Use Gather at the workflow level for parallelism.

    Args:
        worktree_handler: Custom WorktreeHandler, or None to create default
        issue_handler: Custom IssueHandler, or None to create default
        agent_handler: Custom AgentHandler, or None to create default
        git_handler: Custom GitHandler, or None to create default

    Returns:
        Dict mapping effect types to CESK handlers

    Example:
        handlers = default_scheduled_handlers()
        runtime = AsyncRuntime(handlers=handlers)
        result = await runtime.run(workflow_program())
    """
    from .agent_handler import AgentHandler
    from .git_handler import GitHandler
    from .issue_handler import IssueHandler
    from .worktree_handler import WorktreeHandler

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

    # Create default handlers if not provided
    wt = worktree_handler or WorktreeHandler()
    iss = issue_handler or IssueHandler()
    agent = agent_handler or AgentHandler()
    git = git_handler or GitHandler()

    # Build handler map with appropriate wrapping:
    # - Blocking: subprocess, file I/O, network
    # - Sync: fast in-memory lookups
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
    # New CESK-compatible handlers
    "make_cesk_handler",
    "make_cesk_handler_with_store",
    # Backwards compatibility aliases
    "make_scheduled_handler",
    "make_scheduled_handler_with_store",
    "make_async_scheduled_handler",  # Deprecated
    "make_blocking_scheduled_handler",
    "make_blocking_scheduled_handler_with_store",
    # Default handler factory
    "default_scheduled_handlers",
]
