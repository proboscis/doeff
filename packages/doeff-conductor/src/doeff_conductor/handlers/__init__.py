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

from doeff import Delegate, Resume, default_handlers, run

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


def _supports_continuation(handler: HandlerProtocol) -> bool:
    """Return True if callable appears to accept (effect, continuation)."""
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
        uses_continuation = _supports_continuation(handler)

        def typed_handler(
            effect,
            k,
            _effect_type=effect_type,
            _handler=handler,
            _uses_continuation=uses_continuation,
        ):
            if isinstance(effect, _effect_type):
                if _uses_continuation:
                    result = _handler(effect, k)
                else:
                    result = yield Resume(k, _handler(effect))
                if inspect.isgenerator(result):
                    return (yield from result)
                return result
            yield Delegate()

        typed_handlers.append(typed_handler)

    return typed_handlers


def production_handlers(
    worktree_handler: WorktreeHandler | None = None,
    issue_handler: IssueHandler | None = None,
    agent_handler: AgentHandler | None = None,
    git_handler: GitHandler | None = None,
) -> dict[type, HandlerProtocol]:
    """Build the default production handler map for all conductor effects."""
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
