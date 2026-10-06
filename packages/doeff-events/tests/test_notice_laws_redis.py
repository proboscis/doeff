"""The delivery laws of the notice broker on a real Redis server (agora-redesign #3850) — the same law programs
the in-memory broker passes in ``test_notice_laws_memory.py`` — and what a real server does to a subscriber that
does not read.

The server: the fixture looks for ``redis-server`` on ``PATH``, starts it in a temporary directory on a free port
with ``--save "" --appendonly no`` and stops it at the end of the module. Without an executable every test here
is skipped with the reason; the in-memory tests run regardless.

An outage is made by killing every client connection on the server (``CLIENT KILL``): a subscriber's wait ends
with a lost connection. ``AwaitBrokerBack`` is answered by the test (the composition's part) when the law
restores the broker.

No ``from __future__ import annotations`` here: the VM reads a handler's effect types from the annotation of its
first parameter.
"""

import contextlib
import shutil
import socket
import subprocess
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
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
from doeff_events import EventBus, SourceMissed, SourceStarted, subscribed_event_handler
from doeff_events.effects.events import Publish, WaitForEvent
from doeff_events.effects.notices import (
    Announce,
    Announcement,
    AwaitBrokerBack,
    BrokerUnreachable,
    NextAnnouncement,
    ProbeBroker,
    SubscribeChannels,
)
from doeff_events.handlers.notice_events import (
    GAP_NOTICE,
    NoticeGapMarked,
    NoticeRoute,
    NoticeSent,
    notice_events_handler,
)
from doeff_events.handlers.redis_notices import broker_back_by_retry, redis_notice_handler
from doeff_events.notice_laws import BROKER_LAWS, EVENT_LAWS, EventLawHarness, LawNote, law_routes
from doeff_time import Delay, async_time_handler
from notice_law_support import PATIENCE_SECONDS

from doeff import K, Pass, Program, Resume, do, run
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
    server = subprocess.Popen(arguments, cwd=tmp_path_factory.mktemp("redis"), stdout=subprocess.PIPE, text=True)
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

    def as_party(source: str, event_types: tuple[type, ...], body: Program[object]) -> Program[object]:
        events = notice_events_handler(source, law_routes(prefix), PATIENCE_SECONDS)
        queue = subscribed_event_handler(EventBus(), source, event_types)
        return queue(_answers_broker_back(outage)(redis_notice_handler(url)(events(body))))

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
    _run_on_wall_clock(redis_notice_handler(redis_url)(law(_prefix())))


def test_cancel_ends_the_blocked_wait_on_redis(redis_url: str) -> None:
    """A reader that waits for a notice that never comes is stopped by ``Cancel`` alone: the wait has no time
    limit of its own, and the test would hit pytest's timeout if the cancel did not end it."""
    harness = _redis_harness(redis_url, _prefix())

    @do
    def waits_forever(started: Promise[object]) -> "EffectGenerator[object]":
        yield WaitForEvent(SourceStarted)
        yield CompletePromise(started, None)
        return (yield WaitForEvent(LawNote))

    @do
    def cancels() -> "EffectGenerator[str]":
        started: Promise[object] = yield CreatePromise()
        reader = yield Spawn(harness.as_party("law-idle", (SourceStarted, LawNote), waits_forever(started)))
        yield Wait(started.future)
        yield Cancel(reader)
        try:
            yield Wait(reader)
        except TaskCancelledError:
            return "cancelled"
        return "ended"

    assert _run_on_wall_clock(cancels()) == "cancelled"


SLOW_LIMIT: Final = "pubsub 1048576 1048576 0"
DEFAULT_LIMIT: Final = "pubsub 33554432 8388608 60"
NOTICE_BYTES: Final = 256 * 1024
NOTICES: Final = 160


def test_subscriber_that_does_not_read_is_disconnected_on_redis(redis_url: str) -> None:
    """Can notices be dropped while the connection stays alive? Not by the server's output buffer limit: a
    subscriber that does not read is disconnected when the notices waiting for it pass
    ``client-output-buffer-limit pubsub`` (set to 1 MB here; 40 MB are announced). The subscriber reads what was
    already on its way and then learns of the loss as ``BrokerUnreachable`` — never as a silent skip — and the
    announcer sees the receivers drop to 0."""
    channel = f"{_prefix()}:slow"

    @do
    def floods_a_subscriber_that_does_not_read() -> "EffectGenerator[tuple[tuple[int, ...], int, object]]":
        subscription = yield SubscribeChannels((channel,))
        receivers: tuple[int, ...] = ()
        for index in range(NOTICES):
            count = yield Announce(channel, "flood", f"{index:06d}" + "x" * NOTICE_BYTES)
            receivers = (*receivers, count)
        read = 0
        while read <= NOTICES:
            notice = yield NextAnnouncement(subscription)
            if not isinstance(notice, Announcement):
                return (receivers, read, notice)
            # Nothing is skipped in the middle: what arrives is the unbroken beginning of what was announced.
            assert notice.body.startswith(f"{read:06d}"), (read, notice.body[:6])
            read += 1
        return (receivers, read, None)

    with redis.Redis.from_url(redis_url) as admin:
        admin.config_set("client-output-buffer-limit", SLOW_LIMIT)
        try:
            receivers, read, end = _run_on_wall_clock(
                redis_notice_handler(redis_url)(floods_a_subscriber_that_does_not_read())
            )
        finally:
            admin.config_set("client-output-buffer-limit", DEFAULT_LIMIT)
    assert receivers[0] == 1, receivers[:3]
    assert receivers[-1] == 0, receivers[-3:]
    assert isinstance(end, BrokerUnreachable), end
    assert read < receivers.count(1), (read, receivers.count(1))


