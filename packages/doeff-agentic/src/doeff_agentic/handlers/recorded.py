"""The handler that answers the effects on recorded workflows (``doeff_agentic.effects.recorded``).

It reads and writes the records under the state directory and reaches the agents'
sessions through the session backend of doeff-agents (the ``legacy`` extra), as the
``doeff-agentic`` CLI does.
"""

from pathlib import Path
from typing import Any

from doeff import Effect, Pass, Resume, handler
from doeff.program import ProgramHandler
from doeff_agentic.api import AgenticAPI
from doeff_agentic.effects.recorded import (
    AgenticAttachAgent,
    AgenticCaptureAgentOutput,
    AgenticFindWorkflow,
    AgenticListWorkflows,
    AgenticStopWorkflow,
)


def recorded_workflows_handler(state_dir: Path | str | None = None) -> ProgramHandler:
    """Answer the recorded-workflow effects from ``state_dir`` (default: the XDG state directory)."""
    api = AgenticAPI(state_dir)

    @handler
    def answer_recorded(effect: Effect, k: Any):
        if isinstance(effect, AgenticListWorkflows):
            return (yield Resume(k, tuple(api.list_workflows())))
        if isinstance(effect, AgenticFindWorkflow):
            return (yield Resume(k, api.get_workflow(effect.workflow_id)))
        if isinstance(effect, AgenticCaptureAgentOutput):
            output = api.get_agent_output(effect.workflow_id, effect.agent, lines=effect.lines)
            return (yield Resume(k, output))
        if isinstance(effect, AgenticStopWorkflow):
            return (yield Resume(k, tuple(api.stop(effect.workflow_id))))
        if isinstance(effect, AgenticAttachAgent):
            api.attach(effect.workflow_id, effect.agent)
            return (yield Resume(k, None))
        return (yield Pass(effect, k))

    return answer_recorded


__all__ = ["recorded_workflows_handler"]
