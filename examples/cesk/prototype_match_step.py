"""Prototype: Python 3.10+ match-based step function.

This demonstrates how the CESK step function could be rewritten using
structural pattern matching to reduce boilerplate and improve readability.

Compare with doeff/cesk/step.py (400+ lines) vs this (~200 lines for same logic).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

# These would be actual imports in real code
if TYPE_CHECKING:
    from doeff.cesk.state import CESKState, TaskState
    from doeff.cesk.frames import Kontinuation
    from doeff.cesk.types import Store, Environment


# =============================================================================
# Helper: Generator interaction result
# =============================================================================

@dataclass
class GenYielded:
    """Generator yielded an item."""
    item: Any
    
@dataclass
class GenReturned:
    """Generator returned a value."""
    value: Any
    
@dataclass  
class GenRaised:
    """Generator raised an exception."""
    error: Exception
    pre_captured: Any = None


def send_to_generator(gen, value, *, is_throw: bool = False, pre_capture_fn=None):
    """Unified generator interaction with pre-capture support."""
    pre_captured = pre_capture_fn(gen) if pre_capture_fn else None
    try:
        if is_throw:
            item = gen.throw(value)
        else:
            item = gen.send(value)
        return GenYielded(item)
    except StopIteration as e:
        return GenReturned(e.value)
    except Exception as ex:
        return GenRaised(ex, pre_captured)


# =============================================================================
# The step function using match
# =============================================================================

def step_with_match(state: "CESKState", handlers: dict[type, Any] | None = None):
    """
    CESK step function using Python 3.10+ structural pattern matching.
    
    Pattern matching eliminates:
    - Repeated isinstance() checks
    - Manual destructuring (frame = K[0]; K_rest = K[1:])
    - Nested if/elif chains
    
    The match statement naturally expresses the CESK transition rules.
    """
    from doeff.cesk.state import Value, Error, EffectControl, ProgramControl, CESKState
    from doeff.cesk.frames import (
        ReturnFrame, LocalFrame, InterceptFrame, ListenFrame,
        GatherFrame, SafeFrame, AskLazyFrame, GraphCaptureFrame,
    )
    from doeff.cesk.result import Done, Failed, PythonAsyncSyntaxEscape
    from doeff._vendor import Ok, Err, Some, NOTHING
    
    C, E, S, K = state.C, state.E, state.S, state.K
    
    match (C, K):
        # =================================================================
        # Terminal states
        # =================================================================
        case (Value(v), []):
            return Done(v, S)
            
        case (Error(ex, captured_traceback=tb), []):
            return Failed(ex, S, captured_traceback=tb)
        
        # =================================================================
        # Effect dispatch
        # =================================================================
        case (EffectControl(effect), _):
            return _handle_effect(effect, E, S, K, handlers)
        
        # =================================================================
        # Program execution
        # =================================================================
        case (ProgramControl(program), _):
            return _step_program(program, E, S, K)
        
        # =================================================================
        # Value propagation through frames
        # =================================================================
        case (Value(v), [ReturnFrame(gen, saved_env, program_call=pc), *K_rest]):
            return _resume_generator(gen, v, saved_env, S, K_rest, pc, is_throw=False)
            
        case (Value(v), [LocalFrame(restore_env), *K_rest]):
            return CESKState(C=Value(v), E=restore_env, S=S, K=K_rest)
            
        case (Value(v), [InterceptFrame(_), *K_rest]):
            return CESKState(C=Value(v), E=E, S=S, K=K_rest)
            
        case (Value(v), [ListenFrame(log_start), *K_rest]):
            from doeff._types_internal import ListenResult
            from doeff.utils import BoundedLog
            captured = S.get("__log__", [])[log_start:]
            return CESKState(C=Value(ListenResult(v, BoundedLog(captured))), E=E, S=S, K=K_rest)
            
        case (Value(v), [GraphCaptureFrame(graph_start), *K_rest]):
            captured = S.get("__graph__", [])[graph_start:]
            return CESKState(C=Value((v, captured)), E=E, S=S, K=K_rest)
            
        case (Value(v), [GatherFrame(remaining, collected, saved_env), *K_rest]):
            new_collected = collected + [v]
            if not remaining:
                return CESKState(C=Value(new_collected), E=saved_env, S=S, K=K_rest)
            next_prog, *rest = remaining
            return CESKState(
                C=ProgramControl(next_prog),
                E=saved_env,
                S=S,
                K=[GatherFrame(rest, new_collected, saved_env)] + K_rest,
            )
            
        case (Value(v), [SafeFrame(saved_env), *K_rest]):
            return CESKState(C=Value(Ok(v)), E=saved_env, S=S, K=K_rest)
            
        case (Value(v), [AskLazyFrame(ask_key, program), *K_rest]):
            cache = S.get("__ask_lazy_cache__", {})
            new_S = {**S, "__ask_lazy_cache__": {**cache, ask_key: (program, v)}}
            return CESKState(C=Value(v), E=E, S=new_S, K=K_rest)
        
        # =================================================================
        # Error propagation through frames
        # =================================================================
        case (Error(ex, captured_traceback=tb), [ReturnFrame(gen, saved_env, program_call=pc), *K_rest]):
            return _resume_generator(gen, ex, saved_env, S, K_rest, pc, is_throw=True, original_tb=tb)
            
        case (Error(ex, captured_traceback=tb), [LocalFrame(restore_env), *K_rest]):
            return CESKState(C=Error(ex, captured_traceback=tb), E=restore_env, S=S, K=K_rest)
            
        case (Error(ex, captured_traceback=tb), [InterceptFrame(_), *K_rest]):
            return CESKState(C=Error(ex, captured_traceback=tb), E=E, S=S, K=K_rest)
            
        case (Error(ex, captured_traceback=tb), [ListenFrame(_), *K_rest]):
            return CESKState(C=Error(ex, captured_traceback=tb), E=E, S=S, K=K_rest)
            
        case (Error(ex, captured_traceback=tb), [GatherFrame(_, _, saved_env), *K_rest]):
            return CESKState(C=Error(ex, captured_traceback=tb), E=saved_env, S=S, K=K_rest)
            
        case (Error(ex, captured_traceback=tb), [GraphCaptureFrame(_), *K_rest]):
            return CESKState(C=Error(ex, captured_traceback=tb), E=E, S=S, K=K_rest)
            
        case (Error(ex, captured_traceback=tb), [SafeFrame(saved_env), *K_rest]):
            captured_maybe = Some(tb) if tb else NOTHING
            return CESKState(C=Value(Err(ex, captured_traceback=captured_maybe)), E=saved_env, S=S, K=K_rest)
            
        case (Error(ex, captured_traceback=tb), [AskLazyFrame(ask_key, _), *K_rest]):
            cache = S.get("__ask_lazy_cache__", {})
            new_cache = {k: v for k, v in cache.items() if k != ask_key}
            new_S = {**S, "__ask_lazy_cache__": new_cache}
            return CESKState(C=Error(ex, captured_traceback=tb), E=E, S=new_S, K=K_rest)
        
        # =================================================================
        # Catch-all for unhandled states
        # =================================================================
        case _:
            from doeff.cesk.errors import InterpreterInvariantError
            head_desc = type(K[0]).__name__ if K else "empty"
            raise InterpreterInvariantError(f"Unhandled state: C={type(C).__name__}, K head={head_desc}")


def _handle_effect(effect, E, S, K, handlers):
    """Handle effect dispatch with intercept support."""
    from doeff.cesk.classification import has_intercept_frame, is_effectful, is_pure_effect
    from doeff.cesk.helpers import apply_intercept_chain
    from doeff.cesk.result import PythonAsyncSyntaxEscape
    from doeff.cesk.state import CESKState, Value, Error, ProgramControl
    from doeff.cesk.errors import UnhandledEffectError
    from doeff.program import ProgramBase
    from doeff.types import EffectBase
    
    # Apply intercept chain if present
    if has_intercept_frame(K):
        try:
            effect = apply_intercept_chain(K, effect)
        except Exception as ex:
            from doeff.cesk_traceback import capture_traceback_safe
            return CESKState(C=Error(ex, captured_traceback=capture_traceback_safe(K, ex)), E=E, S=S, K=K)
        
        # Intercept might return a Program instead of Effect
        if isinstance(effect, ProgramBase):
            return CESKState(C=ProgramControl(effect), E=E, S=S, K=K)
    
    # Check if we have a handler
    has_handler = (
        (handlers is not None and type(effect) in handlers)
        or is_pure_effect(effect)
        or is_effectful(effect)
    )
    
    if has_handler:
        return PythonAsyncSyntaxEscape(
            effect=effect,
            resume=lambda v, new_store: CESKState(C=Value(v), E=E, S=new_store, K=K),
            resume_error=lambda ex: CESKState(C=Error(ex), E=E, S=S, K=K),
        )
    
    # No handler found
    from doeff.cesk_traceback import capture_traceback_safe
    ex = UnhandledEffectError(f"No handler for {type(effect).__name__}")
    return CESKState(C=Error(ex, captured_traceback=capture_traceback_safe(K, ex)), E=E, S=S, K=K)


def _step_program(program, E, S, K):
    """Step into a program, starting its generator."""
    from doeff.cesk.state import CESKState, Value, Error, EffectControl, ProgramControl
    from doeff.cesk.frames import ReturnFrame
    from doeff.cesk.helpers import to_generator
    from doeff.cesk.errors import InterpreterInvariantError
    from doeff.program import KleisliProgramCall, ProgramBase
    from doeff.types import EffectBase
    
    try:
        gen = to_generator(program)
        program_call = program if isinstance(program, KleisliProgramCall) else None
        item = next(gen)
        
        match item:
            case _ if isinstance(item, EffectBase):
                control = EffectControl(item)
            case _ if isinstance(item, ProgramBase):
                control = ProgramControl(item)
            case _:
                return CESKState(
                    C=Error(InterpreterInvariantError(f"Yielded unexpected type: {type(item).__name__}")),
                    E=E, S=S, K=K,
                )
        
        return CESKState(C=control, E=E, S=S, K=[ReturnFrame(gen, E, program_call=program_call)] + K)
        
    except StopIteration as e:
        return CESKState(C=Value(e.value), E=E, S=S, K=K)
    except Exception as ex:
        from doeff.cesk_traceback import capture_traceback_safe
        return CESKState(C=Error(ex, captured_traceback=capture_traceback_safe(K, ex)), E=E, S=S, K=K)


def _resume_generator(gen, value, saved_env, S, K_rest, program_call, *, is_throw: bool, original_tb=None):
    """Resume a generator with a value or exception."""
    from doeff.cesk.state import CESKState, Value, Error, EffectControl, ProgramControl
    from doeff.cesk.frames import ReturnFrame
    from doeff.cesk.errors import InterpreterInvariantError
    from doeff.program import ProgramBase
    from doeff.types import EffectBase
    
    try:
        if is_throw:
            item = gen.throw(value)
        else:
            item = gen.send(value)
        
        match item:
            case _ if isinstance(item, EffectBase):
                control = EffectControl(item)
            case _ if isinstance(item, ProgramBase):
                control = ProgramControl(item)
            case _:
                return CESKState(
                    C=Error(InterpreterInvariantError(f"Yielded unexpected type: {type(item).__name__}")),
                    E=saved_env, S=S, K=K_rest,
                )
        
        return CESKState(
            C=control, E=saved_env, S=S,
            K=[ReturnFrame(gen, saved_env, program_call=program_call)] + K_rest,
        )
        
    except StopIteration as e:
        return CESKState(C=Value(e.value), E=saved_env, S=S, K=K_rest)
    except Exception as ex:
        from doeff.cesk_traceback import capture_traceback_safe
        # If re-throwing same exception, preserve original traceback
        if is_throw and ex is value:
            return CESKState(C=Error(ex, captured_traceback=original_tb), E=saved_env, S=S, K=K_rest)
        return CESKState(C=Error(ex, captured_traceback=capture_traceback_safe(K_rest, ex)), E=saved_env, S=S, K=K_rest)


# =============================================================================
# Comparison notes
# =============================================================================
"""
BENEFITS OF MATCH-BASED APPROACH:

1. **Structural clarity**: Each case is a declarative transition rule
   - Current: isinstance(C, Value) and K and isinstance(K[0], LocalFrame)
   - Match:   case (Value(v), [LocalFrame(restore_env), *K_rest])

2. **Automatic destructuring**: No manual K[0], K[1:] slicing
   - Current: frame = K[0]; K_rest = K[1:]
   - Match:   [frame, *K_rest] in pattern

3. **Exhaustiveness hints**: match warns about unhandled cases in some IDEs

4. **Reduced line count**: ~200 lines vs ~400 lines for same logic

5. **Easier to add frames**: Just add a case, no need to thread through if/elif

LIMITATIONS:

1. **Python 3.10+ required** (you already require 3.10+ per README)

2. **match on dataclass fields requires exact syntax**:
   - Won't work: case Value(v)  # if Value is frozen dataclass
   - Must use:   case Value(v=v) or define __match_args__

3. **Some patterns are verbose**:
   - isinstance guard still needed for EffectBase/ProgramBase discrimination
   - case _ if isinstance(item, EffectBase) is uglier than isinstance check

4. **Debugging**: Stack traces show match block, not specific case

VERDICT: Match approach is cleaner for frame dispatch but requires __match_args__
on all your dataclasses (which you may already have via @dataclass).
"""
