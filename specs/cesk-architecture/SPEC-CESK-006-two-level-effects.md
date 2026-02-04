# SPEC-CESK-006: Layered Interpreter Architecture for Algebraic Effects

## Status: Draft (v2 - Unified K Architecture)

## Summary

This spec defines the **layered interpreter architecture** for doeff's algebraic effects system. The key insight is that **all control state lives in K** - handlers, dispatch progress, and continuations are unified in a single continuation stack.

```
0. Python (CPython)
   └── 1. Pure CESK Machine (cesk_step)
         └── 2. Algebraic Effects Machine (level2_step)
```

**Key design principle**: Handler dispatch is a VM operation, not external logic. The `DispatchingFrame` makes dispatch state capturable and restorable.

## Background: Koka and OCaml5

### OCaml5 Architecture

OCaml5 uses **fiber-based stack switching** with bytecode primitives:

| Primitive | Type | Purpose |
|-----------|------|---------|
| `PERFORM` | Bytecode instruction | Capture stack, switch to parent, invoke handler |
| `RESUME` | Bytecode instruction | Switch to captured stack, continue execution |
| `RESUMETERM` | Bytecode instruction | Tail-call optimized resume |
| `REPERFORMTERM` | Bytecode instruction | Re-raise effect to next handler |

### Koka Architecture

Koka uses **evidence-passing + yielding flag** (no stack switching):

| Primitive | Type | Purpose |
|-----------|------|---------|
| `kk_yield_to` | C extern | Set yield flag, store clause |
| `kk_yield_prompt` | C extern | Check yield at handler boundary |
| `kk_yield_extend` | C extern | Compose continuation |
| `kk_evv_*` | C extern | Evidence vector manipulation |

### Key Insight

Both languages have **layered interpretation** where dispatch state is part of the machine, not external logic.

---

## Architectural Decisions

### ADR-1: Unified K Architecture (No Separate H Stack)

**Decision**: All handler and dispatch state lives in K. No separate handler stack in Store.

**Rationale**:
- Handlers are tracked via `WithHandlerFrame` in K
- Dispatch state is tracked via `DispatchingFrame` in K
- Capturing K naturally captures handler context
- No synchronization needed between K and H

**Previous approach (rejected)**:
- Separate `handler_stack` in `AlgebraicEffectsState` in Store
- Required manual synchronization with WHFs in K
- K capture didn't naturally include handler context

### ADR-2: Three Frame Types

**Decision**: K contains three frame types:
- `ReturnFrame`: Holds suspended generator (Level 1 processes)
- `WithHandlerFrame`: Marks handler scope + holds handler function (Level 2 processes)
- `DispatchingFrame`: Tracks dispatch progress (Level 2 processes)

**Rationale**:
- `ReturnFrame` = "where does this value go?"
- `WithHandlerFrame` = "what handler is installed here?"
- `DispatchingFrame` = "what dispatch is in progress?"

### ADR-3: DispatchingFrame for Dispatch State

**Decision**: Effect dispatch is a VM operation with its own frame type.

**Rationale**:
- Dispatch logic was previously in `translate_user_effect()` - Python code outside the VM
- When handler forwarded, we couldn't "return to" dispatch logic
- With `DispatchingFrame`, dispatch state is in K, capturable and restorable
- Forwarding naturally works: push new `DispatchingFrame`, old handler frame preserved

**Problem solved**:
```python
# Old approach: dispatch logic outside VM
def translate_user_effect(effect, state):
    handler = find_handler(state)  # Python code, not in K!
    return invoke_handler(handler, effect, state)
# If handler forwards, we can't "continue" this function

# New approach: dispatch logic inside VM
# Push DispatchingFrame, VM handles dispatch step by step
# Forwarding = push new DispatchingFrame, state preserved in K
```

### ADR-4: Handler Snapshot in DispatchingFrame

**Decision**: `DispatchingFrame` holds a snapshot of available handlers at dispatch time.

**Rationale**:
- If handler installs nested handler (WithHandler), live handlers change
- But current dispatch should NOT see newly installed handlers
- Snapshot preserves "what handlers were available when this dispatch started"
- Matches Koka's evidence vector model

### ADR-5: Busy Boundary for Nested Dispatch

**Decision**: When collecting available handlers, a DispatchingFrame creates a "busy boundary" that excludes handlers at or after its `handler_idx`.

**Rationale**:
- Handler H at `handler_idx` is "busy" handling an effect
- Handlers in the "busy section" (idx and beyond) cannot handle nested effects
- Only handlers BEFORE the busy boundary (`handlers[:idx]`) are available
- Plus any WHFs newly installed ABOVE the parent DF in K

**Algorithm**:
```
collect_available_handlers(K):
    handlers = []
    for frame in K:
        if WHF: handlers.append(frame.handler)
        if DispatchingFrame:
            # Found busy boundary
            parent_available = frame.handlers[:frame.handler_idx]
            return parent_available + handlers  # parent's available + newly installed
    return handlers  # No parent DF, all collected WHFs are available
```

**Visual**:
```
K = [inner_gen, DF(handlers=[A,B,C], idx=2), user_gen, WHF(C), WHF(B), WHF(A)]
                    ↑ busy boundary at idx=2
                    
Available for nested dispatch:
- handlers[:2] = [A, B]  (before busy boundary)
- C is busy, not available
- Any WHF above DF (newly installed) would also be available
```

**Key insight**: This is about "busy boundary", not "outer vs inner". The boundary is determined by where dispatch is currently happening, not by nesting depth.

### ADR-6: Resume-Only Handler API (No Abort)

**Decision**: Handlers yield `Resume(value)` to continue user, or `Forward(effect)` to delegate. No explicit `Abort`.

**Rationale**:
- Simpler API: Resume or Forward, that's it
- Implicit abandonment if handler returns without Resume
- Forgetting Resume is usually a bug; well-defined behavior (abandonment) makes debugging easier
- Abort semantics are rare in practice; can be added later if needed

**Handler returning without Resume**:
- User continuation is dropped (implicit abandonment)
- Handler's return value becomes the WithHandler scope's result
- Generators in abandoned continuation are closed

### ADR-7: One-Shot Continuations

**Decision**: Each continuation can only be resumed ONCE.

**Rationale**:
- Python generators are inherently one-shot
- Matches Koka/OCaml5 default behavior
- Can add safeguards later if needed

### ADR-8: K Never Cleared (VM Completeness)

**Decision**: K should NEVER be set to `[]` except naturally reaching empty K at completion.

**Rationale**:
- Clearing K is a code smell indicating logic that should be in the VM but isn't
- If we can't find an expected frame (WHF, DF), that's a VM invariant violation → raise error
- All control flow must be expressible through K manipulation
- This ensures the VM is "complete" - all state is capturable and restorable

**Invariant violations**:
- Can't find handler's WHF during Resume → bug in K arrangement
- Can't find DF during Forward → called outside dispatch context
- Missing frames → VM logic error, not "clear and continue"

### ADR-9: Forward vs Re-yield

**Decision**: Both `Forward(effect)` and `yield effect` work for forwarding. Forward is preferred.

**Rationale**:
- `Forward(effect)`: Explicit intent, could be optimized (fewer frames)
- `yield effect`: Works via `collect_available_handlers()` seeing parent DF, creates nested DFs
- Semantically equivalent, Forward is clearer

**Current implementation**: Forward creates new DF with outer handlers. Re-yield also creates new DF via normal dispatch. Both correct, Forward is self-documenting.

**Decision**: Each continuation can only be resumed ONCE.

**Rationale**:
- Python generators are inherently one-shot
- Matches Koka/OCaml5 default behavior
- Can add safeguards later if needed

---

## doeff Layered Architecture

### Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│ Level 0: Python (CPython)                                           │
│   - Executes Python bytecode                                        │
│   - Manages generator objects                                       │
│   - Provides next()/send()/throw() protocol                         │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Level 2: Algebraic Effects Machine (level2_step) - WRAPS Level 1    │
│                                                                     │
│   Handles:                                                          │
│     - WithHandlerFrame (C=Value, K[0]=WHF → scope ends)             │
│     - DispatchingFrame (C=Value, K[0]=DF → try handler)             │
│     - Resume/Abort (control primitives)                             │
│     - EffectYield (push DispatchingFrame, start dispatch)           │
│                                                                     │
│   ┌───────────────────────────────────────────────────────────────┐ │
│   │ Level 1: Pure CESK Machine (cesk_step)                        │ │
│   │                                                               │ │
│   │   Only handles ReturnFrame. No effect knowledge.              │ │
│   │   State: C, E, S, K                                           │ │
│   │   Produces: Value, EffectYield, Error, Done, Failed           │ │
│   └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
│   INVARIANT: Level 1 only sees ReturnFrame at K[0]                  │
└─────────────────────────────────────────────────────────────────────┘
```

**Key insight**: Level 2 WRAPS Level 1. It intercepts WHF and DispatchingFrame before Level 1 sees them. Level 1 only ever processes ReturnFrame.

**No Level 3**: User effect dispatch is handled by Level 2 via DispatchingFrame. No separate translation layer needed.

### CESK State

```python
@dataclass(frozen=True)
class CESKState:
    """CESK machine state."""
    C: Control              # Current control (Value, ProgramControl, Error, EffectYield)
    E: Environment          # Current environment (immutable)
    S: Store                # Store (user state only, no internal handler state)
    K: Kontinuation         # Continuation stack (all frame types)
```

### Frame Types

```python
@dataclass(frozen=True)
class ReturnFrame:
    """Holds a suspended generator waiting for a value.
    
    Handled by Level 1 (pure CESK).
    """
    generator: Generator


@dataclass(frozen=True)
class WithHandlerFrame:
    """Marks handler scope boundary AND holds the handler.
    
    Handled by Level 2.
    When a Value reaches this frame, the handler scope ends.
    """
    handler: Handler  # The handler function for this scope


@dataclass(frozen=True)
class DispatchingFrame:
    """Tracks effect dispatch progress.
    
    Handled by Level 2.
    Holds snapshot of available handlers at dispatch start.
    """
    effect: EffectBase              # The effect being dispatched
    handler_idx: int                # Current handler index being tried
    handlers: tuple[Handler, ...]   # Snapshot of available handlers
    handler_started: bool = False   # Whether handler has been invoked


# Type alias
Handler = Callable[[EffectBase], Program[Any]]

# K can contain all three frame types
Frame = ReturnFrame | WithHandlerFrame | DispatchingFrame
Kontinuation = list[Frame]
```

### Frame Ownership

| Frame Type | Owned By | When Processed |
|------------|----------|----------------|
| `ReturnFrame` | Level 1 | Value → send to generator |
| `WithHandlerFrame` | Level 2 | Value → scope ends, pop WHF |
| `DispatchingFrame` | Level 2 | Value → start/continue handler dispatch |

### Unified K Architecture

All state is in K. No separate handler stack.

```
K = [handler_gen, DispatchingFrame(e, 1, [h0,h1]), user_gen, WHF(h1), WHF(h0)]
     ↑            ↑                                ↑         ↑        ↑
     │            │                                │         │        └─ h0 installed (outermost)
     │            │                                │         └─ h1 installed (innermost)
     │            │                                └─ user code suspended
     │            └─ dispatch in progress: trying h1 (idx=1)
     └─ h1's handler generator running
```

**To find available handlers**: Walk K, collect handlers from `WithHandlerFrame`s.

**To find dispatch context**: Look for `DispatchingFrame` in K.

**Handler scope ends**: When Value reaches WHF, pop it.

**Dispatch completes**: When Resume processed, pop DispatchingFrame, arrange K.

---

## Control Types

```python
@dataclass(frozen=True)
class ProgramControl:
    """Control: a program to execute."""
    program: Program


@dataclass(frozen=True)
class Value:
    """Control: a computed value."""
    value: Any


@dataclass(frozen=True)
class Error:
    """Control: an exception was raised."""
    error: BaseException


@dataclass(frozen=True)
class EffectYield:
    """Control: generator yielded something.
    
    The yielded value can be:
    - ControlPrimitive (WithHandler, Resume, Forward, GetContinuation, ResumeContinuation)
    - ProgramBase (nested program to execute - monadic bind)
    - EffectBase (user effect to dispatch)
    """
    yielded: Any


@dataclass(frozen=True)
class Done:
    """Terminal: computation completed successfully."""
    value: Any


@dataclass(frozen=True)
class Failed:
    """Terminal: computation failed with exception."""
    error: BaseException
```

---

## Control Primitives

Control primitives are yielded by handlers to control execution flow.

```python
class ControlPrimitive:
    """Base class for Level 2 control primitives.
    
    These are NOT effects. They are instructions to Level 2.
    They NEVER go through handler dispatch.
    """
    pass


@dataclass(frozen=True)
class WithHandler(ControlPrimitive, Generic[T]):
    """Install a handler for a scoped computation."""
    handler: Handler
    program: Program[T]


@dataclass(frozen=True)
class Resume(ControlPrimitive):
    """Resume the captured continuation with a value.
    
    Pops the DispatchingFrame and arranges K so:
    - Value goes to user continuation
    - Handler receives user's return value
    """
    value: Any


@dataclass(frozen=True)
class Forward(ControlPrimitive):
    """Forward an effect to outer handlers.
    
    Explicit forwarding primitive. Creates a new DispatchingFrame
    with only outer handlers (handlers[:current_idx]).
    
    When outer handler resumes, the value is automatically passed
    back to this handler, which can then Resume with it.
    
    Semantically equivalent to:
        result = yield effect  # re-yield (also works, more frames)
        return (yield Resume(result))
    
    But Forward makes the intent explicit and could be optimized.
    """
    effect: EffectBase


# --- Scheduling Primitives (Continuation Capture) ---

@dataclass(frozen=True)
class Continuation:
    """A captured continuation that can be resumed later.
    
    Continuations are first-class values that represent "the rest of the computation."
    They can be stored, passed around, and resumed with ResumeContinuation.
    
    Attributes:
        cont_id: Unique identifier for one-shot tracking
        frames: The captured continuation frames
    
    One-shot invariant: Each continuation can only be resumed once.
    Attempting to resume a continuation twice raises RuntimeError.
    """
    cont_id: int
    frames: tuple[Frame, ...]


@dataclass(frozen=True)
class GetContinuation(ControlPrimitive):
    """Capture the current continuation as a first-class value.
    
    Returns a Continuation object to the handler. The DispatchingFrame
    is NOT consumed - handler can still Resume/Forward after capturing.
    
    This enables scheduler patterns where:
    1. Handler captures continuation (GetContinuation)
    2. Handler stores continuation for later (e.g., in a queue)
    3. Handler can Resume current computation OR switch to another
    
    Unlike Resume, GetContinuation does NOT immediately resume anything.
    It just returns the capability to resume later.
    
    Example:
        @do
        def scheduler_handler(effect):
            if isinstance(effect, Yield):
                k = yield GetContinuation()  # Capture current task
                queue.append(k)               # Store for later
                next_k = queue.pop(0)         # Get next task
                return (yield ResumeContinuation(next_k, None))
            return (yield Forward(effect))
    """
    pass


@dataclass(frozen=True)
class ResumeContinuation(ControlPrimitive):
    """Resume a previously captured continuation with a value.
    
    Unlike Resume (which resumes the current dispatch's continuation),
    ResumeContinuation can resume ANY captured continuation.
    
    This is the key primitive for cooperative scheduling:
    - Resume(v) = resume current DF's continuation immediately
    - ResumeContinuation(k, v) = resume ANY continuation k
    
    When ResumeContinuation is executed:
    1. The current computation is abandoned (current K is dropped)
    2. The captured continuation k becomes the new K
    3. Execution continues with value v
    
    One-shot invariant: The continuation must not have been resumed before.
    
    Attributes:
        continuation: The captured Continuation to resume
        value: The value to send to the resumed continuation
    """
    continuation: Continuation
    value: Any
