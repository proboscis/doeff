"""Handlers for doeff-conductor effects."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from doeff import WithHandler, run

from .agent_handler import AgentHandler
from .exec_handler import ExecHandler
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
from .workspace_handler import WorkspaceHandler

if TYPE_CHECKING:
    from doeff import Program, RunResult


HandlerProtocol = Callable[..., Any]


class _ResultFlag:
    """Boolean flag that also supports legacy call syntax."""

    def __init__(self, value: bool) -> None:
        self._value = value

    def __bool__(self) -> bool:
        return self._value

    def __call__(self) -> bool:
        return self._value


@dataclass(frozen=True)
class RunSyncResult:
    """Small compatibility wrapper for conductor tests and callers."""

    value: Any = None
    error: BaseException | None = None

    @property
    def is_ok(self) -> _ResultFlag:
        return _ResultFlag(self.error is None)

    @property
    def is_err(self) -> _ResultFlag:
        return _ResultFlag(self.error is not None)

    @property
    def result(self) -> SimpleNamespace:
        return SimpleNamespace(value=self.value, error=self.error)


def production_handlers(
    workspace_handler: WorkspaceHandler | None = None,
    issue_handler: IssueHandler | None = None,
    agent_handler: AgentHandler | None = None,
    git_handler: GitHandler | None = None,
    exec_handler: ExecHandler | None = None,
) -> HandlerProtocol:
    """Build the default production protocol handler for all conductor effects."""
    return default_scheduled_handlers(
        workspace_handler=workspace_handler,
        issue_handler=issue_handler,
        agent_handler=agent_handler,
        git_handler=git_handler,
        exec_handler=exec_handler,
    )


def run_sync(
    program: "Program[Any]",
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
    *,
    scheduled_handlers: Sequence[HandlerProtocol] | HandlerProtocol | None = None,
) -> "RunResult[Any]":
    """Run a program synchronously with custom handlers."""
    if env is not None or store is not None:
        raise NotImplementedError("run_sync no longer accepts env/store with explicit handlers")

    protocol_handlers: list[HandlerProtocol] = []
    if scheduled_handlers is not None:
        if callable(scheduled_handlers):
            protocol_handlers = [scheduled_handlers]
        else:
            protocol_handlers = list(scheduled_handlers)

    wrapped_program = program
    for handler in reversed(protocol_handlers):
        wrapped_program = WithHandler(handler, wrapped_program)
    try:
        return RunSyncResult(value=run(wrapped_program))
    except Exception as error:
        return RunSyncResult(error=error)


__all__ = [
    "AgentHandler",
    "ExecHandler",
    "GitHandler",
    "IssueHandler",
    "MockConductorRuntime",
    "RunSyncResult",
    "WorkspaceHandler",
    "default_scheduled_handlers",
    "make_async_scheduled_handler",
    "make_blocking_scheduled_handler",
    "make_blocking_scheduled_handler_with_store",
    "make_scheduled_handler",
    "make_scheduled_handler_with_store",
    "mock_handlers",
    "production_handlers",
    "run_sync",
]
