from __future__ import annotations

from typing import Any, TypeVar

from doeff.cesk_v3.level1_cesk.state import CESKState, Done, Failed, ProgramControl

T = TypeVar("T")


def run(program: Any) -> Any:
    from doeff.cesk_v3.level2_algebraic_effects.step import level2_step

    state = CESKState(
        C=ProgramControl(program),
        E={},
        S={},
        K=[],
    )

    while True:
        result = level2_step(state)

        if isinstance(result, Done):
            return result.value
        if isinstance(result, Failed):
            raise result.error

        state = result