```

### Yield Classification Summary

| Yield Type | Classification | Level 2 Action |
|------------|----------------|----------------|
| `WithHandler(h, p)` | Control primitive | Install handler, start scoped program |
| `Resume(value)` | Control primitive | Resume user continuation with value |
| `Forward(effect)` | Control primitive | Forward to outer handlers |
| `GetContinuation()` | Control primitive | Capture continuation as first-class value |
| `ResumeContinuation(k, v)` | Control primitive | Resume captured continuation k with value v |
| `Program` / `KleisliProgramCall` | Monadic bind | Execute nested program, send result back |
| `EffectBase` subclass | User effect | Start dispatch via DispatchingFrame |
| `return value` | Handler completion | **Implicit abandonment** - user continuation dropped |

### Implicit Abandonment (No Abort)

There is no explicit `Abort` primitive. If a handler returns without yielding `Resume`, the user continuation is implicitly abandoned:

1. Handler generator completes (StopIteration)
2. `DispatchingFrame` detects `handler_started=True` + handler returned
3. User continuation (between DF and WHF) is dropped
4. Handler's return value becomes the result of the WithHandler scope

This is intentional: forgetting to Resume is usually a bug, but the behavior is well-defined.

### Error Handling

Errors raised in handler code propagate normally through K:
- Error → throw into ReturnFrame generators
- Error reaches WHF → scope ends with error, propagates outward
- Error with empty K → Failed(error)

No special error handling in dispatch logic. The VM's normal error propagation applies.

---

## Level 1: Pure CESK Machine

**Module**: `doeff.cesk_v3.level1_cesk`

Level 1 is the pure CESK machine. It knows nothing about effects or handlers - it only steps generators and manages ReturnFrame.

```python
def cesk_step(state: CESKState) -> CESKState | Done | Failed:
    """Pure CESK stepper. Only handles ReturnFrame.
    
    Level 2 intercepts WHF and DispatchingFrame before this is called.
    """
    C, E, S, K = state.C, state.E, state.S, state.K
    
    # Program needs to be started
    if isinstance(C, ProgramControl):
        gen = to_generator(C.program)
        try:
            yielded = next(gen)
            return CESKState(C=EffectYield(yielded), E=E, S=S, K=[ReturnFrame(gen)] + K)
        except StopIteration as e:
            return CESKState(C=Value(e.value), E=E, S=S, K=K)
        except Exception as e:
            return CESKState(C=Error(e), E=E, S=S, K=K)
    
    # Value: send to continuation (must be ReturnFrame)
    if isinstance(C, Value) and K:
        frame = K[0]
        assert isinstance(frame, ReturnFrame), \
            f"Level 1 only handles ReturnFrame, got {type(frame).__name__}"
        try:
            yielded = frame.generator.send(C.value)
            return CESKState(C=EffectYield(yielded), E=E, S=S, K=K)
        except StopIteration as e:
            return CESKState(C=Value(e.value), E=E, S=S, K=K[1:])
        except Exception as e:
            return CESKState(C=Error(e), E=E, S=S, K=K[1:])
    
    # Error: throw into continuation (must be ReturnFrame)
    if isinstance(C, Error) and K:
        frame = K[0]
        assert isinstance(frame, ReturnFrame), \
            f"Level 1 only handles ReturnFrame, got {type(frame).__name__}"
        try:
            yielded = frame.generator.throw(type(C.error), C.error)
            return CESKState(C=EffectYield(yielded), E=E, S=S, K=K)
        except StopIteration as e:
            return CESKState(C=Value(e.value), E=E, S=S, K=K[1:])
        except Exception as e:
            return CESKState(C=Error(e), E=E, S=S, K=K[1:])
    
    # Terminal: value with empty K
    if isinstance(C, Value) and not K:
        return Done(C.value)
    
    # Terminal: error with empty K
    if isinstance(C, Error) and not K:
        return Failed(C.error)
    
    # EffectYield: return for Level 2 to handle
    return state
```

---

## Level 2: Algebraic Effects Machine

**Module**: `doeff.cesk_v3.level2_algebraic_effects`

Level 2 wraps Level 1, handling WithHandlerFrame, DispatchingFrame, and control primitives.

### Main Step Function

```python
def level2_step(state: CESKState) -> CESKState | Done | Failed:
    """Level 2 step: wraps Level 1, handles effect machinery.
    
    Processing order:
    1. Check for WithHandlerFrame at K[0] (scope end)
    2. Check for DispatchingFrame at K[0] (dispatch logic)
    3. Check for EffectYield:
       - ControlPrimitive → execute primitive
       - ProgramBase → monadic bind (execute nested program)
       - EffectBase → start dispatch
    4. Delegate to Level 1 for ReturnFrame
    
    INVARIANTS:
    - K is never cleared. If we can't find expected frames, raise error.
    - EffectYield MUST be consumed and converted to another Control type.
      Level 2 must NEVER return a state with C=EffectYield.
      (Prevents infinite loops where EffectYield bounces between levels)
    """
    C, E, S, K = state.C, state.E, state.S, state.K
    
    # === WithHandlerFrame: scope ends ===
    if isinstance(C, Value) and K and isinstance(K[0], WithHandlerFrame):
        # Handler scope completed - pop WHF, continue with value
        return CESKState(C=C, E=E, S=S, K=K[1:])
    
    # === DispatchingFrame: dispatch logic ===
    if isinstance(C, Value) and K and isinstance(K[0], DispatchingFrame):
        df = K[0]
        
        if not df.handler_started:
            # --- First time: start the handler ---
            if df.handler_idx < 0:
                raise UnhandledEffectError(f"No handler for {type(df.effect).__name__}")
            
            handler = df.handlers[df.handler_idx]
            handler_program = handler(df.effect)
            
            # Update DF to mark handler as started
            new_df = replace(df, handler_started=True)
            
            return CESKState(
                C=ProgramControl(handler_program),
                E=E,
                S=S,
                K=[new_df] + K[1:],  # Replace DF with updated version
            )
        else:
            # --- Handler returned without Resume: implicit abandonment ---
            # Handler completed but didn't yield Resume.
            # User continuation is abandoned. Value flows past WHF.
            return handle_implicit_abandonment(C.value, state)
    
    # === EffectYield: check what was yielded ===
    if isinstance(C, EffectYield):
        yielded = C.yielded
        
        # --- Control Primitives ---
        if isinstance(yielded, WithHandler):
            return handle_with_handler(yielded, state)
        
        if isinstance(yielded, Resume):
            return handle_resume(yielded, state)
        
        if isinstance(yielded, Forward):
            return handle_forward(yielded, state)
        
        if isinstance(yielded, GetContinuation):
            return handle_get_continuation(yielded, state)
        
        if isinstance(yielded, ResumeContinuation):
            return handle_resume_continuation(yielded, state)
        
        # --- Program: monadic bind (ADR-12) ---
        if isinstance(yielded, ProgramBase):
            # Execute nested program; result flows back via ReturnFrame
            return CESKState(C=ProgramControl(yielded), E=E, S=S, K=K)
        
        # --- User Effect: start dispatch ---
        if isinstance(yielded, EffectBase):
            return start_dispatch(yielded, state)
        
        raise TypeError(f"Unknown yield type: {type(yielded)}")
    
    # === Delegate to Level 1 ===
    return cesk_step(state)
```

### WithHandler Translation

```python
def handle_with_handler(wh: WithHandler, state: CESKState) -> CESKState:
    """Install handler and start scoped computation."""
    C, E, S, K = state.C, state.E, state.S, state.K
    
    # Push WithHandlerFrame (holds the handler)
    # Start the scoped program
    return CESKState(
        C=ProgramControl(wh.program),
        E=E,
        S=S,
        K=[WithHandlerFrame(handler=wh.handler)] + K,
    )
