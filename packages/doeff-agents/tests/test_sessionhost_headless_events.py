"""出来事の置き場(headless_events / headless_outbox)の検 — ADR-DOE-AGENTS-012
R-headless-events-go-to-the-tiered-store と R-headless-events-are-read-through-the-host。

3 つの handler(送り待ちの表・memory・file)が同じ契約を持つこと、送り待ちの表の行は送れたと確かめて session が
終わり猶予が過ぎた物だけが外れること、送る body の形(段の DB の表 agentd_records.headless_events が読む属性)を撃つ。
"""

# pyright: strict
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import doeff_hy  # noqa: F401  # registers Hy import hooks
import pytest
from doeff_agents.sessionhost.headless_events import (
    EventKey,
    FileEventStore,
    HeadlessEventAppend,
    HeadlessEventsSince,
    HeadlessEventStore,
    MemoryEventStore,
    key_of_locator,
)
from doeff_agents.sessionhost.headless_outbox import (
    OtlpShipper,
    OutboxEventStore,
    attribution_of,
    prune_shipped,
)
from doeff_agents.sessionhost.store import StoreActor  # type: ignore[attr-defined]

AT = "2026-09-25T00:00:00+00:00"


@pytest.fixture
def root() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="doeff-events-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def actor(root: Path) -> Iterator[StoreActor]:
    store_actor = StoreActor(str(root / "agentd.sqlite"))
    try:
        yield store_actor
    finally:
        store_actor.close()


def _stores(root: Path, actor: StoreActor) -> dict[str, HeadlessEventStore]:
    return {
        "outbox": OutboxEventStore(actor.submit),
        "memory": MemoryEventStore(),
        "file": FileEventStore(),
    }


def _loc(root: Path, sid: str, op: str = "") -> str:
    base = str(root / "events" / f"{sid}.events.jsonl")
    return base + (f".cache-{op}" if op else "")


def test_locator_names_the_session_and_the_cache_op() -> None:
    assert key_of_locator("/x/events/s-1.events.jsonl") == EventKey("s-1", "")
    assert key_of_locator("/x/events/s-1.events.jsonl.cache-ab12") == EventKey("s-1", "ab12")
    assert key_of_locator("/x/events/s-1.jsonl") is None
    assert key_of_locator("/x/events/.events.jsonl") is None


@pytest.mark.parametrize("kind", ["outbox", "memory", "file"])
def test_every_store_reads_back_stdout_lines_after_a_cursor(root: Path, actor: StoreActor, kind: str) -> None:
    store = _stores(root, actor)[kind]
    locator = _loc(root, "s-1")
    store.open_stream(locator)
    assert store.since(HeadlessEventsSince(locator, 0)).text == ""
    store.append(HeadlessEventAppend(locator, "stdout", '{"type":"system"}\n', AT))
    store.append(HeadlessEventAppend(locator, "stderr", "warn: x\n", AT))
    store.append(HeadlessEventAppend(locator, "stdout", '{"type":"result"}', AT))
    first = store.since(HeadlessEventsSince(locator, 0))
    assert first.text == '{"type":"system"}\n{"type":"result"}\n'
    assert store.head(locator) == first.cursor
    store.append(HeadlessEventAppend(locator, "stdout", '{"type":"assistant"}\n', AT))
    second = store.since(HeadlessEventsSince(locator, first.cursor))
    assert second.text == '{"type":"assistant"}\n'
    assert store.since(HeadlessEventsSince(locator, second.cursor)).text == ""
    # cache ping の流れは session の流れと混ざらない
    cache = _loc(root, "s-1", "ab")
    store.append(HeadlessEventAppend(cache, "stdout", '{"type":"cache"}\n', AT))
    assert store.since(HeadlessEventsSince(cache, 0)).text == '{"type":"cache"}\n'
    assert "cache" not in store.since(HeadlessEventsSince(locator, 0)).text


def test_outbox_store_writes_no_file(root: Path, actor: StoreActor) -> None:
    store = OutboxEventStore(actor.submit)
    locator = _loc(root, "s-1")
    store.open_stream(locator)
    store.append(HeadlessEventAppend(locator, "stdout", "{}\n", AT))
    store.append(HeadlessEventAppend(locator, "stderr", "e\n", AT))
    assert not (root / "events").exists()


