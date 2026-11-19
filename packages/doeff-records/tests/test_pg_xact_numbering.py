"""PostgreSQL の置き場の変更の列の番号の transaction の形('xact' — _pg_sql.hy の頭の註の「番号の形」)の検。

transaction の形は番号の錠を取らず、変更の番号 = 書きの transaction の xid8 × 2^20 + transaction の中の順にし、読み手は snapshot の xmin
より前の transaction の行だけを読む。本物の PostgreSQL で次を確かめる:

- 番号を先に取った書き(古い xid の transaction)が、後から番号を取った書きより後に commit する並びで、WatchChanges も ReadChangeLog も
  古い方の変更を取りこぼさない(古い方が commit するまで、新しい方の変更を頭の手前で止める)。番号の錠を外しただけの形(読み手が
  max(seq) まで読む)では、新しい方を見て位置を進め、後から commit した古い方を永久に取りこぼす。
- 番号の錠を別の接続が持っていても、transaction の形の行の書きは待たない(錠の形の書きは待つ — 反例)。
- 切り替えの後、錠の形の番号を取る書き(旧い版の行の書き — sequence の次の番号)は sequence の上限で断られる(黙って取りこぼさない)。
- 切り替えの前の位置は、切り替えの後もそのまま読める(Reset にならない)。
"""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import hy  # noqa: F401  Hy の module を読むため
import psycopg
import pytest
from disposable_postgres import session_dsn, session_postgres_skip_reason
from doeff_core_effects import (
    CreateExternalPromise,
    PostgresConnections,
    PostgresDatabase,
    Spawn,
    Wait,
    inline_compute_handler,
    postgres_sql_handler,
    scheduled,
)
from doeff_hy._frozen import FrozenMap
from doeff_records import (
    Changes,
    ExpectAbsent,
    PutRow,
    ReadChangeLog,
    Reset,
    WatchChanges,
    WatchCursor,
    Written,
    pg_records_handler,
)
from doeff_records._laws import LAW_SCHEMA, MAKER
from doeff_records._pg import drop_records_tables, prepare_records_store, switch_numbering
from doeff_records._pg_sql import (
    NUMBERING_LOCK,
    NUMBERING_XACT,
    ROWS_UNIT,
    XACT_NUMBER_SPAN,
    append_change_statement,
    unit_lock_key,
)
from doeff_time import SimClock, sim_time_handler

from doeff import do, run, with_handlers

PG_DSN_VARIABLE = "DOEFF_RECORDS_TEST_PG_DSN"
DATABASE = "records"
ORIGIN_HOST = "xact-host"
DEADLINE_SECONDS = 10.0
TABLES = ("parts", "tickets")

pytestmark = pytest.mark.skipif(bool(session_postgres_skip_reason(PG_DSN_VARIABLE)), reason="disposable PostgreSQL required")


def dsn():
    return session_dsn(PG_DSN_VARIABLE)


class Store:
    """検ごとの接頭辞の置き場(用意と後片付け)と、仮想の時計の下の走らせ方。"""

    def __init__(self, numbering):
        self.connections = PostgresConnections((PostgresDatabase(name=DATABASE, dsn=dsn()),), size=3)
        prefix = "x" + format(time.monotonic_ns() % (1 << 40), "x") + "_"
        self.store = self.run(prepare_records_store(DATABASE, DATABASE, LAW_SCHEMA, prefix))
        if numbering != NUMBERING_LOCK:
            self.run(switch_numbering(self.store, False, numbering))

    def run(self, program):
        return run(scheduled(with_handlers([sim_time_handler(clock=SimClock()), inline_compute_handler,
                                            postgres_sql_handler(self.connections)], program)))

    def as_writer(self, program):
        return self.run(with_handlers([pg_records_handler(self.store, MAKER, ORIGIN_HOST)], program))

    def close(self):
        self.run(drop_records_tables(self.store))
        self.connections.close()


@pytest.fixture
def xact_store():
    value = Store(NUMBERING_XACT)
    try:
        yield value
    finally:
        value.close()


