"""再利用する handler の形式判定を Spawn のたびに繰り返さない (#1384)。"""

from collections.abc import Callable, Generator

import pytest
from doeff_core_effects import scheduler
from doeff_vm import K

from doeff import EffectBase, Pass, Resume, Spawn, Wait, do, handler, run
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
