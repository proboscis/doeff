"""Handlers for doeff-conductor effects."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from doeff import handler as _program_handler
from doeff import run, stacked_handlers
from doeff.program import ProgramHandler
from doeff_conductor.handlers.agent_handler import agent_handler as agent_handler
from doeff_conductor.handlers.exec_handler import exec_handler as exec_handler
from doeff_conductor.handlers.git_handler import git_handler as git_handler
from doeff_conductor.handlers.issue_handler import issue_handler as issue_handler
from doeff_conductor.handlers.journaled_agent import (
    journaled_agent_handler as journaled_agent_handler,
)
from doeff_conductor.handlers.journaled_workspace import (
    journaled_workspace_handler as journaled_workspace_handler,
)
from doeff_conductor.handlers.state_handler import (
    conductor_state_handler as conductor_state_handler,
)
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
from doeff_conductor.handlers.workspace_handler import workspace_handler as workspace_handler
from doeff_conductor.workflow_effect_journal import (
    journaled_workflow_effect_handler as journaled_workflow_effect_handler,
)

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
    workspace_handler: ProgramHandler | None = None,
    issue_handler: ProgramHandler | None = None,
    agent_handler: ProgramHandler | None = None,
    git_handler: ProgramHandler | None = None,
    exec_handler: ProgramHandler | None = None,
    journal_state_dir: str | Path | None = None,
    journal_run_id: str | None = None,
) -> ProgramHandler:
    """Build the default production protocol handler for all conductor effects.

    With a journal state directory or run id, agent effects and workspace creations are
    journaled (``journaled_agent_handler``, ``journaled_workspace_handler`` installed inside the
    handlers they perform the effects to) and ``time!``/``random!`` replay from the run's journal.
    """
    workflow_effect_handler: ProgramHandler = journaled_workflow_effect_handler(
        state_dir=journal_state_dir,
        run_id=journal_run_id,
    )
    answer_effects: ProgramHandler = default_scheduled_handlers(
        workspace_handler=workspace_handler,
        issue_handler=issue_handler,
        agent_handler=agent_handler,
        git_handler=git_handler,
        exec_handler=exec_handler,
        workflow_effect_handler=workflow_effect_handler,
    )
    journaling_active: bool = journal_state_dir is not None or journal_run_id is not None
    if not journaling_active:
        return answer_effects
    return stacked_handlers(
        answer_effects,
        journaled_agent_handler(state_dir=journal_state_dir, run_id=journal_run_id),
        journaled_workspace_handler(
            state_dir=journal_state_dir,
            run_id=journal_run_id or "",
        ),
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
