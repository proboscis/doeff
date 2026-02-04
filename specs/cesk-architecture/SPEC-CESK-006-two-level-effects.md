# SPEC-CESK-006: Layered Interpreter Architecture for Algebraic Effects

## Status: Draft

## Summary

This spec defines the **layered interpreter architecture** for doeff's algebraic effects system. The system consists of three interpreter layers that share CESK state but have distinct responsibilities:

```
0. Python (CPython)
   └── 1. GeneratorCESK Interpreter (cesk_step)
         └── 2. Algebraic Effects Interpreter (algebraic_effects_step)
               └── 3. User Effects Dispatcher (user_effects_step)
```

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

Both languages have **layered interpretation**:
1. Base runtime (bytecode VM / C runtime)
2. Effect machinery (perform/resume primitives)
3. User effect dispatch (handler lookup and invocation)

---

## Architectural Decisions

This section documents key architectural decisions, their rationale, and alternatives considered.

### ADR-1: H Stack in S (CESK+H via Store)

**Decision**: Store the handler stack (`AlgebraicEffectsState`) in Level 1's Store under a reserved key (`__doeff_internal_ae__`), rather than adding H as a fifth component to CESK.

**Rationale**:
- Level 1 remains pure CESK with no knowledge of effects
- Clean separation: Level 1 manages control flow, Level 2 manages effect semantics
- Store is already the mechanism for "state that persists across steps"

**Alternatives considered**:
- CESK+H (explicit H component): Would require modifying Level 1's state signature
- H in Environment: Environment is for lexical bindings, not runtime state

### ADR-2: Two Frame Types (ReturnFrame + WithHandlerFrame)

**Decision**: K contains two frame types:
- `ReturnFrame`: Holds suspended generator (Level 1 processes)
- `WithHandlerFrame`: Marks handler scope boundary (Level 2 intercepts)

**Rationale**:
- WHF serves as a "bookmark" that says "when value reaches here, pop handler"
- Level 2 intercepts WHF before Level 1 sees it, maintaining layering
- Each WithHandler creates paired (HandlerEntry, WHF) that get popped together

**Alternatives considered**:
- Callback on ReturnFrame: Timing mismatch - WHF needed before generator exists
- Unified Frame with flags: Adds complexity, mixes concerns
- Track handler depth separately: Breaks during dispatch when K is cleared

### ADR-3: Per-Handler captured_k (Two-Stack Model)

**Decision**: Each `HandlerEntry` maintains its own `captured_k`, not a global field in `AlgebraicEffectsState`.

**Rationale**:
- Enables nested handler dispatch (handler yielding WithHandler)
- Without per-handler storage, nested dispatch overwrites original continuation
- Mirrors Koka's evidence vector model where each prompt has its own state

**Problem solved**:
```python
# Without per-handler captured_k:
1. User yields MyEffect → captured_k = [user_gen]
2. outer_handler yields WithHandler(inner, nested())
3. nested() yields InnerEffect → captured_k = [nested_gen, ...] OVERWRITES!
4. outer_handler yields Resume → captured_k is wrong!

# With per-handler captured_k:
- handler_stack[outer_idx].captured_k = [user_gen] (preserved)
- handler_stack[inner_idx].captured_k = [nested_gen, ...]
```

### ADR-4: Push/Pop at END for Index Stability

**Decision**: Push and pop handlers at the END of `handler_stack` tuple, not the front.

**Rationale**:
- `active_handler_index` remains valid when nested handlers are pushed
- Existing handler indices don't shift during nested dispatch

**Problem solved**:
```python
# Push at FRONT (broken):
handler_stack = [outer]  # outer at index 0
active_handler_index = 0
# After nested push:
handler_stack = [inner, outer]  # outer shifted to index 1!
active_handler_index = 0  # Now points to inner, WRONG!

# Push at END (fixed):
handler_stack = [outer]  # outer at index 0
active_handler_index = 0
# After nested push:
handler_stack = [outer, inner]  # outer still at index 0!
active_handler_index = 0  # Still correct!
```

**Implication**: Search for handlers from END (innermost first), not front.

### ADR-5: Explicit Resume (No Tail-Resume Default)

**Decision**: Handlers must explicitly call `Resume(value)` to resume the captured continuation. Handler returning without Resume is either an error or requires explicit `Abort`.

**Rationale**:
- Explicit is better than implicit for control flow
- Allows handlers to transform user's final result (Resume returns user's result)
- Matches the mental model: "handler receives effect, decides what to do"

**Alternatives considered**:
- Tail-resume default: Handler return implicitly resumes with return value
  - Simpler for common case, but less explicit
  - Can't transform user's result
  - Adopted by some implementations (e.g., existing `step_v2.py`)

### ADR-6: One-Shot Continuations

**Decision**: Each continuation can only be resumed ONCE. Tracked via `consumed_continuations: frozenset[int]`.

**Rationale**:
- Python generators are inherently one-shot
- Matches Koka/OCaml5 default behavior
- Multi-shot would require copying generator state (complex, expensive)

**Enforcement**:
- Runtime: `Resume` raises `RuntimeError` if continuation already consumed
- Semgrep: Warn on multiple `Resume` calls in same handler (heuristic)

### ADR-7: Handlers Remain Installed After Resume

**Decision**: After `Resume(value)`, the handler remains installed and continues to handle subsequent effects from user code until the `WithHandler` scope ends.

**Rationale**:
- Most common use case (state, logging, etc.)
- Simpler mental model - handler monitors entire computation
- Matches intuition: "handle all Get/Put effects in this block"

**Implication**: When user code yields another effect after being resumed, the same handler stack is active.

**Terminology note**: This is called "deep handlers" in academic literature (vs "shallow" where handler is removed after each resume). We use deep semantics without requiring users to know the terminology.

### ADR-8: WithHandlerFrame for Scope Tracking

**Decision**: Keep `WithHandlerFrame` as a distinct frame type rather than eliminating it via callbacks.

**Rationale**:
- Clean conceptual separation: ReturnFrame = execution, WHF = scope boundary
- WHF is created when WithHandler is translated, before program's generator exists
- Callback approach has timing mismatch issues

**How it works**:
1. `WithHandler(h, p)` translated → push HandlerEntry, push WHF to K
2. Program runs, may yield effects
3. Program completes → Value flows through K
4. Value reaches WHF → Level 2 intercepts, pops handler, continues

### ADR-9: Abort Primitive for Intentional Non-Resume

**Decision**: Introduce `Abort` control primitive for handlers that intentionally abandon the user's continuation.

**Rationale**:
- Handler must either `Resume` or `Abort` - no implicit behavior
- `Abort` makes abandonment explicit and intentional
- Handler returning without Resume/Abort is an error

**Control primitives**:
```python
Resume(value)  # Resume user continuation with value
Abort(value)   # Abandon user continuation, return value from WithHandler
```

### ADR-10: Handler Exception Goes to User

**Decision**: If handler raises an exception, resume the captured continuation with that error (user code receives the exception).

**Rationale**:
- Handler exception = "I can't handle this effect properly"
- User code should have the opportunity to catch/handle
- Matches intuition: effect failed, error propagates to caller

**Implementation**:
```python
def invoke_handler(handler, effect, state):
    try:
        return run_handler(handler, effect, state)
    except Exception as e:
        # Resume user continuation with the error
        captured_k = get_captured_k_for_active_handler(state)
        return CESKState(C=Error(e), ..., K=list(captured_k))
```

### ADR-11: Warn and Clean Abandoned Continuations

**Decision**: If a continuation is abandoned (neither resumed nor explicitly aborted), emit a warning and clean up. This shouldn't happen often.

**Rationale**:
- Abandoned continuations indicate bugs (forgot to Resume/Abort)
- Warning helps developers find issues
- Cleanup prevents memory leaks
- Not a hard error because it may happen during exception unwinding

**Implementation**:
- Track continuations that were captured but never consumed
- On handler scope end (WHF processed), check if captured_k was used
- If not: `warnings.warn()` and call `.close()` on generators in captured_k

### ADR-12: Rewrite of step.py (v1)

**Decision**: This spec defines a rewrite of `doeff/cesk/step.py` (v1) with proper layered algebraic effects architecture.

**Current state**:
- `step.py` (v1) = current implementation, being rewritten per this spec
- `step_v2.py` = WIP experiment, incorrect, will be removed

