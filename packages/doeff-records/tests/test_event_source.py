"""記録の変化を合図として発する源(event_source の records-signal-handler・records-signal-source)の検 — #3077・#3104・設計 #3072。

組み立ての形(外 → 内): subscribed_event_handler(購読者の列)→(timer_handler)→ 記録の合図の源 → 本体。待ちに答えるのは購読者の列
1 つだけで、源は記録の変化を Publish する。組み立て方は 2 つ(Program の records-signal-source・素の工場 records-signal-handler — 工場は
購読の始まりの位置を包んだ本体の頭で読む)で、包み方は 1 つを共有する。

- 共有の不変条件: doeff-events の tests/event_signal_invariants の関数を、memory の記録の置き場の上で、2 つの組み立て方で呼ぶ。合図を
  「発する」所は、この world では「合図の所(結んだ表の行)へ書く」になる(_publish_as_write — 筋書きの書き手の側の代役)。
  購読の外の型を待つと止まる件は、不変条件 (c)(subscribed_event_handler の実行時の ValueError)が受け持つ。購読の始まりを組み立ての
  時に置く (d) の 2 つは Program の組だけで、工場の組では「本体の頭より前の書きは合図にならない」の検が受け持つ。
- 本体は包みを撃った task のまま走る(2 つの組み立て方の両方): 外の task の Cancel の後の書きで本体が進まない・本体が外の Spawn の
  優先のまま走る・待っている間に源が落ちれば本体が同じ例外で落ちる・最初の読みと待ちの間の書きを落とさない(位置を源の task の
  中で読むと赤)。
- 失敗ケース:
  待ち手の居ない間(本体が 1 拍を回している間)に書いた行の合図が、列に残って次の WaitForEvent で受かる。
  合図と期限(timer_handler の TimerFired)を同じ WaitForEvent の 1 回で待ち、先に来た方を受ける。
  結んだ列への追記で合図が出る(結んでいない列の追記・組み立ての前の追記では出ない)。
  合図の型に結んでいない表の書きでは、その型の合図は出ない。
  置き場が Unreachable を返しても源が繋ぎ直し、本体には出さない。直らなければ名指して落ちる(faults.SetStoreOutage)。
- 時間は仮想の時計(sim-time-handler)。筋書きが合図を受けられずに待ち続けると、仮想の時計の LIMIT_SECONDS 秒で赤にする(_bounded —
  源は待ちの上限ごとに待ち直すので、scheduler の行き止まりにならない)。
"""

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from pathlib import Path

import hy  # noqa: F401  Hy の module を読むため
import pytest
from doeff_core_effects.effects import Listen
from doeff_core_effects.handlers import listen_handler
from doeff_core_effects.scheduler import (
    PRIORITY_HIGH,
    PRIORITY_NORMAL,
    Cancel,
    CompletePromise,
    CreatePromise,
    FailPromise,
    Promise,
    Spawn,
    TaskCancelledError,
    Wait,
    scheduled,
)
from doeff_events import ArmTimer, EventBus, TimerFired, subscribed_event_handler, timer_handler
from doeff_events.effects import PublishEffect, WaitForEvent
from doeff_hy.frozen import FrozenMap
from doeff_records.admission import key_from_text, key_text
from doeff_records.effects import AppendEvent, PutRow, ReadRow
from doeff_records.event_source import (
    RECONNECT_SECONDS,
    RECONNECT_TRIES,
    WATCH_SECONDS,
    ChangedRow,
    SignalSourceUnreachable,
    SignalTables,
    records_signal_handler,
    records_signal_source,
)
from doeff_records.faults import AdvanceStoreEpoch, SetStoreOutage
from doeff_records.memory import MemoryStore, memory_records_handler
from doeff_records.values import Appended, ExpectAny, FieldDecl, RecordsSchema, StreamDecl, TableDecl, Written
from doeff_time import Delay, GetTime, SimClock, WaitWithin, sim_time_handler

from doeff import EffectBase, EffectGenerator, K, Pass, Program, Pure, Resume, do, run, with_handlers
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
    check_resubscribe_discards_previous_queue,
    check_signal_before_subscription_is_not_kept,
)

WRITER = "writer"
DETAIL = "記録の service が落ちている(筋書き)"
# 筋書きが合図を受けられずに待ち続けた時に赤にする、仮想の時計の秒(待ちの上限 30 秒の 20 回ぶん)。
LIMIT_SECONDS = 600.0


