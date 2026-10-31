"""scheduled is a handler applied straight to the program, not a handler factory (ADR-DOE-API-001 R6).

The owner's policy of 2026-10-30: a handler is a factory only where it has to be.
``scheduled`` has neither settings nor per-call state, so like ``maybe`` and ``result`` (ADR-DOE-API-002 R1) it is
a marked handler value: ``scheduled(prog)``, ``scheduled(prog, implementation=...)``, and ``scheduled`` in a stack.
"""

import pytest
from doeff_core_effects import Spawn, Wait, scheduled

from doeff import do, run, with_handlers


@do
def _child(value: int):
    """A task the scheduler runs: answers ``value``."""
    return value


@do
def _doubled(value: int):
    """A program that needs the scheduler: spawns ``_child`` and doubles what it answers."""
    task = yield Spawn(_child(value))
    return 2 * (yield Wait(task))


def test_scheduled_is_a_marked_handler_value():
    """It carries the handler marker without being called, so the inventory reads it as a handler."""
    assert vars(scheduled)["_doeff_is_handler_fn"] is True


@pytest.mark.parametrize("implementation", ["python", "rust"])
def test_scheduled_is_applied_to_the_program(implementation: str):
    """``scheduled(prog)`` and ``scheduled(prog, implementation=...)`` run the program on a new scheduler."""
    assert run(scheduled(_doubled(21))) == 42
    assert run(scheduled(_doubled(21), implementation=implementation)) == 42


def test_scheduled_goes_in_a_stack_without_a_call():
    """``with_handlers([scheduled], prog)`` — the stack takes the value as it is."""
    assert run(with_handlers([scheduled], _doubled(5))) == 10


def test_scheduled_is_not_a_factory():
    """``scheduled()`` without a program is refused: there is no factory form to call."""
    with pytest.raises(TypeError, match="body_program"):
        scheduled()  # type: ignore[call-arg]  # the refused call itself
