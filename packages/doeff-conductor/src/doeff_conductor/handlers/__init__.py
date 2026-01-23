"""
Handlers for doeff-conductor effects.

Each handler implements the logic for a category of effects:
- WorktreeHandler: Git worktree operations
- IssueHandler: Issue vault operations
- AgentHandler: Agent session management
- GitHandler: Git operations

Handler utilities:
- make_scheduled_handler: Wrap sync handlers for scheduled_handlers API
- make_async_scheduled_handler: Wrap async handlers
- make_blocking_scheduled_handler: Wrap blocking handlers (runs in thread)

Testing utilities:
- run_sync: Backwards-compatible wrapper for running programs with handlers
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from .worktree_handler import WorktreeHandler
from .issue_handler import IssueHandler
from .agent_handler import AgentHandler
from .git_handler import GitHandler
from .utils import (
    make_scheduled_handler,
    make_scheduled_handler_with_store,
    make_async_scheduled_handler,
    make_blocking_scheduled_handler,
    make_blocking_scheduled_handler_with_store,
    default_scheduled_handlers,
)

if TYPE_CHECKING:
    from doeff.cesk.runtime_result import RuntimeResult
    from doeff.program import Program


def run_sync(
    program: "Program[Any]",
    scheduled_handlers: dict[type, Callable[..., Any]] | None = None,
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> "RuntimeResult[Any]":
    """Run a program synchronously with custom handlers.

    This function provides backwards compatibility with the old doeff.cesk.run_sync()
    function that was removed in favor of the new SyncRuntime class.

    Args:
        program: The program to execute
        scheduled_handlers: Dict mapping effect types to CESK handlers
        env: Optional initial environment
        store: Optional initial store

    Returns:
        RuntimeResult containing the execution outcome

    Example:
        result = run_sync(my_workflow(), scheduled_handlers=handlers)
        if result.is_ok():
            print(result.value)
    """
    from doeff.cesk.runtime import SyncRuntime

    runtime = SyncRuntime(handlers=scheduled_handlers)
    return runtime.run(program, env=env, store=store)


__all__ = [
    # Handlers
    "WorktreeHandler",
    "IssueHandler",
    "AgentHandler",
    "GitHandler",
    # Utilities
    "make_scheduled_handler",
    "make_scheduled_handler_with_store",
    "make_async_scheduled_handler",
    "make_blocking_scheduled_handler",
    "make_blocking_scheduled_handler_with_store",
    "default_scheduled_handlers",
    # Testing utility
    "run_sync",
]