def _table(name: str) -> TableDecl:
    """鍵の欄 id と欄 note だけの表(書き手 WRITER)。"""
    return TableDecl(name=name, key_fields=("id",), fields=(FieldDecl("id", (WRITER,)), FieldDecl("note", (WRITER,))))


SCHEMA = RecordsSchema(
    tables=FrozenMap({name: _table(name) for name in ("jobs", "lanes", "notes")}),
    streams=FrozenMap({name: StreamDecl(name=name, writers=(WRITER,)) for name in ("intake", "journal")}),
)


@dataclass(frozen=True)
class LaneMoved:
    """表 lanes の行が変わった、という合図(Changed とは別の表に結ぶ型)。"""

    keys: tuple[ChangedRow, ...]


@dataclass(frozen=True)
class IntakeMoved:
    """列 intake に追記された、という合図(列に結ぶ型)。"""

    keys: tuple[ChangedRow, ...]


# 合図の型 → 表と列の名前の組(表 notes と列 journal はどの型にも結ばない)。
CHANGED_ON_JOBS = SignalTables(signal=Changed, tables=("jobs",))
LANE_ON_LANES = SignalTables(signal=LaneMoved, tables=("lanes",))
INTAKE_ON_INTAKE = SignalTables(signal=IntakeMoved, streams=("intake",))
BINDINGS = (CHANGED_ON_JOBS, LANE_ON_LANES, INTAKE_ON_INTAKE)


def _row(table: str, key: str) -> ChangedRow:
    """筋書きのキー key の、表 table の行の所。"""
    return ChangedRow(table=table, key=key_text((key,)))


@do
def _write(table: str, key: str) -> EffectGenerator[None]:
    """表 table の鍵 key の行を書く(合図の源 — 書くたびに変更が 1 つ積まれる)。"""
    written = yield PutRow(table, (key,), FrozenMap({"note": "written"}), ExpectAny())
    assert isinstance(written, Written), written


@do
def _append(stream: str, key: str) -> EffectGenerator[int]:
    """列 stream に冪等キー key の出来事を 1 つ積み、その番号を返す。"""
    appended = yield AppendEvent(stream, key, {"note": "appended"})
    assert isinstance(appended, Appended), appended
    return appended.sequence


@do
def _publish_as_write(effect: EffectBase, k: K) -> EffectGenerator[object]:
    """筋書きの書き手の Publish(Changed(keys)) を、合図の所の行への書きにする(この world で「発する」= 結んだ表へ書く)。"""
    if isinstance(effect, PublishEffect):
        for row in effect.event.keys:
            yield PutRow(row.table, key_from_text(row.key), FrozenMap({"note": "published"}), ExpectAny())
        return (yield Resume(k, None))
    yield Pass(effect, k)


def _stacked(handlers: tuple[ProgramHandler, ...], body: Program[object]) -> Program[object]:
    """handler の組(外 → 内)を本体に被せる。"""
    return with_handlers(list(handlers), body)


Build = Callable[[tuple[SignalTables, ...], str], Program[ProgramHandler]]
"""源の組み立て方 1 つ: bindings と購読者の名前 → 本体を包む関数を返す Program。"""


def _factory(bindings: tuple[SignalTables, ...], subscriber: str) -> Program[ProgramHandler]:
    """素の工場 records_signal_handler の組み立て方(工場は Program ではないので答えを Pure で包む — 位置は本体の頭で読む)。"""
    return Pure(records_signal_handler(bindings, subscriber))


# 2 つの組み立て方(Program の records_signal_source は使い手の付け替えまで 1 版残す・工場 records_signal_handler — #3104)。
BUILDS: tuple[Build, ...] = (records_signal_source, _factory)
BUILD_IDS = ("program", "factory")


@do
def _subscribe(
    build: Build, bus: EventBus, subscriber: str, event_types: tuple[type, ...]
) -> EffectGenerator[ProgramHandler]:
    """購読者の列(subscribed_event_handler)の内側に、event_types に結んだ表の源を置く組を組み立てる Program(不変条件の検の
    組み立て方)。結ぶ型の無い購読者(書き手)は、源の代わりに Publish を書きにする代役を置く。"""
    waiting = subscribed_event_handler(bus, subscriber, event_types)
    bindings = tuple(binding for binding in BINDINGS if binding.signal in event_types)
    if not bindings:
        return partial(_stacked, (waiting, _publish_as_write))
    source: ProgramHandler = yield build(bindings, subscriber)
    return partial(_stacked, (waiting, source))


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


