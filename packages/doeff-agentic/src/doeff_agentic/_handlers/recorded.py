"""The handler that answers the effects on recorded workflows (``doeff_agentic._effects.recorded``).

It reads and writes the records under the state directory, reaches the agents'
sessions through the session backend of doeff-agents (the ``legacy`` extra) and removes
the environments' directories, as the ``doeff-agentic`` CLI and TUI ask.
"""

from pathlib import Path
from typing import Any

from doeff import Effect, Pass, Resume, handler
from doeff.program import ProgramHandler
from doeff_agentic._api import AgenticAPI
from doeff_agentic._effects.recorded import (
    AgenticAnswerInput,
    AgenticAttachAgent,
    AgenticCaptureAgentOutput,
    AgenticCleanupEnvironments,
    AgenticFindWorkflow,
    AgenticInputRequested,
    AgenticListEnvironments,
    AgenticListWorkflows,
    AgenticSendToAgent,
    AgenticStopWorkflow,
)


def recorded_workflows_handler(state_dir: Path | str | None = None) -> ProgramHandler:
    """Answer the recorded-workflow effects from ``state_dir`` (default: the XDG state directory)."""
    api = AgenticAPI(state_dir)

    @handler
    def answer_recorded(effect: Effect, k: Any):
        answer: object
        match effect:
            case AgenticListWorkflows():
                answer = tuple(api.list_workflows())
            case AgenticFindWorkflow():
                answer = api.get_workflow(effect.workflow_id)
            case AgenticCaptureAgentOutput():
                answer = api.get_agent_output(effect.workflow_id, effect.agent, lines=effect.lines)
            case AgenticStopWorkflow():
                answer = tuple(api.stop(effect.workflow_id))
            case AgenticAttachAgent():
                answer = api.attach(effect.workflow_id, effect.agent)
            case AgenticInputRequested():
                answer = api.input_requested(effect.workflow_id)
            case AgenticAnswerInput():
                answer = api.answer_input(effect.workflow_id, effect.text)
            case AgenticSendToAgent():
                answer = api.send_to_agent(effect.workflow_id, effect.agent, effect.message)
            case AgenticListEnvironments():
                answer = tuple(api.list_environments(effect.workflow_id))
            case AgenticCleanupEnvironments():
                answer = tuple(api.cleanup_environments(dry_run=effect.dry_run))
            case _:
                return (yield Pass(effect, k))
        return (yield Resume(k, answer))

    return answer_recorded


__all__ = ["recorded_workflows_handler"]
