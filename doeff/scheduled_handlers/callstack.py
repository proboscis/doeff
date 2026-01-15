from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.runtime import HandlerResult, Resume

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store


def handle_call_frame(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    call_stack = store.get("__call_stack__", ())
    depth = effect.depth
    if depth >= len(call_stack):
        raise IndexError(
            f"Call stack depth {depth} exceeds available stack size {len(call_stack)}"
        )
    frame = call_stack[-(depth + 1)]
    return Resume(frame, store)


def handle_call_stack(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    call_stack = store.get("__call_stack__", ())
    return Resume(tuple(call_stack), store)


__all__ = ["handle_call_frame", "handle_call_stack"]