**Implementation path**:
1. Implement new layered architecture per this spec (Level 1, 2, 3)
2. Rewrite handlers to use new control primitives (Resume, Abort, etc.)
3. Remove `step_v2.py` (incorrect WIP)
4. Replace `step.py` with new implementation

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
│ Level 2: Algebraic Effects (level2_step) - WRAPS Level 1            │
│                                                                     │
│   PRE-STEP:                                                         │
│     - Handles WithHandlerFrame (C=Value, K[0]=WHF → pop handler)    │
│                                                                     │
│   ┌───────────────────────────────────────────────────────────────┐ │
│   │ Level 1: Pure CESK Machine (cesk_step)                        │ │
│   │                                                               │ │
│   │   Only handles ReturnFrame. No effect knowledge.              │ │
│   │   State: C, E, S, K                                           │ │
│   │   Produces: Value, EffectYield, Error, Done, Failed           │ │
│   └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
│   POST-STEP:                                                        │
│     - Translates ControlPrimitive → CESK state change               │
│     - Passes EffectBase → Level 3                                   │
│                                                                     │
│   State: AlgebraicEffectsState in S["__doeff_internal_ae__"]        │
│   Owns: WithHandlerFrame                                            │
│   INVARIANT: ControlPrimitive NEVER reaches Level 3                 │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ EffectBase
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Level 3: User Effects (Translation Layer)                           │
│                                                                     │
│   Translates user effects into handler invocation.                  │
│   Finds handler in AlgebraicEffectsState.handler_stack              │
│   Handler uses Level 2 primitives to respond.                       │
└─────────────────────────────────────────────────────────────────────┘
```

**Key insight**: Level 2 WRAPS Level 1. It intercepts WithHandlerFrame before Level 1 sees it,
and translates ControlPrimitive after Level 1 produces it. Level 1 only ever sees ReturnFrame.

### CESK State

```python
@dataclass
class CESKState:
    """CESK machine state."""
    C: Control              # Current control (Value, Program, Error)
    E: Environment          # Current environment (immutable)
    S: Store                # Store (generic key-value, used by all layers)
    K: Kontinuation         # Continuation stack (ReturnFrame | WithHandlerFrame)
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
    """Marks handler scope boundary.
    
    Handled by Level 2 (algebraic effects layer).
    When a Value reaches this frame, the handler is popped.
    """
    pass  # Just a marker, no data needed


# K can contain both frame types
Kontinuation = list[ReturnFrame | WithHandlerFrame]
```

**Frame ownership:**

| Frame Type | Owned By | When Processed |
|------------|----------|----------------|
| `ReturnFrame` | Level 1 | Value → send to generator |
| `WithHandlerFrame` | Level 2 | Value → pop handler, continue |

### Two-Stack Architecture

Level 2 maintains its own stack-based state machine on top of Level 1's continuation stack:

```
┌─────────────────────────────────────────────────────────────────┐
│ Level 2: Algebraic Effects State Machine (H in S)               │
│                                                                 │
│   handler_stack: [..., HandlerEntry_1, HandlerEntry_0]          │
│                            │               │                    │
│                            ▼               ▼                    │
│                       captured_k_1    captured_k_0              │
│                       (outer)         (innermost)               │
│                                                                 │
│   Push/pop at END for stable indices during nested dispatch     │
│   Search from END (innermost first) for effect dispatch         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ coordinates with
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Level 1: CESK State Machine                                     │
│                                                                 │
│   K: [ReturnFrame, WHF, ReturnFrame, WHF, ReturnFrame, ...]     │
│        └─────────────────────────────────────────────────┘      │
│                    control flow stack                           │
│                                                                 │
│   WHF marks handler scope boundaries (for popping)              │
└─────────────────────────────────────────────────────────────────┘
```

**Key insight**:
- **Level 1 K**: "Where does this value go?" (pure control flow)
- **Level 2 handler_stack**: "Who handles effects and what continuations are they holding?"
- **H is stored in S**: `S["__doeff_internal_ae__"]` keeps Level 1 pure CESK

**Index stability** (push/pop at END):
- `handler_stack[-1]` = innermost (most recently installed)
- `handler_stack[0]` = outermost
- When nested handler is pushed, existing indices don't shift
- `active_handler_index` remains valid across nested pushes

**Why per-handler captured_k?**

When a handler installs a nested handler (via `WithHandler`), the nested handler may dispatch effects that capture NEW continuations. If we used a single global `captured_k`, the nested dispatch would overwrite the original handler's continuation. Per-handler storage (like Koka's evidence vectors) ensures each handler invocation maintains its own captured continuation.

### Stack Interaction Table

| Event | Level 2 (handler_stack) | Level 1 (K) |
|-------|-------------------------|-------------|
| `WithHandler(h, p)` | Push `HandlerEntry(h)` at END | Push `WithHandlerFrame` at front |
| Effect dispatched to handler[i] | `handler_stack[i].captured_k = K` | `K = []` (handler starts fresh) |
| `Resume(value)` | Use `handler_stack[active].captured_k` | `K = captured_k + handler_k` |
| Handler scope ends (Value at WHF) | Pop from END | Pop `WithHandlerFrame` from front |

**Why push/pop at END?**

Index stability during nested handler dispatch:
```
Before nested WithHandler:
  handler_stack = [outer]  # outer at index 0
  active_handler_index = 0

After nested WithHandler (push at END):
  handler_stack = [outer, inner]  # outer still at index 0!
  active_handler_index = 0  # Still correct!

If we pushed at FRONT (broken):
  handler_stack = [inner, outer]  # outer shifted to index 1!
  active_handler_index = 0  # Now points to inner, wrong!
```

### Level 2 State Storage

Level 2 stores its state IN Level 1's Store using a **single reserved key** containing an immutable dataclass.

**Reserved Store Key**:

```python
DOEFF_INTERNAL_AE = "__doeff_internal_ae__"
```

**HandlerEntry and AlgebraicEffectsState** (`doeff.cesk.level2_algebraic_effects.state`):

```python
# Type alias for handlers
# Handler is a function: effect -> Program that uses Level 2 primitives
Handler = Callable[[EffectBase], Program[Any]]


@dataclass(frozen=True)
class HandlerEntry:
    """Entry in Level 2 handler stack.
    
    Each handler invocation maintains its own captured continuation,
    mirroring Koka's evidence vector model where each prompt has its own state.
    """
    handler: Handler
    
    # Captured continuation for THIS handler's effect dispatch
    # Set when effect is dispatched to this handler, used by Resume
    captured_k: tuple[Frame, ...] | None = None
    
    # ID for one-shot tracking
    captured_k_id: int | None = None
    
    def with_captured(
        self, k: tuple[Frame, ...], k_id: int
    ) -> "HandlerEntry":
        """Return new entry with captured continuation."""
        return replace(self, captured_k=k, captured_k_id=k_id)
    
    def clear_captured(self) -> "HandlerEntry":
        """Return new entry with cleared continuation."""
        return replace(self, captured_k=None, captured_k_id=None)


@dataclass(frozen=True)
class AlgebraicEffectsState:
    """Immutable state for Level 2 algebraic effects.
    
    Stored in S[DOEFF_INTERNAL_AE].
    All fields are immutable (frozen dataclass + immutable collections).
    
    This is Level 2's stack-based state machine, running on top of Level 1's
    CESK machine. The handler_stack parallels WithHandlerFrames in K.
    
    IMPORTANT: Push/pop at END to keep indices stable during nested handler dispatch.
    - handler_stack[-1] = innermost (most recently installed)
    - handler_stack[0] = outermost
    - Effect dispatch searches from END (innermost first)
    - active_handler_index remains stable when nested handlers are pushed
    """
    # Handler stack - innermost handler LAST (index -1 = most recently installed)
    # Push/pop at END for stable indices during nested dispatch
    # Each entry contains the handler AND its captured continuation
    handler_stack: tuple[HandlerEntry, ...] = ()
    
    # Continuation registry - maps handle ID to captured K
    # Used for explicit continuation handles (GetContinuation/Resume with k=handle)
    continuation_registry: MappingProxyType[int, Kontinuation] = field(
        default_factory=lambda: MappingProxyType({})
    )
    
    # Counter for generating unique continuation handle IDs
    next_continuation_id: int = 0
    
    # Active handler index in handler_stack (-1 = no handler active)
    # Used for effect forwarding to find next handler
    # Stable across nested handler pushes (because we push at END)
    active_handler_index: int = -1
    
    # Set of continuation IDs that have been consumed (one-shot enforcement)
    # Once a continuation ID is in this set, Resume will reject it
    consumed_continuations: frozenset[int] = frozenset()
    
    # --- Immutable update methods ---
    
    def push_handler(self, handler: Handler) -> "AlgebraicEffectsState":
        """Push a new handler onto the stack (at END for stable indices)."""
        entry = HandlerEntry(handler=handler)
        return replace(self, handler_stack=self.handler_stack + (entry,))
    
    def pop_handler(self) -> "AlgebraicEffectsState":
        """Pop the innermost handler from the stack (from END)."""
        return replace(self, handler_stack=self.handler_stack[:-1])
    
    def innermost_handler_index(self) -> int:
        """Get index of innermost handler (-1 if empty)."""
        return len(self.handler_stack) - 1 if self.handler_stack else -1
    
    def capture_continuation_at(
        self, index: int, k: tuple[Frame, ...], k_id: int
    ) -> "AlgebraicEffectsState":
        """Capture a continuation for handler at given index."""
        entry = self.handler_stack[index]
        new_entry = entry.with_captured(k, k_id)
        new_stack = (
            self.handler_stack[:index] 
            + (new_entry,) 
            + self.handler_stack[index + 1:]
        )
        return replace(
            self,
            handler_stack=new_stack,
            next_continuation_id=k_id + 1,
            active_handler_index=index,
        )
    
    def get_captured_at(self, index: int) -> tuple[tuple[Frame, ...] | None, int | None]:
        """Get captured continuation for handler at index."""
        entry = self.handler_stack[index]
        return entry.captured_k, entry.captured_k_id
    
    def clear_captured_at(self, index: int) -> "AlgebraicEffectsState":
        """Clear captured continuation for handler at index."""
        entry = self.handler_stack[index]
        new_entry = entry.clear_captured()
        new_stack = (
            self.handler_stack[:index] 
            + (new_entry,) 
            + self.handler_stack[index + 1:]
        )
        return replace(self, handler_stack=new_stack)
    
    def mark_consumed(self, k_id: int) -> "AlgebraicEffectsState":
        """Mark a continuation as consumed (one-shot enforcement)."""
        return replace(
            self,
            consumed_continuations=self.consumed_continuations | {k_id},
        )
    
    def is_consumed(self, k_id: int) -> bool:
        """Check if a continuation has been consumed."""
        return k_id in self.consumed_continuations
    
    def store_continuation(
        self, k_id: int, k: Kontinuation
    ) -> "AlgebraicEffectsState":
        """Store a continuation in the registry (for GetContinuation)."""
        new_registry = MappingProxyType({**self.continuation_registry, k_id: k})
        return replace(self, continuation_registry=new_registry)
    
    def get_continuation(self, k_id: int) -> Kontinuation | None:
        """Get a continuation from the registry."""
        return self.continuation_registry.get(k_id)


