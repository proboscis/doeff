"""出来事の置き場の本番の handler(pod): host の sqlite の送り待ちの表 + 段の DB への送り手。

形(transactional outbox の既知の形): 子 process の行は読み手 thread が store の actor へ 1 行ずつ積み
(``OutboxEventStore.append``)、送り手(``OtlpShipper``)が送っていない行を OTLP/HTTP で段の DB
(namespace doeff-worker-lab の effect-telemetry — collector → ClickHouse の表 ``agentd_headless_events``)へ
まとめて送り、2xx を受けた行にだけ ``shipped_at`` を刻む。実況の読み(``session.events_since``)は表から
読むので、送れたかどうかに依らず手番の途中の読みは速い。

表から行を外すのは ``prune_shipped`` の 1 点だけで、条件は 3 つ全部: 送れたと確かめた(shipped_at が在る)∧
その session の行が終端 ∧ 終端から猶予(既定 1 時間 — 実況の読み手が残りを読み切る間)が過ぎた。送れていない
行・動いている session の行は外さない(記録は消さない — 移してからローカルの写しを外す)。

段の DB に届かない間は表が伸びる(送り手は失敗を log して次の拍へ)。容量が尽きれば store の書き込みが
失敗し、host の readiness が落ちる(ADR-DOE-AGENTS-004 R15)— 黙って捨てない。
"""

# pyright: strict
import json
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import NamedTuple, TypeVar, cast

from doeff_agents.sessionhost.headless_events import (
    EventChunk,
    EventClock,
    HeadlessEvent,
    HeadlessEventAppend,
    HeadlessEventsSince,
    Stream,
    key_of_locator,
    strip_newline,
    wall_clock,
)

T = TypeVar("T")
#: store の actor の口(StoreActor.submit)— op(conn を取る callable)を actor の thread で走らせて答えを返す。
Submit = Callable[[Callable[[sqlite3.Connection], T]], T]
#: OTLP の POST の口(url, body bytes)→ HTTP status。本番は urllib・検は fake。
Post = Callable[[str, bytes], int]

SERVICE_NAME = "doeff-agentd"
SCOPE_NAME = "doeff.sessionhost.headless-events"
#: 1 回の送りの行数の上限と送りの周期(秒)。
SHIP_BATCH_ROWS = 500
SHIP_INTERVAL_SECONDS = 2.0
#: 送れた行を、session が終わってからどれだけ残すか(秒)— 実況の読み手が残りを読み切る猶予。
PRUNE_GRACE_SECONDS_DEFAULT = 3600
TERMINAL_STATUSES = ("done", "failed", "exited", "stopped", "cancelled")


class OutboxEventStore:
    """送り待ちの表の置き場(HeadlessEventStore の本番の実装)。"""

    def __init__(self, submit: Submit[object]) -> None:
        self._submit = submit
        # 時刻は呼び手が HeadlessEventAppend.at で渡す(doeff-time の GetTime)— ここは時計を読まない。
        self._lock = threading.Lock()
        self._turns: dict[str, int] = {}

    def _turn_of(self, session_id: str) -> int:
        with self._lock:
            known = self._turns.get(session_id)
        if known is not None:
            return known

        def read(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT COALESCE(MAX(turn), 0) FROM headless_event_outbox WHERE session_id = ?", (session_id,)
            ).fetchone()
            return int(row[0])

        found = cast(int, self._submit(read))
        with self._lock:
            return self._turns.setdefault(session_id, found)

    def open_stream(self, locator: str) -> None:
        return None

    def append(self, effect: HeadlessEventAppend) -> int:
        key = key_of_locator(effect.locator)
        if key is None:
            raise ValueError(f"not a headless events locator: {effect.locator!r}")
        turn = self._turn_of(key.session_id)
        line = strip_newline(effect.line)
        at = effect.at

        def insert(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM headless_event_outbox WHERE session_id = ?", (key.session_id,)
            ).fetchone()
            seq = int(row[0])
            conn.execute(
                "INSERT INTO headless_event_outbox (session_id, seq, op, stream, line, at, turn) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key.session_id, seq, key.op, effect.stream, line, at, turn),
            )
            return seq

        return cast(int, self._submit(insert))

    def begin_turn(self, locator: str) -> None:
        key = key_of_locator(locator)
        if key is None or key.op:
            return
        current = self._turn_of(key.session_id)
        with self._lock:
            self._turns[key.session_id] = max(current, self._turns.get(key.session_id, 0)) + 1

    def since(self, effect: HeadlessEventsSince) -> EventChunk:
        key = key_of_locator(effect.locator)
        if key is None:
            return EventChunk("", effect.cursor)

        def read(conn: sqlite3.Connection) -> list[tuple[int, str]]:
            rows = conn.execute(
                "SELECT seq, line FROM headless_event_outbox "
                "WHERE session_id = ? AND op = ? AND stream = 'stdout' AND seq > ? ORDER BY seq",
                (key.session_id, key.op, effect.cursor),
            ).fetchall()
            return [(int(seq), str(line)) for seq, line in rows]

        rows = cast(list[tuple[int, str]], self._submit(read))
        if not rows:
            return EventChunk("", effect.cursor)
        return EventChunk("".join(line + "\n" for _, line in rows), rows[-1][0])


    def head(self, locator: str) -> int:
        key = key_of_locator(locator)
        if key is None:
            return 0

        def read(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM headless_event_outbox "
                "WHERE session_id = ? AND op = ? AND stream = 'stdout'",
                (key.session_id, key.op),
            ).fetchone()
            return int(row[0])

        return cast(int, self._submit(read))


