"""Tests for mcp_server_loop — the in-VM dispatch loop for McpToolServer.

The loop bridges HTTP request threads to the main VM: HTTP thread pushes
McpToolRequest onto server.request_queue and completes the wakeup
ExternalPromise posted by the loop; the loop drains the queue and Spawns
a task per request inside the same VM as the caller. This keeps sim_time,
scheduler state, and Ask-resolution shared across pipeline + tool calls.
"""

from __future__ import annotations

import threading
import time

import hy  # noqa: F401  (enable .hy imports)
import pytest

from doeff import EffectBase, Pass, Perform, Pure, Resume, WithHandler, do, run
from doeff_core_effects.handlers import state
from doeff_core_effects.scheduler import Spawn, Wait, scheduled
from doeff.mcp import McpParamSchema, McpToolDef

from doeff_agents.handlers.mcp_server_loop import mcp_server_loop
from doeff_agents.mcp_server import McpToolRequest, McpToolServer


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _echo_tool() -> McpToolDef:
    @do
    def _echo(msg):
        return (yield Pure(f"echo: {msg}"))

    return McpToolDef(
        name="echo",
        description="Echo back the message",
        params=(McpParamSchema(name="msg", type="string", description="message"),),
        handler=_echo,
    )


class _GreetEffect(EffectBase):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name


@do
def _greet_handler(effect, k):
    """Domain handler — MCP tool body yields GreetEffect, expects resume with str."""
    if isinstance(effect, _GreetEffect):
        return (yield Resume(k, f"Hello, {effect.name}!"))
    yield Pass(effect, k)


def _greet_tool() -> McpToolDef:
    @do
    def _greet(name):
        result = yield Perform(_GreetEffect(name=name))
        return result

    return McpToolDef(
        name="greet",
        description="Greet by name",
        params=(McpParamSchema(name="name", type="string", description="name"),),
        handler=_greet,
    )


def _push_one_request(server: McpToolServer, tool_name: str, args: dict) -> McpToolRequest:
    """Simulate one HTTP POST /message: push req, wake VM, wait for event."""
    req = McpToolRequest(
        tool_name=tool_name,
        arguments=args,
        event=threading.Event(),
        holder=[],
    )
    server.request_queue.put(req)
    ep = server.wakeup_mailbox.get(timeout=5.0)
    ep.complete(None)
    assert req.event.wait(timeout=5.0), "Tool call did not complete in time"
    return req


# ---------------------------------------------------------------------------
# Tests — single requests
# ---------------------------------------------------------------------------


class TestMcpServerLoopDispatch:
    def test_single_tool_call_runs_in_vm(self):
        """One request → loop drains queue → tool handler runs → holder filled."""
        server = McpToolServer(tools=(_echo_tool(),))
        captured: list = []

        def driver():
            req = _push_one_request(server, "echo", {"msg": "hi"})
            captured.append(req.holder[0])
            # Signal shutdown: the loop will observe the flag on its next
            # iteration and exit. If it happens to be parked on Wait we
            # also wake it; if not, the get times out harmlessly.
            server.shutting_down = True
            try:
                ep = server.wakeup_mailbox.get(timeout=0.5)
                ep.complete(None)
            except Exception:
                pass

        threading.Thread(target=driver, daemon=True).start()

        @do
        def main():
            yield mcp_server_loop(server, [])
            return None

        run(scheduled(main()))
        assert captured == [(True, "echo: hi")]

    def test_captured_handler_stack_is_applied(self):
        """Tool yields _GreetEffect → resolved by greet_handler in captured stack."""
        server = McpToolServer(tools=(_greet_tool(),))
        full_stack = [_greet_handler]

        driver_result: list = []

        def driver():
            req = _push_one_request(server, "greet", {"name": "World"})
            driver_result.append(req.holder[0])
            server.shutting_down = True
            try:
                ep = server.wakeup_mailbox.get(timeout=5.0)
                ep.complete(None)
            except Exception:
                pass

        threading.Thread(target=driver, daemon=True).start()

        @do
        def main():
            yield mcp_server_loop(server, full_stack)
            return None

        run(scheduled(main()))
        assert driver_result == [(True, "Hello, World!")]

    def test_unknown_tool_returns_error(self):
        """Request for a tool the server does not know → holder has (False, msg)."""
        server = McpToolServer(tools=(_echo_tool(),))

        driver_result: list = []

        def driver():
            req = _push_one_request(server, "nonexistent", {})
            driver_result.append(req.holder[0])
            server.shutting_down = True
            try:
                ep = server.wakeup_mailbox.get(timeout=5.0)
                ep.complete(None)
            except Exception:
                pass

        threading.Thread(target=driver, daemon=True).start()

        @do
        def main():
            yield mcp_server_loop(server, [])
            return None

        run(scheduled(main()))
        ok, value = driver_result[0]
        assert ok is False
        assert "unknown tool" in value

    def test_tool_exception_captured(self):
        """Tool handler raises → holder has (False, error_message)."""
        @do
        def _boom(msg):
            raise ValueError(f"boom: {msg}")
            yield Pure(None)  # unreachable but needed to make it a generator

        boom_tool = McpToolDef(
            name="boom",
            description="Always fails",
            params=(McpParamSchema(name="msg", type="string", description="m"),),
            handler=_boom,
        )
        server = McpToolServer(tools=(boom_tool,))
        driver_result: list = []

        def driver():
            req = _push_one_request(server, "boom", {"msg": "x"})
            driver_result.append(req.holder[0])
            server.shutting_down = True
            try:
                ep = server.wakeup_mailbox.get(timeout=5.0)
                ep.complete(None)
            except Exception:
                pass

        threading.Thread(target=driver, daemon=True).start()

        @do
        def main():
            yield mcp_server_loop(server, [])
            return None

        run(scheduled(main()))
        ok, value = driver_result[0]
        assert ok is False
        assert "boom: x" in value


# ---------------------------------------------------------------------------
# Tests — multiple requests & shutdown
# ---------------------------------------------------------------------------


class TestMcpServerLoopMultiple:
    def test_multiple_requests_serial(self):
        """Two sequential requests both run; loop iterates between them."""
        server = McpToolServer(tools=(_echo_tool(),))
        results: list = []

        def driver():
            results.append(_push_one_request(server, "echo", {"msg": "one"}).holder[0])
            results.append(_push_one_request(server, "echo", {"msg": "two"}).holder[0])
            server.shutting_down = True
            try:
                ep = server.wakeup_mailbox.get(timeout=5.0)
                ep.complete(None)
            except Exception:
                pass

        threading.Thread(target=driver, daemon=True).start()

        @do
        def main():
            yield mcp_server_loop(server, [])
            return None

        run(scheduled(main()))
        assert results == [(True, "echo: one"), (True, "echo: two")]

    def test_shutdown_exits_loop_even_without_requests(self):
        """Setting shutting_down + completing a pending wakeup exits the loop."""
        server = McpToolServer(tools=(_echo_tool(),))

        def driver():
            # Give the VM a moment to post its first wakeup ep
            ep = server.wakeup_mailbox.get(timeout=5.0)
            server.shutting_down = True
            ep.complete(None)

        threading.Thread(target=driver, daemon=True).start()

        @do
        def main():
            yield mcp_server_loop(server, [])
            return None

        run(scheduled(main()))  # should return, not hang
