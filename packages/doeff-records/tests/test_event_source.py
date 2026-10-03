"""記録の変更を合図にする handler(event_source.records-signal-handler)の検 — #3077・設計 #3072。

- 共有の不変条件: doeff-events の tests/event_signal_invariants の関数を、memory の記録の置き場の上で、この handler の組み立て方で呼ぶ。
  合図を「発する」所は、この world では「合図の所(結んだ表の行)へ書く」になる(_publish_as_write — 筋書きの書き手の側の代役)。
- 失敗ケース(#3077 の本文):
  (1) 合図の型に結んでいない表の書きでは、その型の合図は起きない(別の型に結んだ表・どの型にも結んでいない表)。
  (2) 読みと待ちの間の書きを取りこぼさない — 不変条件 (a)(購読の始まりの位置は組み立ての時に読む)。
  (3) 置き場が Unreachable を返しても handler が繋ぎ直し、Program には出さない。直らなければ名指して落ちる(faults.SetStoreOutage)。
- 時間は仮想の時計(sim-time-handler)。筋書きが合図を受けられずに待ち続けると、仮想の時計の LIMIT_SECONDS 秒で赤にする
  (_bounded — この handler は待ちの上限ごとに待ち直すので、scheduler の行き止まりにならない)。
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import hy  # noqa: F401  Hy の module を読むため
import pytest
from doeff_core_effects.scheduler import (
    CompletePromise,
    CreatePromise,
    FailPromise,
    Promise,
    Spawn,
    Wait,
    scheduled,
)
from doeff_events.effects import PublishEffect, WaitForEvent
from doeff_hy.frozen import FrozenMap
from doeff_records.admission import key_from_text, key_text
from doeff_records.effects import PutRow
from doeff_records.event_source import (
    RECONNECT_SECONDS,
    RECONNECT_TRIES,
    ChangedRow,
    SignalSourceUnreachable,
    SignalTables,
    records_signal_handler,
)
from doeff_records.faults import AdvanceStoreEpoch, SetStoreOutage
from doeff_records.memory import MemoryStore, memory_records_handler
from doeff_records.values import ExpectAny, FieldDecl, RecordsSchema, TableDecl, Written
from doeff_time import Delay, SimClock, WaitWithin, sim_time_handler

from doeff import EffectBase, EffectGenerator, K, Pass, Program, Resume, do, run, with_handlers
from doeff.program import ProgramHandler

# 共有の不変条件の関数の置き場(doeff-events の tests — package ではないので path で読む)。
EVENT_TESTS = Path(__file__).resolve().parents[2] / "doeff-events" / "tests"
if str(EVENT_TESTS) not in sys.path:
    sys.path.insert(0, str(EVENT_TESTS))

from event_signal_invariants import (  # noqa: E402 - sys.path の後
    INVARIANTS,
    Changed,
    Invariant,
    SignalWorld,
)

WRITER = "writer"
DETAIL = "記録の service が落ちている(筋書き)"
# 筋書きが合図を受けられずに待ち続けた時に赤にする、仮想の時計の秒(待ちの上限 30 秒の 20 回ぶん)。
LIMIT_SECONDS = 600.0


def _table(name: str) -> TableDecl:
    """鍵の欄 id と欄 note だけの表(書き手 WRITER)。"""
    return TableDecl(name=name, key_fields=("id",), fields=(FieldDecl("id", (WRITER,)), FieldDecl("note", (WRITER,))))


SCHEMA = RecordsSchema(tables=FrozenMap({name: _table(name) for name in ("jobs", "lanes", "notes")}))


@dataclass(frozen=True)
class LaneMoved:
    """表 lanes の行が変わった、という合図(Changed とは別の表に結ぶ型)。"""

    keys: tuple[ChangedRow, ...]


# 合図の型 → 表の名前の組(notes はどの型にも結ばない)。
BINDINGS = (SignalTables(signal=Changed, tables=("jobs",)), SignalTables(signal=LaneMoved, tables=("lanes",)))


def _row(table: str, key: str) -> ChangedRow:
    """筋書きのキー key の、表 table の行の所。"""
    return ChangedRow(table=table, key=key_text((key,)))


@do
def _write(table: str, key: str) -> EffectGenerator[None]:
    """表 table の鍵 key の行を書く(合図の源 — 書くたびに変更が 1 つ積まれる)。"""
    written = yield PutRow(table, (key,), FrozenMap({"note": "written"}), ExpectAny())
    assert isinstance(written, Written), written


@do
def _publish_as_write(effect: EffectBase, k: K) -> EffectGenerator[object]:
    """筋書きの書き手の Publish(Changed(keys)) を、合図の所の行への書きにする(この world で「発する」= 結んだ表へ書く)。"""
    if isinstance(effect, PublishEffect):
        for row in effect.event.keys:
            yield PutRow(row.table, key_from_text(row.key), FrozenMap({"note": "published"}), ExpectAny())
        return (yield Resume(k, None))
    yield Pass(effect, k)


def _published_as_writes(receiving: ProgramHandler, body: Program[object]) -> Program[object]:
    """組み立てた handler の外に、Publish を書きにする代役を被せる(handler は Publish を受け持たず外へ渡す)。"""
    return with_handlers([_publish_as_write, receiving], body)


@do
def _subscribe(subscriber: str, event_types: tuple[type, ...]) -> EffectGenerator[ProgramHandler]:
    """event_types に結んだ表で handler を組み立てる Program(不変条件の検の組み立て方)。"""
    bindings = tuple(binding for binding in BINDINGS if binding.signal in event_types)
    receiving: ProgramHandler = yield records_signal_handler(bindings, subscriber)
    return partial(_published_as_writes, receiving)


@dataclass(frozen=True)
class Settled:
    """筋書きが値で終わった(値が None でも、期限の None と分ける)。"""

    value: object


@do
def _settle(program: Program[object], done: Promise[Settled]) -> EffectGenerator[None]:
    """program を走らせ、終わりを約束 done に渡す(例外も渡して、走らせ手の側で上げる)。"""
    try:
        value = yield program
    except Exception as error:  # noqa: BLE001 - 筋書きの例外をそのまま走らせ手に渡す
        yield FailPromise(done, error)
    else:
        yield CompletePromise(done, Settled(value))


@do
def _bounded(program: Program[object]) -> EffectGenerator[object]:
    """program を仮想の時計の LIMIT_SECONDS 秒まで待つ。終わらなければ赤(合図を受けられずに待ち続けている)。"""
    done: Promise[Settled] = yield CreatePromise()
    settling = yield Spawn(_settle(program, done))
    try:
        settled = yield WaitWithin(done.future, LIMIT_SECONDS)
    except Exception:
        # 例外を約束に渡し終えた task の終わりを待ってから上げる(走らせ手が終わる時に task を置き去りにしない)。
        yield Wait(settling)
        raise
    if settled is None:
        raise AssertionError(f"仮想の時計で {LIMIT_SECONDS} 秒待っても筋書きが終わらない — 合図を受けられずに待ち続けている")
    yield Wait(settling)
    return settled.value


def _run_on(store: MemoryStore, program: Program[object]) -> object:
    """仮想の時計と memory の記録の置き場(書き手 WRITER)の下で、program を期限つきで走らせる。"""
    stack = [sim_time_handler(clock=SimClock()), memory_records_handler(store, WRITER)]
    return run(scheduled(with_handlers(stack, _bounded(program))))


def records_world() -> SignalWorld:
    """1 つの世界 = 新しい memory の記録の置き場 1 つ。合図の所は表 jobs の行。"""
    store = MemoryStore(SCHEMA)

    def subscribe(subscriber: str, event_types: tuple[type, ...] = (), /) -> Program[ProgramHandler]:
        """event_types に結んだ表で、この世界の置き場の上の handler を組み立てる Program。"""
        return _subscribe(subscriber, event_types)

    return SignalWorld(subscribe=subscribe, run=partial(_run_on, store), row=partial(_row, "jobs"))


@pytest.mark.parametrize("invariant", INVARIANTS, ids=lambda invariant: invariant.__name__)
def test_records_signal_handler_keeps_invariant(invariant: Invariant) -> None:
    """共有の不変条件を、記録の置き場を源にする組み立て方で 1 つずつ確かめる((2) は check_signal_between_read_and_wait_is_kept)。"""
    invariant(records_world())


# --- (1) 結んでいない表の書きでは起きない -------------------------------------------------------------------


@do
def _first_changed(done: Promise[object]) -> EffectGenerator[Changed]:
    """受け手: 次の Changed を受け、約束 done にも渡して返す。"""
    signal: Changed = yield WaitForEvent(Changed)
    yield CompletePromise(done, signal)
    return signal


@do
def _next_lane() -> EffectGenerator[LaneMoved]:
    """受け手: 次の LaneMoved を受けて返す。"""
    signal: LaneMoved = yield WaitForEvent(LaneMoved)
    return signal


@dataclass(frozen=True)
class UnboundOutcome:
    """(1) の筋書きの結果: quiet = 結んでいない表だけを書いた後、Changed の受け手が起きたか(None = 起きない)/
    changed = jobs を書いた後に受けた Changed / lane = 同じ handler の下で後から待った LaneMoved。"""

    quiet: object
    changed: Changed
    lane: LaneMoved


@do
def _unbound_writes() -> EffectGenerator[UnboundOutcome]:
    """Changed を待つ受け手の前で lanes(LaneMoved に結んだ表)と notes(どの型にも結ばない表)を書き、60 秒起きないことを見てから
    jobs を書く。最後に同じ handler の下で LaneMoved を待つ(lanes の書きは LaneMoved の合図として列に残っている)。"""
    receiving: ProgramHandler = yield records_signal_handler(BINDINGS, "worker")
    done: Promise[object] = yield CreatePromise()
    receiver = yield Spawn(receiving(_first_changed(done)))
    yield _write("lanes", "l1")
    yield _write("notes", "n1")
    quiet = yield WaitWithin(done.future, 60.0)
    yield _write("jobs", "j1")
    yield _write("notes", "n2")
    changed: Changed = yield Wait(receiver)
    lane: LaneMoved = yield receiving(_next_lane())
    return UnboundOutcome(quiet=quiet, changed=changed, lane=lane)


def test_writes_to_tables_not_bound_to_the_signal_type_do_not_raise_it() -> None:
    outcome = _run_on(MemoryStore(SCHEMA), _unbound_writes())
    assert outcome == UnboundOutcome(
        quiet=None,
        changed=Changed((_row("jobs", "j1"),)),
        lane=LaneMoved((_row("lanes", "l1"),)),
    ), outcome


# --- (3) 置き場に届かない時は繋ぎ直し、Program には出さない ------------------------------------------------


@do
def _changed() -> EffectGenerator[Changed]:
    """受け手: 次の Changed を 1 つ受けて返す(Unreachable を受ける口は無い)。"""
    signal: Changed = yield WaitForEvent(Changed)
    return signal


@do
def _outage_then_recovery() -> EffectGenerator[Changed]:
    """受け手が待ち始めた時には置き場に届かず、撃ち直しの 2 回半ぶん後に戻って jobs が書かれる。"""
    receiving: ProgramHandler = yield records_signal_handler(BINDINGS, "worker")
    yield SetStoreOutage(DETAIL)
    receiver = yield Spawn(receiving(_changed()))
    yield Delay(RECONNECT_SECONDS * 2.5)
    yield SetStoreOutage(None)
    yield _write("jobs", "j1")
    signal: Changed = yield Wait(receiver)
    return signal


def test_an_unreachable_store_is_reconnected_inside_the_handler() -> None:
    assert _run_on(MemoryStore(SCHEMA), _outage_then_recovery()) == Changed((_row("jobs", "j1"),))


@do
def _outage_for_good() -> EffectGenerator[Changed]:
    """受け手が待つ間、置き場に届かないまま戻らない。"""
    receiving: ProgramHandler = yield records_signal_handler(BINDINGS, "worker")
    yield SetStoreOutage(DETAIL)
    signal: Changed = yield receiving(_changed())
    return signal


def test_a_store_that_stays_unreachable_brings_the_process_down_by_name() -> None:
    with pytest.raises(SignalSourceUnreachable) as raised:
        _run_on(MemoryStore(SCHEMA), _outage_for_good())
    message = str(raised.value)
    for named in ("worker", "jobs", "lanes", f"{RECONNECT_TRIES} 回", DETAIL):
        assert named in message, message


# --- 置き場の作り直し(Reset)と組み立ての確かめ ----------------------------------------------------------


@do
def _write_after_epoch_advance() -> EffectGenerator[Changed]:
    """購読を始めた後に置き場の版が進み(前の位置は Reset になる)、その後に jobs が書かれる。"""
    receiving: ProgramHandler = yield records_signal_handler(BINDINGS, "worker")
    yield AdvanceStoreEpoch()
    yield _write("jobs", "j1")
    signal: Changed = yield receiving(_changed())
    return signal


def test_a_reset_reads_the_remaining_changes_again_from_the_floor() -> None:
    assert _run_on(MemoryStore(SCHEMA), _write_after_epoch_advance()) == Changed((_row("jobs", "j1"),))


@dataclass(frozen=True)
class NoKeys:
    """欄 keys を持たない型(合図の型に結べない)。"""

    key: str


def test_a_signal_type_without_keys_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="NoKeys"):
        SignalTables(signal=NoKeys, tables=("jobs",))


@do
def _twice_bound() -> EffectGenerator[ProgramHandler]:
    """同じ合図の型を 2 つの組に結んで組み立てる(どちらの表で起こすかが決まらない)。"""
    twice = (SignalTables(signal=Changed, tables=("jobs",)), SignalTables(signal=Changed, tables=("notes",)))
    handler: ProgramHandler = yield records_signal_handler(twice, "worker")
    return handler


def test_a_signal_type_bound_twice_is_refused_by_name() -> None:
    builder: Callable[[], object] = partial(_run_on, MemoryStore(SCHEMA), _twice_bound())
    with pytest.raises(ValueError, match="Changed"):
        builder()