class Attribution(NamedTuple):
    """session の帰属(launch_attribution の agentd の欄)— 無い欄は ""(発明しない)。"""

    conversation_id: str
    agent_job_id: str


NO_ATTRIBUTION = Attribution("", "")


def attribution_of(raw: str | None) -> Attribution:
    """session の行の launch_attribution_json → Attribution。"""
    if not raw:
        return NO_ATTRIBUTION
    try:
        value: object = json.loads(raw)
    except ValueError:
        return NO_ATTRIBUTION
    if not isinstance(value, dict):
        return NO_ATTRIBUTION
    mine = cast(dict[str, object], value).get("agentd")
    if not isinstance(mine, dict):
        return NO_ATTRIBUTION
    fields = cast(dict[str, object], mine)
    conversation = fields.get("conversationId")
    job = fields.get("agentJobId")
    return Attribution(conversation if isinstance(conversation, str) else "", job if isinstance(job, str) else "")


def unshipped(conn: sqlite3.Connection, limit: int) -> list[HeadlessEvent]:
    """送っていない行(古い順)に session の帰属を付けて読む。"""
    rows = conn.execute(
        "SELECT o.session_id, o.seq, o.stream, o.op, o.line, o.at, o.turn, s.launch_attribution_json "
        "FROM headless_event_outbox o LEFT JOIN agent_sessions s ON s.session_id = o.session_id "
        "WHERE o.shipped_at IS NULL AND o.held_at IS NULL ORDER BY o.at, o.session_id, o.seq LIMIT ?",
        (limit,),
    ).fetchall()
    events: list[HeadlessEvent] = []
    for session_id, seq, stream, op, line, at, turn, attribution in rows:
        conversation, job = attribution_of(attribution)
        events.append(
            HeadlessEvent(
                session_id=str(session_id),
                seq=int(seq),
                stream=cast(Stream, stream),
                op=str(op),
                line=str(line),
                at=str(at),
                turn=int(turn),
                conversation_id=conversation,
                agent_job_id=job,
            )
        )
    return events


def mark_shipped(conn: sqlite3.Connection, events: list[HeadlessEvent], at: str) -> int:
    conn.executemany(
        "UPDATE headless_event_outbox SET shipped_at = ? WHERE session_id = ? AND seq = ?",
        [(at, event.session_id, event.seq) for event in events],
    )
    return len(events)


def mark_held(conn: sqlite3.Connection, event: HeadlessEvent, at: str, reason: str) -> None:
    """段の DB が受けない行を手元に留める(送らない・外さない — 記録は消さない)。"""
    conn.execute(
        "UPDATE headless_event_outbox SET held_at = ?, held_reason = ? WHERE session_id = ? AND seq = ?",
        (at, reason, event.session_id, event.seq),
    )


