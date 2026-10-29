"""The agent-await bridge: run a blocking call off the scheduler thread (L-K4-1)."""

import threading
from collections.abc import Callable, Generator
from typing import Any

from doeff_core_effects.scheduler import CreateExternalPromise, Wait

from doeff import EffectBase, do


@do
def offloaded_call(call: Callable[[], object]) -> Generator[EffectBase, Any, Any]:
    """Run ``call`` in a daemon thread and wait for it through the scheduler's ExternalPromise.

    A handler must not perform unbounded blocking I/O synchronously; unbounded waits enter
    only via the scheduler's Await / external-completion path, so the cooperative scheduler
    is free to dispatch sibling tasks while ``call`` runs. A program using this runs under
    ``scheduled``.

    Deadline semantics (L-K4-3): the offloaded agent call re-awaits in a loop until a
    terminal result or the node-spec wall-clock deadline (``agent! :deadline-seconds``).
    Each round-trip is bounded by the transport keep-alive heartbeat (agentd's
    ``DEFAULT_AWAIT_BUDGET_SECONDS + RPC_TIMEOUT_MARGIN_SECONDS``); heartbeat expiry is
    transparent and never surfaces as a node failure.

    Cancellation: if the scheduler cancels the waiting task (e.g. Gather fail-fast), the
    daemon thread runs to its own terminal/deadline outcome and the promise completion is a
    harmless no-op on an already-resolved promise.
    """
    promise = yield CreateExternalPromise()

    def _offload(ep=promise):
        try:
            ep.complete(call())
        except Exception as exc:
            ep.fail(exc)

    threading.Thread(
        target=_offload,
        daemon=True,
        name="agent-await-bridge",
    ).start()

    return (yield Wait(promise.future))
