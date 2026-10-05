"""検の補助: client と PostgreSQL の間に置く TCP の中継で、client が送った文を往復ごとに記録する(agora-redesign #3605)。

答え手が DB へ流す往復の列(往復ごとの文の並び)を、driver の呼び方ではなく wire の上で見るための道具。PostgreSQL の frontend の
message を読むだけで、中身は書き換えずにそのまま server へ渡す(server の答えも読まずにそのまま client へ返す)。

- 往復 = client が Sync('S')か Simple Query('Q')を送るまでに送った文の並び。client はそこで server の答えを待つ(pipeline mode でも
  sync は出口の 1 度 — 答え手の postgres-flush)。文の無い Sync は往復に数えない。
- 文 = Execute('E')で流した portal の文(Bind('B')が結んだ prepared statement を作った Parse('P')の文)と、Simple Query の文。
  psycopg が 5 回目から prepare した文も、Parse の名で元の文に引き戻す。
- 接続の始まり(StartupMessage・SSLRequest・GSSENCRequest — 型の byte が無い)と、文を運ばない message は数えない。

使い方: tap = StatementWireTap(target) — target = unix socket の path(str)か (host, port)。client は 127.0.0.1:tap.port へ繋ぐ
(sslmode=disable・gssencmode=disable)。tap.reset() の後に答え手を走らせ、tap.round_trips() を読む。止める時は tap.close()。
"""

from __future__ import annotations

import socket
import struct
import threading
from dataclasses import dataclass

# 型の byte を持たない始まりの message の code(StartupMessage = protocol 3.0)。
PROTOCOL_3 = 196608


@dataclass(frozen=True)
class FrontendMessage:
    """client が送った message 1 つ: kind = 型の byte(1 字)・payload = 長さの後の本文。"""

    kind: str
    payload: bytes


def c_strings(payload: bytes, count: int) -> tuple[str, ...]:
    """payload の頭から NUL で終わる文字列を count 個読む。"""
    parts = payload.split(b"\x00", count)
    return tuple(part.decode("utf-8", "replace") for part in parts[:count])


class WireConversation:
    """接続 1 本の client の側の流れを読み、文と往復の境を見つける係(1 本の thread だけが触る)。"""

    def __init__(self, on_round_trip) -> None:
        self.on_round_trip = on_round_trip
        self._mut_started = False
        self._mut_buffer = b""
        self._mut_statements: dict[str, str] = {}
        self._mut_portals: dict[str, str] = {}
        self._mut_current: tuple[str, ...] = ()

    def feed(self, data: bytes) -> None:
        """client から届いた bytes を足し、読める message を全部読む。"""
        self._mut_buffer += data
        while True:
            message = self.next_message()
            if message is None:
                return
            if message.kind:
                self.read(message)

    def next_message(self) -> FrontendMessage | None:
        """buffer の頭の message 1 つを切り出す(足りなければ None)。始まりの message は kind = ""。"""
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
        """message 1 つを文と往復の境に写す(頭の註)。"""
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
        """今の往復を閉じて記録へ渡す(文の無い往復は数えない)。"""
        if self._mut_current:
            self.on_round_trip(self._mut_current)
        self._mut_current = ()


class StatementWireTap:
    """127.0.0.1 の口で受けて PostgreSQL へ中継し、client が送った文を往復ごとに記録する中継(頭の註)。"""

    def __init__(self, target: str | tuple[str, int]) -> None:
        self.target = target
        self.lock = threading.Lock()
        self._mut_recorded: tuple[tuple[str, ...], ...] = ()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(16)
        self.port: int = self.listener.getsockname()[1]
        threading.Thread(target=self.accept, name="statement-wire-tap", daemon=True).start()

    def server_socket(self) -> socket.socket:
        """中継の先(PostgreSQL)へ繋いだ socket を作る。"""
        if isinstance(self.target, str):
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        else:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.connect(self.target)
        return server

    def accept(self) -> None:
        """接続を受けるたびに、上り(client → server — 読みながら渡す)と下り(server → client)の 2 本の pump を起こす。"""
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
        """source から読んだ bytes を sink へ渡す(上りは渡す前に読む — 答えが返る前に記録が済む)。"""
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
            try:
                sink.sendall(data)
            except OSError:
                return

    def record(self, statements: tuple[str, ...]) -> None:
        """往復 1 回(文の並び)を記録する(どの接続の thread からも呼ばれる)。"""
        with self.lock:
            self._mut_recorded = self._mut_recorded + (statements,)

    def reset(self) -> None:
        """記録を空にする(答え手を走らせる前に呼ぶ)。"""
        with self.lock:
            self._mut_recorded = ()

    def round_trips(self) -> tuple[tuple[str, ...], ...]:
        """reset の後に記録した往復の列(流れた順)。"""
        with self.lock:
            return self._mut_recorded

    def close(self) -> None:
        """待ち受けを閉じる(繋がっている接続は client が閉じた時に閉じる)。"""
        self.listener.close()
