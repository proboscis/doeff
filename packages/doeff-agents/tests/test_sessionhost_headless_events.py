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
    store.append(HeadlessEventAppend(locator, "stdout", '{"type":"system"}\n'))
    store.append(HeadlessEventAppend(locator, "stderr", "warn: x\n"))
    store.append(HeadlessEventAppend(locator, "stdout", '{"type":"result"}'))
    first = store.since(HeadlessEventsSince(locator, 0))
    assert first.text == '{"type":"system"}\n{"type":"result"}\n'
    assert store.head(locator) == first.cursor
    store.append(HeadlessEventAppend(locator, "stdout", '{"type":"assistant"}\n'))
    second = store.since(HeadlessEventsSince(locator, first.cursor))
    assert second.text == '{"type":"assistant"}\n'
    assert store.since(HeadlessEventsSince(locator, second.cursor)).text == ""
    # cache ping の流れは session の流れと混ざらない
    cache = _loc(root, "s-1", "ab")
    store.append(HeadlessEventAppend(cache, "stdout", '{"type":"cache"}\n'))
    assert store.since(HeadlessEventsSince(cache, 0)).text == '{"type":"cache"}\n'
    assert "cache" not in store.since(HeadlessEventsSince(locator, 0)).text


def test_outbox_store_writes_no_file(root: Path, actor: StoreActor) -> None:
    store = OutboxEventStore(actor.submit)
    locator = _loc(root, "s-1")
    store.open_stream(locator)
    store.append(HeadlessEventAppend(locator, "stdout", "{}\n"))
    store.append(HeadlessEventAppend(locator, "stderr", "e\n"))
    assert not (root / "events").exists()


def test_outbox_numbers_turns_and_sequences_per_session(root: Path, actor: StoreActor) -> None:
    store = OutboxEventStore(actor.submit)
    locator = _loc(root, "s-1")
    store.begin_turn(locator)
    assert store.append(HeadlessEventAppend(locator, "stdout", "a")) == 1
    store.begin_turn(locator)
    assert store.append(HeadlessEventAppend(locator, "stderr", "b")) == 2
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
    store.append(HeadlessEventAppend(locator, "stdout", '{"type":"system"}'))
    store.append(HeadlessEventAppend(locator, "stderr", "warn"))
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
        store.append(HeadlessEventAppend(_loc(root, sid), "stdout", f"line of {sid}"))
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
    store.append(HeadlessEventAppend(locator, "stdout", "a\n"))
    store.append(HeadlessEventAppend(locator, "stderr", "b\n"))
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


def test_migration_plans_ships_deterministically_and_verifies_without_deleting(root: Path, actor: StoreActor) -> None:
    """配備前の file の取り込みの道具: 非終端の session は送らない・seq は file の中の順で決まる(送り直しが畳める)・
    verify は行数の一致した file の一覧を出すだけで、1 つも消さない。"""
    from doeff_agents.sessionhost import headless_events_migrate as migrate

    events_dir = root / "events"
    events_dir.mkdir()
    (events_dir / "old.events.jsonl").write_text('{"a":1}\n{"a":2}\n')
    (events_dir / "old.events.jsonl.stderr").write_text("warn\n")
    (events_dir / "old.events.jsonl.cache-ff").write_text('{"c":1}\n')
    (events_dir / "live.events.jsonl").write_text('{"b":1}\n')
    actor.submit(lambda conn: _session_row(conn, "old", "done", "2026-09-01T00:00:00+00:00", "c-OLD"))
    actor.submit(lambda conn: _session_row(conn, "live", "running", None, "c-LIVE"))
    db = str(root / "agentd.sqlite")
    items = {item.session_id: item for item in migrate.plan(str(events_dir), db)}
    assert items["old"].lines == 4
    assert not items["old"].deferred
    assert items["live"].deferred
    events = migrate.events_of(items["old"], "c-OLD", "job-old")
    assert [(e.seq, e.stream, e.op, e.line) for e in events] == [
        (1, "stdout", "", '{"a":1}'),
        (2, "stdout", "", '{"a":2}'),
        (3, "stderr", "", "warn"),
        (4, "stdout", "ff", '{"c":1}'),
    ]
    assert migrate.events_of(items["old"], "c-OLD", "job-old") == events
    assert migrate.ship(str(events_dir), db, "http://unused", "node", apply=False) == {
        "sessions": 1,
        "rows": 4,
        "applied": 0,
    }
    result = migrate.verify(str(events_dir), db, {"old": 4})
    assert sorted(os.path.basename(path) for path in result["verified_files"]) == [
        "old.events.jsonl",
        "old.events.jsonl.cache-ff",
        "old.events.jsonl.stderr",
    ]
    assert migrate.verify(str(events_dir), db, {"old": 3})["mismatched_sessions"] == ["old"]
    assert (events_dir / "old.events.jsonl").exists()


def test_a_line_the_tiered_store_cannot_take_is_held_locally_and_does_not_block_the_shipper(
    root: Path, actor: StoreActor
) -> None:
    """段の DB の受けの上限(1 POST の body)を 1 行だけで超える行は送らずに手元に留める(held_at と理由)。
    後ろの行は送れ、留めた行は prune でも外れない(記録は消さない)。実測の最大の行は 5.4 MB(2026-09-25)。"""
    from doeff_agents.sessionhost.headless_outbox import outbox_counts

    store = OutboxEventStore(actor.submit)
    actor.submit(lambda conn: _session_row(conn, "s-1", "done", "2026-09-01T00:00:00+00:00", "c-1"))
    locator = _loc(root, "s-1")
    store.append(HeadlessEventAppend(locator, "stdout", "small-1"))
    store.append(HeadlessEventAppend(locator, "stdout", "x" * 6000))
    store.append(HeadlessEventAppend(locator, "stdout", "small-2"))
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
        store.append(HeadlessEventAppend(locator, "stdout", f"line-{index}-" + "y" * 300))
    store.append(HeadlessEventAppend(locator, "stdout", "REFUSED"))
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
    store.append(HeadlessEventAppend(_loc(root, "s-1"), "stdout", "a"))
    shipper = OtlpShipper(submit=actor.submit, url="http://c:4318", node="n", post=lambda _url, _body: 500)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        shipper.ship_once()
    from doeff_agents.sessionhost.headless_outbox import outbox_counts

    assert actor.submit(outbox_counts).unshipped == 1
    assert actor.submit(outbox_counts).held == 0