def test_outbox_numbers_turns_and_sequences_per_session(root: Path, actor: StoreActor) -> None:
    store = OutboxEventStore(actor.submit)
    locator = _loc(root, "s-1")
    store.begin_turn(locator)
    assert store.append(HeadlessEventAppend(locator, "stdout", "a", AT)) == 1
    store.begin_turn(locator)
    assert store.append(HeadlessEventAppend(locator, "stderr", "b", AT)) == 2
    rows = actor.submit(lambda conn: conn.execute("SELECT seq, turn, stream FROM headless_event_outbox ORDER BY seq").fetchall())
    assert rows == [(1, 1, "stdout"), (2, 2, "stderr")]


def _session_row(conn: sqlite3.Connection, sid: str, status: str, finished_at: str | None, conversation: str) -> None:
    conn.execute(
        "INSERT INTO agent_sessions (session_id, session_name, pane_id, agent_type, work_dir, status, backend_kind, "
        "backend_ref_json, started_at, finished_at, launch_attribution_json) VALUES (?, ?, '', 'claude', '/w', ?, "
        "'headless', '{}', '2026-09-25T00:00:00+00:00', ?, ?)",
        (sid, sid, status, finished_at, json.dumps({"agentd": {"conversationId": conversation, "agentJobId": f"job-{sid}"}})),
    )


def test_shipper_sends_the_tiered_row_shape_and_marks_only_what_the_collector_took(root: Path, actor: StoreActor) -> None:
    store = OutboxEventStore(actor.submit)
    actor.submit(lambda conn: _session_row(conn, "s-1", "running", None, "c-ABC"))
    locator = _loc(root, "s-1")
    store.begin_turn(locator)
    store.append(HeadlessEventAppend(locator, "stdout", '{"type":"system"}', AT))
    store.append(HeadlessEventAppend(locator, "stderr", "warn", AT))
    posted: list[tuple[str, dict[str, object]]] = []
    status = {"code": 503}

    def post(url: str, body: bytes) -> int:
        posted.append((url, json.loads(body)))
        return status["code"]

    shipper = OtlpShipper(submit=actor.submit, url="http://collector:4318/", node="agentd-pool-1", post=post)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        shipper.ship_once()
    unshipped = actor.submit(lambda conn: conn.execute("SELECT COUNT(*) FROM headless_event_outbox WHERE shipped_at IS NULL").fetchone()[0])
    assert unshipped == 2
    status["code"] = 200
    assert shipper.drain() == 2
    assert shipper.ship_once().fetched == 0
    url, body = posted[-1]
    assert url == "http://collector:4318/v1/logs"
    resource_logs = body["resourceLogs"]
    assert isinstance(resource_logs, list)
    resource = resource_logs[0]
    resource_attrs = {a["key"]: a["value"]["stringValue"] for a in resource["resource"]["attributes"]}
    assert resource_attrs == {"service.name": "doeff-agentd", "host.name": "agentd-pool-1"}
    records = resource["scopeLogs"][0]["logRecords"]
    assert [r["body"]["stringValue"] for r in records] == ['{"type":"system"}', "warn"]
    attrs = [{a["key"]: a["value"]["stringValue"] for a in r["attributes"]} for r in records]
    assert attrs[0] == {
        "session_id": "s-1",
        "seq": "1",
        "stream": "stdout",
        "op": "",
        "conversation_id": "c-ABC",
        "agent_job_id": "job-s-1",
        "turn": "1",
    }
    assert attrs[1]["stream"] == "stderr"


def test_prune_removes_only_shipped_rows_of_sessions_ended_before_the_cutoff(root: Path, actor: StoreActor) -> None:
    store = OutboxEventStore(actor.submit)

    def seed(conn: sqlite3.Connection) -> None:
        _session_row(conn, "old-done", "done", "2026-09-01T00:00:00+00:00", "c-1")
        _session_row(conn, "live", "running", None, "c-2")
        _session_row(conn, "recent-done", "done", "2026-09-25T09:00:00+00:00", "c-3")
        _session_row(conn, "old-unshipped", "exited", "2026-09-01T00:00:00+00:00", "c-4")

    actor.submit(seed)
    for sid in ("old-done", "live", "recent-done", "old-unshipped"):
        store.append(HeadlessEventAppend(_loc(root, sid), "stdout", f"line of {sid}", AT))
    actor.submit(
        lambda conn: conn.execute(
            "UPDATE headless_event_outbox SET shipped_at = '2026-09-25T00:00:00+00:00' WHERE session_id != 'old-unshipped'"
        )
    )
    removed = actor.submit(lambda conn: prune_shipped(conn, "2026-09-25T08:00:00+00:00"))
    assert removed == 1
    left = actor.submit(lambda conn: [r[0] for r in conn.execute("SELECT session_id FROM headless_event_outbox ORDER BY session_id")])
    assert left == ["live", "old-unshipped", "recent-done"]


