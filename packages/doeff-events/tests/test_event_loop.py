"""event-loop の macro の検(agora-redesign #3080・設計 = agora-controllers docs/design/event-waits/README.md 4 節)。

- 節ごとの結果: 購読者ごとの列(subscribed_event_handler)に出来事を流し、各節の本体の値が次の state になり、(stop 値) で抜ける。
  出来事ごとの記録(slog)が受けた順に残る。
- 止めの合図: 処理の途中で止めが来たら、その刻に止めの節で抜ける(列に残る後の出来事を回さない)。ループの前に止めが来ていたら、
  出来事を 1 つも回さずに止めの節で抜ける。
- 扱わない型の出来事は来ない: 節の頭の型だけを待つので、同じ購読者の列に積まれた別の型の出来事は節に渡らない。
- 期限の節: ArmTimer の期限が TimerFired として節に届く。
- 展開の時に断る形(止めの節が無い・待つ型を導けない節 など)は HyMacroExpansionError。
"""

import types
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import hy
import pytest
from doeff_core_effects.effects import Listen, SlogEffect
from doeff_core_effects.handlers import listen_handler, slog_discard_handler, state
from doeff_core_effects.scheduler import scheduled
from doeff_core_effects.stop_signal_effects import RaiseStop
from doeff_core_effects.stop_signal_handlers import scripted_stop_handler
from doeff_events import EventBus, Publish, TimerFired, subscribed_event_handler, timer_handler
from doeff_time import sim_time_handler

from doeff import do, run, with_handlers

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Added:
    n: int


@dataclass(frozen=True)
class Done:
    pass


@dataclass(frozen=True)
class Unrelated:
    n: int


@dataclass(frozen=True)
class Moved:
    """欄の名に区切りを持つ合図(Hy の defrecord の欄 card-key は属性 card_key になる)。"""

    card_key: str


SUBSCRIBED: tuple[type, ...] = (Added, Done, Unrelated, Moved, TimerFired)

PRELUDE = """
(require doeff-hy.macros [defk <-])
(require doeff-events.macros [event-loop])
(import doeff_core_effects.stop_signal_effects [RaiseStop])
(import doeff_events [ArmTimer TimerFired])
(import datetime [datetime])
"""

LOOPS = """
(defk folded []
  {:pre [] :post [(: % tuple)]}
  "出来事を state に積み、Done で抜ける(止めの合図なら理由を足して抜ける)。state の型を書いた形。"
  (event-loop [seen tuple #()]
    (:stop reason) #(#* seen reason)
    (Added n)      #(#* seen n)
    (Moved :card-key key) #(#* seen key)
    (Done)         (stop seen)))

(defk mistyped []
  {:pre [] :post [(: % tuple)]}
  "state の型 tuple に str を返す節 — 走らせた時に断る。"
  (event-loop [seen tuple #()]
    (:stop reason) seen
    (Added n)      "not a tuple"
    (Done)         (stop seen)))

(defk stop-on-first []
  {:pre [] :post [(: % tuple)]}
  "最初の Added の処理の中で止めを起こす — 列に残る後の出来事は回らない。"
  (event-loop [seen #()]
    (:stop reason) #(#* seen reason)
    (Added n)      (do (when (= n 1) (! (RaiseStop "scene"))) #(#* seen n))
    (Done)         (stop seen)))

(defk doubled-of [n]
  {:pre [(: n int)] :post [(: % int)]}
  "本体の <- が呼ぶ小さな Program。"
  (* n 2))

(defk bound-in-body []
  {:pre [] :post [(: % tuple)]}
  "本体の (do …) の中で <- で束ねる — 文の位置の <- が出来事ごとに回る。"
  (event-loop [seen #()]
    (:stop reason) #(#* seen reason)
    (Added n)      (do (<- doubled int (doubled-of n))
                       #(#* seen doubled))
    (Done)         (stop seen)))

(defk stateless []
  {:pre [] :post [(: % str)]}
  "state を省いた形 — 本体の値は捨て、Done の stop で抜ける。"
  (event-loop
    (:stop reason) reason
    (Added n)      n
    (Done)         (stop "done")))

(defk until-deadline [at]
  {:pre [(: at datetime)] :post [(: % tuple)]}
  "初期値の中で期限を掛け、その TimerFired の節で抜ける。"
  (event-loop [seen (do (! (ArmTimer "end" at)) #())]
    (:stop reason)        #(#* seen reason)
    (Added n)             #(#* seen n)
    (TimerFired :tag tag) (match tag
                            "end" (stop #(#* seen tag))
                            _     seen)))
"""


def _hy_module(source: str) -> types.ModuleType:
    """Hy の字面を新しい module に読み込む(出来事の型は Python の側で置く)。"""
    module = types.ModuleType("event_loop_scene")
    vars(module).update({"Added": Added, "Done": Done, "Unrelated": Unrelated, "Moved": Moved})
    hy.eval(hy.read_many(PRELUDE + source), module=module)
    return module


SCENES = _hy_module(LOOPS)


@dataclass(frozen=True)
class Ran:
    """走らせた結末: value = event-loop を含む Program の値 / events = 受けた出来事の型の名(event-loop の slog の順)。"""

    value: object
    events: tuple[str, ...]