def records_world(build: Build = records_signal_source) -> SignalWorld:
    """1 つの世界 = 新しい memory の記録の置き場 1 つと購読者の列の置き場(EventBus)1 つ。合図の所は表 jobs の行。"""
    store = MemoryStore(SCHEMA)
    bus = EventBus()

    def subscribe(subscriber: str, event_types: tuple[type, ...] = (), /) -> Program[ProgramHandler]:
        """この世界の購読者の列と置き場の上で、購読者の組を組み立てる Program。"""
        return _subscribe(build, bus, subscriber, event_types)

    return SignalWorld(subscribe=subscribe, run=partial(_run_on, store), row=partial(_row, "jobs"))


# 購読の始まりを「組み立て(subscribe)の時」と置く不変条件 — 工場は始まりを包んだ本体の頭に置く(#3104)ので、工場の組では
# test_the_factory_starts_its_subscription_at_the_head_of_the_body が同じ性質(始まりより前の書きは合図にならない)を受け持つ。
STARTS_AT_SUBSCRIBE = (check_signal_before_subscription_is_not_kept, check_resubscribe_discards_previous_queue)
INVARIANT_CASES = tuple(
    pytest.param(build, invariant, id=f"{build_id}-{invariant.__name__}")
    for build, build_id in zip(BUILDS, BUILD_IDS, strict=True)
    for invariant in INVARIANTS
    if build is records_signal_source or invariant not in STARTS_AT_SUBSCRIBE
)


@pytest.mark.parametrize(("build", "invariant"), INVARIANT_CASES)
def test_records_signal_source_keeps_invariant(build: Build, invariant: Invariant) -> None:
    """共有の不変条件を、記録の置き場を源にする 2 つの組み立て方で 1 つずつ確かめる。"""
    invariant(records_world(build))


@do
def _changed() -> EffectGenerator[Changed]:
    """受け手: 次の Changed を 1 つ受けて返す(Unreachable を受ける口は無い)。"""
    signal: Changed = yield WaitForEvent(Changed)
    return signal


# --- 待ち手の居ない間の合図は列に残る ----------------------------------------------------------------------


@do
def _after_a_beat(beat_done: Promise[object]) -> EffectGenerator[Changed]:
    """受け手: 1 拍を回し終えてから(その間は待っていない)Changed を待つ。"""
    yield Wait(beat_done.future)
    signal: Changed = yield WaitForEvent(Changed)
    return signal


@dataclass(frozen=True)
class BusyOutcome:
    """seen = 立ち会いの購読者が受けた合図(源が発し終えた印)/ received = 1 拍の後に受け手が受けた合図。"""

    seen: Changed
    received: Changed


@do
def _write_during_a_beat() -> EffectGenerator[BusyOutcome]:
    """受け手が 1 拍を回している間に jobs を書く。源が発し終えたこと(同じ列の置き場の立ち会いが受けた)を見てから拍を終える。"""
    bus = EventBus()
    worker = subscribed_event_handler(bus, "worker", (Changed,))
    witness = subscribed_event_handler(bus, "witness", (Changed,))
    source: ProgramHandler = yield records_signal_source((CHANGED_ON_JOBS,), "worker")
    beat_done: Promise[object] = yield CreatePromise()
    receiver = yield Spawn(with_handlers([worker, source], _after_a_beat(beat_done)))
    yield _write("jobs", "j1")
    seen: Changed = yield witness(_changed())
    yield CompletePromise(beat_done, None)
    received: Changed = yield Wait(receiver)
    return BusyOutcome(seen=seen, received=received)


def test_a_signal_raised_while_the_program_is_busy_waits_in_its_queue() -> None:
    expected = Changed((_row("jobs", "j1"),))
    assert _run_on(MemoryStore(SCHEMA), _write_during_a_beat()) == BusyOutcome(seen=expected, received=expected)


# --- 合図と期限を 1 回の WaitForEvent で待つ ----------------------------------------------------------------

DEADLINE_SECONDS = 10.0


@do
def _armed_wait() -> EffectGenerator[object]:
    """受け手: 期限を DEADLINE_SECONDS 秒後に置き、記録の合図か期限の先に来た方を 1 回の WaitForEvent で受ける。"""
    now = yield GetTime()
    yield ArmTimer("deadline", now + timedelta(seconds=DEADLINE_SECONDS))
    first = yield WaitForEvent(Changed, TimerFired)
    return first


