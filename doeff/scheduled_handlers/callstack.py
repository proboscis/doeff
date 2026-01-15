"""Call stack effect handlers.

Direct ScheduledEffectHandler implementations for ProgramCallFrameEffect
and ProgramCallStackEffect.

These effects allow programs to introspect their call stack during execution.
The call stack is tracked in the store under the '__call_stack__' key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.runtime import HandlerResult, Resume

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store


def handle_program_call_frame(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    """Handle ProgramCallFrameEffect by returning the call frame at the given depth.
    
    The call stack is stored in store['__call_stack__'] as a tuple of CallFrame objects.
    depth=0 returns the innermost frame, depth=1 returns the parent frame, etc.
    
    Raises IndexError if depth exceeds the available call stack.
    """
    call_stack = store.get("__call_stack__", ())
    depth = effect.depth
    
    if not call_stack:
        raise IndexError(f"Call stack is empty, cannot get frame at depth {depth}")
    
    if depth >= len(call_stack):
        raise IndexError(
            f"Call stack depth {depth} exceeds available stack size {len(call_stack)}"
        )
    
    # Stack is stored with innermost frame last, so we index from the end
    frame = call_stack[-(depth + 1)]
    return Resume(frame, store)


def handle_program_call_stack(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    """Handle ProgramCallStackEffect by returning the entire call stack.
    
    Returns a tuple of CallFrame objects, with the innermost frame last.
    """
    call_stack = store.get("__call_stack__", ())
    return Resume(call_stack, store)


__all__ = [
    "handle_program_call_frame",
    "handle_program_call_stack",
]
