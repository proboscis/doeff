"""Tests for handler module restructuring and compatibility exports."""


from pathlib import Path

import pytest
from doeff_agentic.effects import (
    AgenticCreateSession,
    AgenticGetMessages,
    AgenticGetSessionStatus,
    AgenticSendMessage,
    RunAgentEffect,
)
from doeff_agentic.handlers import mock_handlers, production_handlers
from doeff_agentic.handlers.production import agentic_effectful_handlers
from doeff_agentic.types import AgentConfig

from doeff import do, run


def test_handlers_init_exports_required_factory_functions() -> None:
    """`doeff_agentic.handlers` exports both required factories."""
    assert callable(production_handlers)
    assert callable(mock_handlers)


def test_production_handlers_factory_returns_protocol_handler() -> None:
    """Production factory returns a protocol handler without side effects at construction time."""
    handler = production_handlers()
    assert callable(handler)


def test_mock_handlers_execute_simple_workflow() -> None:
    """Mock handlers can run a full effectful workflow in tests."""

    @do
    def workflow():
        session = yield AgenticCreateSession(name="reviewer")
        msg = yield AgenticSendMessage(
            session_id=session.id,
            content="Review this patch",
            wait=True,
        )
        messages = yield AgenticGetMessages(session_id=session.id)
        status = yield AgenticGetSessionStatus(session_id=session.id)
        return msg.role, messages[-1].role, status.value

    program = mock_handlers()(workflow())
    result = run(program)

    assert result == ("user", "assistant", "done")


def test_legacy_opencode_and_tmux_modules_still_import() -> None:
    """Backward-compatible top-level module imports still resolve."""
    from doeff_agentic.handlers.opencode import opencode_handler as new_opencode_handler
    from doeff_agentic.handlers.tmux import tmux_handler as new_tmux_handler
    from doeff_agentic.opencode_handler import opencode_handler as old_opencode_handler
    from doeff_agentic.tmux_handler import tmux_handler as old_tmux_handler

    assert old_opencode_handler is new_opencode_handler
    assert old_tmux_handler is new_tmux_handler


def test_legacy_handler_module_reexport_when_optional_dependency_available() -> None:
    """Legacy `doeff_agentic.handler` remains importable when doeff-agents is present."""
    pytest.importorskip("doeff_agents")

    from doeff_agentic.handler import agentic_effectful_handlers as old_export
    from doeff_agentic.handlers.production import (
        agentic_effectful_handlers as new_export,
    )

    assert old_export is new_export


def test_legacy_agentic_handler_accepts_injected_tmux_runtime(tmp_path: Path) -> None:
    from doeff_agents.adapters.base import AgentType
    from doeff_agents.effects import Observation, SessionHandle
    from doeff_agents.monitor import SessionStatus

    class FakeTmuxHandler:
        def __init__(self) -> None:
            self.launch_calls = 0

        def handle_launch(self, effect):
            self.launch_calls += 1
            assert effect.agent_type == AgentType.CODEX
            assert Path(effect.work_dir) == tmp_path
            return SessionHandle(session_id=effect.session_name)

        def handle_monitor(self, _effect):
            return Observation(
                status=SessionStatus.DONE,
                output_changed=True,
                output_snippet="done",
            )

        def handle_capture(self, _effect):
            return "done"

        def handle_send(self, _effect):
            return None

        def handle_stop(self, _effect):
            return None

    fake = FakeTmuxHandler()

    @do
    def workflow():
        return (
            yield RunAgentEffect(
                config=AgentConfig(
                    agent_type="codex",
                    prompt="Review this patch",
                    work_dir=str(tmp_path),
                )
            )
        )

    result = run(
        agentic_effectful_handlers(
                workflow_id="wf-test",
                workflow_name="wf-test",
                state_dir=tmp_path / "state",
                tmux_handler=fake,
            )(workflow()),
    )

    assert result == "done"
    assert fake.launch_calls == 1


def test_legacy_agentic_handler_polls_with_time_delay(tmp_path: Path) -> None:
    """While the agent is still running, RunAgent waits with doeff_time Delay."""
    from doeff_agents.effects import Observation, SessionHandle
    from doeff_agents.monitor import SessionStatus
    from doeff_core_effects.scheduler import scheduled
    from doeff_time import sync_time_handler

    statuses = [SessionStatus.RUNNING, SessionStatus.RUNNING, SessionStatus.DONE]
    slept: list[float] = []

    class FakeTmuxHandler:
        def handle_launch(self, effect):
            return SessionHandle(session_id=effect.session_name)

        def handle_monitor(self, _effect):
            status = statuses.pop(0) if statuses else SessionStatus.DONE
            return Observation(status=status, output_changed=False, output_snippet="")

        def handle_capture(self, _effect):
            return "finished"

        def handle_send(self, _effect):
            return None

        def handle_stop(self, _effect):
            return None

    @do
    def workflow():
        return (
            yield RunAgentEffect(
                config=AgentConfig(agent_type="codex", prompt="p", work_dir=str(tmp_path)),
                poll_interval=0.25,
            )
        )

    program = agentic_effectful_handlers(
        workflow_id="wf-poll",
        workflow_name="wf-poll",
        state_dir=tmp_path / "state",
        tmux_handler=FakeTmuxHandler(),
    )(workflow())
    result = run(scheduled(sync_time_handler(sleep=slept.append)(program)))

    assert result == "finished"
    # Status updates also call handle_monitor, so the exact count of polls is not pinned.
    assert slept
    assert all(seconds == 0.25 for seconds in slept)


def test_legacy_agentic_handler_refuses_resume_and_profile(tmp_path: Path) -> None:
    """Launch has no resume / profile any more; asking for them is an error, not a silent drop."""
    from doeff_agentic.exceptions import AgenticUnsupportedOperationError

    for config in (
        AgentConfig(agent_type="codex", prompt="p", work_dir=str(tmp_path), resume=True),
        AgentConfig(agent_type="codex", prompt="p", work_dir=str(tmp_path), profile="work"),
    ):

        @do
        def workflow(config=config):
            return (yield RunAgentEffect(config=config))

        with pytest.raises(AgenticUnsupportedOperationError):
            run(
                agentic_effectful_handlers(
                    workflow_id="wf-refuse",
                    workflow_name="wf-refuse",
                    state_dir=tmp_path / "state",
                    tmux_handler=object(),
                )(workflow())
            )


def test_mock_handlers_pass_through_unrelated_effects() -> None:
    """Effects the agentic mock does not own reach the handlers outside it."""
    from doeff_core_effects import reader

    from doeff import Ask

    @do
    def workflow():
        session = yield AgenticCreateSession(name="reviewer")
        base = yield Ask("base")
        return session.name, base

    result = run(reader(env={"base": 7})(mock_handlers()(workflow())))
    assert result == ("reviewer", 7)