@do
def _listened(program: Any) -> Any:
    """Program の値と、その間の event-loop の slog を値として受け取る(Listen — 記録を書き換えずに集める)。"""

    value, logs = yield Listen(program, types=(SlogEffect,))
    events = tuple(str(log.kwargs["event"]) for log in logs if log.msg == "event-loop")
    return Ran(value=value, events=events)


def _run(program: Any) -> Ran:
    """仮想の時計・購読者 1 つの列・期限・止めの合図(筋書きから起こす形)の上で走らせる。"""

    bus = EventBus()
    inner = with_handlers([state(), scripted_stop_handler, slog_discard_handler, listen_handler], _listened(program))
    stack = subscribed_event_handler(bus, "loop", SUBSCRIBED)(timer_handler()(inner))
    return run(scheduled(sim_time_handler(start_time=T0)(stack)))


@do
def _published_then(events: tuple[object, ...], program: Any) -> Any:
    """出来事を先に列へ積んでからループを走らせる(購読は handler を組んだ時に始まるので落ちない)。"""

    for event in events:
        yield Publish(event)
    return (yield program)


def test_each_clause_returns_the_next_state_and_stop_ends_the_loop() -> None:
    ran = _run(_published_then((Added(1), Added(2), Done(), Added(3)), SCENES.folded()))

    assert ran == Ran(value=(1, 2), events=("Added", "Added", "Done"))


def test_a_clause_binds_a_field_whose_name_has_a_separator() -> None:
    # (Moved :card-key key) は属性 card_key に当たる — 欄の名を属性の名へ直さないと _ の枝へ落ちて TypeError。
    ran = _run(_published_then((Moved("ki-1"), Added(2), Done()), SCENES.folded()))

    assert ran == Ran(value=("ki-1", 2), events=("Moved", "Added", "Done"))


def test_a_clause_value_of_another_type_than_the_state_is_refused() -> None:
    with pytest.raises(AssertionError, match="tuple"):
        _run(_published_then((Added(1), Done()), SCENES.mistyped()))


def test_events_of_types_no_clause_names_never_reach_a_clause() -> None:
    ran = _run(_published_then((Unrelated(9), Added(1), Unrelated(8), Done()), SCENES.folded()))

    assert ran == Ran(value=(1,), events=("Added", "Done"))


def test_a_stop_raised_while_processing_ends_the_loop_before_the_queued_events() -> None:
    ran = _run(_published_then((Added(1), Added(2), Done()), SCENES.stop_on_first()))

    assert ran == Ran(value=(1, "scene"), events=("Added",))


def test_a_stop_requested_before_the_loop_runs_no_clause() -> None:
    @do
    def stopped_first() -> Any:
        yield RaiseStop("before")
        return (yield _published_then((Added(1), Done()), SCENES.folded()))

    assert _run(stopped_first()) == Ran(value=("before",), events=())


def test_a_bind_in_a_clause_body_runs_for_each_event() -> None:
    ran = _run(_published_then((Added(1), Added(2), Done()), SCENES.bound_in_body()))

    assert ran.value == (2, 4)


def test_the_stateless_form_ends_with_stop() -> None:
    ran = _run(_published_then((Added(1), Added(2), Done()), SCENES.stateless()))

    assert ran == Ran(value="done", events=("Added", "Added", "Done"))


def test_a_deadline_armed_in_the_initial_state_reaches_the_timer_clause() -> None:
    ran = _run(_published_then((Added(1),), SCENES.until_deadline(T0 + timedelta(hours=6))))

    assert ran == Ran(value=(1, "end"), events=("Added", "TimerFired"))


@pytest.mark.parametrize(
    ("loop", "message"),
    [
        ("(event-loop [s 0] (Added n) n)", "止めの節"),
        ("(event-loop [s 0] (:stop r) r (:stop q) q (Added n) n)", "止めの節は 1 つだけ"),
        ("(event-loop [s 0] (:stop r) r)", "待つ型の節が無い"),
        ("(event-loop [s 0] (:stop r) r _ s)", "待つ型を導けない"),
        ("(event-loop [s 0] (:stop r) r (_ n) s)", "待つ型を導けない"),
        ("(event-loop [s 0] (:stop r) r ev s)", "待つ型を導けない"),
        ("(event-loop [s 0] (:stop r) r (Added n) n (Added m) m)", "同じ型の節"),
        ("(event-loop [s 0] (:stop r) r (Added n))", "本体の無い節"),
        ("(event-loop [s 0] (:stop r) r (Added 1) s)", "値で絞らず"),
        ("(event-loop [s 0] (:stop r) r (Added n) (do (stop n) n))", "最後の値の位置"),
        ("(event-loop [s] (:stop r) r (Added n) n)", "\\[名 初期値\\]"),
    ],
)
def test_malformed_loops_are_refused_when_expanded(loop: str, message: str) -> None:
    source = f'(defk broken [] {{:pre [] :post [(: % int)]}} "壊れた形" {loop})'
    with pytest.raises(hy.errors.HyMacroExpansionError, match=message):
        _hy_module(source)
