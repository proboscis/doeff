"""合図の handler が守る不変条件の検(agora-redesign #3075・設計 #3072)。

各関数は handler の組み立て方(``SignalWorld``)を引数に取り、筋書きを走らせて不変条件を確かめる。memory の
``subscribed_event_handler`` も、記録の service から合図を受ける handler(agora-redesign #3077)も、同じ関数を自分の
組み立て方で呼ぶ。pytest が直に集めないよう ``test_`` で始めない名の file に置く。

合図は「どこが変わったか」(記録のキー)だけを運び、受け手は記録を読み直して状態を作る。筋書きの記録は
``RecordBook``(キー → 版の索引)で表す。時間で待たず、task どうしの順は約束(``CreatePromise``・``Wait``)で決める。

#3077 の handler も呼べるように 3 点広げた(memory の組み立て方の意味は変えない):

- 組み立ては Program(``subscribe`` の答え = handler を返す Program)— 記録の service を源にする handler は、購読の始まりの
  位置を記録から読む(effect)ので、組み立てそのものが effect を出す。memory の組み立て方は ``Pure`` で包むだけ。
- 合図 ``Changed`` の欄は ``keys``(変わった所の tuple)で、筋書きのキーをその world の「所」へ写すのは ``SignalWorld.row``
  (memory は写さない — 既定)。記録の service の world では、所は記録の行(表の名前と鍵)になる。
- (b) の 2 度目の合図は、受け手が 1 度目を受けた後に発する — 記録の service の handler は 1 回の変更の束の中の同じ型の合図を
  1 つにまとめるので、続けて発した 2 つは 1 つの合図になり得る(重複を受け手に 2 度渡す筋書きは、束をまたぐ形で書く)。
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, final

from doeff_core_effects.scheduler import CompletePromise, CreatePromise, Promise, Spawn, Wait
from doeff_events.effects import Publish, WaitForEvent

from doeff import do

if TYPE_CHECKING:
    from doeff import EffectGenerator, Program
    from doeff.program import ProgramHandler


class Subscribe(Protocol):
    """購読者 ``subscriber`` の handler を組み立てる Program(組み立てた時に購読が始まる)。``event_types=()`` は発するだけ。"""

    def __call__(
        self, subscriber: str, event_types: tuple[type, ...] = (), /
    ) -> Program[ProgramHandler]: ...


def _same_key(key: str) -> Hashable:
    """筋書きのキーを、そのまま合図の所にする(memory の world の既定)。"""
    return key


@dataclass(frozen=True)
class SignalWorld:
    """不変条件の検が受け取る、合図の handler の組み立て方と走らせ方。

    ``subscribe`` = 購読者の handler を組み立てる Program。同じ world の handler どうしが合図を交わす。
    ``run`` = その handler が要る土台(scheduler など)の下で Program を走らせ、値を返す。
    ``row`` = 筋書きのキー → その world の合図が運ぶ所(``Changed.keys`` の要素・``RecordBook`` のキー)。
    """

    subscribe: Subscribe
    run: Callable[[Program[object]], object]
    row: Callable[[str], Hashable] = _same_key


@dataclass(frozen=True)
class Changed:
    """所 ``keys`` が変わった、という合図(状態の差分は運ばない)。"""

    keys: tuple[Hashable, ...]


@dataclass(frozen=True)
class Unrelated:
    """購読の型の外の合図。"""

    key: str


@final
class RecordBook:
    """筋書きの記録: 所 → 版の索引。受け手は合図の所でここを読み直して状態を作る。"""

    __slots__ = ("_versions",)

    def __init__(self, versions: dict[Hashable, int]) -> None:
        self._versions: Final[dict[Hashable, int]] = dict(versions)

    def read(self, key: Hashable) -> int:
        """受け手が状態を作り直すために、所の今の版を読む。"""
        return self._versions[key]

    def write(self, key: Hashable, version: int) -> None:
        """書き手が記録を変える(合図はその後に別に発する)。"""
        self._versions[key] = version


@dataclass(frozen=True)
class GapOutcome:
    """読み → 処理 → 待ちの筋書きの結果。"""

    version_before: int
    signal: Changed
    version_after: int


@do
def _run_as(
    world: SignalWorld, subscriber: str, event_types: tuple[type, ...], program: Program[object]
) -> EffectGenerator[object]:
    """``subscriber`` の handler を組み立て、その下で ``program`` を走らせて答えを返す。"""
    handler: ProgramHandler = yield world.subscribe(subscriber, event_types)
    return (yield handler(program))


def check_signal_between_read_and_wait_is_kept(world: SignalWorld) -> None:
    """(a) 状態を読む → 処理 → ``WaitForEvent`` の間に別の task が発した合図を落とさない。"""
    outcome = run_read_process_wait(world)
    job = world.row("job")
    assert outcome == GapOutcome(version_before=1, signal=Changed((job,)), version_after=2), outcome


def run_read_process_wait(world: SignalWorld) -> GapOutcome:
    """受け手が記録を読み、処理している間に書き手が記録を書いて合図を発し、その後で受け手が合図を待つ筋書き。

    合図を待ち手の居ない間に捨てる handler では、受け手は起こされず scheduler の行き止まりで終わる。
    """
    job = world.row("job")
    book = RecordBook({job: 1})

    @do
    def worker(read_done: Promise[object], written: Promise[object]) -> EffectGenerator[GapOutcome]:
        """受け手: 記録を読み、処理し、それから合図を待って記録を読み直す。"""
        version_before = book.read(job)
        yield CompletePromise(read_done, None)
        # 処理の間に書き手が記録を書き、合図を発し終える。
        yield Wait(written.future)
        signal = yield WaitForEvent(Changed)
        return GapOutcome(version_before, signal, book.read(signal.keys[0]))

    @do
    def writer(read_done: Promise[object], written: Promise[object]) -> EffectGenerator[None]:
        """書き手: 受け手が読み終えた後(処理の間)に記録を書き、合図を発する。"""
        yield Wait(read_done.future)
        book.write(job, 2)
        yield Publish(Changed((job,)))
        yield CompletePromise(written, None)

    @do
    def main() -> EffectGenerator[GapOutcome]:
        """受け手の購読を始めてから受け手と書き手を別の task で走らせ、受け手の結果を返す。"""
        read_done: Promise[object] = yield CreatePromise()
        written: Promise[object] = yield CreatePromise()
        receiving: ProgramHandler = yield world.subscribe("worker", (Changed,))
        sending: ProgramHandler = yield world.subscribe("writer")
        worker_task = yield Spawn(receiving(worker(read_done, written)))
        writer_task = yield Spawn(sending(writer(read_done, written)))
        outcome: GapOutcome = yield Wait(worker_task)
        yield Wait(writer_task)
        return outcome

    result = world.run(main())
    assert isinstance(result, GapOutcome), result
    return result


def check_duplicate_signal_gives_same_state(world: SignalWorld) -> None:
    """(b) 同じ合図が 2 度来ても受け手の結果が同じ — 受け手は合図の所で記録を読み直して状態を作る(冪等)。"""
    job = world.row("job")
    book = RecordBook({job: 1})

    @do
    def worker(ready: Promise[object], first_seen: Promise[object]) -> EffectGenerator[tuple[int, ...]]:
        """受け手: 合図を 2 度受け、そのたびに記録を読み直して作った状態を返す。"""
        yield CompletePromise(ready, None)
        first: Changed = yield WaitForEvent(Changed)
        state_after_first = book.read(first.keys[0])
        yield CompletePromise(first_seen, None)
        second: Changed = yield WaitForEvent(Changed)
        return (state_after_first, book.read(second.keys[0]))

    @do
    def writer(ready: Promise[object], first_seen: Promise[object]) -> EffectGenerator[None]:
        """書き手: 記録を 1 度だけ変え、同じ合図を 2 度発する(2 度目は受け手が 1 度目を受けた後)。"""
        yield Wait(ready.future)
        book.write(job, 2)
        yield Publish(Changed((job,)))
        yield Wait(first_seen.future)
        yield Publish(Changed((job,)))

    @do
    def main() -> EffectGenerator[tuple[int, ...]]:
        """受け手と書き手を別の task で走らせ、受け手の状態の列を返す。"""
        ready: Promise[object] = yield CreatePromise()
        first_seen: Promise[object] = yield CreatePromise()
        worker_task = yield Spawn(_run_as(world, "worker", (Changed,), worker(ready, first_seen)))
        writer_task = yield Spawn(_run_as(world, "writer", (), writer(ready, first_seen)))
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
            world.run(_run_as(world, subscriber, event_types, wait_unrelated()))
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
        yield Publish(Changed((world.row(key),)))

    @do
    def main() -> EffectGenerator[Changed]:
        """合図を 1 つ発し終えてから購読を始め、次の合図を発して、受け手が最初に受ける合図を返す。"""
        yield Wait((yield Spawn(_run_as(world, "writer", (), publish("early")))))
        # 「early」を発し終えた後に購読を始める。
        receive: ProgramHandler = yield world.subscribe("worker", (Changed,))
        yield Wait((yield Spawn(_run_as(world, "writer", (), publish("late")))))
        signal: Changed = yield receive(_wait_changed())
        return signal

    received = world.run(main())
    assert received == Changed((world.row("late"),)), received


def check_resubscribe_discards_previous_queue(world: SignalWorld) -> None:
    """(d) 同じ名で組み立て直すと前の列を捨て、組み立て直した時より後の合図から新しく始める。"""

    @do
    def publish(key: str) -> EffectGenerator[None]:
        """書き手: キー ``key`` の合図を 1 つ発する。"""
        yield Publish(Changed((world.row(key),)))

    @do
    def main() -> EffectGenerator[Changed]:
        """前の列に合図を積んだまま同じ名で組み立て直し、組み立て直した受け手が最初に受ける合図を返す。"""
        yield world.subscribe("worker", (Changed,))
        # 前の列に積まれたまま、受け手が読まずに落ちる。
        yield Wait((yield Spawn(_run_as(world, "writer", (), publish("before-restart")))))
        restarted: ProgramHandler = yield world.subscribe("worker", (Changed,))
        yield Wait((yield Spawn(_run_as(world, "writer", (), publish("after-restart")))))
        signal: Changed = yield restarted(_wait_changed())
        return signal

    received = world.run(main())
    assert received == Changed((world.row("after-restart"),)), received


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
