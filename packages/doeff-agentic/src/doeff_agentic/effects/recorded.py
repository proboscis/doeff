"""Effects on the workflows a run recorded in the state directory.

A workflow run under ``with_agentic_effectful_handlers`` records itself and its agents
under the state directory (``~/.local/state/doeff-agentic``). A monitor — the
``doeff-agentic-tui`` screens — reads and acts on those records with these effects;
``recorded_workflows_handler`` answers them.
"""

from dataclasses import dataclass

from doeff import EffectBase
from doeff_agentic.types import WorkflowInfo

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


__all__ = [
    "AgenticAttachAgent",
    "AgenticCaptureAgentOutput",
    "AgenticFindWorkflow",
    "AgenticListWorkflows",
    "AgenticStopWorkflow",
]