def test_attribution_reads_only_the_agentd_block() -> None:
    assert attribution_of(None) == ("", "")
    assert attribution_of("not json") == ("", "")
    assert attribution_of(json.dumps({"agentd": {"conversationId": "c-1"}})) == ("c-1", "")
    assert attribution_of(json.dumps({"other": {"conversationId": "c-1"}})) == ("", "")


def test_the_file_store_is_todays_physics_for_the_mac(root: Path) -> None:
    store = FileEventStore()
    locator = _loc(root, "s-1")
    store.open_stream(locator)
    assert Path(locator).exists()
    assert Path(locator + ".stderr").exists()
    store.append(HeadlessEventAppend(locator, "stdout", "a\n", AT))
    store.append(HeadlessEventAppend(locator, "stderr", "b\n", AT))
    assert Path(locator).read_text() == "a\n"
    assert Path(locator + ".stderr").read_text() == "b\n"
    assert store.head(locator) == os.path.getsize(locator)



def test_agentd_reads_events_only_through_the_host_rpc() -> None:
    """agentd の実況の読み(SessionEvents / SessionEventsHead)は器の要求の閉じた集合に入り、host の RPC で答える —
    手元の file を読む handler(LocalIo)は答えない。"""
    from doeff_agents.sessionhost.acp.effects import (
        SessionEvents,
        SessionEventsHead,
        TranscriptChunk,
    )
    from doeff_agents.sessionhost.acp.handlers import SessionRpc, handles_session_effect

    assert handles_session_effect(SessionEvents(path="/e/s.events.jsonl", offset=0, session_id="s"))
    assert handles_session_effect(SessionEventsHead(path="/e/s.events.jsonl", session_id="s"))
    asked: list[tuple[str, object]] = []

    class Client:
        def request(self, method: str, params: object) -> object:
            asked.append((method, params))
            if method == "session.events_head":
                return {"cursor": 7}
            return {"text": "a\n", "cursor": 9}

    rpc = SessionRpc("/nonexistent.sock")
    rpc._client = Client()  # type: ignore[assignment]
    assert rpc.answer(SessionEvents(path="/e/s.events.jsonl", offset=3, session_id="s")) == TranscriptChunk("a\n", 9)
    assert rpc.answer(SessionEventsHead(path="/e/s.events.jsonl", session_id="s")) == 7
    assert asked == [
        ("session.events_since", {"locator": "/e/s.events.jsonl", "cursor": 3, "session_id": "s"}),
        ("session.events_head", {"locator": "/e/s.events.jsonl", "session_id": "s"}),
    ]