@do
def _signal_or_deadline(write_after: float) -> EffectGenerator[object]:
    """write_after 秒後に jobs を書く。受け手は列・期限・源の組の下で待つ。"""
    bus = EventBus()
    waiting = subscribed_event_handler(bus, "worker", (Changed, TimerFired))
    source: ProgramHandler = yield records_signal_source((CHANGED_ON_JOBS,), "worker")
    receiver = yield Spawn(with_handlers([waiting, timer_handler(), source], _armed_wait()))
    yield Delay(write_after)
    yield _write("jobs", "j1")
    first = yield Wait(receiver)
    return first


@pytest.mark.parametrize(
    ("write_after", "expected"),
    [(3.0, Changed((_row("jobs", "j1"),))), (20.0, TimerFired("deadline"))],
    ids=["signal-first", "deadline-first"],
)
def test_one_wait_receives_whichever_of_signal_and_deadline_comes_first(write_after: float, expected: object) -> None:
    assert _run_on(MemoryStore(SCHEMA), _signal_or_deadline(write_after)) == expected


# --- 結んだ列への追記 ----------------------------------------------------------------------------------------


@do
def _first_intake(done: Promise[object]) -> EffectGenerator[IntakeMoved]:
    """受け手: 次の IntakeMoved を受け、約束 done にも渡して返す。"""
    signal: IntakeMoved = yield WaitForEvent(IntakeMoved)
    yield CompletePromise(done, signal)
    return signal


@dataclass(frozen=True)
class AppendOutcome:
    """quiet = 組み立ての前の追記と結んでいない列の追記の後に、受け手が起きたか(None = 起きない)/ received = 受けた合図 /
    sequence = 結んだ列への追記の番号。"""

    quiet: object
    received: IntakeMoved
    sequence: int


@do
def _appends() -> EffectGenerator[AppendOutcome]:
    """組み立ての前に intake へ 1 つ積み、組み立ての後に journal(結んでいない列)へ積んで 60 秒起きないことを見てから intake へ積む。"""
    bus = EventBus()
    waiting = subscribed_event_handler(bus, "worker", (IntakeMoved,))
    yield _append("intake", "before")
    source: ProgramHandler = yield records_signal_source((INTAKE_ON_INTAKE,), "worker")
    done: Promise[object] = yield CreatePromise()
    receiver = yield Spawn(with_handlers([waiting, source], _first_intake(done)))
    yield _append("journal", "elsewhere")
    quiet = yield WaitWithin(done.future, 60.0)
    sequence = yield _append("intake", "after")
    received: IntakeMoved = yield Wait(receiver)
    return AppendOutcome(quiet=quiet, received=received, sequence=sequence)


def test_an_append_to_a_bound_stream_raises_its_signal() -> None:
    outcome = _run_on(MemoryStore(SCHEMA), _appends())
    assert isinstance(outcome, AppendOutcome), outcome
    expected = IntakeMoved((ChangedRow(table="intake", key=str(outcome.sequence)),))
    assert outcome == AppendOutcome(quiet=None, received=expected, sequence=outcome.sequence), outcome


# --- 結んでいない表の書きでは起きない -------------------------------------------------------------------


@dataclass(frozen=True)
class UnboundOutcome:
    """quiet = 結んでいない表だけを書いた後、Changed の受け手が起きたか(None = 起きない)/
    changed = jobs を書いた後に受けた Changed / lane = 続けて待った LaneMoved(lanes の書きの合図が列に残っている)。"""

    quiet: object
    changed: Changed
    lane: LaneMoved


@do
def _changed_then_lane(done: Promise[object]) -> EffectGenerator[tuple[Changed, LaneMoved]]:
    """受け手: Changed を受けて約束 done に渡し、続けて LaneMoved を受ける。"""
    changed: Changed = yield WaitForEvent(Changed)
    yield CompletePromise(done, changed)
    lane: LaneMoved = yield WaitForEvent(LaneMoved)
    return (changed, lane)