# --- a sender the broker could not take a notice from, on a real server (agora-redesign #3864) ---------------------
# The production path of a missed notice: the gap mark, broker_back_by_retry trying a connection (PING) at its
# interval only while a gap is held, the gap notice once the server answers again, and the trying stopping after.

RETRY_SECONDS: Final = 0.2


@final
class _SenderLink:
    """A TCP relay between one sender and the server, which the test can make refuse: while it refuses, every
    connection the sender makes is closed at once (and counted) and the connections it had are cut. The receivers
    connect to the server directly, so they stay subscribed and connected."""

    def __init__(self, server_port: int) -> None:
        """Listen on a free port and relay every accepted connection to ``server_port``."""
        self._server_port = server_port
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = int(self._listener.getsockname()[1])
        self._mut_refusing = False
        self._mut_opened = 0
        self._mut_live: tuple[socket.socket, ...] = ()
        threading.Thread(target=self._accepting, daemon=True).start()

    @property
    def url(self) -> str:
        """The URL the sender uses instead of the server's."""
        return f"redis://127.0.0.1:{self.port}/0"

    def refuse(self) -> None:
        """From now on, close every connection the sender makes, and cut the ones it has."""
        self._mut_refusing = True
        live, self._mut_live = self._mut_live, ()
        for end in live:
            with contextlib.suppress(OSError):
                end.close()

    def accept(self) -> None:
        """Relay the sender's connections again."""
        self._mut_refusing = False

    def _accepting(self) -> None:
        """Accept connections for as long as the process runs (the thread is a daemon)."""
        while True:
            client, _ = self._listener.accept()
            self._mut_opened += 1
            if self._mut_refusing:
                client.close()
                continue
            server = socket.create_connection(("127.0.0.1", self._server_port))
            self._mut_live = (*self._mut_live, client, server)
            threading.Thread(target=_pumped, args=(client, server), daemon=True).start()
            threading.Thread(target=_pumped, args=(server, client), daemon=True).start()


def _pumped(source: socket.socket, target: socket.socket) -> None:
    """Copy bytes from ``source`` to ``target`` until either end is closed, then close both."""
    with contextlib.suppress(OSError):
        while chunk := source.recv(65536):
            target.sendall(chunk)
    for end in (source, target):
        with contextlib.suppress(OSError):
            end.close()


def _port_of(url: str) -> int:
    """The port of a ``redis://host:port/db`` URL."""
    return int(url.rsplit(":", 1)[1].split("/", 1)[0])


def _sender_routes(prefix: str) -> "tuple[NoticeRoute[object], ...]":
    """``law_routes`` without the channels they read (a process that only sends)."""
    return tuple(
        NoticeRoute(r.event_type, r.wire_name, r.channel, r.encode, r.decode, r.when_unsent) for r in law_routes(prefix)
    )


def test_a_sender_cut_off_alone_tells_its_gap_to_a_connected_reader_when_it_reaches_the_server_on_redis(
    redis_url: str,
) -> None:
    """Red 11 of the review: only the sender loses the server; the reader stays subscribed and connected. The
    sender's Publish answers NoticeGapMarked, a connection is tried every interval while the gap is held, the gap
    reaches the reader as SourceMissed once the sender's connection works again, and then nothing more is tried."""
    prefix = _prefix()
    link = _SenderLink(_port_of(redis_url))

    @do
    def reads(started: Promise[object]) -> "EffectGenerator[object]":
        yield WaitForEvent(SourceStarted)
        yield CompletePromise(started, None)
        return (yield WaitForEvent(SourceMissed, LawNote))

    @do
    def sends() -> "EffectGenerator[tuple[object, int, int, int]]":
        link.refuse()
        answer = yield Publish(LawNote("lost"))
        yield Delay(1.0)
        during = link._mut_opened
        link.accept()
        yield Delay(1.0)
        told = link._mut_opened
        yield Delay(1.0)
        return (answer, during, told, link._mut_opened)

    @do
    def both() -> "EffectGenerator[tuple[object, ...]]":
        started: Promise[object] = yield CreatePromise()
        reader_events = notice_events_handler("law-connected", law_routes(prefix), PATIENCE_SECONDS)
        reader = yield Spawn(
            subscribed_event_handler(EventBus(), "law-connected", (SourceStarted, SourceMissed, LawNote))(
                redis_notice_handler(redis_url)(reader_events(reads(started)))
            )
        )
        yield Wait(started.future)
        sender_events = notice_events_handler("law-cut-off", _sender_routes(prefix), PATIENCE_SECONDS)
        sent = yield subscribed_event_handler(EventBus(), "law-cut-off")(
            redis_notice_handler(link.url)(broker_back_by_retry(RETRY_SECONDS)(sender_events(sends())))
        )
        heard = yield Wait(reader)
        return (*sent, heard)

    answer, during, told, later, heard = _run_on_wall_clock(both())
    assert isinstance(answer, NoticeGapMarked), answer
    assert during >= 3, f"a connection is tried every {RETRY_SECONDS} s while the gap is held: {during}"
    assert heard == SourceMissed("law-connected", f"{prefix}:note"), heard
    assert later == told, f"nothing is tried once the gap was told: {told} → {later}"


