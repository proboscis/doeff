"""Handlers for doeff-conductor effects."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from doeff import handler as _program_handler
from doeff import run
from doeff_conductor.handlers.agent_handler import (
    AgentBackend as AgentBackend,
)
from doeff_conductor.handlers.agent_handler import (
    AgentdAgentBackend,
    AgentHandler,
)
from doeff_conductor.handlers.exec_handler import ExecHandler
from doeff_conductor.handlers.git_handler import GitHandler
from doeff_conductor.handlers.issue_handler import IssueHandler
from doeff_conductor.handlers.journaled_agent import JournaledAgentHandler
from doeff_conductor.handlers.journaled_workspace import JournaledWorkspaceHandler
from doeff_conductor.handlers.testing import MockConductorRuntime as MockConductorRuntime
from doeff_conductor.handlers.testing import mock_handlers as mock_handlers
from doeff_conductor.handlers.utils import (
    default_scheduled_handlers,
)
from doeff_conductor.handlers.utils import (
    make_async_scheduled_handler as make_async_scheduled_handler,
)
from doeff_conductor.handlers.utils import (
    make_blocking_scheduled_handler as make_blocking_scheduled_handler,
)
from doeff_conductor.handlers.utils import (
    make_blocking_scheduled_handler_with_store as make_blocking_scheduled_handler_with_store,
)
from doeff_conductor.handlers.utils import (
    make_scheduled_handler as make_scheduled_handler,
)
from doeff_conductor.handlers.utils import (
    make_scheduled_handler_with_store as make_scheduled_handler_with_store,
)
from doeff_conductor.handlers.workspace_handler import WorkspaceHandler
from doeff_conductor.workflow_effect_journal import JournaledWorkflowEffectHandler

if TYPE_CHECKING:
    from doeff import Program


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
    journal_state_dir: str | Path | None = None,
    journal_run_id: str | None = None,
) -> HandlerProtocol:
    """Build the default production protocol handler for all conductor effects."""
    active_workspace_handler = workspace_handler or WorkspaceHandler()
    resolved_agent_handler = agent_handler or AgentHandler(
        workflow_id=journal_run_id,
        workspace_resolver=active_workspace_handler.resolve_path,
        backend=AgentdAgentBackend(),
    )
    journaling_active: bool = journal_state_dir is not None or journal_run_id is not None
    create_workspace_override = None
    if journaling_active:
        resolved_agent_handler = JournaledAgentHandler(
            resolved_agent_handler.handle_agent,
            state_dir=journal_state_dir,
            run_id=journal_run_id,
        )
        journaled_workspace = JournaledWorkspaceHandler(
            active_workspace_handler.handle_create_workspace,
            state_dir=journal_state_dir,
            run_id=journal_run_id,
            resolve_path=active_workspace_handler.resolve_path,
        )
        create_workspace_override = journaled_workspace.handle_create_workspace
    workflow_effect_handler = JournaledWorkflowEffectHandler(
        state_dir=journal_state_dir,
        run_id=journal_run_id,
    )
    return default_scheduled_handlers(
        workspace_handler=active_workspace_handler,
        issue_handler=issue_handler,
        agent_handler=resolved_agent_handler,
        git_handler=git_handler,
        exec_handler=exec_handler,
        workflow_effect_handler=workflow_effect_handler,
        create_workspace_override=create_workspace_override,
    )


def run_sync(
    program: "Program",
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
    *,
    scheduled_handlers: Sequence[HandlerProtocol] | HandlerProtocol | None = None,
) -> RunSyncResult:
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
        wrapped_program = _program_handler(handler)(wrapped_program)
    try:
        return RunSyncResult(value=run(wrapped_program))
    except Exception as error:
        return RunSyncResult(error=error)
