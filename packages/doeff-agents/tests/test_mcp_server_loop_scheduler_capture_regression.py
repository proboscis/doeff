"""Regression test for the 2026-07-07 SBI live incident scheduler-capture bug.

Ported from proboscis-ema
``experiments/2026-07-07-doeff-exit0-scheduler-capture-repro/repro12.py``.
See ``docs/proboscis-ema-2026-07-07-live-trade-correctness-architecture-plan.md``
(gap G1, tasks LTC-1/LTC-2/LTC-3) in the proboscis-ema repo for the full
incident writeup.

Incident shape: a pipeline effect handler raises while an MCP tool call is
cooperatively parked (``Wait(..., priority=PRIORITY_IDLE)``) inside
``mcp_server_loop``'s captured-handler execution. The handler stack captured
at the ``Spawn(mcp_server_loop(...))`` call site (``GetHandlers(k)`` +
``GetOuterHandlers()``) includes the scheduler's own handler. Reinstalling
that stack inside the spawned tool task means the pipeline's re-raised
exception gets delivered into the orphaned tool task's dynamic scope instead
of propagating to the caller of ``run()``. ``_tool-result-with-stack``'s
blanket ``except Exception`` then swallows it as the tool's own
``(ok=False, error_message)`` result, and the *tool's return value* becomes
the result of ``run()`` — the pipeline exception never reaches the caller.

Production symptom observed 2026-07-07 (SBI live job
``nakagawa-sbi-company-live-29722895``): exit code 0, restartCount=0,
pod Completed — no Job failure — while the recon blocker that should have
stopped the run was silently discarded and the orphaned mcp tool call's
result took its place.

Fixed by LTC-1: ``scheduler.py make_handler`` marks its raw handler with
``__doeff_scheduler_prompt__`` and ``_tool-result-with-stack`` (the single
reinstall choke point) skips it, so a captured stack can never introduce a
second scheduler prompt into a tool task. This test pins that invariant:
the pipeline RuntimeError must propagate out of ``run()`` even while the
tool call is still parked.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import pytest
from doeff_agents.handlers.mcp_server_loop import mcp_server_loop
from doeff_agents.mcp_server import McpToolRequest, McpToolServer
from doeff_core_effects.scheduler import (
    PRIORITY_IDLE,
    CompletePromise,
    CreateExternalPromise,
    CreatePromise,
    Promise,
    Spawn,
    Wait,
    scheduled,
)

from doeff import EffectBase, GetHandlers, GetOuterHandlers, Pass, Resume, do, run
from doeff.mcp import McpToolDef
from doeff.program import with_handlers


def test_pipeline_exception_propagates_despite_parked_mcp_tool_call():
    """A pipeline handler's raise must reach ``run()``'s caller even while an
    MCP tool call spawned from inside that handler is still cooperatively
    parked on an external promise.

    Topology (matches repro12.py):
      - ``plan_handler`` handles ``GetPlan``, captures the handler stack
        live at that point (``GetHandlers``/``GetOuterHandlers``), spawns
        ``mcp_server_loop`` with it, dispatches one tool call through the
        real HTTP-thread bridge (``request_queue`` + ``wakeup_mailbox``),
        waits for the tool handler's parked signal, then raises.
      - the tool handler cooperatively parks on ``Wait(ep.future,
        priority=PRIORITY_IDLE)`` while its worker thread is blocked on an
        explicit test event — same shape as
        ``_run-blocking-browser-call`` — so it is still in flight when the
        pipeline raises.
      - ``notification_handler`` handles ``TradingEventE`` emitted from the
        pipeline's ``except`` block, itself waiting on a cooperative
        external promise (Slack post), before the pipeline re-raises.

    Only the first of repro12's three failure signatures is asserted here
    (RuntimeError swallowed instead of propagating out of ``run()``); the
    other two (pipeline error text surfacing as the tool's own error
    response, and the orphaned tool's return value becoming ``run()``'s
    result) are the same root cause and are left to repro12/13 for manual
    inspection.
    """

    @dataclass(frozen=True)
    class GetPlan(EffectBase):
        pass

    @dataclass(frozen=True)
    class TradingEventE(EffectBase):
        kind: str = "pipeline-error"

    release_tool_worker = threading.Event()
    tool_parked_promise: Promise | None = None

    @do
    def device_auth_tool_handler():
        # _run-blocking-browser-call shape: worker thread + IDLE external wait.
        ep = yield CreateExternalPromise()

        # The test controls this worker explicitly so the tool stays parked
        # until after the pipeline exception has propagated.
        threading.Thread(target=release_tool_worker.wait, daemon=True).start()
        assert tool_parked_promise is not None
        yield CompletePromise(tool_parked_promise, None)
        return (yield Wait(ep.future, priority=PRIORITY_IDLE))[1]

    tool = McpToolDef(
        name="sbi-complete-device-auth-from-mail",
        description="stub",
        params=(),
        handler=device_auth_tool_handler,
    )

    def http_thread(server, req):
        server.request_queue.put(req)
        wakeup_ep = server.wakeup_mailbox.get()
        wakeup_ep.complete(None)

    @do
    def plan_handler(effect, k):
        nonlocal tool_parked_promise
        if isinstance(effect, GetPlan):
            tool_parked_promise = yield CreatePromise()
            inner = yield GetHandlers(k)
            outer = yield GetOuterHandlers()
            captured = list(inner) + list(outer)
            server = McpToolServer(tools=(tool,))
            yield Spawn(mcp_server_loop(server, captured))
            req = McpToolRequest(
                tool_name="sbi-complete-device-auth-from-mail",
                arguments={},
                event=threading.Event(),
                holder=[],
            )
            # the 'agent' calls the tool before the pipeline-error event
            # finishes; the tool poll is still in flight when we raise below.
            threading.Thread(
                target=http_thread,
                args=(server, req),
                daemon=True,
            ).start()
            # Wait until the tool handler has created and parked on its external
            # promise before raising the pipeline blocker.
            yield Wait(tool_parked_promise.future)
            raise RuntimeError(
                "SBI recon readiness blocker "
                "failure_kind=sbi_shortable_inventory_ui_mismatch (regression)"
            )
        result = yield Pass(effect, k)
        return result

    @do
    def notification_handler(effect, k):
        if isinstance(effect, TradingEventE):
            # slack post: external wait completing while the tool poll is
            # still in flight.
            slack_ep = yield CreateExternalPromise()
            threading.Thread(
                target=slack_ep.complete,
                args=("ok",),
                daemon=True,
            ).start()
            yield Wait(slack_ep.future)
            result = yield Resume(k, None)
            return result
        result = yield Pass(effect, k)
        return result

    @do
    def pipeline():
        try:
            yield GetPlan()
        except Exception:
            yield TradingEventE()
            raise

    try:
        with pytest.raises(RuntimeError, match="sbi_shortable_inventory_ui_mismatch"):
            run(
                scheduled(
                    with_handlers([notification_handler, plan_handler], pipeline())
                )
            )
    finally:
        release_tool_worker.set()
