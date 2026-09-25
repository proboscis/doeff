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
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple, TypeVar, cast

from doeff_agents.sessionhost.headless_events import (
    EventChunk,
    HeadlessEvent,
    HeadlessEventAppend,
    HeadlessEventsSince,
    Stream,
    key_of_locator,
    now_iso,
    strip_newline,
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
        at = now_iso()

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
        "WHERE o.shipped_at IS NULL ORDER BY o.at, o.session_id, o.seq LIMIT ?",
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
    request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return int(response.status)


@dataclass
class OtlpShipper:
    """送り待ちの表 → 段の DB(OTLP/HTTP の collector ``<url>/v1/logs``)。"""

    submit: Submit[object]
    url: str
    node: str
    post: Post = urllib_post
    batch_rows: int = SHIP_BATCH_ROWS

    def ship_once(self) -> int:
        """送っていない行を 1 束送る。戻り = 送れた行の数(0 = 送る物が無い)。2xx 以外・例外は raise
        (行は送っていないまま残り、次の拍で送り直す — collector 側は session_id + seq で畳む)。"""
        batch_rows = self.batch_rows
        events = cast(list[HeadlessEvent], self.submit(lambda conn: unshipped(conn, batch_rows)))
        if not events:
            return 0
        body = json.dumps(otlp_body(events, self.node, now_iso()), ensure_ascii=False).encode("utf-8")
        status = self.post(self.url.rstrip("/") + "/v1/logs", body)
        if not 200 <= status < 300:
            raise RuntimeError(f"OTLP collector answered HTTP {status}; {len(events)} events stay unshipped")
        at = now_iso()
        return cast(int, self.submit(lambda conn: mark_shipped(conn, events, at)))

    def drain(self) -> int:
        total = 0
        while True:
            shipped = self.ship_once()
            total += shipped
            if shipped < self.batch_rows:
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