```

### Start Dispatch

```python
def start_dispatch(effect: EffectBase, state: CESKState) -> CESKState:
    """Start dispatching an effect by pushing DispatchingFrame."""
    C, E, S, K = state.C, state.E, state.S, state.K
    
    # Build available handlers from K
    handlers = collect_available_handlers(K)
    
    if not handlers:
        raise UnhandledEffectError(f"No handler for {type(effect).__name__}")
    
    # Push DispatchingFrame, start from innermost handler
    df = DispatchingFrame(
        effect=effect,
        handler_idx=len(handlers) - 1,  # Innermost first
        handlers=tuple(handlers),
    )
    
    # Set C=Value to trigger DispatchingFrame processing on next step
    return CESKState(
        C=Value(None),  # Dummy value to trigger dispatch
        E=E,
        S=S,
        K=[df] + K,
    )


def collect_available_handlers(K: Kontinuation) -> list[Handler]:
    """Walk K to find available handlers, respecting busy boundaries.
    
    Returns handlers in order: [outermost, ..., innermost]
    
    BUSY BOUNDARY RULE (ADR-5):
    - If DispatchingFrame found in K, it creates a "busy boundary"
    - Handlers at or after handler_idx are "busy" (unavailable)
    - Only handlers[:handler_idx] from parent DF are available
    - Plus any WHFs installed ABOVE the parent DF (newly installed)
    
    Example:
        K = [new_whf, parent_DF(handlers=[A,B,C], idx=2), ...]
        → parent_available = [A, B]  (C is busy at idx=2)
        → newly_installed = [new_whf.handler]
        → return [A, B, new_whf.handler]
    """
    handlers = []  # Collects newly installed WHFs above parent DF
    
    for frame in K:
        if isinstance(frame, WithHandlerFrame):
            handlers.append(frame.handler)
        elif isinstance(frame, DispatchingFrame):
            # Found busy boundary
            # parent_available = handlers before the busy index
            parent_available = list(frame.handlers[:frame.handler_idx])
            # Return: parent's available + newly installed (above parent DF)
            return parent_available + handlers
    
    # No parent DF found - all collected WHFs are available
    return handlers
```

### Resume Translation

```python
def handle_resume(resume: Resume, state: CESKState) -> CESKState:
    """Resume the captured continuation with a value.
    
    Finds DispatchingFrame in K, pops it, and arranges K so:
    - Value flows to user continuation
    - Handler generator receives user's return value
    
    KEY INSIGHT: We must find the WHF that corresponds to THIS handler
    (not just the first WHF). In nested handler scenarios, inner handlers
    have WHFs before the outer handler's WHF.
    """
    C, E, S, K = state.C, state.E, state.S, state.K
    
    # K[0] should be the handler's ReturnFrame
    if len(K) < 2:
        raise RuntimeError("Resume without proper K structure")
    
    handler_frame = K[0]
    if not isinstance(handler_frame, ReturnFrame):
        raise RuntimeError(f"Expected handler ReturnFrame, got {type(handler_frame)}")
    
    # Find DispatchingFrame
    df_idx = None
    for i, frame in enumerate(K[1:], start=1):
        if isinstance(frame, DispatchingFrame):
            df_idx = i
            break
    
    if df_idx is None:
        raise RuntimeError("Resume without DispatchingFrame")
    
    df = K[df_idx]
    handler_gen = K[0]
    
    # User continuation is everything after DispatchingFrame
    user_continuation = K[df_idx + 1:]
    
    # Find the WHF that corresponds to THIS handler
    # The handler identity is preserved: DF.handlers are collected from WHFs,
    # so they are the same object references.
    target_handler = df.handlers[df.handler_idx]
    
    whf_idx = None
    for i, frame in enumerate(user_continuation):
        if isinstance(frame, WithHandlerFrame) and frame.handler is target_handler:
            whf_idx = i
            break
    
    if whf_idx is None:
        raise RuntimeError("Resume: cannot find handler's WithHandlerFrame")
    
    # Arrange new K:
    # - user frames (everything before handler's WHF, includes nested handlers/DFs)
    # - handler_gen (so handler receives final result from its scope)
    # - handler's WHF and everything after
    new_k = (
        list(user_continuation[:whf_idx]) +
        [handler_gen] +
        list(user_continuation[whf_idx:])
    )
    
    return CESKState(
        C=Value(resume.value),
        E=E,
        S=S,
        K=new_k,
    )
```

**Why identity comparison works:** When `WithHandler` is processed, we create `WithHandlerFrame(handler=wh.handler)`. Later, `collect_available_handlers()` collects these exact handler objects from WHFs. So `DF.handlers[i]` is the same object as some `WHF.handler` in K.

**Handler identity requirement:** This design requires that handler functions are stable objects. Do NOT create handlers inline with lambdas inside loops. Instead, define handlers as named functions or store them in variables before use.

```python
# GOOD: handler is a stable object
@do
def my_handler(effect): ...
result = run(WithHandler(my_handler, program))

# BAD: lambda created fresh each time, identity comparison may fail
result = run(WithHandler(lambda e: do_something(e), program))
```

### Implicit Abandonment (Handler Returns Without Resume)

```python
def handle_implicit_abandonment(handler_result: Any, state: CESKState) -> CESKState:
    """Handle case where handler returned without yielding Resume.
    
    The user continuation is abandoned. Handler's result flows past the WHF.
    
    INVARIANT: K is never cleared. If WHF not found, that's a bug.
    """
    C, E, S, K = state.C, state.E, state.S, state.K
    
    # K[0] should be the DispatchingFrame (handler_started=True)
    if not isinstance(K[0], DispatchingFrame):
        raise RuntimeError("Implicit abandonment without DispatchingFrame at K[0]")
    
    df = K[0]
    user_continuation = K[1:]  # Everything after DF
    
    # Find the WHF that corresponds to THIS handler
    target_handler = df.handlers[df.handler_idx]
    
    whf_idx = None
    for i, frame in enumerate(user_continuation):
        if isinstance(frame, WithHandlerFrame) and frame.handler is target_handler:
            whf_idx = i
            break
    
    if whf_idx is None:
        raise RuntimeError(
            "Implicit abandonment: cannot find handler's WithHandlerFrame. "
            "This is a VM invariant violation."
        )
    
    # Clean up generators in abandoned frames (user continuation up to WHF)
    for frame in user_continuation[:whf_idx]:
        if isinstance(frame, ReturnFrame):
            try:
                frame.generator.close()
            except Exception:
                pass
    
    # Continue AFTER the WHF (scope ends, WHF is consumed)
    # K = [frames after WHF...]
    new_k = list(user_continuation[whf_idx + 1:])
    
    return CESKState(
        C=Value(handler_result),
        E=E,
        S=S,
        K=new_k,
    )
```

### Forward Translation

```python
def handle_forward(forward: Forward, state: CESKState) -> CESKState:
    """Forward an effect to outer handlers.
    
    Creates a new DispatchingFrame with only outer handlers.
    When outer handler resumes, value flows back to this handler.
    
    This is semantically equivalent to re-yielding the effect,
    but makes the intent explicit.
    """
    C, E, S, K = state.C, state.E, state.S, state.K
    
    # Find current DispatchingFrame to get outer handlers
    df_idx = None
    for i, frame in enumerate(K):
        if isinstance(frame, DispatchingFrame):
            df_idx = i
            break
    
    if df_idx is None:
        raise RuntimeError("Forward without active dispatch context")
    
    current_df = K[df_idx]
    
    # Outer handlers = handlers before current index
    outer_handlers = current_df.handlers[:current_df.handler_idx]
    
    if not outer_handlers:
        raise UnhandledEffectError(
            f"Forward: no outer handler for {type(forward.effect).__name__}"
        )
    
    # Create new DispatchingFrame for forwarded effect
    new_df = DispatchingFrame(
        effect=forward.effect,
        handler_idx=len(outer_handlers) - 1,  # Start from innermost outer
        handlers=outer_handlers,
        handler_started=False,
    )
    
    # Push new DF, keep rest of K intact
    # Value(None) triggers dispatch processing
    return CESKState(
        C=Value(None),
        E=E,
        S=S,
        K=[new_df] + K,
    )