def get_ae_state(S: Store) -> AlgebraicEffectsState:
    """Get Level 2 state from Store, creating default if missing."""
    return S.get(DOEFF_INTERNAL_AE, AlgebraicEffectsState())


def set_ae_state(S: Store, ae: AlgebraicEffectsState) -> Store:
    """Return new Store with updated Level 2 state."""
    return {**S, DOEFF_INTERNAL_AE: ae}
```

**Benefits of Single Dataclass**:

| Aspect | Multiple Keys | Single Dataclass |
|--------|---------------|------------------|
| Type safety | Dict with Any values | Typed fields |
| Immutability | Manual dict spread | `@dataclass(frozen=True)` + `replace()` |
| Encapsulation | Keys scattered in S | Single namespace |
| Semgrep rule | Match pattern per key | Single key to block |
| Update methods | Inline dict manipulation | Semantic methods |
| Default values | Repeated `.get(..., default)` | Field defaults |

**Semgrep Rule** (to be added to `.semgrep.yaml`):

```yaml
rules:
  - id: no-direct-access-to-algebraic-effects-internal-state
    pattern-either:
      - pattern: $S["__doeff_internal_ae__"]
      - pattern: $S.get("__doeff_internal_ae__")
      - pattern: $S.get("__doeff_internal_ae__", $DEFAULT)
      - pattern: |
          "__doeff_internal_ae__"
    paths:
      exclude:
        - doeff/cesk/level2_algebraic_effects/**/*.py
        - doeff/cesk/level3_user_effects/**/*.py
        - doeff/cesk/run.py
        - doeff/cesk/translate.py
        - tests/cesk/test_*.py
    message: |
      Direct access to algebraic effects internal state is forbidden.
      Use control primitives (Resume, GetContinuation, etc.) instead.
    severity: ERROR
```

### Main Loop

```python
def run(program: Program[T]) -> T:
    """Main interpreter loop.
    
    Level 2 step wraps Level 1 and handles:
    - WithHandlerFrame completion (pre-step)
    - ControlPrimitive translation (post-step)
    
    Main loop handles Level 3 user effects.
    """
    state = initial_state(program)
    
    while True:
        # Level 2 step (wraps Level 1, handles WithHandlerFrame + ControlPrimitive)
        result = level2_step(state)
        
        if isinstance(result, Done):
            return result.value
        if isinstance(result, Failed):
            raise result.error
        
        # Level 3: User effects (only EffectBase reaches here)
        if isinstance(result.C, EffectYield):
            yielded = result.C.yielded
            if isinstance(yielded, EffectBase):
                result = translate_user_effect(yielded, result)
            else:
                raise TypeError(f"Unknown yield type: {type(yielded)}")
        
        state = result
```

---

## Module Organization

Classes and functions are organized by level to make responsibilities clear:

```
doeff/cesk/
├── level1_cesk/                    # Level 1: Pure CESK Machine
│   ├── __init__.py
│   ├── state.py                    # CESKState, Control, Value, Error
│   ├── frames.py                   # ReturnFrame (the ONLY frame type)
│   ├── step.py                     # cesk_step() - the only stepper
│   └── types.py                    # Kontinuation, Environment, Store
│
├── level2_algebraic_effects/       # Level 2: Algebraic Effects
│   ├── __init__.py
│   ├── state.py                    # AlgebraicEffectsState dataclass, DOEFF_INTERNAL key
│   ├── control_primitives.py       # ControlPrimitive base, WithHandler, Resume, etc.
│   ├── continuation_handle.py      # ContinuationHandle (opaque)
│   ├── handler.py                  # Handler type alias
│   └── translate.py                # translate_control_primitive()
│
├── level3_user_effects/            # Level 3: User Effects
│   ├── __init__.py
│   ├── effect_base.py              # EffectBase (user effects inherit from this)
│   ├── dispatch.py                 # translate_user_effect(), find_handler()
│   └── handler_protocol.py         # Handler signature/protocol
│
├── run.py                          # Main loop: run(), orchestrates all levels
└── translate.py                    # translate_yield() - dispatches to L2/L3
```

### Naming Convention

| Level | Module Prefix | Class/Function Examples |
|-------|---------------|-------------------------|
| Level 1 | `level1_cesk` | `CESKState`, `ReturnFrame`, `cesk_step` |
| Level 2 | `level2_algebraic_effects` | `ControlPrimitive`, `WithHandler`, `Resume`, `ContinuationHandle` |
| Level 3 | `level3_user_effects` | `EffectBase`, `Get`, `Put`, `Spawn` |

### Import Examples

```python
# Level 1 - Pure CESK
from doeff.cesk.level1_cesk import CESKState, cesk_step, ReturnFrame

# Level 2 - Algebraic Effects
from doeff.cesk.level2_algebraic_effects import (
    ControlPrimitive,
    WithHandler,
    Resume,
    GetContinuation,
    ContinuationHandle,
)

# Level 3 - User Effects
from doeff.cesk.level3_user_effects import EffectBase

# User-defined effects (Level 3)
from doeff.effects import Get, Put, Ask, Spawn
```

---

## Level 1: CESK Machine

**Module**: `doeff.cesk.level1_cesk`

Level 1 is the pure CESK machine. It knows nothing about effects or handlers - 
it only steps generators and manages the continuation stack.

### Classes (`doeff.cesk.level1_cesk.state`)

```python
@dataclass
class CESKState:
    """Pure CESK machine state."""
    C: Control      # ProgramControl | Value | Error | EffectYield
    E: Environment  # Immutable bindings (dict-like)
    S: Store        # Generic key-value storage (dict)
    K: Kontinuation # List[ReturnFrame] - the ONLY frame type


@dataclass
class ProgramControl:
    """Control: a program to execute."""
    program: Program


@dataclass
class Value:
    """Control: a computed value."""
    value: Any


@dataclass
class Error:
    """Control: an exception was raised."""
    error: BaseException


@dataclass
class EffectYield:
    """Control: generator yielded something (for translation layer)."""
    yielded: Any
```

### Frames (`doeff.cesk.level1_cesk.frames`)

```python
@dataclass
class ReturnFrame:
    """The ONLY frame type in Level 1.
    
    Holds a suspended generator waiting for a value.
    """
    generator: Generator


# K = List[ReturnFrame]  -- ONLY ReturnFrame, nothing else
Kontinuation = list[ReturnFrame]


def assert_valid_k(k: Kontinuation) -> None:
    """Runtime assertion: K must only contain ReturnFrame."""
    for frame in k:
        assert isinstance(frame, ReturnFrame), \
            f"INVARIANT VIOLATION: K contains {type(frame).__name__}, expected ReturnFrame"
```

### K Invariant Enforcement

**INVARIANT**: K contains ONLY `ReturnFrame`. No exceptions.

#### Runtime Assertions

```python
# In cesk_step, after any K modification:
def cesk_step(state: CESKState) -> CESKState | Done | Failed:
    ...
    new_state = CESKState(C=..., E=..., S=..., K=new_k)
    assert_valid_k(new_state.K)  # Enforce invariant
    return new_state

# In translate_control_primitive (Resume):
def translate_resume(value, state):
    captured_k = ...
    handler_k = state.K
    new_k = captured_k + handler_k
    assert_valid_k(new_k)  # Enforce invariant
    ...

# In translate_user_effect:
def translate_user_effect(effect, state):
    captured_k = state.K
    assert_valid_k(captured_k)  # Enforce before capturing
    ...
```

#### Semgrep Rules

```yaml
# .semgrep.yaml
rules:
  - id: k-only-contains-return-frame
    patterns:
      - pattern-either:
          # Forbid pushing non-ReturnFrame to K
          - pattern: $K.append($FRAME)
          - pattern: $K.insert($IDX, $FRAME)
          - pattern: [$FRAME] + $K
          - pattern: $K + [$FRAME]
      - pattern-not: $K.append(ReturnFrame($GEN))
      - pattern-not: [$FRAME, ...] + $K  # Allow list concatenation for Resume
    paths:
      include:
        - doeff/cesk/**/*.py
    message: |
      K must only contain ReturnFrame. Do not push other frame types.
      If you need handler state, use Level 2 store keys instead.
    severity: ERROR

  - id: no-handler-frame-class
    pattern-either:
      - pattern: class HandlerFrame
      - pattern: class HandlerResultFrame
      - pattern: |
          @dataclass
          class $NAME(...):
              ...
          # with "Frame" in name but not ReturnFrame
    paths:
      include:
        - doeff/cesk/**/*.py
      exclude:
        - doeff/cesk/level1_cesk/frames.py  # Only ReturnFrame allowed here
    message: |
      Only ReturnFrame is allowed in Level 1. Handler state belongs in Level 2 store.
    severity: ERROR
