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
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from doeff import Effect, Pass, Resume, default_handlers, do, run
from doeff.do import make_doeff_generator

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


def _supports_continuation(handler: Callable[..., Any]) -> bool:
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


def _is_lazy_program_value(value: object) -> bool:
    return bool(getattr(value, "__doeff_do_expr_base__", False) or getattr(
        value, "__doeff_effect_base__", False
    ))


def _coerce_effect_map_handler(
    handlers: Mapping[type[Any], HandlerProtocol],
) -> HandlerProtocol:
    ordered_handlers = tuple(handlers.items())

    @do
    def map_handler(effect: Effect, k: Any):
        for effect_type, effect_handler in ordered_handlers:
            if not isinstance(effect, effect_type):
                continue

            if _supports_continuation(effect_handler):
                result = effect_handler(effect, k)
                if inspect.isgenerator(result):
                    return (yield make_doeff_generator(result))
                if _is_lazy_program_value(result):
                    return (yield result)
                return result

            return (yield Resume(k, effect_handler(effect)))

        yield Pass()

    return map_handler


def make_typed_handlers(
    scheduled_handlers: (
        Sequence[HandlerProtocol]
        | Mapping[type[Any], HandlerProtocol]
        | HandlerProtocol
        | None
    ) = None,
) -> list[HandlerProtocol]:
    """Normalize one or more protocol handlers into a list."""
    if scheduled_handlers is None:
        return []
    if callable(scheduled_handlers):
        return [scheduled_handlers]
    if isinstance(scheduled_handlers, Mapping):
        return [_coerce_effect_map_handler(scheduled_handlers)]
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
    scheduled_handlers: (
        Sequence[HandlerProtocol]
        | Mapping[type[Any], HandlerProtocol]
        | HandlerProtocol
        | None
    ) = None,
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