```

### Forwarding via Re-yield (Alternative)

Handlers can also forward by simply yielding the effect:

```python
@do
def my_handler(effect):
    if not can_handle(effect):
        # Re-yield forwards to outer handlers
        outer_result = yield effect
        return (yield Resume(outer_result))
    ...
```

This also works because `start_dispatch` → `collect_available_handlers()` finds the parent `DispatchingFrame` and only returns `handlers[:handler_idx]`.

**Trade-off:**
- `Forward(effect)`: Explicit, single frame, self-documenting
- `yield effect`: Implicit, creates additional DispatchingFrame, but natural Python syntax

Both are semantically correct.

---

## Main Run Loop

```python
def run(program: Program[T]) -> T:
    """Main interpreter loop."""
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
```

---

## Example Trace

```python
@do
def user():
    result = yield MyEffect()
    return result + 1

@do
def my_handler(effect):
    if isinstance(effect, MyEffect):
        user_result = yield Resume(42)
        return user_result
    # Forward unknown effects
    outer_result = yield effect
    return (yield Resume(outer_result))

# Run with handler
result = run(WithHandler(my_handler, user()))
```

### Step-by-step:

```
1. C=ProgramControl(WithHandler(...)), K=[]
   → handle_with_handler
   → K=[WHF(my_handler)], C=ProgramControl(user())

2. C=ProgramControl(user()), K=[WHF(my_handler)]
   → cesk_step starts user generator
   → K=[user_gen, WHF(my_handler)], C=EffectYield(MyEffect())

3. C=EffectYield(MyEffect()), K=[user_gen, WHF(my_handler)]
   → start_dispatch
   → handlers=[my_handler]
   → K=[DF(MyEffect, idx=0, handlers=[my_handler], started=False), user_gen, WHF(my_handler)]
   → C=Value(None)

4. C=Value(None), K=[DF(started=False), user_gen, WHF(my_handler)]
   → K[0] is DispatchingFrame with started=False
   → Start handler, update DF to started=True
   → K=[DF(started=True), user_gen, WHF(my_handler)]
   → C=ProgramControl(my_handler(MyEffect()))

5. C=ProgramControl(my_handler(...)), K=[DF(started=True), ...]
   → cesk_step starts handler generator
   → K=[handler_gen, DF(started=True), user_gen, WHF(my_handler)]
   → C=EffectYield(Resume(42))

6. C=EffectYield(Resume(42)), K=[handler_gen, DF, user_gen, WHF]
   → handle_resume
   → Find DF at idx=1, target_handler=my_handler
   → user_continuation = [user_gen, WHF(my_handler)]
   → Find WHF(my_handler) at idx=1
   → new_k = [user_gen] + [handler_gen] + [WHF(my_handler)]
   → K=[user_gen, handler_gen, WHF(my_handler)]
   → C=Value(42)

7. Value(42) → user_gen
   → user receives 42, returns 43
   → K=[handler_gen, WHF(my_handler)]
   → C=Value(43)

8. Value(43) → handler_gen
   → handler receives 43 (from user_result = yield Resume(42))
   → handler returns 43
   → K=[WHF(my_handler)]
   → C=Value(43)

9. Value(43), K=[WHF(my_handler)]
   → WHF at K[0], scope ends
   → K=[]
   → C=Value(43)

10. C=Value(43), K=[]
    → Done(43)

Result: 43
```

### Abbreviations in trace:
- `DF` = DispatchingFrame
- `WHF` = WithHandlerFrame
- `started` = handler_started flag

---

## Example Trace: Nested Forwarding

This trace demonstrates the key scenario: inner handler forwards to outer, both resume.

```python
@do
def user():
    result = yield SomeEffect()
    return result + 1

@do
def inner_handler(effect):
    # Forward to outer, then resume with outer's result
    outer_result = yield Forward(effect)
    return (yield Resume(outer_result))

@do
def outer_handler(effect):
    return (yield Resume(42))

# Run: with_handler(outer, with_handler(inner, user()))
```

### Step-by-step:

```
1. Setup: after both WithHandlers processed
   K = [WHF(inner), WHF(outer)]
   C = ProgramControl(user())

2. User starts, yields SomeEffect
   K = [user_gen, WHF(inner), WHF(outer)]
   C = EffectYield(SomeEffect())

3. start_dispatch
   handlers = [outer, inner]  (collected from WHFs)
   DF1 = DF(SomeEffect, idx=1, handlers=[outer,inner], started=False)
   K = [DF1, user_gen, WHF(inner), WHF(outer)]
   C = Value(None)

4. DF processing: start inner_handler
   DF1.started = True
   K = [DF1(started=True), user_gen, WHF(inner), WHF(outer)]
   C = ProgramControl(inner_handler(SomeEffect()))

5. inner_handler starts, yields Forward(SomeEffect)
   K = [inner_gen, DF1(started=True), user_gen, WHF(inner), WHF(outer)]
   C = EffectYield(Forward(SomeEffect()))

6. handle_forward
   Find DF1 at idx=1
   outer_handlers = DF1.handlers[:1] = [outer]
   DF2 = DF(SomeEffect, idx=0, handlers=[outer], started=False)
   K = [DF2, inner_gen, DF1, user_gen, WHF(inner), WHF(outer)]
   C = Value(None)

7. DF processing: start outer_handler
   DF2.started = True
   K = [DF2(started=True), inner_gen, DF1, user_gen, WHF(inner), WHF(outer)]
   C = ProgramControl(outer_handler(SomeEffect()))

8. outer_handler starts, yields Resume(42)
   K = [outer_gen, DF2(started=True), inner_gen, DF1, user_gen, WHF(inner), WHF(outer)]
   C = EffectYield(Resume(42))

9. handle_resume for DF2
   handler_gen = outer_gen
   df_idx = 1 (DF2)
   target_handler = DF2.handlers[0] = outer
   user_continuation = K[2:] = [inner_gen, DF1, user_gen, WHF(inner), WHF(outer)]
   Find WHF where handler is outer: WHF(outer) at idx=4
   
   new_k = [inner_gen, DF1, user_gen, WHF(inner)] + [outer_gen] + [WHF(outer)]
   K = [inner_gen, DF1, user_gen, WHF(inner), outer_gen, WHF(outer)]
   C = Value(42)

10. Value(42) → inner_gen
    inner_handler receives 42 (from outer_result = yield Forward(...))
    inner_handler yields Resume(42)
    K = [inner_gen, DF1, user_gen, WHF(inner), outer_gen, WHF(outer)]
    C = EffectYield(Resume(42))

11. handle_resume for DF1
    handler_gen = inner_gen (K[0])
    df_idx = 1 (DF1)
    target_handler = DF1.handlers[1] = inner
    user_continuation = K[2:] = [user_gen, WHF(inner), outer_gen, WHF(outer)]
    Find WHF where handler is inner: WHF(inner) at idx=1
    
    new_k = [user_gen] + [inner_gen] + [WHF(inner), outer_gen, WHF(outer)]
    K = [user_gen, inner_gen, WHF(inner), outer_gen, WHF(outer)]
    C = Value(42)

12. Value(42) → user_gen
    user receives 42, returns 43
    K = [inner_gen, WHF(inner), outer_gen, WHF(outer)]
    C = Value(43)

13. Value(43) → inner_gen
    inner_handler returns 43
    K = [WHF(inner), outer_gen, WHF(outer)]
    C = Value(43)

14. Value(43), K[0]=WHF(inner)
    Scope ends, pop WHF
    K = [outer_gen, WHF(outer)]
    C = Value(43)

15. Value(43) → outer_gen
    outer_handler returns 43
    K = [WHF(outer)]
    C = Value(43)

16. Value(43), K[0]=WHF(outer)
    Scope ends, pop WHF
    K = []
    C = Value(43)

17. Done(43)
```

**Key observations:**
1. Each Resume finds its handler's WHF by identity comparison
2. DF1 and DF2 are separate frames - nested dispatch creates nested DFs
3. When outer resumes, inner_gen + DF1 are preserved in K
4. Each handler receives its caller's result, not the original user's result
5. K is never cleared - frames flow naturally through the stack

---

## Detailed Trace: Re-yield Forwarding

This trace shows forwarding via re-yielding the effect directly (`yield effect`).

```python
@do
def user():
    result = yield SomeEffect()
    return result + 1

