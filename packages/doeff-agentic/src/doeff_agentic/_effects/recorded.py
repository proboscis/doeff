"""Effects on the workflows a run recorded in the state directory.

A workflow run under ``with_agentic_effectful_handlers`` records itself and its agents
under the state directory (``~/.local/state/doeff-agentic``). A monitor — the
``doeff-agentic`` CLI and the ``doeff-agentic-tui`` screens — reads and acts on those
records with these effects; ``recorded_workflows_handler`` answers them.
"""

from dataclasses import dataclass

from doeff import EffectBase
from doeff_agentic._types import AgenticEnvironmentHandle, WorkflowInfo

from .workflow import AgenticEffectBase


@dataclass(frozen=True, kw_only=True)
class AgenticListWorkflows(AgenticEffectBase, EffectBase[tuple[WorkflowInfo, ...]]):
    """List the recorded workflows, the most recently updated first."""


@dataclass(frozen=True, kw_only=True)
class AgenticFindWorkflow(AgenticEffectBase, EffectBase[WorkflowInfo | None]):
    """The recorded workflow ``workflow_id`` names (its id or a unique prefix of it).

    Answers None when no recorded workflow has that id; raises ``ValueError`` when the
    prefix names more than one.
    """

    workflow_id: str


@dataclass(frozen=True, kw_only=True)
class AgenticCaptureAgentOutput(AgenticEffectBase, EffectBase[str]):
    """The last ``lines`` lines an agent of a recorded workflow shows in its session.

    Answers "" when the agent's session cannot be read (it ended, or no session backend
    is installed).
    """

    workflow_id: str
    agent: str
    lines: int = 100


@dataclass(frozen=True, kw_only=True)
class AgenticStopWorkflow(AgenticEffectBase, EffectBase[tuple[str, ...]]):
    """Stop the agents of a recorded workflow and record the workflow as stopped.

    Answers the names of the agents whose sessions were stopped.
    """

    workflow_id: str


@dataclass(frozen=True, kw_only=True)
class AgenticAttachAgent(AgenticEffectBase, EffectBase[None]):
    """Attach the terminal to an agent's session of a recorded workflow until it detaches."""

    workflow_id: str
    agent: str


@dataclass(frozen=True, kw_only=True)
class AgenticInputRequested(AgenticEffectBase, EffectBase[bool]):
    """Whether the recorded workflow ``workflow_id`` (its full id) waits for the user's input.

    A workflow waits while a ``WaitForUserInput`` it performed has posted its request and
    no answer has come yet.
    """

    workflow_id: str


@dataclass(frozen=True, kw_only=True)
class AgenticAnswerInput(AgenticEffectBase, EffectBase[None]):
    """Answer the user's input the recorded workflow ``workflow_id`` (its full id) waits for."""

    workflow_id: str
    text: str


@dataclass(frozen=True, kw_only=True)
class AgenticSendToAgent(AgenticEffectBase, EffectBase[bool]):
    """Type ``message`` into the session of ``agent`` of a recorded workflow and press Enter.

    The session is the agent's pane when it recorded one, its session otherwise (the
    session ``doeff-{workflow id}-{agent}`` when the agent is not recorded). Answers whether
    the session took the keys.
    """

    workflow_id: str
    agent: str
    message: str


@dataclass(frozen=True, kw_only=True)
class AgenticListEnvironments(
    AgenticEffectBase,
    EffectBase[tuple[tuple[str, AgenticEnvironmentHandle, tuple[str, ...]], ...]],
):
    """The environments the recorded workflows' event logs created.

    Answers one ``(workflow id, environment, names of the sessions bound to it)`` per
    environment, of the workflow ``workflow_id`` names (its id or a unique prefix of it)
    or of every recorded workflow when it is None. Raises ``ValueError`` when the prefix
    names more than one workflow.
    """

    workflow_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class AgenticCleanupEnvironments(AgenticEffectBase, EffectBase[tuple[str, ...]]):
    """Remove every directory under the worktree and copy roots of the environments.

    The roots are ``/tmp/doeff/worktrees`` (each removed as a git worktree too) and
    ``/tmp/doeff/copies``. Every directory there is removed, whether or not a workflow
    still uses it. With ``dry_run`` nothing is removed. Answers the directories removed
    (or that would be removed).
    """

    dry_run: bool = False


__all__ = [
    "AgenticAnswerInput",
    "AgenticAttachAgent",
    "AgenticCaptureAgentOutput",
    "AgenticCleanupEnvironments",
    "AgenticFindWorkflow",
    "AgenticInputRequested",
    "AgenticListEnvironments",
    "AgenticListWorkflows",
    "AgenticSendToAgent",
    "AgenticStopWorkflow",
]