def outbox_counts(conn: sqlite3.Connection) -> "OutboxCounts":
    """送り待ちの表の数(daemon.status の観測): 送っていない・留めた・送れた。"""
    row = conn.execute(
        "SELECT "
        "COALESCE(SUM(CASE WHEN shipped_at IS NULL AND held_at IS NULL THEN 1 ELSE 0 END), 0), "
        "COALESCE(SUM(CASE WHEN held_at IS NOT NULL THEN 1 ELSE 0 END), 0), "
        "COALESCE(SUM(CASE WHEN shipped_at IS NOT NULL THEN 1 ELSE 0 END), 0) "
        "FROM headless_event_outbox"
    ).fetchone()
    return OutboxCounts(unshipped=int(row[0]), held=int(row[1]), shipped=int(row[2]))


@dataclass(frozen=True)
class OutboxCounts:
    unshipped: int
    held: int
    shipped: int


def prune_cutoff(now: str, grace_seconds: int) -> str:
    """prune の境界 = 時計の今(doeff-time の GetTime の ISO)から猶予を引いた時刻。純関数(時計を読まない)。"""
    return (datetime.fromisoformat(now) - timedelta(seconds=grace_seconds)).isoformat()


def prune_shipped(conn: sqlite3.Connection, cutoff_iso: str) -> int:
    """送れた ∧ session が終端 ∧ 終端が cutoff より前 の行だけを外す(唯一の外す点)。"""
    marks = ",".join("?" for _ in TERMINAL_STATUSES)
    cursor = conn.execute(
        "DELETE FROM headless_event_outbox WHERE shipped_at IS NOT NULL AND session_id IN ("
        f"SELECT session_id FROM agent_sessions WHERE status IN ({marks}) "
        "AND COALESCE(finished_at, last_observed_at) IS NOT NULL "
        "AND COALESCE(finished_at, last_observed_at) < ?)",
        (*TERMINAL_STATUSES, cutoff_iso),
    )
    return cursor.rowcount


def _time_unix_nano(at: str) -> str:
    return str(int(datetime.fromisoformat(at).timestamp() * 1_000_000_000))


def otlp_body(events: list[HeadlessEvent], node: str, observed_iso: str) -> dict[str, object]:
    """出来事の行 → OTLP/HTTP JSON の body(行 1 つ = log record 1 件・属性は全部 string)。純関数。"""

    def attr(key: str, value: str) -> dict[str, object]:
        return {"key": key, "value": {"stringValue": value}}

    observed = _time_unix_nano(observed_iso)
    records: list[dict[str, object]] = [
        {
            "timeUnixNano": _time_unix_nano(event.at),
            "observedTimeUnixNano": observed,
            "body": {"stringValue": event.line},
            "attributes": [
                attr("session_id", event.session_id),
                attr("seq", str(event.seq)),
                attr("stream", event.stream),
                attr("op", event.op),
                attr("conversation_id", event.conversation_id),
                attr("agent_job_id", event.agent_job_id),
                attr("turn", str(event.turn)),
            ],
        }
        for event in events
    ]
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": [attr("service.name", SERVICE_NAME), attr("host.name", node)]},
                "scopeLogs": [{"scope": {"name": SCOPE_NAME}, "logRecords": records}],
            }
        ]
    }


def urllib_post(url: str, body: bytes) -> int:
    """POST して HTTP status を返す(2xx 以外も値で返す — 413 を送り手が読むため)。"""
    request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)


#: 1 回の POST の body の上限(byte)。段の DB の受け(collector の OTLP/HTTP の max_request_body_size)に合わせる —
#: 値の正本は agora-controllers の effect-telemetry の collector の設定で、ここは env で揃える
#: (DOEFF_AGENTD_EVENTS_MAX_BODY_BYTES)。1 行でこれを超える行は送らずに手元に留める。
MAX_BODY_BYTES_DEFAULT = 8 * 1024 * 1024
HTTP_PAYLOAD_TOO_LARGE = 413