@do
def _unbound_writes() -> EffectGenerator[UnboundOutcome]:
    """Changed を待つ受け手の前で lanes(LaneMoved に結んだ表)と notes(どの型にも結ばない表)を書き、60 秒起きないことを見てから
    jobs を書く。"""
    bus = EventBus()
    waiting = subscribed_event_handler(bus, "worker", (Changed, LaneMoved))
    source: ProgramHandler = yield records_signal_source((CHANGED_ON_JOBS, LANE_ON_LANES), "worker")
    done: Promise[object] = yield CreatePromise()
    receiver = yield Spawn(with_handlers([waiting, source], _changed_then_lane(done)))
    yield _write("lanes", "l1")
    yield _write("notes", "n1")
    quiet = yield WaitWithin(done.future, 60.0)
    yield _write("jobs", "j1")
    yield _write("notes", "n2")
    changed, lane = yield Wait(receiver)
    return UnboundOutcome(quiet=quiet, changed=changed, lane=lane)


def test_writes_to_tables_not_bound_to_the_signal_type_do_not_raise_it() -> None:
    outcome = _run_on(MemoryStore(SCHEMA), _unbound_writes())
    assert outcome == UnboundOutcome(
        quiet=None,
        changed=Changed((_row("jobs", "j1"),)),
        lane=LaneMoved((_row("lanes", "l1"),)),
    ), outcome


# --- 置き場に届かない時は繋ぎ直し、本体には出さない ------------------------------------------------------


@do
def _outage_then_recovery() -> EffectGenerator[Changed]:
    """受け手が待ち始めた時には置き場に届かず、撃ち直しの 2 回半ぶん後に戻って jobs が書かれる。"""
    bus = EventBus()
    waiting = subscribed_event_handler(bus, "worker", (Changed,))
    source: ProgramHandler = yield records_signal_source((CHANGED_ON_JOBS, LANE_ON_LANES), "worker")
    yield SetStoreOutage(DETAIL)
    receiver = yield Spawn(with_handlers([waiting, source], _changed()))
    yield Delay(RECONNECT_SECONDS * 2.5)
    yield SetStoreOutage(None)
    yield _write("jobs", "j1")
    signal: Changed = yield Wait(receiver)
    return signal


def test_an_unreachable_store_is_reconnected_inside_the_source() -> None:
    assert _run_on(MemoryStore(SCHEMA), _outage_then_recovery()) == Changed((_row("jobs", "j1"),))


@do
def _outage_for_good() -> EffectGenerator[Changed]:
    """受け手が待つ間、置き場に届かないまま戻らない。"""
    bus = EventBus()
    waiting = subscribed_event_handler(bus, "worker", (Changed,))
    source: ProgramHandler = yield records_signal_source((CHANGED_ON_JOBS, LANE_ON_LANES), "worker")
    yield SetStoreOutage(DETAIL)
    signal: Changed = yield with_handlers([waiting, source], _changed())
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
    bus = EventBus()
    waiting = subscribed_event_handler(bus, "worker", (Changed,))
    source: ProgramHandler = yield records_signal_source((CHANGED_ON_JOBS,), "worker")
    yield AdvanceStoreEpoch()
    yield _write("jobs", "j1")
    signal: Changed = yield with_handlers([waiting, source], _changed())
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


def test_a_pair_without_tables_or_streams_is_refused() -> None:
    with pytest.raises(ValueError, match="tables streams"):
        SignalTables(signal=Changed)


@do
def _twice_bound() -> EffectGenerator[ProgramHandler]:
    """同じ合図の型を 2 つの組に結んで組み立てる(どちらの表で起こすかが決まらない)。"""
    twice = (CHANGED_ON_JOBS, SignalTables(signal=Changed, tables=("notes",)))
    source: ProgramHandler = yield records_signal_source(twice, "worker")
    return source


def test_a_signal_type_bound_twice_is_refused_by_name() -> None:
    builder: Callable[[], object] = partial(_run_on, MemoryStore(SCHEMA), _twice_bound())
    with pytest.raises(ValueError, match="Changed"):
        builder()


def test_the_factory_refuses_a_signal_type_bound_twice_at_the_head_of_the_body() -> None:
    # 工場は Program ではないので、引数の確かめは位置の読みと同じく包んだ本体の頭で走る(本体は走らない)。
    twice = (CHANGED_ON_JOBS, SignalTables(signal=Changed, tables=("notes",)))
    wrapped = with_handlers([records_signal_handler(twice, "worker")], _changed())
    with pytest.raises(ValueError, match="Changed"):
        _run_on(MemoryStore(SCHEMA), wrapped)


# --- 工場の購読は包んだ本体の頭で始まる(#3104)-------------------------------------------------------------