@do
def inner_handler(effect):
    # Re-yield the effect directly (not Forward)
    outer_result = yield effect  # <-- creates new DispatchingFrame via start_dispatch
    return (yield Resume(outer_result))

@do
def outer_handler(effect):
    return (yield Resume(42))

# Run: with_handler(outer, with_handler(inner, user()))
```

### Steps 1-6: Setup and User Effect (same for both methods)

```
Step 1-3: Setup
C = ProgramControl(user())
K = [WHF(inner), WHF(outer)]

Step 4: User yields effect
C = EffectYield(SomeEffect())
K = [user_gen, WHF(inner), WHF(outer)]

Step 5: start_dispatch
- collect_available_handlers(K) → [outer, inner]
- DF1 = DispatchingFrame(SomeEffect(), idx=1, handlers=(outer,inner), started=False)
C = Value(None)
K = [DF1(idx=1, started=False), user_gen, WHF(inner), WHF(outer)]

Step 6: Start inner_handler
C = ProgramControl(inner_handler(SomeEffect()))
K = [DF1(idx=1, started=True), user_gen, WHF(inner), WHF(outer)]
```

### Step 7-8: Inner Re-yields Effect (KEY STEP)

```
Step 7: inner_handler yields raw effect
C = EffectYield(SomeEffect())  ← Raw effect, not Forward
K = [inner_gen, DF1(idx=1, started=True), user_gen, WHF(inner), WHF(outer)]

Step 8: start_dispatch (for re-yielded effect)
- SomeEffect is EffectBase, so start_dispatch is called
- collect_available_handlers(K):
    Walk K:
    - inner_gen: ReturnFrame, skip
    - DF1: DispatchingFrame found!
      parent_available = DF1.handlers[:DF1.handler_idx]
      parent_available = (outer, inner)[:1] = (outer,)
      STOP walking, return (outer,)
  
- handlers = (outer,)  ← Only outer available!
- DF2 = DispatchingFrame(SomeEffect(), idx=0, handlers=(outer,), started=False)

C = Value(None)
K = [DF2(idx=0, started=False), inner_gen, DF1(idx=1, started=True), user_gen, WHF(inner), WHF(outer)]
     ↑ NEW DF                    ↑ waiting for outer's result
```

### Steps 9-17: Outer Handles, Both Resume (same for both methods)

```
Step 9-10: Start outer_handler, yields Resume(42)
C = EffectYield(Resume(42))
K = [outer_gen, DF2, inner_gen, DF1, user_gen, WHF(inner), WHF(outer)]

Step 11: handle_resume for DF2
- target_handler = outer
- Find WHF(outer) in user_continuation
- Insert outer_gen before WHF(outer)
C = Value(42)
K = [inner_gen, DF1, user_gen, WHF(inner), outer_gen, WHF(outer)]

Step 12: inner_gen receives 42, yields Resume(42)
C = EffectYield(Resume(42))
K = [inner_gen, DF1, user_gen, WHF(inner), outer_gen, WHF(outer)]

Step 13: handle_resume for DF1
- target_handler = inner
- Find WHF(inner) in user_continuation
- Insert inner_gen before WHF(inner)
C = Value(42)
K = [user_gen, inner_gen, WHF(inner), outer_gen, WHF(outer)]

Step 14-17: Completion
- user returns 43
- inner returns 43, WHF(inner) pops
- outer returns 43, WHF(outer) pops
- Done(43)
```

---

## Detailed Trace: Forward Primitive

This trace shows forwarding via the explicit `Forward` primitive.

```python
@do
def user():
    result = yield SomeEffect()
    return result + 1

@do
def inner_handler(effect):
    # Use Forward primitive (explicit)
    outer_result = yield Forward(effect)  # <-- Forward primitive
    return (yield Resume(outer_result))

@do
def outer_handler(effect):
    return (yield Resume(42))

# Run: with_handler(outer, with_handler(inner, user()))
```

### Steps 1-6: Setup and User Effect (identical)

```
Step 1-6: (identical to re-yield case)
C = ProgramControl(inner_handler(SomeEffect()))
K = [DF1(idx=1, started=True), user_gen, WHF(inner), WHF(outer)]
```

### Step 7-8: Inner Yields Forward (KEY DIFFERENCE)

```
Step 7: inner_handler yields Forward primitive
C = EffectYield(Forward(SomeEffect()))  ← Forward primitive, not raw effect
K = [inner_gen, DF1(idx=1, started=True), user_gen, WHF(inner), WHF(outer)]

Step 8: handle_forward (NOT start_dispatch!)
- Forward is ControlPrimitive, handled directly by Level 2
- No K-walk needed, directly access parent DF

def handle_forward(forward, state):
    # Find current DispatchingFrame in K
    df_idx = 1  # DF1 at index 1
    current_df = K[df_idx]  # DF1
    
    # Outer handlers = handlers[:current_idx]
    outer_handlers = current_df.handlers[:current_df.handler_idx]
    # (outer, inner)[:1] = (outer,)
    
    # Create new DF directly
    DF2 = DispatchingFrame(
        effect=forward.effect,
        handler_idx=0,
        handlers=(outer,),
        handler_started=False
    )
    return CESKState(C=Value(None), K=[DF2] + K, ...)

C = Value(None)
K = [DF2(idx=0, started=False), inner_gen, DF1(idx=1, started=True), user_gen, WHF(inner), WHF(outer)]
     ↑ NEW DF                    ↑ waiting for outer's result
```

### Steps 9-17: (identical to re-yield)

```
Step 9-17: (identical to re-yield case)
- outer handles, resumes with 42
- inner receives 42, resumes user with 42
- user returns 43
- Done(43)
```

---

## Comparison: Re-yield vs Forward at Step 8

| Aspect | Re-yield (`yield effect`) | Forward (`yield Forward(effect)`) |
|--------|---------------------------|-----------------------------------|
| Yielded value | `EffectYield(SomeEffect())` | `EffectYield(Forward(SomeEffect()))` |
| Detection | `isinstance(yielded, EffectBase)` | `isinstance(yielded, Forward)` |
| Handler | `start_dispatch()` | `handle_forward()` |
| Find outer handlers | `collect_available_handlers(K)` walks K | Direct: `DF.handlers[:idx]` |
| Creates | DF2 with `handlers=(outer,)` | DF2 with `handlers=(outer,)` |
| **Result** | **Identical K structure** | **Identical K structure** |

### Why Forward Exists

1. **Clarity**: `yield Forward(effect)` explicitly communicates forwarding intent
2. **Efficiency**: Skips `collect_available_handlers` K-walk (minor optimization)
3. **Better errors**: Forward validates it's inside a handler context

```python
# handle_forward can give a clear error:
if df_idx is None:
    raise RuntimeError("Forward called outside of handler context")

# vs start_dispatch which would give:
raise UnhandledEffectError("No handler for SomeEffect")  # Less clear
```

### Recommendation

Use `Forward` for explicit forwarding. Use re-yield only when you want to treat the effect as a "new" effect that happens to be the same.

---

## Scheduling Primitives: GetContinuation and ResumeContinuation

### Overview

The scheduling primitives enable **cooperative scheduling** patterns similar to OCaml5's effect handlers and Koka's evidence passing. They allow handlers to:

1. Capture the current continuation as a first-class value
2. Store continuations for later resumption
3. Switch between different captured continuations

This enables patterns like:
- Cooperative multitasking (yield/resume)
- Async/await semantics
- Coroutine-based concurrency
- Custom schedulers

### Key Design Decisions

**GetContinuation does NOT consume DF**: Unlike `Resume`, which consumes the `DispatchingFrame` and resumes the continuation immediately, `GetContinuation` merely returns a captured continuation as a value. The handler can still `Resume`, `Forward`, or do other operations after capturing.

**Resume vs ResumeContinuation are SEPARATE**:
- `Resume(v)` = Resume the *current dispatch's* continuation (the common case)
- `ResumeContinuation(k, v)` = Resume *any* captured continuation (scheduling case)

**Scheduler is NOT a coroutine**: The scheduler pattern works via "state + fresh handler invocations":
- Each `Yield` effect → fresh handler invocation
- Queue state persists in closure or external store
- No "re-entering the same generator"

### Continuation Type

```python
@dataclass(frozen=True)
class Continuation:
    """Captured continuation that can be resumed later."""
    cont_id: int                    # One-shot tracking (unique ID)
    frames: tuple[Frame, ...]       # The captured continuation frames
