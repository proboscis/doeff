"""Handler utilities for doeff-conductor."""

from collections.abc import Callable
from typing import Any

from doeff_git import production_handlers as _git_production_handlers

from doeff import Effect, Resume, do, stacked_handlers
from doeff import handler as _as_handler
from doeff.program import ProgramHandler
from doeff_conductor._handlers._agent_handler import agent_handler as _agent_handler
from doeff_conductor._handlers._exec_handler import exec_handler as _exec_handler
from doeff_conductor._handlers._git_handler import git_handler as _git_handler
from doeff_conductor._handlers._issue_handler import issue_handler as _issue_handler
from doeff_conductor._handlers._offload import offloaded_call as _offloaded_call
from doeff_conductor._handlers._workspace_handler import workspace_handler as _workspace_handler
from doeff_conductor._workflow_effect_journal import (
    journaled_workflow_effect_handler as _journaled_workflow_effect_handler,
)

SimpleHandler = Callable[[Any], Any]


def _scheduled_body(handler: SimpleHandler) -> Callable[..., Any]:
    """The ``(effect, k)`` body that resumes ``k`` with ``handler(effect)``.

    Not a handler (it has no handler marker and answers whatever effect it is given): a handler
    that picks the body by effect type calls it with ``(effect, k)``, as ``mock_handlers`` does, and
    ``make_blocking_scheduled_handler`` marks it as a handler that answers every effect.
    """

    @do
    def scheduled_body(effect: Effect, k: Any):
        return (yield Resume(k, handler(effect)))

    return scheduled_body


def make_blocking_scheduled_handler(handler: SimpleHandler) -> ProgramHandler:
    """The handler that answers every effect with ``handler(effect)``, blocking while it runs.

    A handler factory (ADR-DOE-API-001 R3), applied like every handler:
    ``make_blocking_scheduled_handler(fn)(program)``. The handler answers every effect the program
    performs and passes none outward, so ``fn`` must answer each one. ``fn`` holds the scheduler's
    thread while it runs: a blocking call such as an agent's await goes through
    ``_offloaded_scheduled_body`` instead (L-K4-1).
    """
    return _as_handler(_scheduled_body(handler))


def _offloaded_scheduled_body(handler: SimpleHandler) -> Callable[..., Any]:
    """The ``(effect, k)`` body that bridges a blocking handler through the scheduler's
    ExternalPromise (``offloaded_call``).

    The handler executes in a daemon thread while the cooperative scheduler is free to
    dispatch sibling tasks — the agent-await bridge required by L-K4-1. Like
    ``_scheduled_body``, it is called with ``(effect, k)`` from a handler that picks it by
    effect type.
    """

    @do
    def offloaded_scheduled_body(effect: Effect, k: Any):
        result = yield _offloaded_call(lambda: handler(effect))
        return (yield Resume(k, result))

    return offloaded_scheduled_body


def make_scheduled_handler_with_store(_handler: Callable[..., Any]) -> Callable[..., Any]:
    """Store-aware wrappers were removed in direct handler-protocol migration."""
    raise NotImplementedError("Store-aware scheduled handlers are not supported")


def make_blocking_scheduled_handler_with_store(_handler: Callable[..., Any]) -> Callable[..., Any]:
    """Store-aware wrappers were removed in direct handler-protocol migration."""
    raise NotImplementedError("Store-aware scheduled handlers are not supported")


def make_async_scheduled_handler(
    handler: Callable[[Any], Any],
) -> ProgramHandler:
    """Deprecated: ``make_blocking_scheduled_handler(handler)``, with a DeprecationWarning.

    A handler factory (ADR-DOE-API-001 R3): ``make_async_scheduled_handler(fn)(program)``. Await
    effects in the program replace it.
    """
    import warnings

    warnings.warn(
        "make_async_scheduled_handler is deprecated; use Await effects in programs.",
        DeprecationWarning,
        stacklevel=2,
    )
    return _as_handler(_scheduled_body(handler))


def default_scheduled_handlers(
    workspace_handler: ProgramHandler | None = None,
    issue_handler: ProgramHandler | None = None,
    agent_handler: ProgramHandler | None = None,
    git_handler: ProgramHandler | None = None,
    exec_handler: ProgramHandler | None = None,
    workflow_effect_handler: ProgramHandler | None = None,
) -> ProgramHandler:
    """Stack the handlers that answer every conductor effect, outermost first.

    The workspace handler sits outside the agent, git and exec handlers, which ask it where a
    workspace is materialized; doeff-git's handlers sit outermost and run the git and gh commands
    the conductor git handler performs.

    Args:
        workspace_handler: Custom workspace handler, or None for ``workspace_handler()``
        issue_handler: Custom issue handler, or None for ``issue_handler()``
        agent_handler: Custom agent handler, or None for ``agent_handler``
        git_handler: Custom git handler, or None for ``git_handler``
        exec_handler: Custom exec handler, or None for ``exec_handler()``
        workflow_effect_handler: Custom time!/random! handler, or None for
            ``journaled_workflow_effect_handler()``

    Returns:
        One handler for all conductor effects; other effects pass to the outer handlers.
    """
    return stacked_handlers(
        _git_production_handlers,
        workspace_handler or _workspace_handler(),
        issue_handler or _issue_handler(),
        agent_handler or _agent_handler,
        git_handler or _git_handler,
        exec_handler or _exec_handler(),
        workflow_effect_handler or _journaled_workflow_effect_handler(),
    )
