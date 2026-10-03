"""合図の handler が守る不変条件の検(agora-redesign #3075・設計 #3072)。

各関数は handler の組み立て方(``SignalWorld``)を引数に取り、筋書きを走らせて不変条件を確かめる。memory の
``subscribed_event_handler`` も、記録の service から合図を受ける handler(agora-redesign #3077)も、同じ関数を自分の
組み立て方で呼ぶ。pytest が直に集めないよう ``test_`` で始めない名の file に置く。

合図は「どこが変わったか」(記録のキー)だけを運び、受け手は記録を読み直して状態を作る。筋書きの記録は
``RecordBook``(キー → 版の索引)で表す。時間で待たず、task どうしの順は約束(``CreatePromise``・``Wait``)で決める。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, final

from doeff_core_effects.scheduler import CompletePromise, CreatePromise, Promise, Spawn, Wait
from doeff_events.effects import Publish, WaitForEvent

from doeff import do

if TYPE_CHECKING:
    from doeff import EffectGenerator, Program
    from doeff.program import ProgramHandler


class Subscribe(Protocol):
    """購読者 ``subscriber`` の handler を組み立てる(組み立てた時に購読が始まる)。``event_types=()`` は発するだけ。"""

    def __call__(
        self, subscriber: str, event_types: tuple[type, ...] = (), /
    ) -> ProgramHandler: ...


@dataclass(frozen=True)
class SignalWorld:
    """不変条件の検が受け取る、合図の handler の組み立て方と走らせ方。

    ``subscribe`` = 購読者の handler を組み立てる。同じ world の handler どうしが合図を交わす。
    ``run`` = その handler が要る土台(scheduler など)の下で Program を走らせ、値を返す。
    """

    subscribe: Subscribe
    run: Callable[[Program[object]], object]


@dataclass(frozen=True)
class Changed:
    """記録のキー ``key`` が変わった、という合図(状態の差分は運ばない)。"""

    key: str


@dataclass(frozen=True)
class Unrelated:
    """購読の型の外の合図。"""

    key: str


@final
class RecordBook:
    """筋書きの記録: キー → 版の索引。受け手は合図のキーでここを読み直して状態を作る。"""

    __slots__ = ("_versions",)

    def __init__(self, versions: dict[str, int]) -> None:
        self._versions: Final[dict[str, int]] = dict(versions)

    def read(self, key: str) -> int:
        """受け手が状態を作り直すために、キーの今の版を読む。"""
        return self._versions[key]

    def write(self, key: str, version: int) -> None:
        """書き手が記録を変える(合図はその後に別に発する)。"""
        self._versions[key] = version


@dataclass(frozen=True)
class GapOutcome:
    """読み → 処理 → 待ちの筋書きの結果。"""

    version_before: int
    signal: Changed
    version_after: int


def check_signal_between_read_and_wait_is_kept(world: SignalWorld) -> None:
    """(a) 状態を読む → 処理 → ``WaitForEvent`` の間に別の task が発した合図を落とさない。"""
    outcome = run_read_process_wait(world)
    assert outcome == GapOutcome(version_before=1, signal=Changed("job"), version_after=2), outcome


def run_read_process_wait(world: SignalWorld) -> GapOutcome:
    """受け手が記録を読み、処理している間に書き手が記録を書いて合図を発し、その後で受け手が合図を待つ筋書き。

    合図を待ち手の居ない間に捨てる handler では、受け手は起こされず scheduler の行き止まりで終わる。
    """
    book = RecordBook({"job": 1})

    @do
    def worker(read_done: Promise[object], written: Promise[object]) -> EffectGenerator[GapOutcome]:
        """受け手: 記録を読み、処理し、それから合図を待って記録を読み直す。"""
        version_before = book.read("job")
        yield CompletePromise(read_done, None)
        # 処理の間に書き手が記録を書き、合図を発し終える。
        yield Wait(written.future)
        signal = yield WaitForEvent(Changed)
        return GapOutcome(version_before, signal, book.read(signal.key))

    @do
    def writer(read_done: Promise[object], written: Promise[object]) -> EffectGenerator[None]:
        """書き手: 受け手が読み終えた後(処理の間)に記録を書き、合図を発する。"""
        yield Wait(read_done.future)
        book.write("job", 2)
        yield Publish(Changed("job"))
        yield CompletePromise(written, None)

    @do
    def main() -> EffectGenerator[GapOutcome]:
        """受け手と書き手を別の task で走らせ、受け手の結果を返す。"""
        read_done: Promise[object] = yield CreatePromise()
        written: Promise[object] = yield CreatePromise()
        worker_task = yield Spawn(world.subscribe("worker", (Changed,))(worker(read_done, written)))
        writer_task = yield Spawn(world.subscribe("writer")(writer(read_done, written)))
        outcome: GapOutcome = yield Wait(worker_task)
        yield Wait(writer_task)
        return outcome

    result = world.run(main())
    assert isinstance(result, GapOutcome), result
    return result


def check_duplicate_signal_gives_same_state(world: SignalWorld) -> None:
    """(b) 同じ合図が 2 度来ても受け手の結果が同じ — 受け手は合図のキーで記録を読み直して状態を作る(冪等)。"""
    book = RecordBook({"job": 1})

    @do
    def worker(ready: Promise[object]) -> EffectGenerator[tuple[int, ...]]:
        """受け手: 合図を 2 度受け、そのたびに記録を読み直して作った状態を返す。"""
        yield CompletePromise(ready, None)
        first: Changed = yield WaitForEvent(Changed)
        state_after_first = book.read(first.key)
        second: Changed = yield WaitForEvent(Changed)
        return (state_after_first, book.read(second.key))

    @do
    def writer(ready: Promise[object]) -> EffectGenerator[None]:
        """書き手: 記録を 1 度だけ変え、同じ合図を 2 度発する。"""
        yield Wait(ready.future)
        book.write("job", 2)
        yield Publish(Changed("job"))
        yield Publish(Changed("job"))

    @do
    def main() -> EffectGenerator[tuple[int, ...]]:
        """受け手と書き手を別の task で走らせ、受け手の状態の列を返す。"""
        ready: Promise[object] = yield CreatePromise()
        worker_task = yield Spawn(world.subscribe("worker", (Changed,))(worker(ready)))
        writer_task = yield Spawn(world.subscribe("writer")(writer(ready)))
        states: tuple[int, ...] = yield Wait(worker_task)
        yield Wait(writer_task)
        return states

    states = world.run(main())
    assert states == (2, 2), states


def check_wait_outside_subscription_is_rejected(world: SignalWorld) -> None:
    """(c) 購読の型の外を待つと ``ValueError`` — 文に購読者の名と外れた型を名指す。発するだけの購読者の待ちも同じ。"""

    @do
    def wait_unrelated() -> EffectGenerator[object]:
        """購読の型の外の合図を待つ(配線の誤り)。"""
        return (yield WaitForEvent(Unrelated))

    for subscriber, event_types in (("worker", (Changed,)), ("writer", ())):
        try:
            world.run(world.subscribe(subscriber, event_types)(wait_unrelated()))
        except ValueError as error:
            message = str(error)
            assert subscriber in message, message
            assert "Unrelated" in message, message
        else:
            raise AssertionError(f"{subscriber} が購読の型の外 Unrelated を待てた")


def check_signal_before_subscription_is_not_kept(world: SignalWorld) -> None:
    """(d) 購読を始める前に発した合図は積まれない — 購読はその時より後の合図だけを積む。"""

    @do
    def publish(key: str) -> EffectGenerator[None]:
        """書き手: キー ``key`` の合図を 1 つ発する。"""
        yield Publish(Changed(key))

    @do
    def main() -> EffectGenerator[Changed]:
        """合図を 1 つ発し終えてから購読を始め、次の合図を発して、受け手が最初に受ける合図を返す。"""
        yield Wait((yield Spawn(world.subscribe("writer")(publish("early")))))
        # 「early」を発し終えた後に購読を始める。
        receive = world.subscribe("worker", (Changed,))
        yield Wait((yield Spawn(world.subscribe("writer")(publish("late")))))
        signal: Changed = yield receive(_wait_changed())
        return signal

    received = world.run(main())
    assert received == Changed("late"), received


def check_resubscribe_discards_previous_queue(world: SignalWorld) -> None:
    """(d) 同じ名で組み立て直すと前の列を捨て、組み立て直した時より後の合図から新しく始める。"""

    @do
    def publish(key: str) -> EffectGenerator[None]:
        """書き手: キー ``key`` の合図を 1 つ発する。"""
        yield Publish(Changed(key))

    @do
    def main() -> EffectGenerator[Changed]:
        """前の列に合図を積んだまま同じ名で組み立て直し、組み立て直した受け手が最初に受ける合図を返す。"""
        world.subscribe("worker", (Changed,))
        # 前の列に積まれたまま、受け手が読まずに落ちる。
        yield Wait((yield Spawn(world.subscribe("writer")(publish("before-restart")))))
        restarted = world.subscribe("worker", (Changed,))
        yield Wait((yield Spawn(world.subscribe("writer")(publish("after-restart")))))
        signal: Changed = yield restarted(_wait_changed())
        return signal

    received = world.run(main())
    assert received == Changed("after-restart"), received


@do
def _wait_changed() -> EffectGenerator[Changed]:
    """受け手: 次の Changed の合図を 1 つ受けて返す。"""
    signal: Changed = yield WaitForEvent(Changed)
    return signal


Invariant = Callable[[SignalWorld], None]
"""不変条件の検 1 つ: world を受け、破れていれば AssertionError を上げる。"""

INVARIANTS: Final[tuple[Invariant, ...]] = (
    check_signal_between_read_and_wait_is_kept,
    check_duplicate_signal_gives_same_state,
    check_wait_outside_subscription_is_rejected,
    check_signal_before_subscription_is_not_kept,
    check_resubscribe_discards_previous_queue,
)
