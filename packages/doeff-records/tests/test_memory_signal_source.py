"""模擬の源(memory の置き場の書きで合図を発する memory_signal_handler・memory_signal_source)と、源の工場を土台が渡す鍵
SignalSourceFactory の検 — #3127・設計 #3072。

組み立ては記録の置き場の源と同じ(外 → 内): subscribed_event_handler(購読者の列)→(timer_handler)→ 源 → 本体。違いは源の工場だけ
(本番の土台 = RECORDS_SIGNAL_SOURCE の records_signal_handler・模擬の土台 = memory_signal_source(置き場))。

- 共有の不変条件: doeff-events の tests/event_signal_invariants の関数を、記録の置き場の源の工場の組と同じ組み方で、模擬の源で呼ぶ
  (購読の始まりを本体の頭に置く工場なので、始まりを組み立ての時に置く 2 つの不変条件は除く — 記録の置き場の工場と同じ)。
- 失敗ケース:
  書いた瞬間に合図が列に在る(仮想の時計を進めない — 書いた刻と受けた刻が同じ)。
  同じ書き(PutRows)の結んだ 2 つの表は 1 つの合図(keys に両方)— 2 つ目の合図は来ない。
  別の handler の組(模擬の別の process — 同じ置き場を別の memory_records_handler で書く)の書きでも合図が出る。
  poll の effect(WatchChanges・WatchEvents・Delay)を出さない。
  本体が終われば源の task が止まり、置き場に呼び鈴を残さない。
"""

from dataclasses import dataclass
from datetime import timedelta
from functools import partial

import hy  # noqa: F401  Hy の module を読むため
import pytest
from doeff_core_effects.handlers import reader
from doeff_core_effects.scheduler import Spawn, Wait
from doeff_events import ArmTimer, EventBus, TimerFired, subscribed_event_handler, timer_handler
from doeff_events.effects import WaitForEvent
from doeff_hy.frozen import FrozenMap
from doeff_records.effects import PutRows, RowWrite, WatchChanges, WatchEvents
from doeff_records.event_source import (
    RECORDS_SIGNAL_SOURCE,
    ReadSignalSource,
    SignalSourceFactory,
    SignalTables,
    read_signal_handler,
    records_signal_handler,
)
from doeff_records.memory import MemoryStore, memory_records_handler, memory_signal_handler, memory_signal_source
from doeff_records.http_client import RecordsEndpoint, http_records_handler
from doeff_records.values import ExpectAny, WrittenRows
from doeff_time import Delay, DelayEffect, GetTime
from tests.test_event_source import (
    SCHEMA,
    STARTS_AT_SUBSCRIBE,
    WRITER,
    _row,
    _run_on,
    _stacked,
    _subscribe,
    _write,
)

from doeff import EffectBase, EffectGenerator, K, Pass, Program, Pure, Resume, do, run, with_handlers
from doeff.program import ProgramHandler

# 共有の不変条件(test_event_source が path に足した doeff-events の tests から読む)。
from event_signal_invariants import INVARIANTS, Changed, Invariant, SignalWorld  # noqa: E402 - test_event_source の path の後


def memory_world() -> SignalWorld:
    """1 つの世界 = 新しい memory の置き場 1 つと購読者の列の置き場 1 つ。源は模擬の源(置き場を閉じた工場)。"""
    store = MemoryStore(SCHEMA)
    bus = EventBus()
    factory: SignalSourceFactory = run(memory_signal_source(store))

    def build(bindings: tuple[SignalTables, ...], subscriber: str) -> Program[ProgramHandler]:
        """模擬の源の工場の組み立て方(工場は Program ではないので答えを Pure で包む — 位置は本体の頭で読む)。"""
        return Pure(factory.make(bindings, subscriber))

    def subscribe(subscriber: str, event_types: tuple[type, ...] = (), /) -> Program[ProgramHandler]:
        """この世界の購読者の列と置き場の上で、購読者の組を組み立てる Program。"""
        return _subscribe(build, bus, subscriber, event_types)

    return SignalWorld(subscribe=subscribe, run=partial(_run_on, store), row=partial(_row, "jobs"))


