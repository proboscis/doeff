"""Do not repeat the form check of a reused handler on every Spawn (#1384)."""

from collections.abc import Callable, Generator

import pytest
from doeff_core_effects import _scheduler as scheduler
from doeff_vm import K

from doeff import EffectBase, Pass, Pure, Resume, Spawn, Wait, do, handler, run
from doeff.program import ProgramHandler


class ReadNumber(EffectBase[int]):
    pass


@handler
def answer_number(effect: object, k: K) -> Resume | Pass:
    if isinstance(effect, ReadNumber):
        return (yield Resume(k, 7))
    return (yield Pass(effect, k))


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
    # The value GetBoundaries returns is a dispatcher the VM has already accepted. Do not
    # redo the form check of the user-facing installer / dispatcher for each child task.
    assert answer_number not in normalized


@pytest.mark.parametrize("invalid", [None, 7, object()])
def test_invalid_handler_is_rejected_at_public_entry(invalid) -> None:
    with pytest.raises(TypeError, match="handler: raw_handler must be callable"):
        handler(invalid)


@pytest.mark.parametrize("invalid", [None, 7, object()])
def test_reinstall_still_rejects_an_unvalidated_non_callable(invalid) -> None:
    # Even if an invalid value is passed directly to the internal reinstall, the VM check of WithBoundaries
    # rejects it (the same callable check as WithHandler — folded into one instruction in #3149).
    # Skipping the form check does not bypass the callable validation.
    with pytest.raises(TypeError, match=r"WithBoundaries: boundary\[0\] handler must be callable"):
        scheduler._reinstall_boundaries(Pure(None), [("handler", invalid)])


def test_wrong_handler_arity_is_rejected_at_entry_and_by_vm_on_reinstall() -> None:
    @do
    def wrong_arity(effect: object) -> Pure[object]:
        return Pure(effect)

    # Even for an @do function, if it cannot accept the dispatcher arguments (effect, k), the original entry
    # rejects it by name at build time. The handoff target skips the form check but
    # keeps the VM runtime TypeError — skipping does not bypass the argument count error.
    with pytest.raises(TypeError, match=r"without the handler marker: .*wrong_arity"):
        handler(wrong_arity)
    reinstalled = scheduler._reinstall_boundaries(read_number(), [("handler", wrong_arity)])
    with pytest.raises(TypeError, match="positional argument"):
        run(reinstalled)
