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

from doeff import WithHandler, default_handlers, do, run


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

    program = WithHandler(mock_handlers(), workflow())
    result = run(program, handlers=default_handlers())

    assert result.value == ("user", "assistant", "done")


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
            return SessionHandle(
                session_name=effect.session_name,
                pane_id="%fake0",
                agent_type=AgentType.CODEX,
                work_dir=Path(effect.config.work_dir),
            )

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

        def handle_sleep(self, _effect):
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
        WithHandler(
            agentic_effectful_handlers(
                workflow_id="wf-test",
                workflow_name="wf-test",
                tmux_handler=fake,
            ),
            workflow(),
        ),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value == "done"
    assert fake.launch_calls == 1
