"""Making a subscription on Redis is held to ``timeout_seconds`` as a whole (card [id]).

The subscriptions' client sets no limit on a call (its wait for a notice is held by the silence instead), so before
this a server that took the connection and answered nothing held ``SubscribeChannels`` without a limit: redis-py's
``on_connect`` reads the answers of its handshake with the client's call limit. In agora, durable attempts stopped
there — before their wait's own deadline was armed — and kept their task's slot by their lease's heartbeat.

No redis-server is needed: a fake server on a local port takes the connection and answers nothing (the handshake
hangs), or answers the handshake and never confirms ``SUBSCRIBE``. The silence is set far above the timeout, so the
answer within the timeout is the timeout's.

No ``from __future__ import annotations`` here: the VM reads a handler's effect types from the annotation of its
first parameter.
"""

import contextlib
import socket
import threading
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, BinaryIO, Final

import pytest
from doeff_core_effects import await_handler, scheduled
from doeff_events import BrokerUnreachable, SubscribeChannels, redis_notice_handler
from doeff_time import async_time_handler

from doeff import do, run

if TYPE_CHECKING:
    from doeff import EffectGenerator

TIMEOUT_SECONDS: Final = 0.5
# Far above the timeout: an answer within the timeout is not the silence's.
SILENCE_SECONDS: Final = 30.0


def _commands(connection: socket.socket) -> Iterator[list[bytes]]:
    """The RESP command arrays a client sends on ``connection``, until it closes."""
    reader = connection.makefile("rb")
    while True:
        head = reader.readline()
        if not head:
            return
        if not head.startswith(b"*"):
            continue
        yield [_bulk(reader) for _ in range(int(head[1:].strip()))]


def _bulk(reader: "BinaryIO") -> bytes:
    """One RESP bulk string of a command array."""
    size = int(reader.readline()[1:].strip())
    return reader.read(size + 2)[:size]


def _serve(connection: socket.socket, confirms_handshake: bool) -> None:
    """Answer one client: nothing at all, or ``+OK`` to every command but ``SUBSCRIBE`` (never confirmed)."""
    with connection, contextlib.suppress(OSError, ValueError):
        for command in _commands(connection):
            if confirms_handshake and command[:1] != [b"SUBSCRIBE"]:
                connection.sendall(b"+OK\r\n")


@contextlib.contextmanager
def _fake_server(*, confirms_handshake: bool) -> Iterator[int]:
    """A local server that takes every connection and answers as ``_serve`` does; yields its port."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    stopping = threading.Event()

    def accept() -> None:
        while not stopping.is_set():
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=_serve, args=(connection, confirms_handshake), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    try:
        yield int(listener.getsockname()[1])
    finally:
        stopping.set()
        listener.close()


@do
def _subscribes() -> "EffectGenerator[tuple[object, float]]":
    """Make one subscription; answer what came back and how long it took."""
    started = time.monotonic()
    answer = yield SubscribeChannels(("bounds:one",))
    return (answer, time.monotonic() - started)


@pytest.mark.parametrize(
    ("confirms_handshake", "protocol"),
    [(False, 3), (True, 2)],
    ids=["handshake-unanswered", "subscribe-unconfirmed"],
)
def test_a_subscription_the_server_does_not_answer_is_unreachable_within_the_timeout(
    confirms_handshake: bool, protocol: int
) -> None:
    with _fake_server(confirms_handshake=confirms_handshake) as port:
        url = f"redis://127.0.0.1:{port}/0?protocol={protocol}"
        handler = redis_notice_handler(url, TIMEOUT_SECONDS, SILENCE_SECONDS)
        answer, seconds = run(scheduled(await_handler(async_time_handler()(handler(_subscribes())))))
    assert isinstance(answer, BrokerUnreachable), answer
    assert "within 0.5 s" in answer.detail, answer.detail
    assert seconds < 2 * TIMEOUT_SECONDS + 1.0, f"the subscription was held {seconds:.2f} s"