@pytest.mark.parametrize("invariant", [inv for inv in INVARIANTS if inv not in STARTS_AT_SUBSCRIBE], ids=lambda inv: inv.__name__)
def test_memory_signal_source_keeps_invariant(invariant: Invariant) -> None:
    """共有の不変条件を、模擬の源で 1 つずつ確かめる。"""
    invariant(memory_world())


@dataclass(frozen=True)
class Seen:
    """受けた合図と受けた刻。"""

    signal: object
    at: object


CHANGED_ON_JOBS_AND_LANES = (SignalTables(signal=Changed, tables=("jobs", "lanes")),)
SUBSCRIBER = "reader"


def _layer(store: MemoryStore, bindings: tuple[SignalTables, ...], *extra: ProgramHandler) -> tuple[ProgramHandler, ...]:
    """購読者の列(Changed と TimerFired)→ 期限 → (extra)→ 模擬の源 の組(外 → 内)。"""
    return (
        subscribed_event_handler(EventBus(), SUBSCRIBER, (Changed, TimerFired)),
        timer_handler(),
        *extra,
        memory_signal_handler(store, bindings, SUBSCRIBER),
    )


@do
def _write_later(seconds: float, table: str, key: str) -> EffectGenerator[object]:
    """seconds 秒(仮想)の後に表 table の鍵 key の行を書き、書いた刻を返す。"""
    yield Delay(seconds)
    yield _write(table, key)
    at = yield GetTime()
    return at


@do
def _seen_after_write(seconds: float) -> EffectGenerator[tuple[Seen, object]]:
    """書き手の task を Spawn して合図を待ち、受けた合図と刻・書いた刻を返す。"""
    writer = yield Spawn(_write_later(seconds, "jobs", "j1"))
    signal = yield WaitForEvent(Changed, TimerFired)
    at = yield GetTime()
    written_at = yield Wait(writer)
    return Seen(signal, at), written_at


def test_a_write_signals_at_the_same_virtual_instant() -> None:
    """書いた瞬間に合図が列に在る: 受けた刻 = 書いた刻(模擬の時計を進めない — poll の間隔を待たない)。"""
    store = MemoryStore(SCHEMA)
    seen, written_at = _run_on(store, _stacked(_layer(store, CHANGED_ON_JOBS_AND_LANES), _seen_after_write(7.0)))
    assert isinstance(seen.signal, Changed), seen
    assert seen.at == written_at, (seen.at, written_at)


@do
def _one_signal_for_put_rows() -> EffectGenerator[tuple[object, object]]:
    """表 jobs と lanes を 1 つの PutRows で書き、最初の合図と、その後 1 秒の期限までに来た物(期限なら合図の 2 つ目は無い)を返す。"""
    written = yield PutRows(
        (
            RowWrite("jobs", ("j1",), FrozenMap({"note": "x"}), ExpectAny()),
            RowWrite("lanes", ("l1",), FrozenMap({"note": "y"}), ExpectAny()),
        )
    )
    assert isinstance(written, WrittenRows), written
    first = yield WaitForEvent(Changed, TimerFired)
    now = yield GetTime()
    yield ArmTimer("after", now + timedelta(seconds=1))
    second = yield WaitForEvent(Changed, TimerFired)
    return first, second


def test_one_put_rows_over_two_bound_tables_is_one_signal() -> None:
    """同じ書き(1 つの transaction)の結んだ 2 つの表は 1 つの合図(keys に両方)— 2 つ目の合図は来ず、期限が先に来る。"""
    store = MemoryStore(SCHEMA)
    first, second = _run_on(store, _stacked(_layer(store, CHANGED_ON_JOBS_AND_LANES), _one_signal_for_put_rows()))
    assert isinstance(first, Changed), first
    assert {(row.table, row.key) for row in first.keys} == {("jobs", '["j1"]'), ("lanes", '["l1"]')}, first
    assert second == TimerFired("after"), second


@do
def _written_by_another_stack(store: MemoryStore) -> EffectGenerator[object]:
    """別の handler の組(別の memory_records_handler — 模擬の別の process の代役)で書く task を Spawn し、合図を待つ。"""
    writer = yield Spawn(with_handlers([memory_records_handler(store, WRITER)], _write_later(3.0, "lanes", "l9")))
    signal = yield WaitForEvent(Changed, TimerFired)
    yield Wait(writer)
    return signal


