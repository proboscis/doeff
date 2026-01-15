"""
Handler utilities for doeff-conductor.

Provides adapter functions to wrap simple handlers into ScheduledEffectHandler
functions compatible with the doeff CESK runtime.

Handler signature: (effect, env, store) -> HandlerResult
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeVar

from doeff.runtime import AwaitPayload, Resume, Schedule

if TYPE_CHECKING:
    from doeff.cesk import Environment, Store
    from doeff.runtime import HandlerResult
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
StoreAwareHandler = Callable[[Any, "Environment", "Store"], tuple[Any, "Store"]]


def make_scheduled_handler(
    handler: SimpleHandler,
) -> Callable[..., "HandlerResult"]:
    """Wrap a simple sync handler for fast, in-memory operations.

    USE FOR: Fast, deterministic operations that complete instantly.
    DO NOT USE FOR: File I/O, subprocess, network - use make_blocking_scheduled_handler.

    The handler runs synchronously and returns Resume immediately.
    This blocks the event loop, so only use for truly instant operations.
    """

    def scheduled_handler(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
    ) -> "HandlerResult":
        result = handler(effect)
        return Resume(result, store)

    return scheduled_handler


def make_scheduled_handler_with_store(
    handler: StoreAwareHandler,
) -> Callable[..., "HandlerResult"]:
    """Wrap a store-aware sync handler that can update store.

    USE FOR: Fast operations that need to track state in store.

    Handler receives (effect, env, store) and returns (value, new_store).
    """

    def scheduled_handler(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
    ) -> "HandlerResult":
        value, new_store = handler(effect, env, store)
        return Resume(value, new_store)

    return scheduled_handler


def make_async_scheduled_handler(
    handler: Callable[[Any], Awaitable[Any]],
) -> Callable[..., "HandlerResult"]:
    """Wrap an async handler for native async I/O.

    USE FOR: Operations using async libraries (aiohttp, asyncpg, etc.)
    """

    def scheduled_handler(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
    ) -> "HandlerResult":
        return Schedule(AwaitPayload(handler(effect)), store)

    return scheduled_handler


def make_blocking_scheduled_handler(
    handler: SimpleHandler,
) -> Callable[..., "HandlerResult"]:
    """Wrap a blocking handler to run in a thread pool.

    USE FOR: Anything that can block - subprocess, file I/O, sync network calls.
    This enables true parallelism with Gather.
    """

    def scheduled_handler(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
    ) -> "HandlerResult":
        async def run_in_thread():
            return await asyncio.to_thread(handler, effect)

        return Schedule(AwaitPayload(run_in_thread()), store)

    return scheduled_handler


def make_blocking_scheduled_handler_with_store(
    handler: Callable[[Any, "Environment", "Store"], tuple[Any, "Store"]],
) -> Callable[..., "HandlerResult"]:
    """Wrap a store-aware blocking handler to run in a thread pool.

    USE FOR: Blocking operations that need to track state.
    Handler receives (effect, env, store) and returns (value, new_store).
    """

    def scheduled_handler(
        effect: "EffectBase",
        env: "Environment",
        store: "Store",
    ) -> "HandlerResult":
        async def run_in_thread():
            return await asyncio.to_thread(handler, effect, env, store)

        return Schedule(AwaitPayload(run_in_thread()), store)

    return scheduled_handler


def default_scheduled_handlers(
    worktree_handler: "WorktreeHandler | None" = None,
    issue_handler: "IssueHandler | None" = None,
    agent_handler: "AgentHandler | None" = None,
    git_handler: "GitHandler | None" = None,
) -> dict[type, Callable[..., "HandlerResult"]]:
    """Build a complete handler map for all conductor effects.

    Creates scheduled handlers with appropriate wrapping:
    - Blocking for I/O-bound operations (git, file system, agent)
    - Sync for fast in-memory operations (issue queries)

    Args:
        worktree_handler: Custom WorktreeHandler, or None to create default
        issue_handler: Custom IssueHandler, or None to create default
        agent_handler: Custom AgentHandler, or None to create default
        git_handler: Custom GitHandler, or None to create default

    Returns:
        Dict mapping effect types to scheduled handlers

    Example:
        handlers = default_scheduled_handlers()
        interpreter = ProgramInterpreter(scheduled_handlers=handlers)
        result = await interpreter.run(workflow_program())
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
    "make_scheduled_handler",
    "make_scheduled_handler_with_store",
    "make_async_scheduled_handler",
    "make_blocking_scheduled_handler",
    "make_blocking_scheduled_handler_with_store",
    "default_scheduled_handlers",
]