def _started_server(port: int, directory: str) -> "subprocess.Popen[str]":
    """Start a redis-server of a test's own on ``port`` and wait for it to say it listens."""
    executable = shutil.which("redis-server")
    if executable is None:
        pytest.skip("no redis-server executable on PATH — the restart of a real Redis server is not run")
    arguments = [executable, "--port", str(port), "--bind", "127.0.0.1", "--save", "", "--appendonly", "no"]
    server = subprocess.Popen(arguments, cwd=directory, stdout=subprocess.PIPE, text=True)
    assert server.stdout is not None
    for line in server.stdout:
        if READY_LINE in line:
            return server
    raise AssertionError(f"redis-server ended before it was ready (exit {server.poll()})")


@final
class _Tries:
    """What the recording layer saw on the sender's side: how many connection tries and how many gap notices."""

    __slots__ = ("_mut_gaps", "_mut_probes")

    def __init__(self) -> None:
        """Start having seen nothing."""
        self._mut_probes = 0
        self._mut_gaps = 0


def _counting(tries: _Tries) -> "ProgramHandler":
    """The layer between ``broker_back_by_retry`` and the Redis handler that counts the tries and the gap notices
    and passes everything on."""

    @do
    def handler(effect: ProbeBroker | Announce, k: K) -> "EffectGenerator[object]":
        """Count a try or a gap notice; pass the effect on."""
        if isinstance(effect, ProbeBroker):
            tries._mut_probes += 1
        elif effect.name == GAP_NOTICE:
            tries._mut_gaps += 1
        yield Pass(effect, k)
        return None

    return program_handler(handler)


def test_a_restarted_server_is_tried_only_while_a_gap_is_held_and_told_one_gap_on_redis(tmp_path: Path) -> None:
    """The server itself stops and starts again on the same port. Nothing is tried before the outage; while the gap
    is held a connection is tried every interval; once the server answers, exactly one gap notice goes out and
    the trying stops; the next Publish is sent as usual."""
    port = _free_port()
    url = f"redis://127.0.0.1:{port}/0"
    tries = _Tries()
    server = _started_server(port, str(tmp_path))
    try:

        @do
        def rides_a_restart() -> "EffectGenerator[tuple[object, ...]]":
            nonlocal server
            first = yield Publish(LawNote("before"))
            yield Delay(0.5)
            before = tries._mut_probes
            server.terminate()
            server.wait(timeout=20)
            missed = yield Publish(LawNote("lost"))
            yield Delay(1.0)
            during = tries._mut_probes
            server = _started_server(port, str(tmp_path))
            yield Delay(1.0)
            told = (tries._mut_probes, tries._mut_gaps)
            yield Delay(1.0)
            later = (tries._mut_probes, tries._mut_gaps)
            after = yield Publish(LawNote("after"))
            return (first, before, missed, during, told, later, after)

        events = notice_events_handler("law-restart", _sender_routes(_prefix()), PATIENCE_SECONDS)
        program = subscribed_event_handler(EventBus(), "law-restart")(
            redis_notice_handler(url)(_counting(tries)(broker_back_by_retry(RETRY_SECONDS)(events(rides_a_restart()))))
        )
        first, before, missed, during, told, later, after = _run_on_wall_clock(program)
    finally:
        server.terminate()
        server.wait(timeout=20)
    assert isinstance(first, NoticeSent), first
    assert before == 0, "nothing is tried while no gap is held"
    assert isinstance(missed, NoticeGapMarked), missed
    assert during >= 3, f"a connection is tried every {RETRY_SECONDS} s while the gap is held: {during}"
    assert told[1] == 1, f"one gap notice once the server answers: {told}"
    assert later == told, f"nothing more is tried or told: {told} → {later}"
    assert isinstance(after, NoticeSent), after
