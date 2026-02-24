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

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from doeff import default_handlers, run

from .agent_handler import AgentHandler
from .git_handler import GitHandler
from .issue_handler import IssueHandler
from .testing import MockConductorRuntime, mock_handlers
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
    from doeff import Program, RunResult


HandlerProtocol = Callable[..., Any]


def make_typed_handlers(
    scheduled_handlers: Sequence[HandlerProtocol] | HandlerProtocol | None = None,
) -> list[HandlerProtocol]:
    """Normalize one or more protocol handlers into a list."""
    if scheduled_handlers is None:
        return []
    if callable(scheduled_handlers):
        return [scheduled_handlers]
    return list(scheduled_handlers)


def production_handlers(
    worktree_handler: WorktreeHandler | None = None,
    issue_handler: IssueHandler | None = None,
    agent_handler: AgentHandler | None = None,
    git_handler: GitHandler | None = None,
) -> HandlerProtocol:
    """Build the default production protocol handler for all conductor effects."""
    return default_scheduled_handlers(
        worktree_handler=worktree_handler,
        issue_handler=issue_handler,
        agent_handler=agent_handler,
        git_handler=git_handler,
    )


def run_sync(
    program: Program[Any],
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
    *,
    scheduled_handlers: Sequence[HandlerProtocol] | HandlerProtocol | None = None,
) -> RunResult[Any]:
    """Run a program synchronously with custom handlers.

    Args:
        program: The program to execute
        env: Optional initial environment
        store: Optional initial store
        scheduled_handlers: Optional effect->handler mapping

    Returns:
        RunResult containing the execution outcome

    Example:
        result = run_sync(my_workflow())
        if result.is_ok():
            print(result.value)
    """
    handlers = [*make_typed_handlers(scheduled_handlers), *default_handlers()]
    return run(program, handlers=handlers, env=env, store=store)


__all__ = [
    "AgentHandler",
    "GitHandler",
    "IssueHandler",
    "MockConductorRuntime",
    # Handlers
    "WorktreeHandler",
    "default_scheduled_handlers",
    "make_async_scheduled_handler",
    "make_blocking_scheduled_handler",
    "make_blocking_scheduled_handler_with_store",
    # Utilities
    "make_scheduled_handler",
    "make_scheduled_handler_with_store",
    "make_typed_handlers",
    "mock_handlers",
    "production_handlers",
    # Testing utility
    "run_sync",
]
