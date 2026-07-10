"""In-process report_result data channel (ADR 0035 parity for the tmux path).

Regression context (2026-07-10, nakagawa SBI recon readiness): the agent
printed a correct 585-char single-line result JSON between the marker lines,
the Claude Code TUI wrapped it at the 80-column pane width, and the verbatim
parse (wrap-repair deleted by ADR 0035 R5) failed with "Invalid control
character" on every poll until the caller's no-result budget was exhausted.
The typed fix: schema sessions that already talk to the in-VM MCP server get
a ``report_result`` tool on that server, and ``handle_await_result`` reads the
reported payload result-first, so the result never rides rendered terminal
bytes.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from doeff import run
from doeff_agents import AgentType
from doeff_agents.effects import AgentSpec, AwaitResultEffect, AwaitStatus, SessionHandle
from doeff_agents.handlers.production import (
    REPORT_RESULT_TOOL_NAME,
    RESULT_BLOCK_BEGIN,
    RESULT_BLOCK_END,
    SessionState,
    TmuxAgentHandler,
    _launch_effect_from_spec,
    get_adapter,
    make_report_result_tool,
    spec_uses_report_result_transport,
)
from doeff_agents.adapters.base import AgentSessionLifecycle
from doeff_agents.mcp_server import McpToolServer

RESULT_SCHEMA = {
    "type": "object",
    "required": ["status", "reason"],
    "properties": {
        "status": {"type": "string"},
        "reason": {"type": "string"},
    },
    "additionalProperties": True,
}

# Long single-line payload of the shape that wrapped at pane width 80.
LONG_PAYLOAD = {
    "status": "readiness_blocker",
    "reason": (
        "sbi-query-shortable-inventory returned ui_mismatch (missing_symbols "
        "9433.T; body '19:00受付再開 readiness injected unexpected SBI WebUI "
        "state'); broker inventory state unusable for readiness and recovery "
        "is live-trade-only"
    ),
}


def _wrapped_marker_block(payload: dict, width: int = 78) -> str:
    """Render the marker block the way an 80-column TUI shows it."""
    oneline = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    wrapped = "\n".join(
        textwrap.wrap(
            oneline,
            width=width,
            drop_whitespace=False,
            break_long_words=True,
            break_on_hyphens=False,
        )
    )
    return f"{RESULT_BLOCK_BEGIN}\n{wrapped}\n{RESULT_BLOCK_END}\n"


class _AliveBackend:
    """Minimal backend: session alive, pane shows a TUI-wrapped marker block."""

    def __init__(self, pane_output: str) -> None:
        self.pane_output = pane_output

    def has_session(self, name: str) -> bool:
        return True

    def capture_pane(self, target: str, lines: int = 100, **_: object) -> str:
        return self.pane_output


def _handler_with_session(
    backend: _AliveBackend,
    session_id: str,
    tmp_path: Path,
) -> TmuxAgentHandler:
    handler = TmuxAgentHandler(backend=backend)
    handle = SessionHandle(session_id=session_id)
    handler._sessions[session_id] = SessionState(
        handle=handle,
        adapter=get_adapter(AgentType.CLAUDE),
        pane_id="%0",
        agent_type=AgentType.CLAUDE,
        work_dir=tmp_path,
        lifecycle=AgentSessionLifecycle.RUN_TO_COMPLETION,
        result_schema=RESULT_SCHEMA,
    )
    return handler


def _spec(tmp_path: Path, **overrides: object) -> AgentSpec:
    kwargs: dict = dict(
        run_id="run-rr",
        node_id="node-rr",
        attempt=0,
        agent_type=AgentType.CLAUDE,
        work_dir=tmp_path,
        prompt="read state and report",
        result_schema=RESULT_SCHEMA,
        mcp_tools=(),
        mcp_server_name="sbi",
    )
    kwargs.update(overrides)
    return AgentSpec(**kwargs)


def test_report_result_tool_accepts_valid_payload_and_fills_sink() -> None:
    sink: dict[str, object] = {"payload": None}
    tool = make_report_result_tool(sink, RESULT_SCHEMA)

    reply = run(tool.handler(LONG_PAYLOAD))

    assert reply == {"status": "accepted"}
    assert sink["payload"] == LONG_PAYLOAD


def test_report_result_tool_rejects_invalid_payload_in_band() -> None:
    sink: dict[str, object] = {"payload": None}
    tool = make_report_result_tool(sink, RESULT_SCHEMA)

    reply = run(tool.handler({"status": "readiness_blocker"}))

    assert reply["status"] == "rejected"
    assert "reason" in reply["validation_error"]
    assert sink["payload"] is None


def test_report_result_tool_is_a_servable_mcp_tool() -> None:
    sink: dict[str, object] = {"payload": None}
    tool = make_report_result_tool(sink, RESULT_SCHEMA)

    server = McpToolServer(tools=(tool,))
    assert REPORT_RESULT_TOOL_NAME in server._tools
    assert tool.param_names() == ("result",)


def test_await_result_returns_reported_payload_result_first(tmp_path: Path) -> None:
    """The sink wins even while the pane shows an unparseable wrapped block."""
    backend = _AliveBackend(_wrapped_marker_block(LONG_PAYLOAD))
    handler = _handler_with_session(backend, "run-rr-node-rr-0", tmp_path)
    sink = handler.create_result_sink("run-rr-node-rr-0")
    sink["payload"] = LONG_PAYLOAD

    outcome = handler.handle_await_result(
        AwaitResultEffect(
            handle=SessionHandle(session_id="run-rr-node-rr-0"),
            timeout_seconds=0.0,
        )
    )

    assert outcome.status == AwaitStatus.EXITED
    assert outcome.validation_error is None
    assert outcome.result == LONG_PAYLOAD


def test_wrapped_pane_block_alone_still_fails_verbatim_parse(tmp_path: Path) -> None:
    """Documents the legacy failure mode this channel exists to bypass."""
    backend = _AliveBackend(_wrapped_marker_block(LONG_PAYLOAD))
    handler = _handler_with_session(backend, "run-rr-node-rr-0", tmp_path)

    outcome = handler.handle_await_result(
        AwaitResultEffect(
            handle=SessionHandle(session_id="run-rr-node-rr-0"),
            timeout_seconds=0.0,
        )
    )

    assert outcome.result is None
    assert "not valid JSON" in (outcome.validation_error or "")


def test_release_session_discards_sink(tmp_path: Path) -> None:
    from doeff_agents.effects import ReleaseSessionEffect

    backend = _AliveBackend("")
    handler = _handler_with_session(backend, "run-rr-node-rr-0", tmp_path)
    handler.create_result_sink("run-rr-node-rr-0")

    handler.handle_release_session(
        ReleaseSessionEffect(handle=SessionHandle(session_id="run-rr-node-rr-0"))
    )

    assert handler._result_sinks == {}


def test_spec_predicate_requires_mcp_tools_and_schema(tmp_path: Path) -> None:
    sink: dict[str, object] = {"payload": None}
    tool = make_report_result_tool(sink, RESULT_SCHEMA)

    assert spec_uses_report_result_transport(_spec(tmp_path, mcp_tools=(tool,)))
    assert not spec_uses_report_result_transport(_spec(tmp_path))
    assert not spec_uses_report_result_transport(
        _spec(tmp_path, mcp_tools=(tool,), result_schema=None)
    )


def test_prompt_contract_switches_to_tool_transport(tmp_path: Path) -> None:
    spec = _spec(tmp_path)

    tool_mode = _launch_effect_from_spec(spec, "sbi").prompt
    marker_mode = _launch_effect_from_spec(spec).prompt

    assert REPORT_RESULT_TOOL_NAME in tool_mode
    assert "`sbi` MCP server" in tool_mode
    assert RESULT_BLOCK_BEGIN not in tool_mode
    assert RESULT_BLOCK_BEGIN in marker_mode
    assert REPORT_RESULT_TOOL_NAME not in marker_mode
