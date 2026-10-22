"""PostgreSQL の置き場の書きの錠を、置き場全体の 1 つから錠の単位(行の単位・列ごとの単位)に分けた形の検(card ki-0306f5a614b4)。

錠の設計は pg_sql.hy の頭の註の「書きの錠」: 書き 1 回は置き場全体の錠を共有で、触る単位の錠を排他で持ち、版の繰り上げと表の用意と
旧い版の書きは置き場全体の錠を排他で持つ。本番の組み立て(pg-records-handler の gated・writer-gates・計器)を本物の PostgreSQL で回し、
別の接続で錠を持つ「止め」(pg_advisory_lock — 本番の書きと同じ hashtext の鍵)で書きを DB の中に留めて、次を確かめる:

- 列の追記は、行の単位の錠の後ろで待たない(列の追記が他の表の書きの後ろで待たない形)。行の書きは同じ単位の錠を待つ。
- 置き場全体の錠を排他で持つ書き手(入れ替えの間に重なる旧い版の書き)が居る間は、行の書きも列の追記も待つ(番号の読みの約束を旧い版と
  重なった間も保つ)。
- 同じ単位の書きは、同じ process の中では接続を借りる前に並ぶ(writer-gates): 単位の錠を DB で待つ書きが 1 本でも、後ろの書きは接続を
  持たず、読みは答える。並びを外した組(gated = False)は後ろの書きが接続を持ったまま待ち、読みが接続を待つ(反例)。
- 書きが待った秒と錠を持った秒を、操作・単位ごとの計器の系列 records_lock_<操作>_<単位>_<wait|hold> に積む。

番号の読みの約束そのもの(番号は積んだ順に見える)は、法 law-concurrent-writes-appear-in-number-order(laws.hy)が memory と PostgreSQL の
両方で確かめる。
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import hy  # noqa: F401  Hy の module を読むため
import psycopg
import pytest
from disposable_postgres import session_dsn, session_postgres_skip_reason
from doeff_core_effects.handlers import state
from doeff_core_effects.memory_meter import memory_meter_handler
from doeff_core_effects.meter_effects import MeterSettings, ReadMeter
from doeff_core_effects.pooled_postgres_sql import pooled_postgres_sql_handler
from doeff_core_effects.postgres_sql import PostgresConnections, PostgresDatabase, postgres_sql_handler
from doeff_core_effects.scheduler import Cancel, CreateExternalPromise, Spawn, Wait, scheduled
from doeff_core_effects.thread_pool_compute import thread_pool_compute_handler
from doeff_hy.frozen import FrozenMap
from doeff_records.effects import AppendEvent, AppendEvents, EventAppend, PutRow, ReadEvents, ReadRow
from doeff_records.laws import LAW_SCHEMA, MAKER
from doeff_records.pg import drop_records_tables, pg_records_handler, prepare_records_store, writer_gates
from doeff_records.pg_sql import ROWS_UNIT, stream_unit, unit_lock_key, writer_lock_key
from doeff_records.values import Appended, ExpectAbsent, ExpectAny, Row, Written
from doeff_time import SimClock, sim_time_handler, sync_time_handler

from doeff import do, run, with_handlers

PG_DSN_VARIABLE = "DOEFF_RECORDS_TEST_PG_DSN"
DATABASE = "records"
ORIGIN_HOST = "unit-host"
DEADLINE_SECONDS = 10.0
# 待たないはずの書きが「待っていない」と言うまでに見る秒(止めを外す前に答えが来ない事を確かめる短い窓)。
HELD_WINDOW_SECONDS = 1.0

pytestmark = pytest.mark.skipif(bool(session_postgres_skip_reason(PG_DSN_VARIABLE)), reason="disposable PostgreSQL required")


def dsn():
    return session_dsn(PG_DSN_VARIABLE)


@do
def pause():
    """scheduler の他の task を回しながら 10ms 待つため(止めの外れや DB の錠の待ちを、時計の handler に頼らず見張る)。"""
    promise = yield CreateExternalPromise()
    threading.Timer(0.01, lambda: promise.complete(None)).start()
    yield Wait(promise.future)


@do
def until(predicate, seconds=DEADLINE_SECONDS):
    """predicate が真になるまで待つため(期限を越えれば検の失敗)。"""
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "待ち合わせの期限を越えた"
        yield pause()


@do
def stays_false(predicate, seconds=HELD_WINDOW_SECONDS):
    """seconds の間 predicate が偽のままである事を確かめるため(止めの後ろで待つはずの物が答えていない)。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        assert not predicate(), "止めの後ろで待つはずの物が答えた"
        yield pause()