```

### K Design Decision

**K contains ONLY `ReturnFrame`s.** No handler frames, no result frames.

| Frame Type | In K? | Where Instead? |
|------------|-------|----------------|
| `ReturnFrame` | YES | - |
| `HandlerFrame` | NO | `AlgebraicEffectsState.handler_stack` in `__doeff_internal_ae__` |
| `HandlerResultFrame` | NO | Removed - handled by `Resume` primitive |

**Rationale:**

1. **Purity**: Level 1 CESK knows nothing about effects/handlers
2. **Simplicity**: K is just a call stack of suspended generators
3. **Clean capture**: When capturing K for a handler, we get exactly the user's continuation
4. **No interleaving**: Handler state and continuation state are separate

**Handler execution flow (two-stack model):**

```
User code running:     K = [ReturnFrame(user_gen)]
                       handler_stack = [HandlerEntry(state_handler)]
                            │
                            ▼ yield Get("key")
                            
Level 3 captures K:    handler_stack[0].captured_k = [ReturnFrame(user_gen)]
                       K = []  (cleared for handler)

Handler starts:        K = [ReturnFrame(handler_gen)]
                            │
                            ▼ handler runs
                            
Handler running:       K = [ReturnFrame(handler_gen)]
                            │
                            ▼ yield Resume(value)
                            
Level 2 restores K:    K = [ReturnFrame(user_gen), ReturnFrame(handler_gen)]
                           (captured_k + handler_k concatenation)
                       C = Value(value)
                            │
                            ▼
User code receives value, continues, returns result
                            │
                            ▼
Handler receives result (from K concatenation)
```

### Level 2 Step Function (`doeff.cesk.level2_algebraic_effects.step`)

Level 2 wraps Level 1, handling WithHandlerFrame before delegating to pure CESK:

```python
def level2_step(state: CESKState) -> CESKState | Done | Failed:
    """Level 2 step: wraps Level 1, handles WithHandlerFrame and ControlPrimitive.
    
    PRE-STEP: Handle WithHandlerFrame completion
    DELEGATE: Level 1 pure CESK step
    POST-STEP: Translate ControlPrimitive yields
    """
    C, E, S, K = state.C, state.E, state.S, state.K
    
    # PRE-STEP: Check for handler completion (Value at WithHandlerFrame)
    if isinstance(C, Value) and K and isinstance(K[0], WithHandlerFrame):
        # Handler scope completed - pop handler, continue with value
        ae = get_ae_state(S)
        new_ae = ae.pop_handler()
        return CESKState(
            C=C,
            E=E,
            S=set_ae_state(S, new_ae),
            K=K[1:],  # Remove WithHandlerFrame
        )
    
    # DELEGATE: Level 1 pure CESK step
    result = cesk_step(state)
    
    # POST-STEP: Translate control primitives
    if isinstance(result, CESKState) and isinstance(result.C, EffectYield):
        yielded = result.C.yielded
        if isinstance(yielded, ControlPrimitive):
            return translate_control_primitive(yielded, result)
    
    return result