def encoded_body(events: list[HeadlessEvent], node: str, observed: str) -> bytes:
    return json.dumps(otlp_body(events, node, observed), ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class ShipOutcome:
    """1 束の結末: 読んだ行・送れた行・留めた行の数。"""

    fetched: int
    shipped: int
    held: int


@dataclass
class OtlpShipper:
    """送り待ちの表 → 段の DB(OTLP/HTTP の collector ``<url>/v1/logs``)。"""

    submit: Submit[object]
    url: str
    node: str
    post: Post = urllib_post
    batch_rows: int = SHIP_BATCH_ROWS
    max_body_bytes: int = MAX_BODY_BYTES_DEFAULT
    #: 送った時刻・留めた時刻の時計(doeff-time — 本番は壁時計・模擬環境は仮想の時計)。
    clock: EventClock = field(default_factory=wall_clock)

    def _hold(self, event: HeadlessEvent, reason: str) -> None:
        at = self.clock()
        self.submit(lambda conn: mark_held(conn, event, at, reason))
        sys.stderr.write(
            f"doeff-sessionhost events shipper: held {event.session_id}#{event.seq} ({len(event.line.encode('utf-8'))} bytes): {reason}\n"
        )

    def _send(self, events: list[HeadlessEvent]) -> int:
        """1 つの POST。戻り = HTTP status。2xx なら送れた印を刻む。"""
        status = self.post(self.url.rstrip("/") + "/v1/logs", encoded_body(events, self.node, self.clock()))
        if 200 <= status < 300:
            at = self.clock()
            self.submit(lambda conn: mark_shipped(conn, events, at))
        return status

    def ship_once(self) -> ShipOutcome:
        """送っていない行を 1 束送る。

        - 1 行だけで body の上限を超える行は送らずに留める(held_at と理由 — 送り手を詰まらせない・消さない)。
        - 残りは body が上限に収まる束に分けて送る。
        - 受けが 413 を返した束は 1 行ずつ送り直し、それでも 413 の行だけを留める。
        - それ以外の 2xx でない答え・例外は raise(行は送っていないまま残り、次の拍で送り直す — 表は
          session_id + seq で畳む)。"""
        batch_rows = self.batch_rows
        events = cast(list[HeadlessEvent], self.submit(lambda conn: unshipped(conn, batch_rows)))
        shipped = 0
        held = 0
        chunk: list[HeadlessEvent] = []
        chunk_bytes = 0
        chunks: list[list[HeadlessEvent]] = []
        for event in events:
            size = len(encoded_body([event], self.node, event.at))
            if size > self.max_body_bytes:
                self._hold(event, f"one OTLP body of {size} bytes exceeds the tiered store's limit {self.max_body_bytes}")
                held += 1
                continue
            if chunk and chunk_bytes + size > self.max_body_bytes:
                chunks.append(chunk)
                chunk, chunk_bytes = [], 0
            chunk.append(event)
            chunk_bytes += size
        if chunk:
            chunks.append(chunk)
        for part in chunks:
            status = self._send(part)
            if 200 <= status < 300:
                shipped += len(part)
                continue
            if status != HTTP_PAYLOAD_TOO_LARGE:
                raise RuntimeError(f"OTLP collector answered HTTP {status}; {len(part)} events stay unshipped")
            for event in part:
                single = self._send([event])
                if 200 <= single < 300:
                    shipped += 1
                elif single == HTTP_PAYLOAD_TOO_LARGE:
                    self._hold(event, "the collector answered HTTP 413 for this line alone")
                    held += 1
                else:
                    raise RuntimeError(f"OTLP collector answered HTTP {single}; events stay unshipped")
        return ShipOutcome(fetched=len(events), shipped=shipped, held=held)

    def drain(self) -> int:
        total = 0
        while True:
            outcome = self.ship_once()
            total += outcome.shipped
            if outcome.fetched < self.batch_rows:
                return total

    def loop(self, stop: threading.Event, interval: float = SHIP_INTERVAL_SECONDS) -> None:
        while not stop.is_set():
            try:
                self.drain()
            except Exception as error:
                sys.stderr.write(f"doeff-sessionhost events shipper error: {error}\n")
                sys.stderr.flush()
            stop.wait(interval)


def run_forever(shipper: OtlpShipper, stop: threading.Event) -> threading.Thread:
    thread = threading.Thread(target=shipper.loop, args=(stop,), daemon=True, name="sessionhost-events-shipper")
    thread.start()
    return thread

