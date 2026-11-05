"""Test helper: a TCP relay placed between the client and PostgreSQL that records, per round trip, the statements the client sent.

A tool for seeing, on the wire rather than through the driver's call style, the sequence of round trips (the list of statements per round trip) that the answerer sends to the DB. It only reads PostgreSQL frontend
messages and passes everything to the server as is without rewriting (the server's answers are likewise returned to the client without being read, and only the number of bytes is counted --
for a check that looks at how many bytes an answer's rows come to on the wire).

- Round trip = the sequence of statements sent until the client sends Sync('S') or Simple Query('Q'). The client then waits for the server's answer (even in pipeline mode,
  sync is once at the exit -- the answerer's postgres-flush). A Sync with no statements is not counted as a round trip.
- Statement = the statement of the portal run by Execute('E') (the statement of the Parse('P') that made the prepared statement bound by Bind('B')), and the statement of Simple Query.
  Statements that psycopg prepares from the 5th time are also mapped back to the original statement by the Parse name.
- The start of a connection (StartupMessage, SSLRequest, GSSENCRequest -- no type byte) and messages that carry no statement are not counted.

Usage: tap = StatementWireTap(target) -- target = a unix socket path (str) or (host, port). The client connects to 127.0.0.1:tap.port
(sslmode=disable, gssencmode=disable). After tap.reset(), run the answerer and read tap.round_trips() (the sequence of round trips) or tap.downstream_bytes() (bytes server -> client). To stop, call
tap.close().
"""

from __future__ import annotations

import socket
import struct
import threading
from dataclasses import dataclass

# Code of the start message that has no type byte (StartupMessage = protocol 3.0).
PROTOCOL_3 = 196608


@dataclass(frozen=True)
class FrontendMessage:
    """One message the client sent: kind = type byte (1 character), payload = the body after the length."""

    kind: str
    payload: bytes


def c_strings(payload: bytes, count: int) -> tuple[str, ...]:
    """Read count NUL-terminated strings from the head of payload."""
    parts = payload.split(b"\x00", count)
    return tuple(part.decode("utf-8", "replace") for part in parts[:count])


class WireConversation:
    """The part that reads the client-side stream of one connection and finds statement and round-trip boundaries (touched by only one thread)."""

    def __init__(self, on_round_trip) -> None:
        self.on_round_trip = on_round_trip
        self._mut_started = False
        self._mut_buffer = b""
        self._mut_statements: dict[str, str] = {}
        self._mut_portals: dict[str, str] = {}
        self._mut_current: tuple[str, ...] = ()

    def feed(self, data: bytes) -> None:
        """Append the bytes that arrived from the client and read all readable messages."""
        self._mut_buffer += data
        while True:
            message = self.next_message()
            if message is None:
                return
            if message.kind:
                self.read(message)

    def next_message(self) -> FrontendMessage | None:
        """Cut out one message at the head of the buffer (None if not enough). The start message has kind = ""."""
        if not self._mut_started:
            if len(self._mut_buffer) < 8:
                return None
            length, code = struct.unpack("!ii", self._mut_buffer[:8])
            if len(self._mut_buffer) < length:
                return None
            self._mut_buffer = self._mut_buffer[length:]
            self._mut_started = code == PROTOCOL_3
            return FrontendMessage(kind="", payload=b"")
        if len(self._mut_buffer) < 5:
            return None
        kind = chr(self._mut_buffer[0])
        (length,) = struct.unpack("!i", self._mut_buffer[1:5])
        if len(self._mut_buffer) < 1 + length:
            return None
        payload = self._mut_buffer[5 : 1 + length]
        self._mut_buffer = self._mut_buffer[1 + length :]
        return FrontendMessage(kind=kind, payload=payload)

    def read(self, message: FrontendMessage) -> None:
        """Map one message onto statement and round-trip boundaries (header comment)."""
        match message.kind:
            case "P":
                name, query = c_strings(message.payload, 2)
                self._mut_statements = self._mut_statements | {name: query}
            case "B":
                portal, statement = c_strings(message.payload, 2)
                self._mut_portals = self._mut_portals | {portal: self._mut_statements.get(statement, "?")}
            case "E":
                (portal,) = c_strings(message.payload, 1)
                self._mut_current = self._mut_current + (self._mut_portals.get(portal, "?"),)
            case "Q":
                (query,) = c_strings(message.payload, 1)
                self._mut_current = self._mut_current + (query,)
                self.close_round_trip()
            case "S":
                self.close_round_trip()
            case _:
                return

    def close_round_trip(self) -> None:
        """Close the current round trip and hand it to the record (a round trip with no statements is not counted)."""
        if self._mut_current:
            self.on_round_trip(self._mut_current)
        self._mut_current = ()


class StatementWireTap:
    """A relay that accepts on a 127.0.0.1 port, relays to PostgreSQL, and records the statements the client sent per round trip (header comment)."""

    def __init__(self, target: str | tuple[str, int]) -> None:
        self.target = target
        self.lock = threading.Lock()
        self._mut_recorded: tuple[tuple[str, ...], ...] = ()
        self._mut_downstream = 0
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(16)
        self.port: int = self.listener.getsockname()[1]
        threading.Thread(target=self.accept, name="statement-wire-tap", daemon=True).start()

    def server_socket(self) -> socket.socket:
        """Make a socket connected to the relay's destination (PostgreSQL)."""
        if isinstance(self.target, str):
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        else:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.connect(self.target)
        return server

    def accept(self) -> None:
        """Each time a connection is accepted, start two pumps: upstream (client -> server -- passes while reading) and downstream (server -> client)."""
        while True:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            server = self.server_socket()
            conversation = WireConversation(self.record)
            threading.Thread(target=self.pump, args=(client, server, conversation), daemon=True).start()
            threading.Thread(target=self.pump, args=(server, client, None), daemon=True).start()

    def pump(self, source: socket.socket, sink: socket.socket, conversation: WireConversation | None) -> None:
        """Pass the bytes read from source to sink (upstream is read before passing -- the record is done before the answer returns)."""
        while True:
            try:
                data = source.recv(65536)
            except OSError:
                data = b""
            if not data:
                for end in (sink, source):
                    try:
                        end.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                return
            if conversation is not None:
                conversation.feed(data)
            else:
                self.count_downstream(len(data))
            try:
                sink.sendall(data)
            except OSError:
                return

    def record(self, statements: tuple[str, ...]) -> None:
        """Record one round trip (a sequence of statements) (called from the thread of any connection)."""
        with self.lock:
            self._mut_recorded = self._mut_recorded + (statements,)

    def count_downstream(self, size: int) -> None:
        """Add the bytes passed server -> client (called from the thread of any connection)."""
        with self.lock:
            self._mut_downstream += size

    def reset(self) -> None:
        """Empty the records and the counted bytes (call before running the answerer)."""
        with self.lock:
            self._mut_recorded = ()
            self._mut_downstream = 0

    def downstream_bytes(self) -> int:
        """Sum of bytes passed server -> client since reset (to see the size the answer's rows occupied on the wire)."""
        with self.lock:
            return self._mut_downstream

    def round_trips(self) -> tuple[tuple[str, ...], ...]:
        """The sequence of round trips recorded since reset (in the order they flowed)."""
        with self.lock:
            return self._mut_recorded

    def close(self) -> None:
        """Close the listener (connections that are connected close when the client closes)."""
        self.listener.close()