def test_the_composition_root_swaps_in_the_outbox_only_when_the_tiered_store_is_named(
    root: Path, actor: StoreActor, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from doeff_agents.sessionhost import host  # type: ignore[attr-defined]
    from doeff_agents.sessionhost.headless_process import HeadlessRegistry

    registry = HeadlessRegistry()
    monkeypatch.setattr(host, "HEADLESS_REGISTRY", registry)
    monkeypatch.delenv("DOEFF_AGENTD_EVENTS_OTLP_URL", raising=False)
    stop = threading.Event()
    stop.set()
    assert host.install_event_store(actor, stop) is None
    assert isinstance(registry.event_store, FileEventStore)
    monkeypatch.setenv("DOEFF_AGENTD_EVENTS_OTLP_URL", "http://127.0.0.1:9")
    shipper = host.install_event_store(actor, stop)
    assert isinstance(shipper, OtlpShipper)
    assert isinstance(registry.event_store, OutboxEventStore)


def test_a_line_the_tiered_store_cannot_take_is_held_locally_and_does_not_block_the_shipper(
    root: Path, actor: StoreActor
) -> None:
    """段の DB の受けの上限(1 POST の body)を 1 行だけで超える行は送らずに手元に留める(held_at と理由)。
    後ろの行は送れ、留めた行は prune でも外れない(記録は消さない)。実測の最大の行は 5.4 MB(2026-09-25)。"""
    from doeff_agents.sessionhost.headless_outbox import outbox_counts

    store = OutboxEventStore(actor.submit)
    actor.submit(lambda conn: _session_row(conn, "s-1", "done", "2026-09-01T00:00:00+00:00", "c-1"))
    locator = _loc(root, "s-1")
    store.append(HeadlessEventAppend(locator, "stdout", "small-1", AT))
    store.append(HeadlessEventAppend(locator, "stdout", "x" * 6000, AT))
    store.append(HeadlessEventAppend(locator, "stdout", "small-2", AT))
    bodies: list[bytes] = []

    def post(url: str, body: bytes) -> int:
        bodies.append(body)
        return 200

    shipper = OtlpShipper(submit=actor.submit, url="http://c:4318", node="n", post=post, max_body_bytes=4000)
    outcome = shipper.ship_once()
    assert (outcome.shipped, outcome.held) == (2, 1)
    assert all(len(body) <= 4000 for body in bodies)
    sent = [r["body"]["stringValue"] for b in bodies for r in json.loads(b)["resourceLogs"][0]["scopeLogs"][0]["logRecords"]]
    assert sent == ["small-1", "small-2"]
    # 留めた行は次の拍で読み直されない(送り手が詰まらない)
    assert shipper.ship_once().fetched == 0
    held = actor.submit(
        lambda conn: conn.execute("SELECT seq, held_reason FROM headless_event_outbox WHERE held_at IS NOT NULL").fetchall()
    )
    assert [seq for seq, _ in held] == [2]
    assert "exceeds the tiered store's limit 4000" in held[0][1]
    counts = actor.submit(outbox_counts)
    assert (counts.unshipped, counts.held, counts.shipped) == (0, 1, 2)
    # 送れた行だけが外れ、留めた行は残る
    assert actor.submit(lambda conn: prune_shipped(conn, "2026-09-25T00:00:00+00:00")) == 2
    assert actor.submit(outbox_counts).held == 1
    # 実況の読みは留めた行も読む(手元の写しは生きている)
    assert "x" * 6000 in store.since(HeadlessEventsSince(locator, 0)).text


def test_batches_are_split_by_body_bytes_and_a_413_is_narrowed_to_the_one_line(root: Path, actor: StoreActor) -> None:
    store = OutboxEventStore(actor.submit)
    locator = _loc(root, "s-1")
    for index in range(6):
        store.append(HeadlessEventAppend(locator, "stdout", f"line-{index}-" + "y" * 300, AT))
    store.append(HeadlessEventAppend(locator, "stdout", "REFUSED", AT))
    posts: list[list[str]] = []

    def post(url: str, body: bytes) -> int:
        lines = [r["body"]["stringValue"] for r in json.loads(body)["resourceLogs"][0]["scopeLogs"][0]["logRecords"]]
        posts.append(lines)
        return 413 if "REFUSED" in lines else 200

    shipper = OtlpShipper(submit=actor.submit, url="http://c:4318", node="n", post=post, max_body_bytes=2500)
    outcome = shipper.ship_once()
    assert (outcome.fetched, outcome.shipped, outcome.held) == (7, 6, 1)
    assert max(len(lines) for lines in posts) < 7  # 束は body の上限で割れた
    assert ["REFUSED"] in posts  # 413 の束は 1 行ずつ送り直した
    reason = actor.submit(
        lambda conn: conn.execute("SELECT held_reason FROM headless_event_outbox WHERE held_at IS NOT NULL").fetchone()[0]
    )
    assert "413" in reason


def test_other_refusals_keep_the_rows_unshipped_for_the_next_tick(root: Path, actor: StoreActor) -> None:
    store = OutboxEventStore(actor.submit)
    store.append(HeadlessEventAppend(_loc(root, "s-1"), "stdout", "a", AT))
    shipper = OtlpShipper(submit=actor.submit, url="http://c:4318", node="n", post=lambda _url, _body: 500)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        shipper.ship_once()
    from doeff_agents.sessionhost.headless_outbox import outbox_counts

    assert actor.submit(outbox_counts).unshipped == 1
    assert actor.submit(outbox_counts).held == 0


def test_event_times_come_from_the_doeff_time_clock_not_the_os(root: Path, actor: StoreActor) -> None:
    """出来事の at・送れた時刻・留めた時刻は doeff-time の GetTime(組んだ時間の handler)から取る — 置き場も器も
    OS の時計を読まない。模擬環境の仮想の時計(sim_time_handler)で組めば、どの行の時刻も仮想の時刻ちょうどになり、
    順序を不変条件に使える(移行の会話の区画 H の指摘 2026-09-25)。"""
    from datetime import UTC, datetime

    from doeff_agents.sessionhost.headless_events import time_handler_clock
    from doeff_agents.sessionhost.headless_process import HeadlessRegistry
    from doeff_agents.sessionhost.headless_protocol import ClaudeDialogue
    from doeff_time import sim_time_handler

    virtual = datetime(2031, 1, 2, 3, 4, 5, tzinfo=UTC)
    clock = time_handler_clock(sim_time_handler(start_time=virtual))
    assert clock() == virtual.isoformat()
    memory = MemoryEventStore()
    registry = HeadlessRegistry(memory, clock)
    locator = _loc(root, "s-clock")
    process = registry.spawn(
        "s-clock",
        ["sh", "-c", 'echo \'{"type":"system"}\'; echo warn >&2; echo \'{"type":"result"}\''],
        str(root),
        dict(os.environ),
        locator,
        ClaudeDialogue(),
    )
    process.join_io(10.0)
    registry.kill_all()
    assert len(memory.events) == 3
    assert {event.at for event in memory.events} == {virtual.isoformat()}
    # 送り手も同じ時計: 送れた時刻は仮想の時刻
    outbox = OutboxEventStore(actor.submit)
    outbox.append(HeadlessEventAppend(_loc(root, "s-2"), "stdout", "a", clock()))
    shipper = OtlpShipper(submit=actor.submit, url="http://c:4318", node="n", post=lambda _u, _b: 200, clock=clock)
    assert shipper.ship_once().shipped == 1
    shipped_at = actor.submit(lambda conn: conn.execute("SELECT at, shipped_at FROM headless_event_outbox").fetchone())
    assert tuple(shipped_at) == (virtual.isoformat(), virtual.isoformat())


def test_prune_reads_the_same_doeff_time_clock(root: Path, actor: StoreActor, monkeypatch: pytest.MonkeyPatch) -> None:
    """prune の境界も登記簿の時計(doeff-time)から — 仮想の時計の今から猶予を引いた時刻で外す行が決まる。"""
    from datetime import UTC, datetime

    from doeff_agents.sessionhost import host  # type: ignore[attr-defined]
    from doeff_agents.sessionhost.headless_events import time_handler_clock
    from doeff_agents.sessionhost.headless_outbox import prune_cutoff
    from doeff_agents.sessionhost.headless_process import HeadlessRegistry
    from doeff_time import sim_time_handler

    assert prune_cutoff("2031-01-02T03:00:00+00:00", 3600) == "2031-01-02T02:00:00+00:00"
    virtual = datetime(2031, 1, 2, 3, 0, 0, tzinfo=UTC)
    store = OutboxEventStore(actor.submit)
    registry = HeadlessRegistry(store, time_handler_clock(sim_time_handler(start_time=virtual)))
    monkeypatch.setattr(host, "HEADLESS_REGISTRY", registry)
    monkeypatch.delenv("DOEFF_AGENTD_EVENTS_PRUNE_GRACE_SECS", raising=False)

    def seed(conn: sqlite3.Connection) -> None:
        _session_row(conn, "ended-early", "done", "2031-01-02T01:59:00+00:00", "c-1")
        _session_row(conn, "ended-late", "done", "2031-01-02T02:30:00+00:00", "c-2")

    actor.submit(seed)
    for sid in ("ended-early", "ended-late"):
        store.append(HeadlessEventAppend(_loc(root, sid), "stdout", sid, AT))
    actor.submit(lambda conn: conn.execute("UPDATE headless_event_outbox SET shipped_at = ?", (AT,)))
    assert host.events_prune_tick(actor) == 1
    left = actor.submit(lambda conn: [r[0] for r in conn.execute("SELECT session_id FROM headless_event_outbox")])
    assert left == ["ended-late"]
