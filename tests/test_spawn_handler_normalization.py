"""再利用する handler の形式判定を Spawn のたびに繰り返さない (#1384)。"""

from collections.abc import Callable, Generator

import pytest
from doeff_core_effects import scheduler
from doeff_vm import K

from doeff import EffectBase, Pass, Pure, Resume, Spawn, Wait, do, handler, run
from doeff.program import ProgramHandler


class ReadNumber(EffectBase[int]):
    pass


def answer_number(effect: object, k: K) -> Resume | Pass:
    if isinstance(effect, ReadNumber):
        return Resume(k, 7)
    return Pass(effect, k)


@do
def read_number() -> Generator[ReadNumber, int, int]:
    return (yield ReadNumber())


@do
def read_in_children() -> Generator[Spawn | Wait, object, list[object]]:
    answers: list[object] = []
    for _ in range(4):
        task = yield Spawn(read_number())
        assert isinstance(task, scheduler.Task)
        answers.append((yield Wait(task)))
    return answers


def test_spawn_does_not_renormalize_captured_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    normalized: list[Callable[..., object]] = []

    def record_normalization(raw: Callable[..., object]) -> ProgramHandler:
        normalized.append(raw)
        return handler(raw)

    monkeypatch.setattr(scheduler, "_program_handler", record_normalization)
    program = scheduler.scheduled(handler(answer_number)(read_in_children()), implementation="python")

    assert run(program) == [7, 7, 7, 7]
    # GetBoundaries が返す値は VM が受理済みの dispatcher。利用者向けの
    # installer / dispatcher の形式判定を、子タスクごとにやり直さない。
    assert answer_number not in normalized


@pytest.mark.parametrize("invalid", [None, 7, object()])
def test_invalid_handler_is_rejected_at_public_entry(invalid) -> None:
    with pytest.raises(TypeError, match="handler: raw_handler must be callable"):
        handler(invalid)


@pytest.mark.parametrize("invalid", [None, 7, object()])
def test_reinstall_still_rejects_an_unvalidated_non_callable(invalid) -> None:
    # 内部の再設定へ不正な値を直接渡しても、WithBoundaries の VM 検証が
    # 拒否する(WithHandler と同じ callable の検め — #3149 で 1 命令に)。
    # 形式判定の省略で callable の検証を迂回できない。
    with pytest.raises(TypeError, match=r"WithBoundaries: boundary\[0\] handler must be callable"):
        scheduler._reinstall_boundaries(Pure(None), [("handler", invalid)])


def test_wrong_handler_arity_is_still_rejected_by_vm() -> None:
    def wrong_arity(effect: object) -> Pure[object]:
        return Pure(effect)

    # callable でも dispatcher の引数 (effect, k) を受け取れなければ、
    # 元の入口と引き継ぎ先のどちらでも VM 実行時の TypeError を保つ。
    programs: tuple[object, ...] = (
        handler(wrong_arity)(read_number()),
        scheduler._reinstall_boundaries(read_number(), [("handler", wrong_arity)]),
    )
    for program in programs:
        with pytest.raises(TypeError, match="positional argument"):
            run(program)
