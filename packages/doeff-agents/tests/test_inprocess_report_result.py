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


# ---------------------------------------------------------------------------
# Integration through agent_effectful_handler (tmux-agent-defhandler): the
# handler that nakagawa installs. Regression guard for the duplicated
# LaunchSessionEffect branches in effectful.hy — the first fix landed only in
# agent-handler-defhandler and this launch path kept marker-mode prompts.
# ---------------------------------------------------------------------------

import http.client
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse

from doeff import do
from doeff_agents.adapters.base import InjectionMethod, LaunchParams
from doeff_agents.effects.agent import AwaitResult, LaunchSession, StopSession
from doeff_agents.handlers import agent_effectful_handler
from doeff_agents.session_backend import SessionBackend
from doeff_agents.tmux import SessionInfo
from doeff_core_effects.handlers import lazy_ask, state
from doeff_core_effects.scheduler import CreateExternalPromise, Wait, scheduled


class _FakeAdapter:
    def __init__(self) -> None:
        self.params: list[LaunchParams] = []

    def is_available(self) -> bool:
        return True

    def launch_command(self, params: LaunchParams) -> list[str]:
        self.params.append(params)
        return ["fake-agent"]

    @property
    def injection_method(self) -> InjectionMethod:
        return InjectionMethod.TMUX

    @property
    def ready_pattern(self) -> str | None:
        return None

    @property
    def status_bar_lines(self) -> int:
        return 3


class _FakeLaunchBackend:
    def __init__(self) -> None:
        self.sessions: dict[str, str] = {}
        self.killed: list[str] = []

    def has_session(self, name: str) -> bool:
        return name in self.sessions

    def new_session(self, cfg) -> SessionInfo:
        pane_id = f"%{len(self.sessions)}"
        self.sessions[cfg.session_name] = pane_id
        return SessionInfo(
            session_name=cfg.session_name,
            pane_id=pane_id,
            created_at=datetime.now(timezone.utc),
        )

    def send_keys(self, target: str, keys: str, *, literal=True, enter=True) -> None:
        pass

    def capture_pane(self, target: str, lines=100, *, strip_ansi_codes=True) -> str:
        return ""

    def kill_session(self, session: str) -> None:
        self.killed.append(session)
        self.sessions.pop(session, None)


def _read_sse_data(resp) -> str:
    buf = ""
    while not buf.endswith("\n\n"):
        buf += resp.read(1).decode()
    for line in buf.strip().split("\n"):
        if line.startswith("data:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"No data field in SSE event: {buf!r}")


def _call_report_result_from_agent_side(work_dir: Path, payload: dict) -> dict:
    config = json.loads((work_dir / ".mcp.json").read_text(encoding="utf-8"))
    server_url = config["mcpServers"]["sbi"]["url"]
    parsed = urlparse(server_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    conn.request("GET", "/sse")
    resp = conn.getresponse()
    endpoint = _read_sse_data(resp)

    post = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": REPORT_RESULT_TOOL_NAME,
                "arguments": {"result": payload},
            },
        }
    )
    post.request("POST", endpoint, body.encode(), {"Content-Type": "application/json"})
    post_resp = post.getresponse()
    assert post_resp.status == 202
    post_resp.read()
    post.close()

    data = json.loads(_read_sse_data(resp))
    conn.close()
    return data


def test_launch_session_via_effectful_handler_carries_report_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    adapter = _FakeAdapter()
    backend = _FakeLaunchBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: adapter,
    )

    @do
    def noop_tool_body():
        return {"status": "done"}

    from doeff.mcp import McpToolDef

    domain_tool = McpToolDef(
        name="sbi-login",
        description="fake domain tool",
        params=(),
        handler=noop_tool_body,
    )

    @do
    def workflow():
        spec = AgentSpec(
            run_id="run-rr-int",
            node_id="node-rr-int",
            attempt=0,
            agent_type=AgentType.CLAUDE,
            work_dir=tmp_path,
            prompt="read state and report",
            result_schema=RESULT_SCHEMA,
            mcp_tools=(domain_tool,),
            mcp_server_name="sbi",
        )
        handle = yield LaunchSession(spec)
        done = yield CreateExternalPromise()
        holder: list[object] = []

        def call_tool() -> None:
            try:
                holder.append(
                    _call_report_result_from_agent_side(tmp_path, LONG_PAYLOAD)
                )
            except Exception as exc:  # pragma: no cover - re-raised below
                holder.append(exc)
            finally:
                done.complete(None)

        threading.Thread(target=call_tool, daemon=True).start()
        yield Wait(done.future)
        outcome = yield AwaitResult(handle, timeout_seconds=5.0)
        yield StopSession(handle, reason="test cleanup")
        reply = holder[0]
        if isinstance(reply, Exception):
            raise reply
        return outcome, reply

    outcome, reply = run(
        scheduled(
            lazy_ask(env={SessionBackend: backend})(
                state()(agent_effectful_handler()(workflow()))
            )
        )
    )

    tool_reply = json.loads(reply["result"]["content"][0]["text"])
    assert tool_reply == {"status": "accepted"}
    assert outcome.validation_error is None
    assert outcome.result == LONG_PAYLOAD
    # The launch prompt must instruct the typed transport, not markers.
    assert REPORT_RESULT_TOOL_NAME in adapter.params[0].prompt
    assert RESULT_BLOCK_BEGIN not in adapter.params[0].prompt