class Hold:
    """別の接続で記録の書きの錠(本番と同じ hashtext の鍵)を session の advisory lock で持つ止め。"""

    def __init__(self, key, shared=False):
        self.key = key
        self.db = psycopg.connect(dsn(), autocommit=True)
        self.observer = psycopg.connect(dsn(), autocommit=True)
        lock = "pg_advisory_lock_shared" if shared else "pg_advisory_lock"
        self.unlock = "pg_advisory_unlock_shared" if shared else "pg_advisory_unlock"
        self.db.execute(f"SELECT {lock}(hashtext(%s))", (key,))
        self._mut_released = False

    def waiters(self):
        """この鍵の錠を DB の中で待っている接続の数を読むため。"""
        return self.observer.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted AND objid = (hashtext(%s)::bigint & 4294967295)::oid",
            (self.key,),
        ).fetchone()[0]

    def release(self):
        if not self._mut_released:
            self._mut_released = True
            self.db.execute(f"SELECT {self.unlock}(hashtext(%s))", (self.key,))

    def close(self):
        self.release()
        self.db.close()
        self.observer.close()


class Store:
    """検ごとの接頭辞の置き場(用意と後片付け)と、本番の組み立てを真似た走らせ方。"""

    def __init__(self, size):
        self.size = size
        self.connections = PostgresConnections((PostgresDatabase(name=DATABASE, dsn=dsn()),), size=size)
        self.pool = ThreadPoolExecutor(max_workers=size)
        # 束の読みの読み解きを回す計算の pool(records-connected の compute-pool と同じ役 — ADR-DOE-RECORDS-002)。
        self.compute_pool = ThreadPoolExecutor(max_workers=2)
        prefix ="u" + format(time.monotonic_ns() % (1 << 40), "x") + "_"
        self.store = self.run_sql(prepare_records_store(DATABASE, DATABASE, LAW_SCHEMA, prefix))

    def run_sql(self, program):
        return run(scheduled(with_handlers([sim_time_handler(clock=SimClock()), postgres_sql_handler(self.connections)], program)))

    def run(self, program, gated=True):
        """本番の組み立て(records-connected の writer-gates・pooled の答え手・計算の pool・計器)の下で program を走らせるため。
        gated = False は並びを外した組(反例)。"""
        handlers = [state(), sync_time_handler(), memory_meter_handler(MeterSettings())]
        if gated:
            handlers.append(writer_gates)
        handlers.append(pooled_postgres_sql_handler(self.connections, self.pool))
        handlers.append(thread_pool_compute_handler(self.compute_pool))
        return run(scheduled(with_handlers(handlers, program)))

    def as_writer(self, program, gated=True):
        return with_handlers([pg_records_handler(self.store, MAKER, ORIGIN_HOST, gated=gated)], program)

    def close(self):
        self.run_sql(drop_records_tables(self.store))
        self.pool.shutdown()
        self.compute_pool.shutdown()
        self.connections.close()


@pytest.fixture
def store():
    value = Store(size=3)
    try:
        yield value
    finally:
        value.close()


def rows_key(store):
    return run(unit_lock_key(store.store.prefix, ROWS_UNIT))


def journal_key(store):
    return run(unit_lock_key(store.store.prefix, run(stream_unit("journal"))))


def whole_key(store):
    return run(writer_lock_key(store.store.prefix))


@do
def append_beside_held_rows(store, hold):
    """行の単位の錠を止めが持つ間に、行の書きが DB で待ち、列の追記と読みは答える事を確かめるため。"""
    put = yield Spawn(store.as_writer(PutRow("parts", ("p1",), FrozenMap({"label": "a"}), ExpectAbsent())))
    yield until(lambda: hold.waiters() >= 1)
    appended = yield store.as_writer(AppendEvent("journal", "k1", {"n": 1}))
    assert isinstance(appended, Appended) and appended.replayed is False, appended
    read = yield store.as_writer(ReadRow("parts", ("p-none",)))
    assert not isinstance(read, Row), read
    hold.release()
    written = yield Wait(put)
    assert isinstance(written, Written) and written.version == 1, written
    return appended


