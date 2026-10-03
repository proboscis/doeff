"""記録の合図の源の工場 records_signal_handler を、閉じの検の道具(doeff-effect-analyzer)が読めるかの検(#3104)。

使い手の係は工場を with_handlers の列に呼びの字面で置く。以前の形(Program の records_signal_source を `<-` で受けて列に置く)は、
analyzer が列の値を読めず、本番の土台で閉じているかの検(foundation-closure)に unknown を 1 つ出した。

- 工場の宣言(__doeff_handles__ = ()・__doeff_effects__ = SOURCE_EFFECTS)を analyzer が読み、何にも答えず本体の周りで
  SOURCE_EFFECTS を出す包みと数える。失敗ケース: 宣言を外すと unread(閉じの検の unknown)で赤。
- 閉じの検の形: 工場の宣言の effect と、包み(run_signal_source)が本体の周りで実際に出す effect が一致する。失敗ケース: 包みが
  出す effect を変えても、宣言を変えても赤。
- 使い手の形の job(購読者の列と工場を並べた列)を scheduler の下で読むと、読めない handler が残らず、残るのは記録と時計の
  effect(本番の土台の記録の handler と時計が答える物)だけ。
"""

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import hy  # noqa: F401  Hy の module を読むため
from doeff_events import EventBus, subscribed_event_handler
from doeff_events.effects import WaitForEvent
from doeff_records.effects import ListRows, ReadStreamEnd, WatchChanges, WatchEvents
from doeff_records.event_source import (
    SOURCE_EFFECTS,
    ChangedRow,
    SignalTables,
    records_signal_handler,
    run_signal_source,
)
from doeff_time import DelayEffect

from doeff import EffectGenerator, do, with_handlers

# doeff-effect-analyzer の Python の front end(開発の道具 — doeff-records の実行時の依存ではない。doeff-cluster の tests の
# conftest と同じ扱い)。
if importlib.util.find_spec("doeff_effect_analyzer") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "doeff-effect-analyzer" / "python"))

from doeff_effect_analyzer.handler_effects import Basis, analyze_handler, check_coverage  # noqa: E402 - sys.path の後
from doeff_effect_analyzer.program_effects import analyze_program, runs_where_performed  # noqa: E402 - sys.path の後


@dataclass(frozen=True)
class Moved:
    """表 jobs の行が変わった、という合図(検の的)。"""

    keys: tuple[ChangedRow, ...]


SIGNALS = (SignalTables(signal=Moved, tables=("jobs",)),)
BUS = EventBus()


@do
def _waits() -> EffectGenerator[Moved]:
    """本体: 合図を 1 つ待って返す。"""
    signal: Moved = yield WaitForEvent(Moved)
    return signal


@do
def _wrapped() -> EffectGenerator[Moved]:
    """工場の包みが本体 _waits を走らせる形(包みが本体の周りで出す effect を読むための的)。"""
    answer: Moved = yield run_signal_source(SIGNALS, "worker", _waits())
    return answer


@do
def _job() -> EffectGenerator[Moved]:
    """使い手の形: 購読者の列の内側に工場を呼びの字面で置き、本体を包む。"""
    answer: Moved = yield with_handlers(
        [subscribed_event_handler(BUS, "worker", (Moved,)), records_signal_handler(SIGNALS, "worker")], _waits()
    )
    return answer


def test_the_analyzer_reads_the_factory_from_its_declaration() -> None:
    handler = analyze_handler("doeff_records.event_source:records_signal_handler")
    assert handler.basis is Basis.DECLARED, handler.unresolved
    assert handler.handled == frozenset()
    assert handler.performs is not None
    assert handler.performs.effect_types == frozenset(SOURCE_EFFECTS)


def test_the_declared_effects_are_what_the_wrapper_performs_around_the_body() -> None:
    declared = analyze_handler("doeff_records.event_source:records_signal_handler").performs
    assert declared is not None
    around = analyze_program(_wrapped).residual_with(runs_where_performed).effect_types
    body = analyze_program(_waits).residual_with(runs_where_performed).effect_types
    assert declared.effect_types == around - body


def test_a_job_placing_the_factory_leaves_no_unreadable_handler() -> None:
    scheduler = analyze_handler("doeff_core_effects.scheduler:scheduled", name="scheduled")
    coverage = check_coverage(analyze_program(_job), [scheduler], include=runs_where_performed)
    assert coverage.unknown_handlers == (), coverage.unknown_handlers
    assert {gap.effect for gap in coverage.gaps} == {ListRows, ReadStreamEnd, WatchChanges, WatchEvents, DelayEffect}