@pytest.fixture
def lock_store():
    value = Store(NUMBERING_LOCK)
    try:
        yield value
    finally:
        value.close()


NEUTRAL_PARAMETER = re.compile(r"(?<![:\w]):([a-z_][a-z0-9_]*)")


def psycopg_text(statement):
    """中立の記法の文(`:name`)を psycopg の `%(name)s` の文にするため(答え手の postgres-statement と同じ書き換え — `::型` は残す)。"""
    return NEUTRAL_PARAMETER.sub(r"%(\1)s", statement.text.replace("%", "%%"))


class EarlyWriter:
    """別の接続で、行の書きと同じ文で変更を 1 つ積み、commit を遅らせる書き手(番号を先に取って後に commit する transaction)。"""

    def __init__(self, store, key):
        self.db = psycopg.connect(dsn())
        statement = run(append_change_statement(store.store.prefix, "parts", key, 1, '{"id":"' + key + '","label":"early"}',
                                                1, 0))
        epoch = self.db.execute(f"SELECT epoch FROM {store.store.prefix}store_epoch WHERE id = 1").fetchone()[0]
        params = {param.name: param.value for param in statement.params}
        params["epoch"] = epoch
        self.sequence = self.db.execute(psycopg_text(statement), params).fetchone()[0]

    def commit(self):
        self.db.commit()

    def close(self):
        self.db.close()


def start_cursor(store):
    """変更の無い置き場の頭の位置(読み手の始まり)を、待たない WatchChanges の答えから読むため。"""
    answer = store.as_writer(WatchChanges(TABLES, WatchCursor(1, 0), timeout=0))
    assert isinstance(answer, Reset), answer
    return WatchCursor(answer.epoch, answer.floor)


def watched(store, cursor):
    """待たない WatchChanges 1 回の答え(Changes)を読むため。"""
    answer = store.as_writer(WatchChanges(TABLES, cursor, timeout=0))
    assert isinstance(answer, Changes), answer
    return answer


def logged(store, after):
    """ReadChangeLog 1 回の変更の番号の並びを読むため。"""
    log = store.as_writer(ReadChangeLog(after, limit=100))
    return tuple(item.sequence for item in log.items)


def test_an_earlier_number_committed_later_is_not_skipped(xact_store):
    store = xact_store
    cursor = start_cursor(store)
    early = EarlyWriter(store, "early")
    try:
        later = store.as_writer(PutRow("tickets", ("g1", "t1"), FrozenMap({"owner": "o1"}), ExpectAbsent()))
        assert isinstance(later, Written), later
        assert early.sequence < later.position.sequence, (early.sequence, later.position)
        # 古い transaction が commit していない間: 新しい方の変更も頭の手前で止まり、位置は古い方の番号を越えない。
        before = watched(store, cursor)
        assert before.items == (), before
        assert before.cursor.sequence < early.sequence, (before.cursor, early.sequence)
        assert logged(store, cursor.sequence) == ()
        early.commit()
    finally:
        early.close()
    after = watched(store, before.cursor)
    assert tuple(item.sequence for item in after.items) == (early.sequence, later.position.sequence), after
    assert after.cursor.sequence >= later.position.sequence, after
    assert logged(store, cursor.sequence) == (early.sequence, later.position.sequence)


@do
def pause():
    """scheduler の他の task を回しながら 10ms 待つため(止めの外れや DB の錠の待ちを見張る)。"""
    promise = yield CreateExternalPromise()
    threading.Timer(0.01, lambda: promise.complete(None)).start()
    yield Wait(promise.future)


def numbering_waiters(observer, key):
    """番号の錠を DB の中で待っている接続の数を読むため。"""
    return observer.execute(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted AND objid = (hashtext(%s)::bigint & 4294967295)::oid",
        (key,),
    ).fetchone()[0]