def test_an_append_does_not_wait_behind_a_held_row_unit(store):
    hold = Hold(rows_key(store))
    try:
        store.run(append_beside_held_rows(store, hold))
    finally:
        hold.close()


@do
def both_wait_behind_the_whole_store(store, hold):
    """置き場全体の錠を排他で持つ書き手(旧い版の書き)が居る間、行の書きも列の追記も待ち、外れた後に答える事を確かめるため。"""
    put = yield Spawn(store.as_writer(PutRow("parts", ("p1",), FrozenMap({"label": "a"}), ExpectAbsent())))
    append = yield Spawn(store.as_writer(AppendEvent("journal", "k1", {"n": 1})))
    yield until(lambda: hold.waiters() >= 2)
    hold.release()
    written = yield Wait(put)
    appended = yield Wait(append)
    assert isinstance(written, Written), written
    assert isinstance(appended, Appended), appended


def test_an_old_version_writer_holding_the_whole_store_holds_every_unit(store):
    hold = Hold(whole_key(store))
    try:
        store.run(both_wait_behind_the_whole_store(store, hold))
    finally:
        hold.close()


@do
def append_held_by_its_stream(store, hold):
    """列の単位の錠を止めが持つ間、その列の追記は待ち、行の書きは答える事を確かめるため(単位は列ごと)。"""
    append = yield Spawn(store.as_writer(AppendEvent("journal", "k1", {"n": 1})))
    yield until(lambda: hold.waiters() >= 1)
    other = yield store.as_writer(AppendEvent("pulses", "k1", {"n": 1}))
    assert isinstance(other, Appended), other
    written = yield store.as_writer(PutRow("parts", ("p1",), FrozenMap({"label": "a"}), ExpectAbsent()))
    assert isinstance(written, Written), written
    hold.release()
    appended = yield Wait(append)
    assert isinstance(appended, Appended), appended


def test_a_held_stream_unit_holds_only_its_own_appends(store):
    hold = Hold(journal_key(store))
    try:
        store.run(append_held_by_its_stream(store, hold))
    finally:
        hold.close()


@do
def queued_writers_leave_a_read_slot(store, hold, gated):
    """同じ単位の書き 3 本を、止めが単位の錠を持つ間に撃ち、読みが接続を得たか(止めが持つ間に答えたか)を返すため(接続 3 本)。"""
    tasks = ()
    for n in range(store.size):
        task = yield Spawn(store.as_writer(PutRow("parts", (f"q{n}",), FrozenMap({"label": "a"}), ExpectAny()), gated=gated))
        tasks = (*tasks, task)
    yield until(lambda: hold.waiters() >= 1)
    reader = yield Spawn(store.as_writer(ReadRow("parts", ("p-none",)), gated=gated))
    done = threading.Event()

    @do
    def watch():
        yield Wait(reader)
        done.set()

    yield Spawn(watch())
    if gated:
        yield until(done.is_set)
        read_while_held = True
        assert hold.waiters() == 1, "並びの後ろの書きが DB の錠を待っている"
    else:
        yield until(lambda: hold.waiters() >= store.size)
        yield stays_false(done.is_set)
        read_while_held = False
    hold.release()
    for task in tasks:
        written = yield Wait(task)
        assert isinstance(written, Written), written
    yield until(done.is_set)
    return read_while_held


@pytest.mark.parametrize("gated", [True, False], ids=["gated", "counterexample-ungated"])
def test_same_unit_writers_queue_before_taking_a_connection(store, gated):
    hold = Hold(rows_key(store))
    try:
        read_while_held = store.run(queued_writers_leave_a_read_slot(store, hold, gated), gated=gated)
    finally:
        hold.close()
    assert read_while_held is gated


@do
def metered_writes(store):
    """行の書きと列の追記を 1 つずつ撃ち、計器の断面を返すため。"""
    yield store.as_writer(PutRow("parts", ("p1",), FrozenMap({"label": "a"}), ExpectAbsent()))
    yield store.as_writer(AppendEvent("journal", "k1", {"n": 1}))
    snapshot = yield ReadMeter()
    return snapshot