@do
def _early_then_late() -> EffectGenerator[Changed]:
    """工場の組を作った後・本体が走る前に early を書き、本体が待ち始めてから late を書く。"""
    bus = EventBus()
    waiting = subscribed_event_handler(bus, "worker", (Changed,))
    source = records_signal_handler((CHANGED_ON_JOBS,), "worker")
    yield _write("jobs", "early")
    receiver = yield Spawn(with_handlers([waiting, source], _changed()))
    yield Delay(1.0)
    yield _write("jobs", "late")
    signal: Changed = yield Wait(receiver)
    return signal


def test_the_factory_starts_its_subscription_at_the_head_of_the_body() -> None:
    # 始まりより前の書き(early)は合図にならず、本体が走り出した後の書き(late)が最初の合図になる。
    assert _run_on(MemoryStore(SCHEMA), _early_then_late()) == Changed((_row("jobs", "late"),))


@do
def _read_then_wait(read_done: Promise[object]) -> EffectGenerator[Changed]:
    """受け手: 記録を読み(本体の最初の読み)、読み終えたことを知らせてから Changed を待つ。"""
    yield ReadRow("jobs", ("j1",))
    yield CompletePromise(read_done, None)
    signal: Changed = yield WaitForEvent(Changed)
    return signal


@do
def _write_when_read(read_done: Promise[object]) -> EffectGenerator[None]:
    """書き手: 受け手が最初の読みを終えたら jobs を書く。"""
    yield Wait(read_done.future)
    yield _write("jobs", "j1")


@do
def _write_between_read_and_wait(build: Build) -> EffectGenerator[Changed]:
    """受け手の最初の読みと待ちの間に、書き手が jobs を書く。受け手と書き手は源の task(既定の優先)より高い優先で走るので、源の
    task が回るより先に書きが終わる — 位置を源の task の中で読むと、その位置は書きの後になる。"""
    bus = EventBus()
    waiting = subscribed_event_handler(bus, "worker", (Changed,))
    source: ProgramHandler = yield build((CHANGED_ON_JOBS,), "worker")
    read_done: Promise[object] = yield CreatePromise()
    writer = yield Spawn(_write_when_read(read_done), priority=PRIORITY_HIGH)
    receiver = yield Spawn(with_handlers([waiting, source], _read_then_wait(read_done)), priority=PRIORITY_HIGH)
    signal: Changed = yield Wait(receiver)
    yield Wait(writer)
    return signal


@pytest.mark.parametrize("build", BUILDS, ids=BUILD_IDS)
def test_a_write_between_the_first_read_and_the_wait_is_signalled(build: Build) -> None:
    # 失敗ケース: 始まりの位置を源の task の中(Spawn の後)で読むと、位置が書きの後になり、この書きの合図を落として待ち続ける。
    assert _run_on(MemoryStore(SCHEMA), _write_between_read_and_wait(build)) == Changed((_row("jobs", "j1"),))


# --- 本体は包みを撃った task のまま走る(vg-w46 の指摘・#3100)----------------------------------------------


@do
def _first_signal(got: Promise[Changed]) -> EffectGenerator[None]:
    """受け手: Changed を 1 つ受けたら約束 got に渡す(受けた = 本体が読みを進めた印)。"""
    signal: Changed = yield WaitForEvent(Changed)
    yield CompletePromise(got, signal)


@dataclass(frozen=True)
class CancelOutcome:
    """seen = 外の task を Cancel した後に本体が受けた合図(None = 受けない)/ cancelled = 外の task が Cancel で終わったか。"""

    seen: Changed | None
    cancelled: bool


@do
def _write_after_cancel(build: Build) -> EffectGenerator[CancelOutcome]:
    """本体が待ち始めた後に外の task を Cancel し、その直後に jobs を書いて、待ちの上限の 2 回ぶん本体の受けを待つ。外の task は通常
    より低い優先(源の task より後に回る)— Cancel を受けた外が次に回るより先に、書きの合図が届く。"""
    bus = EventBus()
    waiting = subscribed_event_handler(bus, "worker", (Changed,))
    source: ProgramHandler = yield build((CHANGED_ON_JOBS,), "worker")
    got: Promise[Changed] = yield CreatePromise()
    receiver = yield Spawn(with_handlers([waiting, source], _first_signal(got)), priority=PRIORITY_NORMAL - 1)
    yield Delay(1.0)
    yield Cancel(receiver)
    yield _write("jobs", "j1")
    seen: Changed | None = yield WaitWithin(got.future, WATCH_SECONDS * 2)
    try:
        yield Wait(receiver)
    except TaskCancelledError:
        return CancelOutcome(seen=seen, cancelled=True)
    return CancelOutcome(seen=seen, cancelled=False)