```

The `cont_id` enables **one-shot enforcement**: each continuation can only be resumed once. The VM tracks which `cont_id`s have been consumed.

### GetContinuation Semantics

When a handler yields `GetContinuation()`:

1. **Capture**: Build continuation from current K (frames between DF and WHF)
2. **Return**: Send `Continuation` object back to handler generator
3. **Preserve DF**: The `DispatchingFrame` remains in K (not consumed)

```
Before GetContinuation:
K = [handler_gen, DF(started=True), user_gen, nested_gen, WHF(handler), ...]

After GetContinuation (k returned to handler_gen):
K = [handler_gen, DF(started=True), user_gen, nested_gen, WHF(handler), ...]
    ↑ unchanged! DF still present

k.frames = (user_gen, nested_gen)  # Captured from between DF and WHF
```

### ResumeContinuation Semantics

When a handler yields `ResumeContinuation(k, value)`:

1. **Validate**: Check `k.cont_id` hasn't been consumed (one-shot)
2. **Mark consumed**: Record `k.cont_id` as used
3. **Context switch**: Replace current K with `k.frames + remaining K after WHF`
4. **Send value**: Set `C = Value(value)` to resume the continuation

```
Before ResumeContinuation(k, 42):
K = [handler_gen, DF, user_gen, WHF(handler), outer_gen, WHF(outer)]
k.frames = (other_user_gen, other_nested_gen)

After ResumeContinuation:
K = [other_user_gen, other_nested_gen, handler_gen, WHF(handler), outer_gen, WHF(outer)]
    ↑ k.frames inserted              ↑ handler_gen preserved for final result
C = Value(42)  # Flows into other_user_gen
```

**KEY**: The handler generator is preserved so it receives the final result when the resumed continuation completes.

### One-Shot Continuation Tracking

The VM maintains a set of consumed continuation IDs (in Store or global state):

```python
# In Store or VM state
consumed_continuations: set[int] = set()

def handle_resume_continuation(rc: ResumeContinuation, state: CESKState) -> CESKState:
    if rc.continuation.cont_id in state.S.get('_consumed_conts', set()):
        raise RuntimeError(f"Continuation {rc.continuation.cont_id} already consumed")
    
    # Mark as consumed
    new_consumed = state.S.get('_consumed_conts', set()) | {rc.continuation.cont_id}
    new_store = {**state.S, '_consumed_conts': new_consumed}
    
    # ... perform context switch ...
```

### Example: Simple Cooperative Scheduler

```python
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class Yield(EffectBase):
    """Yield control to scheduler."""
    pass

# Task queue (external state, not in K)
task_queue: list[Continuation] = []
cont_id_counter = 0

@do
def scheduler_handler(effect):
    """Handler that implements cooperative scheduling."""
    global cont_id_counter
    
    if isinstance(effect, Yield):
        # 1. Capture current task's continuation
        k = yield GetContinuation()
        task_queue.append(k)
        
        # 2. Get next task from queue
        if task_queue:
            next_k = task_queue.pop(0)
            # 3. Switch to next task
            return (yield ResumeContinuation(next_k, None))
        else:
            # No more tasks - resume current (k is still valid)
            return (yield Resume(None))
    
    # Forward unknown effects
    return (yield Forward(effect))

@do
def task_a():
    print("A: start")
    yield Yield()
    print("A: after yield 1")
    yield Yield()
    print("A: done")
    return "A"

@do
def task_b():
    print("B: start")
    yield Yield()
    print("B: done")
    return "B"

@do
def main():
    # Run both tasks with scheduler
    a_result = yield WithHandler(scheduler_handler, task_a())
    # Note: task_b would need to be spawned differently for true concurrency
    b_result = yield WithHandler(scheduler_handler, task_b())
    return (a_result, b_result)
```

### Example Trace: GetContinuation + ResumeContinuation

```python
@do
def user():
    print("user: before yield")
    result = yield Yield()
    print(f"user: after yield, got {result}")
    return result + 1

@do
def scheduler(effect):
    if isinstance(effect, Yield):
        k = yield GetContinuation()   # Capture
        print(f"scheduler: captured continuation {k.cont_id}")
        # Immediately resume the same continuation (simple case)
        return (yield ResumeContinuation(k, 42))
    return (yield Forward(effect))

# Run
result = run(WithHandler(scheduler, user()))
# Output:
# user: before yield
# scheduler: captured continuation 1
# user: after yield, got 42
# Result: 43
```

### Step-by-step Trace:

```
1. Setup: WithHandler processed
   K = [WHF(scheduler)]
   C = ProgramControl(user())

2. user starts, yields Yield()
   K = [user_gen, WHF(scheduler)]
   C = EffectYield(Yield())

3. start_dispatch
   DF = DispatchingFrame(Yield(), idx=0, handlers=[scheduler], started=False)
   K = [DF, user_gen, WHF(scheduler)]
   C = Value(None)

4. DF processing: start scheduler
   K = [DF(started=True), user_gen, WHF(scheduler)]
   C = ProgramControl(scheduler(Yield()))

5. scheduler starts, yields GetContinuation()
   K = [sched_gen, DF(started=True), user_gen, WHF(scheduler)]
   C = EffectYield(GetContinuation())

6. handle_get_continuation
   - Find DF at idx=1
   - Find WHF(scheduler) in K
   - Capture frames between DF and WHF: (user_gen,)
   - Create Continuation(cont_id=1, frames=(user_gen,))
   - Send Continuation to sched_gen
   
   K = [sched_gen, DF(started=True), user_gen, WHF(scheduler)]  # Unchanged!
   C = Value(Continuation(1, (user_gen,)))

7. Value → sched_gen
   sched_gen receives Continuation, yields ResumeContinuation(k, 42)
   K = [sched_gen, DF(started=True), user_gen, WHF(scheduler)]
   C = EffectYield(ResumeContinuation(Continuation(1, (user_gen,)), 42))

8. handle_resume_continuation
   - Validate cont_id=1 not consumed (OK)
   - Mark cont_id=1 as consumed
   - Find target WHF for this handler (WHF(scheduler))
   - Replace continuation: insert k.frames, preserve sched_gen
   
   K = [user_gen, sched_gen, WHF(scheduler)]
       ↑ k.frames  ↑ preserved handler gen
   C = Value(42)

9. Value(42) → user_gen
   user receives 42, returns 43
   K = [sched_gen, WHF(scheduler)]
   C = Value(43)

10. Value(43) → sched_gen
    scheduler returns 43
    K = [WHF(scheduler)]
    C = Value(43)

11. Value(43), K[0]=WHF(scheduler)
    Scope ends
    K = []
    C = Value(43)

12. Done(43)
```

### ADR-10: GetContinuation Preserves DF

**Decision**: `GetContinuation` returns a continuation value but does NOT consume the `DispatchingFrame`.

**Rationale**:
- Handler may want to capture AND then Resume normally
- Handler may capture multiple times before deciding what to do
- Separates "capture capability" from "use capability"
- Matches OCaml5's `Obj.get_continuation` semantics

**Example where this matters**:
```python
@do
def handler(effect):
    k = yield GetContinuation()  # Capture
    if should_defer(effect):
        defer_queue.append(k)
        return (yield ResumeContinuation(other_k, None))
    else:
        return (yield Resume(compute(effect)))  # Normal resume