def test_lock_seconds_are_metered_by_operation_and_unit(store):
    snapshot = store.run(metered_writes(store))
    durations = snapshot.durations
    for name in (
        "records_lock_put_row_rows_wait",
        "records_lock_put_row_rows_hold",
        "records_lock_append_event_stream_journal_wait",
        "records_lock_append_event_stream_journal_hold",
    ):
        assert name in durations and durations[name].count == 1, (name, sorted(durations))


class InFlightAppend:
    """別の接続で、本番の書きと同じ錠(置き場全体を共有・列 journal の単位を排他)を取り、列 journal に 1 つ積んで番号を取ったまま commit
    しない書き手(番号を取ってから commit するまでの窓を開いたまま止める)。"""

    def __init__(self, store):
        prefix = store.store.prefix
        self.db = psycopg.connect(dsn())
        self.db.execute("SELECT pg_advisory_xact_lock_shared(hashtext(%s))", (whole_key(store),))
        self.db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (journal_key(store),))
        epoch = self.db.execute(f"SELECT epoch FROM {prefix}store_epoch").fetchone()[0]
        payload = '{"body":{"n":0},"idempotencyKey":"in-flight","writer":"maker"}'
        self.sequence = self.db.execute(
            f"INSERT INTO {prefix}append_rows (ledger, at, payload, origin_host, epoch) VALUES ('journal', 0, %s, 'other', %s) RETURNING seq",
            (payload, epoch),
        ).fetchone()[0]
        self.observer = psycopg.connect(dsn(), autocommit=True)
        self.key = journal_key(store)

    def waiters(self):
        return Hold.waiters(self)

    def commit(self):
        self.db.commit()

    def close(self):
        self.db.close()
        self.observer.close()


@do
def reader_beside_an_in_flight_append(store, flight):
    """番号を取った書き(flight)が commit する前に同じ列へ積む書きを撃ち、読み手が位置を進めながら読む並びを返すため。"""
    append = yield Spawn(store.as_writer(AppendEvent("journal", "after", {"n": 1})))
    answered = threading.Event()

    @do
    def watch():
        yield Wait(append)
        answered.set()

    yield Spawn(watch())
    yield until(lambda: flight.waiters() >= 1 or answered.is_set())
    seen = ()
    after = 0
    page = yield store.as_writer(ReadEvents("journal", after=after))
    seen = (*seen, *(event.sequence for event in page.items))
    after = seen[-1] if seen else after
    flight.commit()
    appended = yield Wait(append)
    page = yield store.as_writer(ReadEvents("journal", after=after))
    seen = (*seen, *(event.sequence for event in page.items))
    whole = yield store.as_writer(ReadEvents("journal"))
    return seen, tuple(event.sequence for event in whole.items), appended


def test_a_reader_never_skips_a_number_taken_before_its_cursor(store):
    flight = InFlightAppend(store)
    try:
        seen, whole, appended = store.run(reader_beside_an_in_flight_append(store, flight))
    finally:
        flight.close()
    assert whole == (flight.sequence, appended.sequence), (whole, flight.sequence, appended)
    assert seen == whole, f"読み手が位置を進めながら読んだ番号 {seen} が列の全部 {whole} と違う(取りこぼし)"


def trails_key(store):
    return run(unit_lock_key(store.store.prefix, run(stream_unit("trails"))))


@do
def cancelled_between_two_units(store, hold):
    """束の追記が列 journal の並びを通って列 trails の並びを待つ間に取り消されても、journal の並びが返る事を確かめるため。"""
    blocker = yield Spawn(store.as_writer(AppendEvent("trails", "first", {"n": 0})))
    yield until(lambda: hold.waiters() >= 1)
    batch = yield Spawn(
        store.as_writer(AppendEvents((EventAppend("journal", "both", {"n": 1}), EventAppend("trails", "both#1", {"n": 1}))))
    )
    yield pause()
    yield pause()
    yield Cancel(batch)
    appended = yield store.as_writer(AppendEvent("journal", "after-cancel", {"n": 2}))
    assert isinstance(appended, Appended), appended
    hold.release()
    first = yield Wait(blocker)
    assert isinstance(first, Appended), first


def test_a_cancelled_batch_returns_the_units_it_passed(store):
    hold = Hold(trails_key(store))
    try:
        store.run(cancelled_between_two_units(store, hold))
    finally:
        hold.close()
