"""The delivery laws of the event broker on a real Redis server (agora-redesign #3850) — the same law programs
the in-memory broker passes in ``test_stream_laws_memory.py``.

The server: the fixture looks for ``redis-server`` on ``PATH``, starts it in a temporary directory on a free port with
``--save "" --appendonly no`` and stops it at the end of the module. Without an executable every test here is
skipped with the reason; the in-memory tests run regardless.

An outage is made by killing every client connection on the server (``CLIENT KILL``): the readers' blocked reads
end with a lost connection. ``AwaitBrokerBack`` is answered by the test (the composition's part) when the law
restores the broker.

No ``from __future__ import annotations`` here: the VM reads a handler's effect types from the annotation of its
first parameter.
"""

import shutil
import socket
import subprocess
import uuid
from collections.abc import Iterator
from typing import TYPE_CHECKING, Final, final

import pytest
import redis
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import (
    Cancel,
    CompletePromise,
    CreatePromise,
    Promise,
    Spawn,
    TaskCancelledError,
    Wait,
    scheduled,
)
from doeff_events import EventBus, SourceStarted, subscribed_event_handler
from doeff_events.effects.events import WaitForEvent
from doeff_events.effects.streams import AwaitBrokerBack
from doeff_events.handlers.redis_streams import redis_stream_handler
from doeff_events.handlers.stream_events import stream_events_handler
from doeff_events.stream_laws import BROKER_LAWS, EVENT_LAWS, EventLawHarness, LawWork, law_routes
from doeff_time import async_time_handler
from stream_law_support import PATIENCE_SECONDS

from doeff import K, Program, Resume, do, run
from doeff import handler as program_handler

if TYPE_CHECKING:
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler

READY_LINE: Final = "Ready to accept connections"


def _free_port() -> int:
    """A TCP port nobody listens on right now."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def redis_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A redis-server of this module's own, without persistence; skipped with the reason when there is none."""
    executable = shutil.which("redis-server")
    if executable is None:
        pytest.skip("no redis-server executable on PATH — the laws on a real Redis server are not run")
    port = _free_port()
    arguments = [executable, "--port", str(port), "--bind", "127.0.0.1", "--save", "", "--appendonly", "no"]
    server = subprocess.Popen(
        arguments, cwd=tmp_path_factory.mktemp("redis"), stdout=subprocess.PIPE, text=True
    )
    try:
        # The server says when it listens; reading its output until then waits for that event (no sleep).
        assert server.stdout is not None
        started = False
        for line in server.stdout:
            if READY_LINE in line:
                started = True
                break
        assert started, f"redis-server ended before it was ready (exit {server.poll()})"
        yield f"redis://127.0.0.1:{port}/0"
    finally:
        server.terminate()
        server.wait(timeout=20)


@final
class _Outage:
    """The test's knowledge of the outage it made: the promise the wait for the broker's return hangs on."""

    __slots__ = ("gate",)

    def __init__(self) -> None:
        """Start with the broker reachable."""
        self.gate: Promise[object] | None = None


def _answers_broker_back(outage: _Outage) -> "ProgramHandler":
    """The composition's part: answer ``AwaitBrokerBack`` when the test restores the broker."""

    @do
    def handler(effect: AwaitBrokerBack, k: K) -> "EffectGenerator[object]":
        """Wait for the restore if an outage is on."""
        if outage.gate is not None:
            yield Wait(outage.gate.future)
        return (yield Resume(k, None))

    return program_handler(handler)


def _redis_harness(url: str, prefix: str) -> EventLawHarness:
    """The harness of the real server: every party is a process of its own (own queue, own connections)."""
    outage = _Outage()

    def as_party(consumer: str, event_types: tuple[type, ...], body: Program[object]) -> Program[object]:
        events = stream_events_handler(consumer, law_routes(prefix), PATIENCE_SECONDS)
        queue = subscribed_event_handler(EventBus(), consumer, event_types)
        return queue(_answers_broker_back(outage)(redis_stream_handler(url)(events(body))))

    @do
    def cut() -> "EffectGenerator[None]":
        """Break every client connection of the server (the admin connection that asks is spared)."""
        outage.gate = yield CreatePromise()
        with redis.Redis.from_url(url) as admin:
            admin.client_kill_filter(_type="normal", skipme=True)
            admin.client_kill_filter(_type="pubsub", skipme=True)

    @do
    def restore() -> "EffectGenerator[None]":
        """Tell everyone waiting that the broker is back (the server itself never stopped)."""
        gate, outage.gate = outage.gate, None
        if gate is not None:
            yield CompletePromise(gate, None)

    return EventLawHarness(as_party=as_party, cut=cut, restore=restore)


def _run_on_wall_clock(program: Program[object]) -> object:
    """Run under the scheduler with the asyncio bridge and the wall clock (what a real process uses)."""
    return run(scheduled(await_handler()(async_time_handler()(program))))


def _prefix() -> str:
    """Names of a test's own on the shared server."""
    return f"law-{uuid.uuid4().hex[:12]}"


@pytest.mark.parametrize("law", EVENT_LAWS, ids=lambda law: law.__name__)
def test_event_law_holds_on_redis(law, redis_url: str) -> None:
    _run_on_wall_clock(law(_redis_harness(redis_url, _prefix())))


@pytest.mark.parametrize("law", BROKER_LAWS, ids=lambda law: law.__name__)
def test_broker_law_holds_on_redis(law, redis_url: str) -> None:
    _run_on_wall_clock(redis_stream_handler(redis_url)(law(_prefix())))


def test_cancel_ends_the_blocked_read_on_redis(redis_url: str) -> None:
    """A reader that waits for an event that never comes is stopped by ``Cancel`` alone: the blocked read has no
    time limit of its own, and the test would hit pytest's timeout if the cancel did not end it."""
    harness = _redis_harness(redis_url, _prefix())

    @do
    def waits_forever(started: Promise[object]) -> "EffectGenerator[object]":
        yield WaitForEvent(SourceStarted)
        yield CompletePromise(started, None)
        return (yield WaitForEvent(LawWork))

    @do
    def cancels() -> "EffectGenerator[str]":
        started: Promise[object] = yield CreatePromise()
        reader = yield Spawn(harness.as_party("law-idle", (SourceStarted, LawWork), waits_forever(started)))
        yield Wait(started.future)
        yield Cancel(reader)
        try:
            yield Wait(reader)
        except TaskCancelledError:
            return "cancelled"
        return "ended"

    assert _run_on_wall_clock(cancels()) == "cancelled"