```

### ADR-11: One-Shot Continuation Enforcement

**Decision**: Continuations are one-shot. Resuming the same continuation twice raises `RuntimeError`.

**Rationale**:
- Python generators are inherently one-shot
- Matches OCaml5 and Koka default behavior
- Multi-shot would require generator cloning (complex, expensive)
- One-shot is sufficient for most scheduling patterns

**Future extension**: If multi-shot is needed, could add explicit `Continuation.clone()` that deep-copies generators (if supported by Python).

### ADR-12: Program Yields Handled in Level 2 (Monadic Bind)

**Decision**: When a generator yields a `Program` (including `KleisliProgramCall`), Level 2 handles it by converting to `ProgramControl` - the monadic bind operation.

**Rationale**:
- `Program` is the fundamental composable unit in doeff
- Yielding a Program means "run this program, give me its result" (flatMap/bind)
- This is NOT an effect - it's sequencing/composition
- Level 1 is pure CESK (generators only), shouldn't know about Program types
- Level 2 already handles program semantics (WithHandler installs programs)

**Yield Classification in Level 2**:
| Yielded Type | Classification | Action |
|--------------|----------------|--------|
| `ControlPrimitive` | VM instruction | Execute primitive (Resume, Forward, etc.) |
| `ProgramBase` | Monadic bind | Convert to `ProgramControl`, execute nested program |
| `EffectBase` | User effect | Start dispatch via `DispatchingFrame` |
| Other | Error | Raise `TypeError` |

**Implementation**:
```python
if isinstance(yielded, ProgramBase):
    # Monadic bind: execute nested program, result flows back via ReturnFrame
    return CESKState(C=ProgramControl(yielded), E=E, S=S, K=K)
```

**Key insight**: The existing `ReturnFrame` machinery handles the "send result back" part. When the nested program completes with `Value(result)`, the outer generator's `ReturnFrame` receives it via `send()`.

---

## Module Organization

```
doeff/cesk_v3/
├── __init__.py
├── errors.py                    # UnhandledEffectError
│
├── level1_cesk/
│   ├── __init__.py
│   ├── state.py                 # CESKState, Control types (Value, Error, etc.)
│   ├── frames.py                # ReturnFrame only
│   ├── step.py                  # cesk_step()
│   └── types.py                 # Environment, Store type aliases
│
├── level2_algebraic_effects/
│   ├── __init__.py
│   ├── frames.py                # WithHandlerFrame, DispatchingFrame
│   ├── primitives.py            # ControlPrimitive, WithHandler, Resume, Forward,
│   │                            # GetContinuation, ResumeContinuation, Continuation
│   ├── step.py                  # level2_step()
│   ├── dispatch.py              # start_dispatch(), collect_available_handlers()
│   └── handlers.py              # handle_resume(), handle_forward(), handle_with_handler(),
│                                # handle_implicit_abandonment(), handle_get_continuation(),
│                                # handle_resume_continuation()
│
└── run.py                       # Main loop: run()
```

**Note**: No `level3_user_effects/` - user effect dispatch is handled by Level 2 via DispatchingFrame.

---

## Summary

```
┌─────────────────────────────────────────────────────────────────────┐
│  Level 0: Python (CPython)                                          │
│  - Executes Python bytecode, manages generators                     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Level 1: CESK Machine (cesk_step)                                  │
│  Module: doeff.cesk_v3.level1_cesk                                  │
│  State: C, E, S, K                                                  │
│  Frames: ReturnFrame only                                           │
│  Produces: Value, EffectYield, Error, Done, Failed                  │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Level 2: Algebraic Effects Machine (level2_step)                   │
│  Module: doeff.cesk_v3.level2_algebraic_effects                     │
│  Frames: WithHandlerFrame, DispatchingFrame                         │
│  Handles: WithHandler, Resume, Forward, EffectBase dispatch         │
│                                                                     │
│  KEY INSIGHT: All state in K. No separate H stack.                  │
│  - WHF holds handler function                                       │
│  - DispatchingFrame tracks dispatch progress + handler_started      │
│  - Forwarding = Forward primitive or re-yield (both work)           │
│  - Implicit abandonment if handler returns without Resume           │
└─────────────────────────────────────────────────────────────────────┘
```

**Key Architecture Points:**

1. **Unified K**: All handler and dispatch state lives in K
2. **Three frame types**: ReturnFrame, WithHandlerFrame, DispatchingFrame
3. **WHF holds handler**: Not just a marker, contains the handler function
4. **DispatchingFrame**: Makes dispatch a VM operation, state is capturable
5. **handler_started flag**: Distinguishes initial dispatch from handler completion
6. **Handler snapshot**: DispatchingFrame freezes available handlers at dispatch start
7. **Busy boundary**: Nested dispatch uses `handlers[:idx]` from parent DF (see ADR-5)
8. **No Level 3**: User effect dispatch is Level 2's DispatchingFrame logic
9. **K never cleared**: Invariant violation → raise, never `K=[]`

**Handler API:**

| Yield | Effect |
|-------|--------|
| `Resume(value)` | Resume user continuation, handler receives user's result |
| `Forward(effect)` | Delegate to outer handlers (explicit) |
| `GetContinuation()` | Capture current continuation. DF preserved. |
| `ResumeContinuation(k, v)` | Context switch to captured continuation k |
| `yield effect` | Also forwards (via nested DF), works but more frames |
| `return value` | Implicit abandonment - user continuation dropped |

**Invariants:**

1. Level 1 only sees ReturnFrame at K[0]
2. ControlPrimitive never goes through dispatch (handled directly by Level 2)
3. WHF and DispatchingFrame are always intercepted by Level 2
4. K is never set to `[]` except naturally at computation end
5. **EffectYield consumption**: Level 2 MUST consume EffectYield and convert to another Control type. Level 2 must NEVER return a state with `C=EffectYield`. (Prevents infinite loops)

---

## Implementation Plan

### Phase 1: Foundation
- Module structure
- Frame types (ReturnFrame, WithHandlerFrame, DispatchingFrame with handler_started)
- Control types (Value, Error, EffectYield, etc.)
- ControlPrimitive types (WithHandler, Resume, Forward)

### Phase 2: Level 1
- `cesk_step()` - pure CESK, only ReturnFrame

### Phase 3: Level 2 Core
- `level2_step()` - wraps Level 1
- `handle_with_handler()` - push WHF, start program
- WHF processing - scope end

### Phase 4: Dispatch
- `start_dispatch()` - push DispatchingFrame (handler_started=False)
- `collect_available_handlers()` - walk K for handlers
- DispatchingFrame processing - check handler_started flag
  - False: start handler, update to True
  - True: handler returned without Resume → implicit abandonment

### Phase 5: Control Primitives
- `handle_resume()` - find handler's WHF by identity, arrange K
- `handle_forward()` - create nested DF with outer handlers
- `handle_implicit_abandonment()` - drop user continuation, close generators

### Phase 6: Integration
- `run()` main loop
- Basic handler examples

### Phase 7: Testing
- Unit tests per level
- Integration tests for nested handlers
- Forwarding tests (Forward and re-yield)
- Implicit abandonment tests

### Phase 8: Scheduling Primitives
- `Continuation` dataclass with one-shot tracking
- `GetContinuation` primitive - capture continuation without consuming DF
- `ResumeContinuation` primitive - context switch to captured continuation
- `handle_get_continuation()` - capture frames between DF and WHF
- `handle_resume_continuation()` - validate one-shot, perform context switch
- One-shot enforcement via consumed continuation tracking in Store

### Phase 9: Scheduling Tests
- Simple GetContinuation + ResumeContinuation roundtrip
- One-shot violation detection
- Cooperative scheduler example (Yield effect)
- Multiple task interleaving

---

## References

- [Koka Language](https://koka-lang.github.io/) - Evidence-passing, multi-prompt delimited control
- [OCaml 5 Effects](https://ocaml.org/manual/effects.html) - Fiber-based stack switching
- [Generalized Evidence Passing](https://www.microsoft.com/en-us/research/publication/generalized-evidence-passing-for-effect-handlers/) - Koka's compilation strategy