@pytest.mark.parametrize("build", BUILDS, ids=BUILD_IDS)
def test_cancelling_the_outer_task_stops_the_body_before_a_later_write(build: Build) -> None:
    # 失敗ケース: 本体を別の task にした包みでは、外の Cancel の後の書きの合図で本体が起きて読みを進めた(#3100 の画面の赤)。
    # 本体が外の task のまま走るので、本体は外の Spawn の優先のまま走る(包みが本体を別の task に立て直すと、この検が赤)。
    # 以前ここに在った「本体と通常の優先の立ち会いの起きた順」の検は外した(#3135): scheduler は約束を完了するたびに起こした task を
    # 完了した側より先に回すので(#493)、同じ合図で起きる 2 つの待ち手の順は購読の順で決まり、優先は効かない。その検が優先を
    # 見分けられたのは、本体の待ちを子の task にして源と競わせていた 1 段の回り道があった間だけだった。
    assert _run_on(MemoryStore(SCHEMA), _write_after_cancel(build)) == CancelOutcome(seen=None, cancelled=True)


@do
def _outage_while_waiting(build: Build) -> EffectGenerator[Changed]:
    """本体が待ち始めた後に置き場が落ち、戻らない(源の task が繋ぎ直しを使い切って落ちる)。"""
    bus = EventBus()
    waiting = subscribed_event_handler(bus, "worker", (Changed,))
    source: ProgramHandler = yield build((CHANGED_ON_JOBS,), "worker")
    receiver = yield Spawn(with_handlers([waiting, source], _changed()))
    yield Delay(1.0)
    yield SetStoreOutage(DETAIL)
    signal: Changed = yield Wait(receiver)
    return signal


@pytest.mark.parametrize("build", BUILDS, ids=BUILD_IDS)
def test_a_source_that_fails_while_the_body_waits_brings_the_body_down(build: Build) -> None:
    # 本体は包みを撃った task のまま待つので、源の失敗(源の task が同じ bus に発する SourceFailed)は本体の待ちに届いて同じ例外で
    # 落ちる。
    with pytest.raises(SignalSourceUnreachable, match=re.escape(DETAIL)):
        _run_on(MemoryStore(SCHEMA), _outage_while_waiting(build))


# --- 本体の待ちごとに task を立てない(#3135)--------------------------------------------------------------------


@do
def _receive_changed(count: int) -> EffectGenerator[int]:
    """受け手: Changed を count 回受けて、受けた回数を返す。"""
    for _ in range(count):
        yield WaitForEvent(Changed)
    return count


@do
def _spawns_while_receiving(build: Build, writes: int) -> EffectGenerator[tuple[int, int]]:
    """本体が書きの合図を writes 回受ける間に、包みの中で出た Spawn の数を読む(源の task の分は寿命に決まった数だけ入る)。"""
    bus = EventBus()
    waiting = subscribed_event_handler(bus, "worker", (Changed,))
    source: ProgramHandler = yield build((CHANGED_ON_JOBS,), "worker")
    listened = Listen(with_handlers([source], _receive_changed(writes)), types=(Spawn,))
    receiver = yield Spawn(with_handlers([waiting, listen_handler], listened))
    for n in range(writes):
        yield Delay(1.0)
        yield _write("jobs", f"j{n}")
    received, spawns = yield Wait(receiver)
    return (received, len(spawns))


@pytest.mark.parametrize("build", BUILDS, ids=BUILD_IDS)
def test_waiting_for_each_signal_spawns_no_task(build: Build) -> None:
    # 失敗ケース: 本体の待ちを子の task にして源と Race する形では、受けた合図 1 つごとに Spawn が 1 つ増えた(automation の 1 本で
    # 293 回)。源の失敗は bus の合図で届くので、Spawn は源の task の分だけ。
    one = _run_on(MemoryStore(SCHEMA), _spawns_while_receiving(build, 1))
    three = _run_on(MemoryStore(SCHEMA), _spawns_while_receiving(build, 3))

    assert isinstance(one, tuple)
    assert isinstance(three, tuple)
    assert (one[0], three[0]) == (1, 3)
    assert one[1] == three[1], (one, three)