def test_a_write_from_another_handler_stack_signals() -> None:
    """置き場 1 つを共有する別の handler の組の書きでも合図が出る(置き場の呼び鈴が鳴る — 書き手の組に源が居なくてよい)。"""
    store = MemoryStore(SCHEMA)
    signal = _run_on(store, _stacked(_layer(store, CHANGED_ON_JOBS_AND_LANES), _written_by_another_stack(store)))
    assert isinstance(signal, Changed), signal
    assert [(row.table, row.key) for row in signal.keys] == [("lanes", '["l9"]')], signal


POLLS = (WatchChanges, WatchEvents, DelayEffect)


@do
def _no_polls(effect: EffectBase, k: K) -> EffectGenerator[object]:
    """源と本体が外へ出す effect のうち、poll の effect(WatchChanges・WatchEvents・Delay)を見つけたら赤にする見張り。"""
    if isinstance(effect, POLLS):
        raise AssertionError(f"模擬の源が poll の effect を出した: {effect!r}")
    yield Pass(effect, k)


@do
def _write_then_receive() -> EffectGenerator[object]:
    """書き手の task(源の外 — 見張りを通らない)が書き、本体が合図を受ける。"""
    writer = yield Spawn(_write("jobs", "j2"))
    signal = yield WaitForEvent(Changed, TimerFired)
    yield Wait(writer)
    return signal


def test_the_memory_source_issues_no_poll_effects() -> None:
    """模擬の源は WatchChanges・WatchEvents・Delay を出さない(見張りを源のすぐ外に置く — 源の task の effect も見張りを通る)。"""
    store = MemoryStore(SCHEMA)
    signal = _run_on(store, _stacked(_layer(store, CHANGED_ON_JOBS_AND_LANES, _no_polls), _write_then_receive()))
    assert isinstance(signal, Changed), signal
    assert memory_signal_handler.__doeff_effects__ and not set(POLLS) & set(memory_signal_handler.__doeff_effects__)


@do
def _receive_once() -> EffectGenerator[object]:
    """書いて合図を 1 つ受けて終わる本体。"""
    writer = yield Spawn(_write("jobs", "j3"))
    signal = yield WaitForEvent(Changed, TimerFired)
    yield Wait(writer)
    return signal


def test_the_source_stops_and_leaves_no_bell_when_the_body_ends() -> None:
    """本体が終われば源の task を止め、置き場に掛けた呼び鈴を残さない(鳴らない呼び鈴が置き場に溜まらない)。"""
    store = MemoryStore(SCHEMA)
    signal = _run_on(store, _stacked(_layer(store, CHANGED_ON_JOBS_AND_LANES), _receive_once()))
    assert isinstance(signal, Changed), signal
    assert store.bells == {}, store.bells


def test_the_factory_key_names_the_production_and_the_memory_sources() -> None:
    """鍵 SignalSourceFactory: 本番の値の make は records_signal_handler・模擬の値の make は置き場を閉じた memory_signal_handler。"""
    assert isinstance(RECORDS_SIGNAL_SOURCE, SignalSourceFactory)
    assert RECORDS_SIGNAL_SOURCE.make is records_signal_handler
    store = MemoryStore(SCHEMA)
    memory = run(memory_signal_source(store))
    assert isinstance(memory, SignalSourceFactory)
    assert memory.make.func is memory_signal_handler and memory.make.args == (store,), memory.make


# --- 源の工場の問い ReadSignalSource に答えるのは、その組で記録に答えている handler 自身(#3127)---------------------------


@do
def _asked_factory() -> EffectGenerator[object]:
    """源の工場を問うて答えを返す(組み立ての entry と同じ問い方)。"""
    source = yield ReadSignalSource()
    return source


