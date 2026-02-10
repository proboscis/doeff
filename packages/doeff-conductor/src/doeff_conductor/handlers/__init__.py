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

import inspect
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from doeff import Delegate, default_handlers, run

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
    from doeff import Program, RunResult


HandlerProtocol = Callable[[Any, Any], Any]


def make_typed_handlers(
    scheduled_handlers: Mapping[type, HandlerProtocol] | None = None,
) -> list[HandlerProtocol]:
    """Convert an effect->handler map into typed VM handlers.

    Each output handler:
    - handles only its declared effect type
    - delegates when the effect type does not match
    """

    typed_handlers: list[HandlerProtocol] = []
    for effect_type, handler in (scheduled_handlers or {}).items():

        def typed_handler(effect, k, _effect_type=effect_type, _handler=handler):
            if isinstance(effect, _effect_type):
                result = _handler(effect, k)
                if inspect.isgenerator(result):
                    return (yield from result)
                return result
            yield Delegate()

        typed_handlers.append(typed_handler)

    return typed_handlers


def run_sync(
    program: Program[Any],
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
    *,
    scheduled_handlers: Mapping[type, HandlerProtocol] | None = None,
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
    # Testing utility
    "run_sync",
]