```

### Level 1 Step Function (`doeff.cesk.level1_cesk.step`)

```python
def cesk_step(state: CESKState) -> CESKState | Done | Failed:
    """Pure CESK stepper. Only handles ReturnFrame.
    
    Level 2 intercepts WithHandlerFrame before this is called.
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
        frame, *rest_k = K
        assert isinstance(frame, ReturnFrame), \
            f"Level 1 only handles ReturnFrame, got {type(frame).__name__}"
        try:
            yielded = frame.generator.send(C.value)
            return CESKState(C=EffectYield(yielded), E=E, S=S, K=K)
        except StopIteration as e:
            return CESKState(C=Value(e.value), E=E, S=S, K=rest_k)
        except Exception as e:
            return CESKState(C=Error(e), E=E, S=S, K=rest_k)
    
    # Error: throw into continuation (must be ReturnFrame)
    if isinstance(C, Error) and K:
        frame, *rest_k = K
        assert isinstance(frame, ReturnFrame), \
            f"Level 1 only handles ReturnFrame, got {type(frame).__name__}"
        try:
            yielded = frame.generator.throw(type(C.error), C.error)
            return CESKState(C=EffectYield(yielded), E=E, S=S, K=K)
        except StopIteration as e:
            return CESKState(C=Value(e.value), E=E, S=S, K=rest_k)
        except Exception as e:
            return CESKState(C=Error(e), E=E, S=S, K=rest_k)
    
    # Terminal: value with empty K
    if isinstance(C, Value) and not K:
        return Done(C.value)
    
    # Terminal: error with empty K
    if isinstance(C, Error) and not K:
        return Failed(C.error)
    
    # EffectYield: return for translation layer
    return state
```

---

## Level 2: Algebraic Effects (Translation Layer)

**Module**: `doeff.cesk.level2_algebraic_effects`

Level 2 is NOT a stepper - it's a translation layer that interprets control 
primitives yielded by Level 1 and produces new CESK states.

**INVARIANT**: Control primitives NEVER reach Level 3 handlers.

### Control Primitives (`doeff.cesk.level2_algebraic_effects.control_primitives`)

```python
class ControlPrimitive:
    """Base class for Level 2 control primitives.
    
    These are NOT effects. They are instructions to the Level 2 translator.
    They NEVER go through user handler dispatch.
    
    Enforced by: semgrep rule, runtime assertion in Level 3
    """
    pass


@dataclass(frozen=True)
class WithHandler(ControlPrimitive, Generic[T]):
    """Install a handler for a scoped computation."""
    handler: Handler
    program: Program[T]


@dataclass(frozen=True)
class Resume(ControlPrimitive):
    """Resume a continuation with a value.
    
    k=None: resume implicit current continuation (common case)
    k=handle: resume stored continuation (async pattern)
    
    Returns: The final result of the resumed computation.
    
    IMPORTANT: Resume concatenates captured_k + handler_k, so when
    the resumed code completes, its result flows back to the handler.
    This allows handlers to transform user results:
    
        @do
        def double_handler(effect):
            if isinstance(effect, Get):
                value = yield AskStore()[effect.key]
                user_result = yield Resume(value)  # User's final result!
                return user_result * 2             # Transform it
    """
    value: Any
    k: ContinuationHandle | None = None


@dataclass(frozen=True)
class Abort(ControlPrimitive):
    """Abandon the captured continuation and return a value from WithHandler.
    
    Use when handler intentionally does NOT want to resume user code.
    The captured continuation is cleaned up (generators closed).
    
    Example:
        @do
        def early_exit_handler(effect):
            if isinstance(effect, Exit):
                return (yield Abort(effect.value))  # Don't resume user
            result = yield Resume(handle_normally(effect))
            return result
    """
    value: Any


@dataclass(frozen=True)
class GetContinuation(ControlPrimitive):
    """Capture current continuation as an opaque handle.
    
    For async patterns where continuation must be stored and resumed later.
    """
    pass
```

### Continuation Handle (`doeff.cesk.level2_algebraic_effects.continuation_handle`)

```python
@dataclass(frozen=True)
class ContinuationHandle:
    """Opaque handle to a captured continuation.
    
    Cannot be inspected or manipulated.
    Can only be passed to Resume(value, k=handle).
    """
    _id: int  # Internal ID, interpreter maintains registry


# Context access primitives
@dataclass(frozen=True)
class AskStore(ControlPrimitive):
    """Get current store (immutable snapshot)."""
    pass


@dataclass(frozen=True)
class ModifyStore(ControlPrimitive):
    """Atomically update store."""
    updates: Mapping[str, Any]


@dataclass(frozen=True)
class AskEnv(ControlPrimitive):
    """Get current environment."""
    pass
```

### Design Decision: No Explicit ResumeError

**Decision**: We do NOT have an explicit `ResumeError` primitive.

**Rationale**: Handler exceptions automatically resume the continuation with the error.

```python
# Instead of:
yield ResumeError(KeyError("missing"), k)

# Handlers just raise naturally:
@do
def state_handler(effect):
    if isinstance(effect, Get):
        store = yield AskStore()
        if effect.key not in store:
            raise KeyError(effect.key)  # Interpreter catches, resumes with error
        result = yield Resume(store[effect.key])
        return result
```

**Implementation**: The interpreter wraps handler execution in try/except:
```python
def invoke_handler(handler, effect, captured_k, state):
    try:
        result = run_handler(handler, effect, state)
        return process_handler_result(result, state)
    except Exception as e:
        # Auto-resume continuation with error
        return resume_continuation_with_error(captured_k, e, state)
```

**Future**: If explicit `ResumeError` is needed, it can be added later.

### Design Decision: One-Shot Continuations

**INVARIANT**: Each continuation can only be resumed ONCE.

**Rationale**: Python generators are inherently one-shot. Once a generator receives a value via `send()`, that moment in execution is gone forever. Attempting to resume the same continuation twice would corrupt the program state.

This matches Koka and OCaml5's default behavior (both use linear/one-shot continuations by default).

**Tracking via AlgebraicEffectsState**:

The `consumed_continuations: frozenset[int]` field in `AlgebraicEffectsState` tracks which continuation IDs have been resumed.

**Runtime Enforcement**:

```python
def check_one_shot(ae: AlgebraicEffectsState, k_id: int) -> None:
    """Raise if continuation already consumed."""
    if ae.is_consumed(k_id):
        raise RuntimeError(
            f"INVARIANT VIOLATION: Continuation {k_id} already resumed. "
            "Continuations are one-shot and cannot be resumed multiple times."
        )

# In Resume translation (using per-handler captured_k):
case Resume(value, k):
    if k is None:
        # Implicit continuation - get from active handler's slot
        handler_idx = ae.active_handler_index
        captured_k, k_id = ae.get_captured_at(handler_idx)
        if k_id is not None:
            check_one_shot(ae, k_id)
            new_ae = ae.clear_captured_at(handler_idx).mark_consumed(k_id)
        ...
    else:
        # Explicit continuation handle
        check_one_shot(ae, k._id)
        new_ae = ae.mark_consumed(k._id)
        ...
```

**Semgrep Rule** (warning for potential violations):

```yaml
rules:
  - id: potential-multi-resume-in-handler
    patterns:
      - pattern-inside: |
          @do
          def $HANDLER($EFFECT):
              ...
      - pattern: yield Resume($VALUE)
    options:
      # Count occurrences - warn if more than 1
      # Note: This is a heuristic. Multiple Resume in different branches is OK.
      # Multiple Resume in same execution path is the actual violation.
    message: |
      Handler contains Resume. Ensure each continuation is only resumed ONCE.
      Multiple Resume calls to the same continuation will raise RuntimeError.
      
      OK patterns (different branches):
        if isinstance(effect, Get):
            yield Resume(value1)
        elif isinstance(effect, Put):
            yield Resume(value2)
      
      VIOLATION (same continuation resumed twice):
        result1 = yield Resume(value)
        result2 = yield Resume(value)  # ERROR: already consumed!
    severity: INFO
    metadata:
      category: correctness
      subcategory: one-shot-continuation

  - id: definite-multi-resume-violation
    patterns:
      - pattern-inside: |
          @do
          def $HANDLER($EFFECT):
              ...
      - pattern: |
          $VAR1 = yield Resume($VALUE1)
          ...
          $VAR2 = yield Resume($VALUE2)
    paths:
      include:
        - doeff/cesk/**/*.py
    message: |
      VIOLATION: Multiple Resume calls in sequence without branching.
      This will cause RuntimeError at runtime - continuations are one-shot.
      
      If you need to transform the result, use a single Resume:
        result = yield Resume(value)
        return transform(result)
      
      NOT:
        result1 = yield Resume(value)
        result2 = yield Resume(value)  # CRASH!
    severity: ERROR
```

**What This Catches**:

| Pattern | Caught By | Severity |
|---------|-----------|----------|
| Sequential `yield Resume(...)` calls | Semgrep (definite) | ERROR |
| Multiple `yield Resume(...)` in handler | Semgrep (potential) | INFO |
| Runtime double-resume attempt | Runtime assertion | RuntimeError |

**Note**: Semgrep cannot catch all violations statically (e.g., Resume in a loop, Resume of stored handle). The runtime check is the authoritative enforcement.

### Level 2 Translation Implementation

```python
from doeff.cesk.level2_algebraic_effects.state import (
    DOEFF_INTERNAL_AE,
    AlgebraicEffectsState,
    get_ae_state,
    set_ae_state,
)


def translate_control_primitive(prim: ControlPrimitive, state: CESKState) -> CESKState:
    """Translate Level 2 control primitive to CESK state change.
    
    Reads/writes Level 2 state via AlgebraicEffectsState dataclass.
    Uses per-handler captured_k (two-stack model).
    """
    S = state.S
    ae = get_ae_state(S)
    
    match prim:
        case WithHandler(handler, program):
            # Push handler onto stack and add WithHandlerFrame to K
            # Handler is just the function - Python closures handle lexical scoping
            new_ae = ae.push_handler(handler)
            
            return CESKState(
                C=ProgramControl(program),
                E=state.E,
                S=set_ae_state(S, new_ae),
                K=[WithHandlerFrame()] + state.K,  # Handler scope boundary
            )
        
        case Resume(value, k):
            if k is None:
                # Implicit k - use captured continuation from ACTIVE handler
                handler_idx = ae.active_handler_index
                if handler_idx < 0:
                    raise RuntimeError("Resume without active handler")
                
                captured_k, k_id = ae.get_captured_at(handler_idx)
                if captured_k is None:
                    raise RuntimeError("Resume without captured continuation")
                
                # ONE-SHOT ENFORCEMENT
                if k_id is not None and ae.is_consumed(k_id):
                    raise RuntimeError(
                        f"INVARIANT VIOLATION: Continuation {k_id} already resumed. "
                        "Continuations are one-shot and cannot be resumed multiple times."
                    )
                
                # Mark consumed and clear captured at this handler
                new_ae = ae.clear_captured_at(handler_idx)
                if k_id is not None:
                    new_ae = new_ae.mark_consumed(k_id)
            else:
                # Explicit k - lookup from registry
                captured_k = ae.get_continuation(k._id)
                if captured_k is None:
                    raise RuntimeError(f"Unknown continuation handle: {k._id}")
                
                # ONE-SHOT ENFORCEMENT
                if ae.is_consumed(k._id):
                    raise RuntimeError(
                        f"INVARIANT VIOLATION: Continuation {k._id} already resumed. "
                        "Continuations are one-shot and cannot be resumed multiple times."
                    )
                
                # Mark consumed
                new_ae = ae.mark_consumed(k._id)
            
            # KEY: Concatenate captured_k + handler_k
            # This ensures when resumed code completes, result flows back to handler
            new_k = list(captured_k) + list(state.K)
            
            return CESKState(
                C=Value(value),
                E=state.E,
                S=set_ae_state(S, new_ae),
                K=new_k,
            )
        
        case Abort(value):
            # Abandon the captured continuation - DO NOT resume user code
            handler_idx = ae.active_handler_index
            if handler_idx < 0:
                raise RuntimeError("Abort without active handler")
            
            captured_k, k_id = ae.get_captured_at(handler_idx)
            
            # Clean up abandoned continuation (close generators)
            if captured_k:
                for frame in captured_k:
                    if isinstance(frame, ReturnFrame) and frame.generator:
                        try:
                            frame.generator.close()
                        except Exception:
                            pass  # Best effort cleanup
                warnings.warn(
                    f"Continuation {k_id} abandoned via Abort. "
                    "User code will not be resumed.",
                    stacklevel=2,
                )
            
            # Mark consumed (even though not resumed, it's "used up")
            new_ae = ae.clear_captured_at(handler_idx)
            if k_id is not None:
                new_ae = new_ae.mark_consumed(k_id)
            
            # Continue with handler's K (skip captured_k entirely)
            # Value flows to next frame in handler's continuation
            return CESKState(
                C=Value(value),
                E=state.E,
                S=set_ae_state(S, new_ae),
                K=state.K,  # Handler's K only, NOT captured_k + handler_k
            )
        
        case GetContinuation():
            # Get captured continuation from ACTIVE handler
            handler_idx = ae.active_handler_index
            if handler_idx < 0:
                raise RuntimeError("GetContinuation without active handler")
            
            captured_k, _ = ae.get_captured_at(handler_idx)
            if captured_k is None:
                raise RuntimeError("GetContinuation without captured continuation")
            
            # Store continuation in registry with new ID
            k_id = ae.next_continuation_id
            new_ae = ae.store_continuation(k_id, captured_k)
            new_ae = replace(new_ae, next_continuation_id=k_id + 1)
            
            return CESKState(
                C=Value(ContinuationHandle(_id=k_id)),
                E=state.E,
                S=set_ae_state(S, new_ae),
                K=state.K,
            )
        
        case AskStore():
            # Return the user-visible portion of the store
            # (filter out __doeff_internal_* keys)
            user_store = {k: v for k, v in S.items() 
                         if not k.startswith("__doeff_internal_")}
            return state.with_value(user_store)
        
        case ModifyStore(updates):
            # Only allow modifying user keys (not internal)
            for key in updates:
                if key.startswith("__doeff_internal_"):
                    raise RuntimeError(f"Cannot modify internal key: {key}")
            new_S = {**S, **updates}
            return CESKState(
                C=Value(None),
                E=state.E,
                S=new_S,
                K=state.K,
            )
        
        case AskEnv():
            return state.with_value(state.E)
```

---

## Level 3: User Effects (Translation Layer)

**Module**: `doeff.cesk.level3_user_effects`

Level 3 is NOT a stepper - it's a translation layer that dispatches user effects
to handlers and produces new CESK states.

### Effect Base Class (`doeff.cesk.level3_user_effects.effect_base`)

```python
class EffectBase:
    """Base class for user-defined effects (Level 3).
    
    These go through Level 3 handler dispatch.
    Handlers receive them and use Level 2 primitives to respond.
    
    NOT a ControlPrimitive - these ARE dispatched to handlers.
    """
    pass
```

### Translation Function (`doeff.cesk.level3_user_effects.translate`)

```python
def translate_user_effect(effect: EffectBase, state: CESKState) -> CESKState:
    """Translate user effect to handler invocation.
    
    PRECONDITION: effect is NOT a ControlPrimitive (Level 2 intercepted those)
    
    Uses the two-stack model: captures K into the handler's own captured_k slot,
    not a global field. This allows nested handlers to each maintain their own
    captured continuation (like Koka's evidence vectors).
    
    Search order: innermost first (highest index), moving outward.
    - handler_stack[-1] = innermost
    - handler_stack[0] = outermost
    """
    # INVARIANT: Control primitives must never reach here
    assert not isinstance(effect, ControlPrimitive), \
        f"BUG: Control primitive {effect} leaked to Level 3"
    
    S = state.S
    ae = get_ae_state(S)
    
    # Find handler - search from innermost (END) to outermost
    # If handler is active (forwarding), search from active_handler_index - 1 (outward)
    # Otherwise start from innermost (len - 1)
    if ae.active_handler_index >= 0:
        # Forwarding: search outward from current handler
        start_idx = ae.active_handler_index - 1
    else:
        # Fresh dispatch: start from innermost
        start_idx = len(ae.handler_stack) - 1
    
    if start_idx < 0:
        raise UnhandledEffectError(f"No handler for {type(effect).__name__}")
    
    handler_idx = start_idx
    entry = ae.handler_stack[handler_idx]
    
    # Capture continuation INTO THIS HANDLER'S SLOT (two-stack model)
    # Each handler maintains its own captured_k, enabling nested handler dispatch
    new_ae = ae.capture_continuation_at(
        index=handler_idx,
        k=tuple(state.K),  # Immutable copy
        k_id=ae.next_continuation_id,
    )
    
    # Create handler program by invoking handler function
    handler_program = entry.handler(effect)
    
    return CESKState(
        C=ProgramControl(handler_program),
        E=state.E,  # Handler runs in current E; uses AskEnv() if needed
        S=set_ae_state(S, new_ae),
        K=[],  # Handler starts with empty K
    )
```

### Resume Semantics: K Concatenation

**Key insight**: `Resume(value)` concatenates `captured_k + handler_k`.

```
Before Resume:
  captured_k = [ReturnFrame(user_gen)]     # User's continuation
  handler_k  = [ReturnFrame(handler_gen)]  # Handler's continuation

After Resume:
  K = [ReturnFrame(user_gen), ReturnFrame(handler_gen)]
  C = Value(value)
```

**Flow:**
```
1. User: x = yield Get("key")
   K = [user_gen]
   
2. Handler catches, K captured:
   captured_k = [user_gen]
   K = [handler_gen]
   
3. Handler: result = yield Resume(42)
   K = [user_gen] + [handler_gen] = [user_gen, handler_gen]
   C = Value(42)
   
4. User receives 42, continues, returns 100:
   K = [handler_gen]  (user_gen popped)
   C = Value(100)
   
5. Handler receives 100 (as `result`):
   result = 100
   Handler returns result * 2 = 200
   
6. K = []
   C = Value(200)
   
7. Done! WithHandler result is 200
```

**This enables:**
- Handlers can transform user's final result
- Handlers can log/trace before and after
- Handlers can wrap user computation with setup/teardown

### Effect Forwarding

When handler doesn't handle an effect, it re-yields to forward to outer handlers:

```python
@do
def my_handler(effect):
    if isinstance(effect, MyEffect):
        result = yield Resume(42)
        return result
    else:
        # Forward to outer handler by re-yielding
        outer_result = yield effect
        result = yield Resume(outer_result)
        return result
```

**Key mechanism: `active_handler_index`**

The `AlgebraicEffectsState.active_handler_index` tracks which handler is currently executing.
When a handler yields an effect, Level 3 searches from `active_handler_index + 1`, preventing:
- Infinite recursion (handler calling itself)
- Inner handlers being invoked (only outer handlers searched)

**Flow for forwarding `yield effect`:**
1. Handler[0] running, `active_handler_index = 0`
2. Handler yields `effect` (an EffectBase)
3. Level 3 searches from index 1 (outer handlers only)
4. Handler[0]'s K is captured (so result flows back)
5. Handler[1] handles effect, yields `Resume(value)`
6. Result flows back to Handler[0]
7. Handler[0] receives result, yields `Resume(result)` to user
8. User receives value

**Important**: Search always starts from `active_handler_index + 1` for effects yielded by handlers.

### Design Decision: No Explicit Forward Primitive (For Now)

**Current approach**: Handlers re-yield effects to forward them:

```python
@do
def my_handler(effect):
    if isinstance(effect, MyEffect):
        result = yield Resume(42)
        return result
    else:
        # Forward by re-yielding
        outer_result = yield effect
        result = yield Resume(outer_result)
        return result
```

**How it works**:
- `active_handler_index` tracks which handler is currently running
- When handler yields an effect, Level 3 searches from `active_handler_index + 1`
- Handler's K is captured, result flows back to handler
- Handler then resumes to user

**Alternative considered: Explicit `Forward` primitive**

```python
@dataclass(frozen=True)
class Forward(ControlPrimitive):
    """Forward effect to outer handler (transparent pass-through)."""
    effect: EffectBase
```

Usage would be:
```python
yield Forward(effect)  # Terminal - handler abandoned, result goes directly to user
# unreachable
```

**Koka/OCaml5 semantics**: Forward is transparent - forwarding handler's K is NOT captured, 
result flows directly from outer handler to original user. Forwarding handler is abandoned.

**Comparison**:

| Aspect | Re-yield (current) | Forward primitive |
|--------|-------------------|-------------------|
| Handler gets result back | Yes | No (abandoned) |
| Can transform/log forwarded result | Yes | No |
| Extra Resume hop | Yes | No |
| Matches Koka/OCaml5 | Partial | Yes |
| Implementation complexity | Simpler | More complex |

**Decision**: Use re-yield for now. It's simpler and more flexible (handlers can intercept).
Forward primitive can be added later if transparent pass-through semantics are needed
for performance or Koka/OCaml5 compatibility.

### Handler Invocation with Error Handling

```python
def invoke_handler_with_error_handling(
    handler: Handler, 
    effect: EffectBase,
    state: CESKState,
) -> CESKState:
    """Invoke handler, catching exceptions to auto-resume with error.
    
    If handler raises, we resume the captured continuation with the error.
    This implements the "no explicit ResumeError" design decision.
    Uses per-handler captured_k from the two-stack model.
    """
    ae = get_ae_state(state.S)
    handler_idx = ae.active_handler_index
    
    try:
        handler_program = handler(effect)
        return CESKState(
            C=ProgramControl(handler_program),
            E=state.E,
            S=state.S,
            K=[],
        )
    except Exception as e:
        # Handler raised - auto-resume continuation with error
        # Get captured_k from the active handler's slot
        captured_k, _ = ae.get_captured_at(handler_idx)
        return CESKState(
            C=Error(e),
            E=state.E,
            S=state.S,
            K=list(captured_k) if captured_k else [],
        )
```

### Example User Effects (`doeff.effects.*`)

```python
# doeff/effects/state.py
@dataclass(frozen=True)
class Get(EffectBase):
    """Read from state. Level 3 effect."""
    key: str

@dataclass(frozen=True)
class Put(EffectBase):
    """Write to state. Level 3 effect."""
    key: str
    value: Any


# doeff/effects/reader.py
@dataclass(frozen=True)
class Ask(EffectBase):
    """Read from environment. Level 3 effect."""
    key: str


# doeff/effects/spawn.py
@dataclass(frozen=True)
class Spawn(EffectBase):
    """Spawn a new task. Level 3 effect."""
    program: Program[Any]
```

---

## Handler Example

Handlers receive Level 3 effects and use Level 2 primitives to respond:

```python
# doeff/cesk/handlers/state_handler.py
from doeff.cesk.level2_algebraic_effects import Resume, AskStore, ModifyStore
from doeff.cesk.level3_user_effects import EffectBase
from doeff.effects.state import Get, Put

@do
def state_handler(effect: EffectBase):
    """Handler for Get/Put effects.
    
    Receives: Level 3 effects (Get, Put)
    Uses: Level 2 primitives (AskStore, ModifyStore, Resume)
    """
    if isinstance(effect, Get):
        store = yield AskStore()                    # Level 2
        if effect.key not in store:
            raise KeyError(effect.key)              # Auto-resume with error
        result = yield Resume(store[effect.key])   # Level 2
        return result
    
    if isinstance(effect, Put):
        yield ModifyStore({effect.key: effect.value})  # Level 2
        result = yield Resume(None)                     # Level 2
        return result
    
    # Forward unhandled effects to outer handler
    outer_result = yield effect                    # Level 3 (recursive)
    result = yield Resume(outer_result)            # Level 2
    return result
```

---

## Main Run Loop (`doeff.cesk.run`)

The main loop orchestrates all levels:

```python
from doeff.cesk.level1_cesk import cesk_step, CESKState, Done, Failed, EffectYield
from doeff.cesk.level2_algebraic_effects import ControlPrimitive, translate_control_primitive
from doeff.cesk.level3_user_effects import EffectBase, translate_user_effect


def run(program: Program[T]) -> T:
    """Main interpreter loop.
    
    Level 2: level2_step wraps Level 1, handles WithHandlerFrame + ControlPrimitive
    Level 3: translate_user_effect for user effects
    """
    state = initial_state(program)
    
    while True:
        # Level 2 step (wraps Level 1)
        result = level2_step(state)
        
        # Terminal states
        if isinstance(result, Done):
            return result.value
        if isinstance(result, Failed):
            raise result.error
        
        # Level 3: User effects
        if isinstance(result.C, EffectYield):
            yielded = result.C.yielded
            if isinstance(yielded, EffectBase):
                result = translate_user_effect(yielded, result)
            else:
                raise TypeError(f"Unknown yield type: {type(yielded)}")
        
        state = result

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
│  Level 1: CESK Machine (cesk_step) - THE ONLY STEPPER               │
│  Module: doeff.cesk.level1_cesk                                     │
│  State: C, E, S, K (pure, no effect knowledge)                      │
│  Frames: ReturnFrame only                                           │
│  Produces: Value, EffectYield, Error, Done, Failed                  │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ EffectYield
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Level 2: Algebraic Effects (TRANSLATION LAYER)                     │
│  Module: doeff.cesk.level2_algebraic_effects                        │
│  Function: translate_control_primitive()                            │
│  State: AlgebraicEffectsState in S["__doeff_internal_ae__"]         │
│                                                                     │
│  INTERCEPTS: ControlPrimitive (WithHandler, Resume, GetCont...)     │
│  PASSES: EffectBase to Level 3                                      │
│                                                                     │
│  INVARIANT: ControlPrimitive NEVER reaches Level 3                  │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ EffectBase
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Level 3: User Effects (TRANSLATION LAYER)                          │
│  Module: doeff.cesk.level3_user_effects                             │
│  Function: translate_user_effect()                                  │
│                                                                     │
│  - Finds handler in AlgebraicEffectsState.handler_stack             │
│  - Invokes handler with effect                                      │
│  - Handler uses Level 2 primitives to respond                       │
└─────────────────────────────────────────────────────────────────────┘
```

**Key Architecture Points:**

1. **Level 2 wraps Level 1**: `level2_step()` handles WithHandlerFrame before/ControlPrimitive after `cesk_step()`
2. **Two frame types in K**: `ReturnFrame` (Level 1) and `WithHandlerFrame` (Level 2)
3. **Shared Store**: Level 2 stores `AlgebraicEffectsState` in Level 1's Store under single key
4. **Immutable State**: `AlgebraicEffectsState` is a frozen dataclass with update methods
5. **Handler = Function**: Handler is `Callable[[EffectBase], Program[Any]]`, Python closures handle scoping
6. **Two-Stack Model**: Level 2 maintains `handler_stack` parallel to K's `WithHandlerFrame`s
7. **Per-Handler captured_k**: Each `HandlerEntry` holds its own captured continuation, enabling nested handler dispatch

**Frame Ownership:**

| Frame Type | Owned By | Handles |
|------------|----------|---------|
| `ReturnFrame` | Level 1 (`cesk_step`) | Send value to generator |
| `WithHandlerFrame` | Level 2 (`level2_step`) | Pop handler on completion |

**Invariants (enforced by runtime assertions + semgrep):**

1. **Level 1 never processes WHF at K[0]** - Level 2 intercepts WithHandlerFrame before Level 1 sees it
2. **ControlPrimitive never reaches Level 3** - intercepted by Level 2
3. **Internal store key is private** - user code cannot access `__doeff_internal_ae__`
4. **One-shot continuations** - each continuation can only be resumed ONCE
5. **Paired push/pop** - Each WithHandler creates (HandlerEntry, WHF) pair, popped together
6. **Index stability** - Push/pop at END keeps `active_handler_index` valid across nested handlers

---

## Implementation Plan

See **[ISSUE-CESK-006-implementation.md](../../issues/ISSUE-CESK-006-implementation.md)** for the detailed phased implementation plan.

### Phase Overview

| Phase | Focus | Key Deliverable |
|-------|-------|-----------------|
| 1 | Foundation | Module structure, all types |
| 2 | Level 1 | `cesk_step()` - pure CESK |
| 3 | Level 2 (install) | `level2_step()`, WithHandler |
| 4 | Level 2 (resume) | Resume, Abort, one-shot |
| 5 | Level 3 | `translate_user_effect()` |
| 6 | Integration | `run()`, basic handlers |
| 7 | Edge cases | Nested handlers, errors |
| 8 | Migration | Handler migration, cleanup |
| 9 | Polish | Semgrep, docs |

### Target Module Structure

```
doeff/cesk/
├── level1_cesk/
│   ├── __init__.py
│   ├── state.py          # CESKState, Value, Error, EffectYield, ProgramControl, Done, Failed
│   ├── frames.py         # ReturnFrame, WithHandlerFrame
│   ├── step.py           # cesk_step()
│   └── types.py          # Kontinuation, Environment, Store type aliases
│
├── level2_algebraic_effects/
│   ├── __init__.py
│   ├── state.py          # AlgebraicEffectsState, HandlerEntry, DOEFF_INTERNAL_AE
│   ├── primitives.py     # ControlPrimitive, WithHandler, Resume, Abort, etc.
│   ├── step.py           # level2_step()
│   ├── translate.py      # translate_control_primitive()
│   └── handle.py         # ContinuationHandle, Handler type alias
│
├── level3_user_effects/
│   ├── __init__.py
│   ├── base.py           # EffectBase
│   └── translate.py      # translate_user_effect()
│
├── run.py                # Main loop: run()
└── errors.py             # UnhandledEffectError, etc.
```

---

## Success Criteria

1. **Module clarity**: Each class lives in the correct level module
2. **One stepper**: Only `cesk_step()` exists; no `algebraic_effects_step()` or `user_effects_step()`
3. **Translation functions**: `translate_control_primitive()` and `translate_user_effect()`
4. **Immutable state dataclass**: `AlgebraicEffectsState` in `S["__doeff_internal_ae__"]`
5. **Handler = function**: `Handler = Callable[[EffectBase], Program[Any]]`, no wrapper class
6. **No hacks**: No `DirectState`, no `ResumeK`, no `ctx.k`, no `HandlerCtx`
7. **Tests pass**: All existing functionality preserved

**Two-Stack Model:**

8. **Per-handler captured_k**: 
   - `HandlerEntry` contains `captured_k` and `captured_k_id`
   - `handler_stack: tuple[HandlerEntry, ...]` (not `tuple[Handler, ...]`)
   - Each handler invocation maintains its own captured continuation
   - Enables nested handler dispatch (handler yielding WithHandler)

9. **Stack coordination** (relaxed, not strict correspondence):
   - Each `WithHandler` creates paired (HandlerEntry, WHF) - they get popped together
   - Push/pop at END of handler_stack for index stability
   - `active_handler_index` remains valid across nested handler pushes
   - Note: During dispatch, K is cleared so count(WHF in K) != len(handler_stack) temporarily

**Invariant Enforcement:**

10. **Level 1 never processes WHF at K[0]**: 
    - Runtime: assert in `cesk_step()` that K[0] is `ReturnFrame` when processing
    - Level 2 intercepts `WithHandlerFrame` before delegating to Level 1
    - Note: K CAN contain `WithHandlerFrame` deeper in the stack
   
11. **ControlPrimitive never reaches Level 3**:
    - Runtime: `assert not isinstance(effect, ControlPrimitive)` in Level 3
    - Semgrep: Block ControlPrimitive in handler dispatch paths
   
12. **Internal store key private**:
    - Runtime: `AskStore()` filters out `__doeff_internal_*` keys
    - Semgrep: Block direct access to `__doeff_internal_ae__` outside Level 2

13. **One-shot continuations**:
    - Runtime: Track in `AlgebraicEffectsState.consumed_continuations`
    - Runtime: `Resume` raises `RuntimeError` if continuation already consumed
    - Semgrep: Warn on multiple `yield Resume(...)` in same handler (heuristic)
    - Semgrep: Error on sequential `yield Resume(...)` calls (definite violation)

---

## Testing Structure

Tests are organized by layer with dedicated fixtures per layer. No mocking between layers - Level 1 is pure and deterministic, so Level 2/3 tests use the real Level 1 implementation.

### Directory Structure

```
tests/cesk/
├── conftest.py                     # Shared across all cesk tests
│
├── level1_cesk/
│   ├── conftest.py                 # Level 1 fixtures
│   ├── test_cesk_step.py           # cesk_step() transitions
│   ├── test_return_frame.py        # ReturnFrame behavior
│   ├── test_generator_protocol.py  # next/send/throw mechanics
│   └── test_terminal_states.py     # Done/Failed conditions
│
├── level2_algebraic_effects/
│   ├── conftest.py                 # Level 2 fixtures
│   ├── test_with_handler_frame.py  # WithHandlerFrame lifecycle
│   ├── test_control_primitives.py  # WithHandler, Resume, GetContinuation
│   ├── test_one_shot.py            # One-shot continuation enforcement
│   ├── test_ae_state.py            # AlgebraicEffectsState immutability
│   └── test_level2_step.py         # level2_step() wrapping behavior
│
├── level3_user_effects/
│   ├── conftest.py                 # Level 3 fixtures
│   ├── test_handler_dispatch.py    # Finding/invoking handlers
│   ├── test_effect_forwarding.py   # active_handler_index mechanics
│   └── test_handler_errors.py      # Auto-resume with error
│
├── integration/
│   ├── conftest.py
│   ├── test_full_stack.py          # End-to-end effect handling
│   ├── test_nested_handlers.py     # Handler composition
│   ├── test_handler_yields_with_handler.py  # Handler installing nested handlers
│   ├── test_resume_flow.py         # K concatenation semantics
│   └── test_handler_transforms.py  # Handlers transforming results
│
├── invariants/
│   ├── conftest.py
│   ├── test_l1_never_sees_whf.py   # Level 1 never processes WHF at K[0]
│   ├── test_control_primitive_intercept.py  # ControlPrimitive never reaches L3
│   ├── test_internal_key_private.py # __doeff_internal_ae__ hidden
│   ├── test_one_shot_violations.py  # Double-resume raises RuntimeError
│   ├── test_paired_push_pop.py     # HandlerEntry and WHF popped together
│   └── test_index_stability.py     # active_handler_index valid across nested pushes
│
└── migration/
    ├── conftest.py
    ├── test_existing_handlers.py   # Current handlers still work
    └── test_backward_compat.py     # Public API unchanged
```

### Testing Philosophy

| Layer | Testing Approach | Isolation |
|-------|------------------|-----------|
| **Level 1** | Pure unit tests | Fully isolated - no effects knowledge |
| **Level 2** | Unit tests with real Level 1 | Uses real `cesk_step()` |
| **Level 3** | Unit tests with real Level 1+2 | Uses real `level2_step()` |
| **Integration** | Full stack tests | No mocks, verify end behavior |
| **Invariants** | Adversarial + property-based | Try to break invariants |

### Corrected Invariants

**Important**: K CAN contain `WithHandlerFrame` - it marks handler scope boundaries deeper in the stack.

The correct invariant is: **Level 1 never processes `WithHandlerFrame` at `K[0]`**.

```
User code running inside handler scope:
  K = [ReturnFrame(user_gen), WithHandlerFrame, ReturnFrame(outer_gen)]
                              ↑
                              Handler boundary (valid, deeper in stack)

When Value reaches WithHandlerFrame:
  C = Value(result)
  K = [WithHandlerFrame, ReturnFrame(outer_gen)]
       ↑
       Level 2 intercepts BEFORE cesk_step sees it
```

### Key Test Cases

#### Level 1 Tests

```python
# test_cesk_step.py
def test_program_control_starts_generator():
    """ProgramControl → EffectYield + ReturnFrame pushed"""
    
def test_value_sends_to_return_frame():
    """Value + ReturnFrame → send() to generator"""
    
def test_value_empty_k_is_done():
    """Value + [] → Done(value)"""
    
def test_error_throws_to_return_frame():
    """Error + ReturnFrame → throw() to generator"""

def test_asserts_on_non_return_frame():
    """Assert if K[0] is not ReturnFrame when processing"""
```

#### Level 2 Tests

```python
# test_level2_step.py
def test_intercepts_whf_before_level1():
    """Value + WithHandlerFrame → pop handler, cesk_step NOT called"""
    
def test_delegates_return_frame_to_level1():
    """Value + ReturnFrame → cesk_step called"""
    
def test_translates_control_primitive():
    """EffectYield(ControlPrimitive) → translate, not passed to L3"""

# test_one_shot.py
def test_resume_marks_consumed():
    """Resume adds k_id to consumed_continuations"""
    
def test_double_resume_raises():
    """Second Resume with same k_id → RuntimeError"""
```

#### Level 3 Tests

```python
# test_handler_dispatch.py
def test_finds_handler_in_stack():
    """Effect → handler_stack[0] invoked"""
    
def test_captures_continuation_per_handler():
    """K captured in handler_stack[i].captured_k, not global field"""

# test_effect_forwarding.py  
def test_active_handler_index_increments():
    """Handler yields effect → search from index+1"""
```

#### Integration Tests: Handler Yields WithHandler (Two-Stack Model)

```python
# test_handler_yields_with_handler.py

def test_handler_installs_nested_handler():
    """Handler can yield WithHandler to install nested handler.
    
    outer_handler handles MyEffect, installs inner_handler for sub-program,
    then resumes user with the result.
    """
    @do
    def inner_handler(effect):
        if isinstance(effect, InnerEffect):
            return (yield Resume(100))
        outer = yield effect
        return (yield Resume(outer))

    @do
    def outer_handler(effect):
        if isinstance(effect, MyEffect):
            @do
            def nested():
                return (yield InnerEffect())
            
            result = yield WithHandler(inner_handler, nested())
            return (yield Resume(result))  # Resume ORIGINAL user
        ...

    @do
    def user_code():
        x = yield MyEffect()
        return x + 1
    
    # Expected: user_code receives 100, returns 101
    result = run(WithHandler(outer_handler, user_code()))
    assert result == 101


def test_nested_handler_does_not_overwrite_outer_captured_k():
    """Per-handler captured_k prevents outer continuation loss.
    
    When inner_handler dispatches, it stores captured_k in handler_stack[0].
    outer_handler's captured_k remains in handler_stack[1], untouched.
    """
    @do
    def inner_handler(effect):
        if isinstance(effect, InnerEffect):
            # This captures nested()'s K into handler_stack[0].captured_k
            return (yield Resume("inner_result"))
        outer = yield effect
        return (yield Resume(outer))

    @do
    def outer_handler(effect):
        if isinstance(effect, MyEffect):
            @do
            def nested():
                return (yield InnerEffect())
            
            inner_result = yield WithHandler(inner_handler, nested())
            # handler_stack[1].captured_k still has user's K
            user_result = yield Resume(inner_result)
            return user_result
        ...

    @do
    def user_code():
        x = yield MyEffect()
        return f"user got {x}"
    
    result = run(WithHandler(outer_handler, user_code()))
    assert result == "user got inner_result"


def test_deeply_nested_handlers():
    """Three levels of nested handlers, each with its own captured_k."""
    # handler_stack[0] → innermost, has nested2's K
    # handler_stack[1] → middle, has nested1's K  
    # handler_stack[2] → outermost, has user's K
    ...
```

### Property-Based Tests (Hypothesis)

```python
# test_l1_never_sees_whf.py
from hypothesis import given, strategies as st

@given(k_stack=st.lists(st.sampled_from([
    ReturnFrame(mock_gen()),
    WithHandlerFrame(),
])))
def test_level2_always_intercepts_whf_before_level1(k_stack):
    """Property: If K[0] is WithHandlerFrame and C is Value,
    level2_step handles it without calling cesk_step."""
    if k_stack and isinstance(k_stack[0], WithHandlerFrame):
        state = CESKState(C=Value(42), E={}, S={}, K=k_stack)
        result = level2_step(state)
        # Verify WHF was popped
        assert not (result.K and isinstance(result.K[0], WithHandlerFrame))


# test_internal_key_private.py
@given(user_keys=st.dictionaries(
    keys=st.text().filter(lambda k: not k.startswith("__doeff_internal_")),
    values=st.integers()
))
def test_ask_store_never_exposes_internal_keys(user_keys):
    """Property: AskStore() result never contains __doeff_internal_* keys."""
    internal_state = AlgebraicEffectsState()
    store = {**user_keys, DOEFF_INTERNAL_AE: internal_state}
    
    result = translate_ask_store(store)
    
    assert DOEFF_INTERNAL_AE not in result
    assert all(not k.startswith("__doeff_internal_") for k in result)
```

---

## References

### Language Implementations

- [Koka Language](https://koka-lang.github.io/) - Evidence-passing, multi-prompt delimited control
- [Koka Book - Section 3.4.8](https://koka-lang.github.io/koka/doc/book.html#sec-overriding-handlers) - Handlers installing nested handlers
- [OCaml 5 Effects](https://ocaml.org/manual/effects.html) - Fiber-based stack switching
- [OCaml 5 Manual - Section 24.5](https://v2.ocaml.org/manual/effects.html#s:effects-nesting) - Nested effect handlers

### Academic Papers

- [Generalized Evidence Passing for Effect Handlers](https://www.microsoft.com/en-us/research/publication/generalized-evidence-passing-for-effect-handlers/) (Xie & Leijen, 2021) - Koka's compilation strategy
- [Effect Handlers via Generalised Continuations](https://homepages.inf.ed.ac.uk/slindley/papers/ehgc.pdf) (Hillerström et al., 2020) - Deep, shallow, parameterized handlers
- [Handlers of Algebraic Effects](https://www.eff-lang.org/handlers-tutorial.pdf) (Pretnar, 2015) - Tutorial on algebraic effects

### Key Insights from Research

| System | Continuation Management | Nested Handlers |
|--------|------------------------|-----------------|
| **Koka** | Evidence vectors - each prompt has own state | ✓ Fully supported |
| **OCaml5** | Fibers - linked list of stack segments | ✓ Fully supported |
| **doeff** | Per-handler captured_k in handler_stack | ✓ Supported via two-stack model |

Both Koka and OCaml5 allow handlers to:
1. Perform effects (handled by outer handlers)
2. Install nested handlers before resuming

This informed our decision for per-handler `captured_k` (ADR-3).

### Internal Specs

- SPEC-CESK-001: Separation of Concerns
- SPEC-CESK-003: Minimal Frame Architecture
- SPEC-CESK-005: Simplify PythonAsyncSyntaxEscape
