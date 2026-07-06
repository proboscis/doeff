"""Production MCP dispatch must execute tools inside the caller's doeff VM."""

from __future__ import annotations

import http.client
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from doeff_agents.adapters.base import AgentType, InjectionMethod, LaunchParams
from doeff_agents.effects.agent import (
    AgentSpec,
    AwaitResult,
    AwaitStatus,
    LaunchEffect,
    LaunchSession,
    StopEffect,
    StopSession,
)
from doeff_agents.handlers import agent_effectful_handler
from doeff_agents.session_backend import SessionBackend
from doeff_agents.tmux import SessionInfo
from doeff_core_effects.handlers import lazy_ask, state
from doeff_core_effects.scheduler import (
    AcquireSemaphore,
    CreateExternalPromise,
    CreateSemaphore,
    ReleaseSemaphore,
    Wait,
    scheduled,
)

from doeff import do, run
from doeff.mcp import McpToolDef


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


class _FakeBackend:
    def __init__(self) -> None:
        self.sessions: dict[str, str] = {}
        self.captures: dict[str, str] = {}
        self.sent: list[tuple[str, str]] = []
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
        self.sent.append((target, keys))

    def capture_pane(self, target: str, lines=100, *, strip_ansi_codes=True) -> str:
        return self.captures.get(target, "")

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


def _call_tool_from_agent_side(work_dir: Path) -> dict:
    config = json.loads((work_dir / ".mcp.json").read_text(encoding="utf-8"))
    server_url = config["mcpServers"]["probe"]["url"]
    parsed = urlparse(server_url)
    assert parsed.hostname is not None
    assert parsed.port is not None

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
            "params": {"name": "same-vm-probe", "arguments": {}},
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


def test_agent_effectful_mcp_tools_run_inside_caller_scheduler_vm(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Regression for live MCP KeyError: semaphores must be shared with tools.

    The tool closes over a Semaphore allocated by the caller's scheduler VM.
    If doeff-agents dispatches the MCP tool in a fresh run(scheduled(...)) VM,
    AcquireSemaphore sees an unknown semaphore id and fails. The production
    handler must dispatch via mcp_server_loop so the same scheduler state is
    visible to the tool.
    """
    adapter = _FakeAdapter()
    backend = _FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: adapter,
    )

    @do
    def workflow():
        semaphore = yield CreateSemaphore(1)

        @do
        def same_vm_probe():
            yield AcquireSemaphore(semaphore)
            yield ReleaseSemaphore(semaphore)
            return {"status": "same-vm"}

        tool = McpToolDef(
            name="same-vm-probe",
            description="Acquire a caller-owned scheduler semaphore",
            params=(),
            handler=same_vm_probe,
        )
        handle = yield LaunchEffect(
            session_name="same-vm-session",
            agent_type=AgentType.CLAUDE,
            work_dir=tmp_path,
            prompt="probe",
            mcp_tools=(tool,),
            mcp_server_name="probe",
            ready_timeout=0.1,
        )
        done = yield CreateExternalPromise()
        holder: list[object] = []

        def call_tool() -> None:
            try:
                holder.append(_call_tool_from_agent_side(tmp_path))
            except Exception as exc:  # pragma: no cover - failure is re-raised below
                holder.append(exc)
            finally:
                done.complete(None)

        threading.Thread(target=call_tool, daemon=True).start()
        yield Wait(done.future)
        yield StopEffect(handle=handle)
        result = holder[0]
        if isinstance(result, Exception):
            raise result
        return result

    response = run(
        scheduled(
            lazy_ask(env={SessionBackend: backend})(state()(agent_effectful_handler()(workflow())))
        )
    )

    result = response["result"]
    assert result["isError"] is False, result
    assert json.loads(result["content"][0]["text"]) == {"status": "same-vm"}
    assert adapter.params[0].mcp_servers is not None
    assert "probe" in adapter.params[0].mcp_servers
    assert backend.killed == ["same-vm-session"]


def test_agent_mcp_loop_runs_while_await_result_is_pending(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """AwaitResult must not block the caller VM and starve mcp_server_loop.

    Live regression: LaunchSession spawned mcp_server_loop in the caller VM, but
    AwaitResult then synchronously blocked the same VM. HTTP tool calls could
    reach the MCP server yet never receive a wakeup endpoint.
    """
    adapter = _FakeAdapter()
    backend = _FakeBackend()
    monkeypatch.setattr(
        "doeff_agents.handlers.production.get_adapter",
        lambda _agent_type: adapter,
    )

    @do
    def workflow():
        semaphore = yield CreateSemaphore(1)

        @do
        def same_vm_probe():
            yield AcquireSemaphore(semaphore)
            yield ReleaseSemaphore(semaphore)
            return {"status": "same-vm-during-await"}

        tool = McpToolDef(
            name="same-vm-probe",
            description="Acquire a caller-owned scheduler semaphore",
            params=(),
            handler=same_vm_probe,
        )
        spec = AgentSpec(
            run_id="same-vm-await",
            node_id="worker",
            attempt=0,
            agent_type=AgentType.CLAUDE,
            work_dir=tmp_path,
            prompt="probe",
            result_schema={"type": "object"},
            mcp_tools=(tool,),
            mcp_server_name="probe",
        )
        handle = yield LaunchSession(spec)
        done = yield CreateExternalPromise()
        holder: list[object] = []

        def call_tool_and_finish() -> None:
            try:
                # Runs on a real threading.Thread racing the doeff VM's
                # AwaitResult wait; SimulationRuntime only controls the effect
                # clock, not this background OS thread.
                time.sleep(0.2)  # nosemgrep: doeff-no-sleep-in-tests
                holder.append(_call_tool_from_agent_side(tmp_path))
                pane_id = backend.sessions[handle.session_id]
                backend.captures[pane_id] = (
                    "DOEFF_AGENT_RESULT_BEGIN\n"
                    f"{json.dumps({'status': 'done'})}\n"
                    "DOEFF_AGENT_RESULT_END\n"
                )
            except Exception as exc:  # pragma: no cover - failure is re-raised below
                holder.append(exc)
            finally:
                done.complete(None)

        threading.Thread(target=call_tool_and_finish, daemon=True).start()
        outcome = yield AwaitResult(handle, timeout_seconds=5.0)
        yield Wait(done.future)
        yield StopSession(handle=handle)
        result = holder[0]
        if isinstance(result, Exception):
            raise result
        return {"tool": result, "outcome": outcome}

    response = run(
        scheduled(
            lazy_ask(env={SessionBackend: backend})(state()(agent_effectful_handler()(workflow())))
        )
    )

    result = response["tool"]["result"]
    assert result["isError"] is False, result
    assert json.loads(result["content"][0]["text"]) == {"status": "same-vm-during-await"}
    assert response["outcome"].status == AwaitStatus.EXITED
    assert response["outcome"].result == {"status": "done"}
    assert backend.killed == ["same-vm-await-worker-0"]