@do
def write_beside_held_numbering(store, observer, key, expect_wait):
    """番号の錠を別の接続が持つ間の行の書きが、DB で錠を待つか(expect_wait)・待たずに答えるかを確かめるため。"""
    put = yield Spawn(with_handlers([pg_records_handler(store.store, MAKER, ORIGIN_HOST)],
                                    PutRow("parts", ("p1",), FrozenMap({"label": "a"}), ExpectAbsent())))
    deadline = time.monotonic() + DEADLINE_SECONDS
    if expect_wait:
        while numbering_waiters(observer, key) < 1:
            assert time.monotonic() < deadline, "錠の形の書きが番号の錠を待たなかった"
            yield pause()
        return put
    written = yield Wait(put)
    assert isinstance(written, Written), written
    assert numbering_waiters(observer, key) == 0
    return None


def held_numbering(store):
    holder = psycopg.connect(dsn(), autocommit=True)
    key = run(unit_lock_key(store.store.prefix, ROWS_UNIT))
    holder.execute("SELECT pg_advisory_lock(hashtext(%s))", (key,))
    return holder, key


def test_a_row_write_in_the_transaction_form_does_not_wait_for_the_numbering_lock(xact_store):
    holder, key = held_numbering(xact_store)
    observer = psycopg.connect(dsn(), autocommit=True)
    try:
        xact_store.run(write_beside_held_numbering(xact_store, observer, key, False))
    finally:
        holder.close()
        observer.close()


def test_a_row_write_in_the_lock_form_waits_for_the_numbering_lock(lock_store):
    holder, key = held_numbering(lock_store)
    observer = psycopg.connect(dsn(), autocommit=True)
    try:

        @do
        def waits_then_answers():
            put = yield write_beside_held_numbering(lock_store, observer, key, True)
            holder.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))
            written = yield Wait(put)
            assert isinstance(written, Written), written

        lock_store.run(waits_then_answers())
    finally:
        holder.close()
        observer.close()


def test_a_lock_form_number_is_refused_after_the_switch(lock_store):
    store = lock_store
    before = store.as_writer(PutRow("parts", ("p0",), FrozenMap({"label": "before"}), ExpectAbsent()))
    assert isinstance(before, Written), before
    cursor = WatchCursor(before.position.epoch, before.position.sequence - 1)
    assert store.run(switch_numbering(store.store, False, NUMBERING_XACT)) == NUMBERING_LOCK
    # 旧い版の行の書きと同じ取り方(bigserial の次の番号)は sequence の上限で断られる。
    old = psycopg.connect(dsn(), autocommit=True)
    try:
        with pytest.raises(psycopg.errors.SequenceGeneratorLimitExceeded):
            old.execute(f"INSERT INTO {store.store.prefix}row_changes (ledger, key, version, payload, at, epoch) "
                        "VALUES ('parts', 'old', 1, NULL, 0, 1)")
    finally:
        old.close()
    after = store.as_writer(PutRow("parts", ("p1",), FrozenMap({"label": "after"}), ExpectAbsent()))
    assert isinstance(after, Written), after
    assert after.position.sequence >= XACT_NUMBER_SPAN, after
    # 切り替えの前の位置から、前の変更と後の変更を順に読む(Reset にならない)。
    answer = watched(store, cursor)
    assert tuple(item.sequence for item in answer.items) == (before.position.sequence, after.position.sequence), answer
    # 同じ形への切り替えは何もしない・錠の形へ戻すと、戻した後の番号は渡した全部の位置より大きい。
    assert store.run(switch_numbering(store.store, False, NUMBERING_XACT)) == NUMBERING_XACT
    assert store.run(switch_numbering(store.store, False, NUMBERING_LOCK)) == NUMBERING_XACT
    returned = store.as_writer(PutRow("parts", ("p2",), FrozenMap({"label": "returned"}), ExpectAbsent()))
    assert isinstance(returned, Written), returned
    assert returned.position.sequence > answer.cursor.sequence, (returned, answer.cursor)
    tail = watched(store, answer.cursor)
    assert tuple(item.sequence for item in tail.items) == (returned.position.sequence,), tail
