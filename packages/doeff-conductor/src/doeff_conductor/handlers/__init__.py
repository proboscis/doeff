"""
Handlers for doeff-conductor effects.

Each handler implements the logic for a category of effects:
- WorktreeHandler: Git worktree operations
- IssueHandler: Issue vault operations
- AgentHandler: Agent session management
- GitHandler: Git operations

Handler utilities:
- make_scheduled_handler
- make_async_scheduled_handler
- make_blocking_scheduled_handler

Execution utilities:
- run_sync: direct synchronous execution with default handlers
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff import default_handlers, run

from .agent_handler import AgentHandler
from .git_handler import GitHandler
from .issue_handler import IssueHandler
from .utils import (
    default_scheduled_handlers,
    make_async_scheduled_handler,
    make_blocking_scheduled_handler,
    make_blocking_scheduled_handler_with_store,
    make_scheduled_handler,
    make_scheduled_handler_with_store,
)
from .worktree_handler import WorktreeHandler

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff.types import RunResult


def run_sync(
    program: Program[Any],
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> RunResult[Any]:
    """Run a program synchronously with custom handlers.

    Args:
        program: The program to execute
        env: Optional initial environment
        store: Optional initial store

    Returns:
        RunResult containing the execution outcome

    Example:
        result = run_sync(my_workflow())
        if result.is_ok():
            print(result.value)
    """
    return run(program, handlers=default_handlers(), env=env, store=store)


__all__ = [
    "AgentHandler",
    "GitHandler",
    "IssueHandler",
    # Handlers
    "WorktreeHandler",
    "default_scheduled_handlers",
    "make_async_scheduled_handler",
    "make_blocking_scheduled_handler",
    "make_blocking_scheduled_handler_with_store",
    # Utilities
    "make_scheduled_handler",
    "make_scheduled_handler_with_store",
    # Testing utility
    "run_sync",
]