def test_the_memory_records_handler_answers_with_a_source_on_its_own_store() -> None:
    """memory の記録の handler は、自分の置き場を閉じた模擬の源で答える(外に別の置き場の handler が在っても内側の置き場 — 源が別の置き場に結ばれない)。"""
    store = MemoryStore(SCHEMA)
    other = MemoryStore(SCHEMA)
    source = run(with_handlers([memory_records_handler(other, WRITER), memory_records_handler(store, WRITER)], _asked_factory()))
    assert isinstance(source, SignalSourceFactory), source
    assert source.make.func is memory_signal_handler and source.make.args == (store,), source.make


def test_the_http_records_handler_answers_with_the_production_source() -> None:
    """記録の HTTP の client は本番の源 RECORDS_SIGNAL_SOURCE で答える(問いでは service を呼ばない)。"""
    source = run(with_handlers([http_records_handler(RecordsEndpoint("http://records.invalid"))], _asked_factory()))
    assert source is RECORDS_SIGNAL_SOURCE, source


@dataclass(frozen=True)
class OtherKey:
    """組の内側の設定の読み手が持つ鍵(源の工場の問いとは別の物)。"""


def test_an_inner_settings_reader_does_not_swallow_the_question() -> None:
    """組の内側に設定の読み手(決まった鍵だけを持ち、知らない鍵の Ask は断る reader)が居ても、源の工場の問いは記録の handler に届く
    (記録の effect なので設定の読み手は触らない — Ask の鍵にすると、ここで断られた: 画面の模擬の土台で 124 本赤)。"""
    store = MemoryStore(SCHEMA)
    source = run(with_handlers([memory_records_handler(store, WRITER), reader({OtherKey: "設定"})], _asked_factory()))
    assert isinstance(source, SignalSourceFactory) and source.make.args == (store,), source


@do
def _entry_shaped(bindings: tuple[SignalTables, ...]) -> EffectGenerator[object]:
    """組み立ての entry の形: 源の工場を問うて、購読者の列 → 期限 → 源 を被せた本体で書きの合図を受ける。"""
    source: SignalSourceFactory = yield ReadSignalSource()
    layer = (subscribed_event_handler(EventBus(), SUBSCRIBER, (Changed, TimerFired)), timer_handler(), source.make(bindings, SUBSCRIBER))
    signal = yield _stacked(layer, _write_then_receive())
    return signal


def test_an_entry_that_asks_gets_signals_from_the_store_its_records_handler_serves() -> None:
    """entry が問うだけで、その組の記録の handler の置き場の書きの合図を受ける(本番と模擬の違いは記録の handler の差し替えだけ)。"""
    store = MemoryStore(SCHEMA)
    signal = _run_on(store, _entry_shaped(CHANGED_ON_JOBS_AND_LANES))
    assert isinstance(signal, Changed), signal
    assert [(row.table, row.key) for row in signal.keys] == [("jobs", '["j2"]')], signal


@do
def _entry_with_read_signal_handler(bindings: tuple[SignalTables, ...]) -> EffectGenerator[object]:
    """組み立ての entry の形(源の種類を名指さない): 購読者の列 → 期限 → read_signal_handler を被せた本体で書きの合図を受ける。"""
    layer = (subscribed_event_handler(EventBus(), SUBSCRIBER, (Changed, TimerFired)), timer_handler(), read_signal_handler(bindings, SUBSCRIBER))
    signal = yield _stacked(layer, _write_then_receive())
    return signal


def test_read_signal_handler_uses_the_source_of_the_records_handler_in_scope() -> None:
    """entry が read_signal_handler を置くだけで、その組の記録の handler(ここでは memory)の置き場の書きの合図を受ける。"""
    store = MemoryStore(SCHEMA)
    signal = _run_on(store, _entry_with_read_signal_handler(CHANGED_ON_JOBS_AND_LANES))
    assert isinstance(signal, Changed), signal
    assert [(row.table, row.key) for row in signal.keys] == [("jobs", '["j2"]')], signal


def test_read_signal_source_lives_with_the_records_effects() -> None:
    """源の工場の問いは記録の effect の置き場 doeff_records.effects に在り、event_source からも同じ型が読める(模擬の柵は effects と faults の
    module の型を集めるので、ここに在れば柵が通す — #3127)。"""
    from doeff_records import effects, event_source

    assert effects.ReadSignalSource is event_source.ReadSignalSource
