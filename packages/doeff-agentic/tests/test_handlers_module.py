"""Tests for handler module restructuring and compatibility exports."""

from __future__ import annotations

import pytest
from doeff_agentic.effects import (
    AgenticCreateSession,
    AgenticGetMessages,
    AgenticGetSessionStatus,
    AgenticSendMessage,
)
from doeff_agentic.handlers import mock_handlers, production_handlers

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
