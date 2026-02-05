# SPEC-CESK-007: Segment-Based Continuation Architecture

## Status: Draft

## Summary

This spec defines the **Segment-based continuation architecture** for doeff's algebraic effects system, inspired by OCaml 5's fiber model and Koka's evidence-passing approach.

**Key insight**: Separate **continuation structure** (Segments) from **effect semantics** (handler registry, dispatch context). This enables clean implementation of call-resume, tail-transfer, and cooperative scheduling.

```
Level 1: Pure CESK (unchanged)
    └── ReturnFrame, generator management

Level 2: Segment-Based Effects (NEW)
    ├── Segment (pure continuation structure)
    ├── Handler Registry (effect semantics)
    ├── Dispatch Stack (dispatch state)
    └── VM Primitives (Resume, Transfer, Delegate, etc.)
```

## Background

### OCaml 5: Fiber Chain

OCaml 5 uses **fiber-based stack switching**:
- Each `match_with` creates a new fiber (stack segment)
- Fibers form a parent chain via `stack_handler.parent`
- `reperform` walks up the parent chain
- `continue k v` switches to resumed fiber, current becomes parent

### Koka: Evidence Vector + Yield Bubbling

Koka uses **evidence-passing**:
- Evidence vector tracks available handlers
- Operations "bubble up" through the stack
- Continuations composed during bubbling
- Marker-based handler matching

### Key Insight

Both languages separate:
1. **Continuation structure** (fibers/segments)
2. **Handler lookup** (evidence vector/parent chain)
3. **Value flow** (caller/return chain)

---

## Core Design Principles

### Principle 1: Segment = Delimited Continuation Frame

Segment represents a **delimited continuation frame** (following Felleisen, Danvy & Filinski):
- Frames (K) - the continuation "inside" this prompt
- Caller link (who to return to)
- Marker (prompt identity) - identifies the delimiting prompt
- scope_chain (evidence vector snapshot) - handler scope at creation time

**Clarification on "pure"**: The marker IS prompt identity, which is structural in delimited 
continuation theory. This is separate from handler *implementations* (which live in registry).
The marker determines WHERE the continuation is delimited, not WHAT effects are handled.

Handler implementations are externalized:
- ~~handler~~ → Handler Registry
- ~~resumed/forwarded~~ → Dispatch Context

### Principle 2: Three Distinct Contexts

Following OCaml 5 and Koka, we distinguish three contexts:

| Context | What it is | Tracked by |
|---------|------------|------------|
| User code location | Where effect was performed | `k_user.segment` |
| Handler scope boundary | Where WithHandler was installed | **PromptSegment** |
| Handler execution | Where handler code runs | `handler_seg` |

**Key insight**: `Segment.caller` is the return path. `Segment.scope_chain` tracks handler scope.
These are DIFFERENT and must be managed separately:
- `caller` = "who do I return my value to?"
- `scope_chain` = "what handlers are in scope for my effects?"

**PromptSegment**: A special segment marking the WithHandler boundary. Handler returns HERE
(not to user code), enabling correct abandon semantics.

### Principle 3: Explicit Continuations

Handlers receive continuations explicitly:

```python
def handler(effect: E, k: Continuation) -> Program[R]:
    # k = explicit continuation to resume
    result = yield Resume(k, some_value)
    return result
```

This matches OCaml's `continue k v` model.

### Principle 4: Immutability

All data structures are immutable (frozen dataclasses) except:
- Generator internal state (unavoidable in Python)

---

## Data Structures

### Segment (Delimited Continuation Frame)

```python
@dataclass(frozen=True)
class Segment:
    """Delimited continuation frame.
    
    Represents a continuation delimited by a prompt (marker).
    
    Attributes:
        marker: Prompt identity (delimiting prompt for this segment)
        K: Level 1 frames (ReturnFrames) in this segment
        caller: Caller link - who to return value to
        scope_chain: Evidence vector snapshot - handlers in scope at creation time
    """
    marker: int
    K: tuple[Frame, ...]
    caller: Segment | None
    scope_chain: tuple[int, ...]  # Captured at creation, restored on resume
```

### PromptSegment (WithHandler Boundary)

```python
@dataclass(frozen=True)
class PromptSegment(Segment):
    """Special segment marking a WithHandler boundary.
    
    When a handler completes (with or without Resume), control returns HERE,
    not to user code. This enables correct abandon semantics:
    - Abandon = handler returns without Resume → value flows to PromptSegment.caller
    - Resume = handler explicitly invokes k_user → value flows to user, then back
    
    Attributes:
        is_prompt_boundary: Always True (marker for isinstance checks)
        handled_marker: Which prompt this boundary delimits
    """
    is_prompt_boundary: bool = True
    handled_marker: int = 0  # The marker of the handler installed here
```

### Continuation (Explicit, Subject to One-Shot)

```python
@dataclass(frozen=True)
class Continuation:
    """Capturable continuation (subject to one-shot check).
    
    Used for: Resume, Transfer, ResumeThenTransfer targets.
    
    Attributes:
        cont_id: Unique identifier for one-shot tracking
        segment: The continuation segment (includes scope_chain)
        dispatch_id: Which dispatch created this (for completion detection)
    
    Note: scope_chain is now stored IN segment, not separately.
    This enables structural restoration on resume.
    """
    cont_id: int
    segment: Segment
    dispatch_id: int | None = None  # Set for callsite continuations
```

### RunnableContinuation (Ready to Execute) — INTERNAL

```python
@dataclass(frozen=True)
class RunnableContinuation:
    """Ready-to-run continuation. INTERNAL - not user accessible.
    
    Created by ResumeThenTransfer for scheduler queues.
    
    IMPORTANT: This type is internal to the VM. User code should never
    construct or duplicate RunnableContinuation directly. The scheduler
    handler receives these from ResumeThenTransfer and executes them.
    
    Attributes:
        runnable_id: Unique identifier for execution tracking
        segment: The continuation segment (includes scope_chain)
        pending_value: Value to deliver when executed
    
    One-shot enforcement:
        Tracked via S["consumed_runnable_ids"]. Double-execute raises error.
        This prevents scheduler bugs from accidentally running same task twice.
    """
    runnable_id: int
    segment: Segment  # scope_chain is in segment
    pending_value: Any
```

### DispatchContext (Per-Effect Dispatch State)

```python
@dataclass(frozen=True)
class DispatchContext:
    """Tracks state of a specific effect dispatch.
    
    Stored in dispatch_stack (not in Segment).
    
    Attributes:
        dispatch_id: Unique identifier
        effect: The effect being dispatched
        handler_chain: Snapshot of handler markers [innermost, ..., outermost]
        handler_idx: Current position (0 = innermost)
        callsite_cont_id: cont_id of k_user (for completion detection)
        completed: Marked True when Resume targets callsite (lazy pop)
    """
    dispatch_id: int
    effect: EffectBase
    handler_chain: tuple[int, ...]  # [innermost, ..., outermost]
    handler_idx: int
    callsite_cont_id: int
    completed: bool = False
```

### VMState

```python
@dataclass(frozen=True)
class VMState:
    """Level 2 VM state.
    
    Attributes:
        C: Current control (Value, ProgramControl, EffectYield, etc.)
        E: Environment (immutable)
        S: Store containing:
            - "handlers": Mapping[int, Handler]  # marker -> handler (intern table)
            - "consumed_cont_ids": frozenset[int]  # one-shot tracking
            - "consumed_runnable_ids": frozenset[int]  # runnable one-shot tracking
            - "dispatch_stack": tuple[DispatchContext, ...]  # dispatch state
        segment: Current segment (includes scope_chain)
    
    Note on scope_chain:
        scope_chain is stored IN segment.scope_chain, not as separate VMState field.
        This enables structural restoration: resuming a segment automatically
        restores its scope_chain. Current scope is `segment.scope_chain`.
    
    Note on handler registry:
        S["handlers"] is an intern table - markers are never reused, entries
        never removed. For production use with dynamic handler creation,
        implementations SHOULD use weak references or explicit deregistration.
    """
    C: Control
    E: Environment
    S: Store
    segment: Segment
    
    @property
    def scope_chain(self) -> tuple[int, ...]:
        """Current scope_chain is segment's scope_chain."""
        return self.segment.scope_chain
```

---

## Handler Signature

Handlers receive effect AND explicit continuation:

```python
Handler = Callable[[EffectBase, Continuation], Program[Any]]

@do
def my_handler(effect: MyEffect, k: Continuation) -> Program[Any]:
    """
    effect: The effect being handled
    k: Continuation to resume (explicit target)
    """
    # Option 1: Resume the continuation
    result = yield Resume(k, some_value)
    return result
    
    # Option 2: Delegate to outer handler
    outer_result = yield Delegate(effect)
    return (yield Resume(k, outer_result + 1))
    
    # Option 3: Abandon (don't resume)
    return default_value
```

---

## Handler Chain Ordering

**Fixed convention**: `[innermost, ..., outermost]`

```
handler_chain[0] = innermost handler (closest to user)
handler_chain[1] = next outer
...
handler_chain[N-1] = outermost handler

handler_idx:
    Current handler index, starting from innermost (0)
    
Delegate:
    next_idx = handler_idx + 1  # Move toward outer
    if next_idx >= len(handler_chain):
        raise UnhandledEffectError
```

---

## VM Primitives

### Primitive 1: Resume(k, v) — Call-Resume

**Semantics**: Send v to k, run callee, receive return value.

```
─────────────────────────────────────────────────────────────
Precondition:
    k.cont_id not in S["consumed_cont_ids"]
    segment = caller_seg  # Current segment is the caller

Semantics:
    1. Lazy pop completed dispatch contexts first (stabilize top):
       while dispatch_stack and dispatch_stack[0].completed:
           dispatch_stack = dispatch_stack[1:]
    
    2. Mark k.cont_id as consumed:
       S' = S | {"consumed_cont_ids": S["consumed_cont_ids"] | {k.cont_id}}
    
    3. Attach caller link to target segment:
       callee_seg = Segment(
           marker = k.segment.marker,
           K = k.segment.K,
           caller = caller_seg,  # Caller link attached
       )
    
    4. Check dispatch completion (BOTH cont_id AND dispatch_id must match):
       # Only mark completed if:
       # - dispatch_stack is not empty (else this Resume is not part of dispatch)
       # - k.dispatch_id matches TOP's dispatch_id (structural match)
       # - k.cont_id matches TOP's callsite_cont_id (identity match)
       if (dispatch_stack and 
           k.dispatch_id is not None and
           k.dispatch_id == dispatch_stack[0].dispatch_id and
           k.cont_id == dispatch_stack[0].callsite_cont_id):
           dispatch_stack[0] = dispatch_stack[0].with_completed(True)
       # If matches fail, do nothing (Resume to non-callsite or scheduler resume)
    
    5. Switch to callee (scope_chain is IN segment):
       C = Value(v)
       segment = callee_seg  # segment.scope_chain is restored automatically
       S = S'

Note:
    This is NOT "copying" k.segment. It's creating execution context
    with caller link. Identity is managed by cont_id.
    
    The dispatch_id + cont_id double-check prevents accidental matches
    in scheduler scenarios where continuations are stored and resumed
    outside the normal dispatch flow.
    
    scope_chain restoration is automatic: segment carries its scope_chain.
─────────────────────────────────────────────────────────────
```

### Primitive 2: Transfer(k, v) — Tail-Transfer (Non-Returning)

**Semantics**: Jump to k with v, abandon current continuation. Does not return.

```
─────────────────────────────────────────────────────────────
Precondition:
    k.cont_id not in S["consumed_cont_ids"]

Semantics:
    1. Mark k.cont_id as consumed
    
    2. Cleanup abandoned caller chain (best-effort):
       close_chain(current_segment)
    
    3. Switch to target (no caller link - non-returning):
       target_seg = Segment(
           marker = k.segment.marker,
           K = k.segment.K,
           caller = None,  # No one to return to
           scope_chain = k.segment.scope_chain,  # Preserved from captured segment
       )
    
    4. C = Value(v)
       segment = target_seg  # scope_chain is IN segment

Note:
    Transfer does NOT return. The current continuation is abandoned.
    Use for scheduler context switches.
    
    scope_chain is preserved IN the segment, so Transfer automatically
    restores the correct handler scope for the transferred-to code.
─────────────────────────────────────────────────────────────
```

### Primitive 3: ResumeThenTransfer(k_return, v, k_next) — Atomic Return-and-Switch

**Semantics**: Atomically deliver v to k_return, then transfer to k_next. For scheduler fairness.

```
─────────────────────────────────────────────────────────────
Precondition:
    k_return.cont_id not in S["consumed_cont_ids"]

Semantics:
    1. Mark k_return.cont_id as consumed
    
    2. Create RunnableContinuation for k_return:
       delivered_seg = Segment(
           marker = k_return.segment.marker,
           K = k_return.segment.K,
           caller = None,  # Scheduler is NOT the caller (tail)
           scope_chain = k_return.segment.scope_chain,  # Preserved in segment
       )
       runnable = RunnableContinuation(
           runnable_id = fresh_id(),  # For one-shot tracking
           segment = delivered_seg,   # scope_chain is IN segment
           pending_value = v,
       )
       # Scheduler stores runnable in its queue (handler's job)
    
    3. Cleanup scheduler's caller chain (best-effort):
       close_chain(current_segment)
    
    4. Switch to k_next:
       next_seg = Segment(
           marker = k_next.segment.marker,
           K = k_next.segment.K,
           caller = None,  # Fresh execution context
           scope_chain = k_next.segment.scope_chain,  # Preserved in segment
       )
    
    5. C = Value(())  # or specific value for k_next
       segment = next_seg  # scope_chain restored via segment

Atomic guarantee:
    - No handler code between deliver and switch
    - Scheduler handler does not remain in call chain

Note:
    k_next.cont_id is NOT consumed here. Task continuations
    regenerate on each Yield.
    
    scope_chain is preserved IN each segment, so both k_return
    and k_next automatically restore correct handler scope.
─────────────────────────────────────────────────────────────
```

### Primitive 4: WithHandler(handler, program) — Install Handler

**Semantics**: Install handler in registry, create PromptSegment boundary, run program.

```
─────────────────────────────────────────────────────────────
Semantics:
    1. Generate fresh marker:
       new_marker = fresh_marker()
    
    2. Register handler (intern table):
       S' = S | {"handlers": S["handlers"] | {new_marker: handler}}
    
    3. Build new scope_chain (innermost position):
       new_scope_chain = (new_marker,) + current_segment.scope_chain
    
    4. Create PromptSegment (handler boundary):
       # This is WHERE handler returns on completion (abandon or after resume)
       prompt_seg = PromptSegment(
           marker = fresh_marker(),  # Separate from handler marker
           K = (),
           caller = current_segment,       # Return to invoker on completion
           scope_chain = current_segment.scope_chain,  # OUTER scope
           is_prompt_boundary = True,
           handled_marker = new_marker,
       )
    
    5. Create body segment (runs user program):
       body_seg = Segment(
           marker = new_marker,
           K = (),  # Will add RF(program_gen) when started
           caller = prompt_seg,            # Returns to prompt boundary
           scope_chain = new_scope_chain,  # Handler is in scope
       )
    
    6. Start program:
       C = ProgramControl(program)
       segment = body_seg
       S = S'

Handler Return Path (IMPORTANT):
    - Program completes → returns to prompt_seg (via caller)
    - Handler completes (after Resume/abandon) → returns to prompt_seg
    - prompt_seg completes → returns to original invoker
    
    This ensures abandon semantics are correct:
    - Handler returns without Resume → value flows to prompt_seg → invoker
    - User code is NEVER implicitly resumed
─────────────────────────────────────────────────────────────
```

### Primitive 5: Delegate(effect) — Delegate to Outer Handler

**Semantics**: Pause current handler, let outer handle, receive outer's Resume value.

```
─────────────────────────────────────────────────────────────
Precondition:
    dispatch_stack not empty (after lazy pop of completed)

Semantics:
    1. Lazy pop completed dispatch contexts:
       while dispatch_stack and dispatch_stack[0].completed:
           dispatch_stack = dispatch_stack[1:]
    
    2. Capture inner's continuation (scope_chain is IN segment):
       k_inner = Continuation(
           cont_id = fresh_id(),
           segment = current_segment,  # Includes scope_chain
           dispatch_id = None,  # Delegate continuation, not callsite
       )
    
    3. Find outer handler via dispatch context:
       top = dispatch_stack[0]
       next_idx = top.handler_idx + 1
       if next_idx >= len(top.handler_chain):
           raise UnhandledEffectError(effect)
       
       outer_marker = top.handler_chain[next_idx]
       outer_handler = S["handlers"][outer_marker]
    
    4. Update dispatch context:
       new_top = DispatchContext(
           dispatch_id = top.dispatch_id,
           effect = effect,
           handler_chain = top.handler_chain,
           handler_idx = next_idx,
           callsite_cont_id = top.callsite_cont_id,
           completed = False,
       )
       dispatch_stack = (new_top,) + dispatch_stack[1:]
    
    5. Find PromptSegment for outer handler:
       outer_prompt_seg = find_prompt_segment(outer_marker, current_segment)
    
    6. Create outer's execution segment:
       outer_seg = Segment(
           marker = outer_marker,
           K = (),  # Will add RF(outer_gen) when started
           caller = outer_prompt_seg,  # Outer returns to its prompt boundary
           scope_chain = current_segment.scope_chain,  # Inherit current scope
       )
    
    7. Start outer handler:
       C = ProgramControl(outer_handler(effect, k_inner))
       segment = outer_seg
─────────────────────────────────────────────────────────────
```

---

## Level 2 Rules

### Rule: Return (when K empty)

**Not a primitive** - Level 2 behavior when segment.K becomes empty.

```
─────────────────────────────────────────────────────────────
Condition:
    C = Value(v)
    segment.K = ()  # Empty after last RF popped by Level 1
    segment.caller ≠ None

Action:
    # Return value to caller
    segment = segment.caller
    # v continues to segment.caller.K[0] by Level 1

─────────────────────────────────────────────────────────────
Condition:
    C = Value(v)
    segment.K = ()
    segment.caller = None

Action:
    # Final result - computation complete
    return Done(v)
─────────────────────────────────────────────────────────────
```

### Rule: Execute RunnableContinuation

**Scheduler picks from queue. One-shot checked via runnable_id.**

```
─────────────────────────────────────────────────────────────
Precondition:
    runnable.runnable_id not in S["consumed_runnable_ids"]

Action:
    1. Mark runnable_id as consumed:
       S' = S | {"consumed_runnable_ids": 
                 S["consumed_runnable_ids"] | {runnable.runnable_id}}
    
    2. Restore execution state:
       C = Value(runnable.pending_value)
       segment = runnable.segment  # scope_chain is in segment
       S = S'
       # Continue stepping normally

Note:
    RunnableContinuation is INTERNAL. User code cannot construct it.
    This check prevents scheduler bugs from accidentally running
    the same task twice (would corrupt execution state).
─────────────────────────────────────────────────────────────
```

### Rule: Start Dispatch

**When user yields an effect.**

```
─────────────────────────────────────────────────────────────
Condition:
    C = EffectYield(effect) where effect is EffectBase

Action:
    1. Lazy pop completed dispatch contexts:
       while dispatch_stack and dispatch_stack[0].completed:
           dispatch_stack = dispatch_stack[1:]
    
    2. Compute visible handlers (BUSY BOUNDARY):
       handler_chain = visible_handlers(segment.scope_chain, dispatch_stack)
       
       See: visible_handlers function below. This filters out handlers
       that are currently "busy" handling an effect.
    
    3. Generate fresh dispatch_id:
       new_dispatch_id = fresh_id()
    
    4. Capture callsite continuation (with dispatch_id for completion detection):
       k_user = Continuation(
           cont_id = fresh_id(),
           segment = current_segment,  # scope_chain is IN segment
           dispatch_id = new_dispatch_id,  # For completion matching
       )
    
    5. Create dispatch context:
       ctx = DispatchContext(
           dispatch_id = new_dispatch_id,
           effect = effect,
           handler_chain = handler_chain,
           handler_idx = 0,  # Start from innermost
           callsite_cont_id = k_user.cont_id,
           completed = False,
       )
       dispatch_stack = (ctx,) + dispatch_stack
    
    6. Find the PromptSegment for this handler:
       handler_marker = handler_chain[0]
       handler = S["handlers"][handler_marker]
       prompt_seg = find_prompt_segment(handler_marker, current_segment)
    
    7. Create handler execution segment:
       handler_seg = Segment(
           marker = handler_marker,
           K = (),
           caller = prompt_seg,  # Handler returns to prompt boundary
           scope_chain = segment.scope_chain,  # Inherit current scope
       )
       
       C = ProgramControl(handler(effect, k_user))
       segment = handler_seg
─────────────────────────────────────────────────────────────
```

### visible_handlers (Busy Boundary)

```python
def visible_handlers(
    scope_chain: tuple[int, ...], 
    dispatch_stack: tuple[DispatchContext, ...]
) -> tuple[int, ...]:
    """Compute handlers visible for a new dispatch, excluding busy handlers.
    
    A handler is "busy" if it appears in an active (non-completed) dispatch
    at or before the current handler_idx.
    
    Args:
        scope_chain: Current evidence vector [innermost, ..., outermost]
        dispatch_stack: Current dispatch contexts [newest, ..., oldest]
    
    Returns:
        Handler markers visible for this dispatch [innermost, ..., outermost]
    """
    if not dispatch_stack:
        # No active dispatches - all handlers visible
        return scope_chain
    
    # Collect busy handlers from active dispatches
    busy = set()
    for ctx in dispatch_stack:
        if not ctx.completed:
            # Handlers at idx and before are busy (being handled or skipped)
            busy.update(ctx.handler_chain[:ctx.handler_idx + 1])
    
    # Filter scope_chain to exclude busy handlers
    visible = tuple(m for m in scope_chain if m not in busy)
    return visible


def find_prompt_segment(
    handler_marker: int, 
    current_segment: Segment
) -> PromptSegment:
    """Find the PromptSegment that installed this handler.
    
    Walk up the caller chain to find the PromptSegment with
    handled_marker == handler_marker.
    
    Raises:
        RuntimeError if no matching PromptSegment found (VM invariant violation)
    """
    seg = current_segment
    while seg is not None:
        if isinstance(seg, PromptSegment) and seg.handled_marker == handler_marker:
            return seg
        seg = seg.caller
    raise RuntimeError(f"No PromptSegment found for handler {handler_marker}")
```

---

## Cleanup

### close_chain (Best-Effort)

```python
def close_chain(seg: Segment | None) -> None:
    """Close all generators in caller chain. Best-effort.
    
    Called by Transfer and ResumeThenTransfer when abandoning
    the current continuation.
    
    Exception handling: Swallow all exceptions from generator.close().
    Cleanup exceptions should not crash VM/scheduler operations.
    """
    while seg is not None:
        for frame in seg.K:
            if isinstance(frame, ReturnFrame):
                try:
                    frame.generator.close()
                except Exception:
                    pass  # Best-effort: swallow, optionally log
        seg = seg.caller
```

---

## One-Shot Continuation Rules

### Continuation (Subject to Check)

- Resume, Transfer, ResumeThenTransfer consume `cont_id`
- Double-resume raises error
- Tracked via `S["consumed_cont_ids"]`

### RunnableContinuation (Exempt from Check)

- Already "delivered" - just waiting to execute
- Scheduler executes directly without check
- `cont_id` retained for debugging only

### Task Continuations

- Scheduler-managed task continuations appear to be "multi-shot"
- Actually: each Yield generates a NEW continuation
- Old continuation is consumed, new one is queued
- No violation of one-shot semantics

---

## Trace: Nested Delegate/Resume (with PromptSegments)

```python
WithHandler(outer, WithHandler(inner, program))

@do
def inner(effect, k_user):
    outer_result = yield Delegate(effect)  # Gets 100
    return (yield Resume(k_user, outer_result + 1))  # Sends 101 to user

@do
def outer(effect, k_inner):
    inner_result = yield Resume(k_inner, 100)  # Sends 100 to inner
    return inner_result * 2
```

### Step 1: Initial State (after WithHandler setup)

```
S["handlers"] = {1: outer, 2: inner}
S["dispatch_stack"] = ()

Segment structure (showing PromptSegments):

root_seg = Segment(
    marker=0, K=[], caller=None,
    scope_chain=()  # No handlers at root
)

outer_prompt = PromptSegment(
    marker=10, K=[], caller=root_seg,
    scope_chain=(),
    is_prompt_boundary=True,
    handled_marker=1  # Delimits outer handler
)

outer_body = Segment(
    marker=1, K=[], caller=outer_prompt,
    scope_chain=(1,)  # outer in scope
)

inner_prompt = PromptSegment(
    marker=20, K=[], caller=outer_body,
    scope_chain=(1,),
    is_prompt_boundary=True,
    handled_marker=2  # Delimits inner handler
)

user_seg = Segment(
    marker=2, K=[RF(user_gen)], caller=inner_prompt,
    scope_chain=(2, 1)  # inner, outer in scope
)

current segment = user_seg
```

### Step 2: User yields SomeEffect()

```
# visible_handlers excludes busy handlers (none busy yet)
handler_chain = visible_handlers((2, 1), ()) = (2, 1)

dispatch_id = 1
k_user = Continuation(
    cont_id=1,
    segment=user_seg,  # scope_chain=(2,1) is IN segment
    dispatch_id=1
)

dispatch_stack = (DispatchContext(
    dispatch_id=1,
    effect=SomeEffect(),
    handler_chain=(2, 1),
    handler_idx=0,
    callsite_cont_id=1,
),)

# Find inner's PromptSegment
inner_prompt_seg = find_prompt_segment(2, user_seg) = inner_prompt

# Start inner handler
handler_seg = Segment(
    marker=2, K=[RF(inner_gen)],
    caller=inner_prompt,  # Returns to prompt boundary!
    scope_chain=(2, 1)
)

segment = handler_seg
```

### Step 3: Inner yields Delegate(SomeEffect())

```
k_inner = Continuation(
    cont_id=2,
    segment=handler_seg,  # scope_chain=(2,1) in segment
    dispatch_id=None  # Delegate continuation
)

# Update dispatch context
dispatch_stack[0].handler_idx = 1

# Find outer's PromptSegment
outer_prompt_seg = find_prompt_segment(1, handler_seg) = outer_prompt

# Start outer handler
outer_handler_seg = Segment(
    marker=1, K=[RF(outer_gen)],
    caller=outer_prompt,  # Returns to outer prompt boundary
    scope_chain=(2, 1)
)

segment = outer_handler_seg
```

### Step 4: Outer yields Resume(k_inner, 100)

```
Consume k_inner.cont_id=2

# dispatch_id is None, so no completion marking

# Create resumed segment with caller attached
callee_seg = Segment(
    marker=2,
    K=k_inner.segment.K,
    caller=outer_handler_seg,  # Caller link attached for call-resume
    scope_chain=(2, 1)  # Restored from k_inner.segment
)

segment = callee_seg  # Switch to inner
C = Value(100)  # inner receives 100 as Delegate result
```

### Step 5: Inner yields Resume(k_user, 101)

```
Consume k_user.cont_id=1

# Check completion: dispatch_id=1, cont_id=1 both match top
dispatch_stack[0].completed = True

# Create resumed segment
callee_seg = Segment(
    marker=2,
    K=k_user.segment.K,
    caller=callee_seg,  # Caller link for return
    scope_chain=(2, 1)  # From k_user.segment
)

segment = callee_seg  # Switch to user
C = Value(101)  # user receives 101
```

### Step 6: User returns 50

```
segment.K = []  # RF(user_gen) popped

Return to caller: segment = inner's resumed segment
C = Value(50)  # inner receives 50 as Resume result
```

### Step 7: Inner returns 50

```
segment.K = []  # RF(inner_gen) popped

Return to caller: segment = outer's handler segment
C = Value(50)  # outer receives 50 as Resume result
```

### Step 8: Outer returns 100

```
outer_gen returns 50 * 2 = 100

segment.K = []
Return to caller: segment = outer_prompt (PromptSegment)

outer_prompt.K = []
Return to caller: segment = root_seg

root_seg.K = []
root_seg.caller = None

Done(100)
```

### Key Observations

1. **PromptSegments** delimit each WithHandler, enabling correct abandon semantics
2. **scope_chain in Segment** enables automatic restoration on resume
3. **dispatch_id + cont_id** double-check prevents accidental completion marking
4. **Handler returns to PromptSegment**, not to user code
5. **visible_handlers** would exclude busy handlers if nested dispatch occurred

---

## Relationship to SPEC-CESK-006

This spec **supersedes** SPEC-CESK-006's K-manipulation approach with:

| SPEC-006 | SPEC-007 |
|----------|----------|
| Flat K with WHF/DF | Segment chain with parent/caller |
| Handler in WithHandlerFrame | Handler in registry (marker → handler) |
| Dispatch state in DispatchingFrame | Dispatch state in dispatch_stack |
| Forward + implicit Resume | Delegate + explicit Resume |
| K reordering for Forward | Segment switching, no reorder |

**Level 1 unchanged**: ReturnFrame, generator management, pure CESK.

**Level 2 redesigned**: Segment-based with explicit continuations.

---

## Lazy Pop Rule (Formal)

**Rule**: Lazy pop MUST consecutively remove ALL completed dispatch contexts from the top of dispatch_stack.

**Where applied**:
1. `start_dispatch` — before pushing new context
2. `Delegate` — before accessing top for outer handler lookup
3. `Resume` — before checking callsite_cont_id match
4. `ResumeThenTransfer` — before any dispatch_stack access (optional but recommended)

**Implementation**:
```python
def lazy_pop_completed(dispatch_stack):
    """Remove all consecutive completed contexts from top."""
    while dispatch_stack and dispatch_stack[0].completed:
        dispatch_stack = dispatch_stack[1:]
    return dispatch_stack
```

**Rationale**: Ensures top is always the "current active dispatch" after lazy pop.

---

## Invariants (Implementation Guarantees)

These invariants are the foundation for correct implementation:

### Invariant 1: Top is Always Active

```
After lazy pop, dispatch_stack[0] (if exists) has completed=False.
```

The top of dispatch_stack always represents the **currently active dispatch**.
Completed contexts are transition states that get cleaned up at dispatch boundaries.

### Invariant 2: Completed = Transition State

```
completed=True means:
"Value has been delivered to callsite, but next dispatch boundary not yet reached."
```

A dispatch context marked `completed=True` is in a **transition state**:
- The callsite continuation has received its value (via Resume)
- But the computation hasn't yet reached a point where new dispatch starts
- This transition state is resolved on next `start_dispatch` or `Delegate`

### Invariant 3: Lazy Pop Resolves Transitions

```
start_dispatch and Delegate always lazy pop first.
Therefore, transition states are always resolved at dispatch boundaries.
```

No matter how deeply nested the dispatch, transition states never accumulate:
- Each new dispatch starts with a clean top
- Delegate sees the correct active dispatch context

### Invariant 4: Resume Completion Requires Double Match

```
Resume only marks dispatch_stack[0].completed=True when BOTH:
  - k.dispatch_id == dispatch_stack[0].dispatch_id (structural match)
  - k.cont_id == dispatch_stack[0].callsite_cont_id (identity match)
```

This double-check prevents accidental completion marking in scheduler scenarios
where continuations are stored and resumed outside normal dispatch flow.

```python
if (dispatch_stack and 
    k.dispatch_id is not None and
    k.dispatch_id == dispatch_stack[0].dispatch_id and
    k.cont_id == dispatch_stack[0].callsite_cont_id):
    # Only mark top as completed
```

### Invariant 5: scope_chain is Per-Segment

```
Each Segment carries its own scope_chain (evidence vector).
Resuming a segment automatically restores its scope_chain.
```

The scope_chain is stored IN `Segment.scope_chain`:
- Set at segment creation time
- Captured automatically when segment is captured in Continuation
- Restored automatically when segment becomes current
- Used via `visible_handlers()` to compute available handlers

**Structural restoration**: No explicit "restore scope_chain" step needed.
Switching to a segment = restoring that segment's scope_chain.

### Invariant 6: PromptSegment Enables Abandon

```
Every WithHandler creates a PromptSegment boundary.
Handler returns to PromptSegment, not to user code.
```

This enables correct abandon semantics:
- Handler returning without Resume → value flows to PromptSegment → original invoker
- User code is NEVER implicitly resumed by handler return
- Matches OCaml 5 / Koka semantics

### Invariant 7: Busy Handlers Excluded via visible_handlers

```
Handlers currently executing are excluded from dispatch.
visible_handlers(scope_chain, dispatch_stack) computes available handlers.
```

A handler at position idx in dispatch_stack[0].handler_chain is "busy".
Nested effects from handler code dispatch to outer handlers only.

---

## Implementation Checklist

### Data Structures
- [ ] Define `Segment` (marker, K, caller, scope_chain)
- [ ] Define `PromptSegment` extends Segment (is_prompt_boundary, handled_marker)
- [ ] Define `Continuation` (cont_id, segment, dispatch_id)
- [ ] Define `RunnableContinuation` (runnable_id, segment, pending_value) — INTERNAL
- [ ] Define `DispatchContext` (dispatch_id, effect, handler_chain, handler_idx, callsite_cont_id, completed)
- [ ] Define `VMState` with segment (scope_chain accessed via segment.scope_chain)

### Store & Registry
- [ ] Implement handler registry in Store (`S["handlers"]`) — intern table
- [ ] Implement dispatch_stack in Store with lazy pop (`S["dispatch_stack"]`)
- [ ] Implement consumed_cont_ids tracking (`S["consumed_cont_ids"]`)
- [ ] Implement consumed_runnable_ids tracking (`S["consumed_runnable_ids"]`)

### VM Primitives
- [ ] Implement `WithHandler(handler, program)` — create PromptSegment boundary
- [ ] Implement `Resume(k, v)` — check dispatch_id + cont_id match
- [ ] Implement `Transfer(k, v)` — scope_chain restored via segment
- [ ] Implement `ResumeThenTransfer(k_return, v, k_next)` — create RunnableContinuation with runnable_id
- [ ] Implement `Delegate(effect)` — find outer handler's PromptSegment

### Helper Functions
- [ ] Implement `visible_handlers(scope_chain, dispatch_stack)` — busy boundary
- [ ] Implement `find_prompt_segment(marker, segment)` — walk caller chain

### Rules & Behaviors
- [ ] Implement Level 2 Return rule (K empty → caller)
- [ ] Implement `start_dispatch` using visible_handlers
- [ ] Implement `Execute RunnableContinuation` with runnable_id check
- [ ] Implement `close_chain()` for cleanup

### Handler Migration
- [ ] Update handler signature to `(effect, k)`
- [ ] Migrate existing handlers to new signature
- [ ] Add comprehensive tests for nested Delegate/Resume
- [ ] Add tests for scope_chain preservation across Transfer

---

## References

- OCaml 5 Effect Handlers: https://ocaml.org/manual/effects.html
- Koka Language: https://koka-lang.github.io/
- "Retrofitting Effect Handlers onto OCaml" (PLDI 2021)
- "Generalized Evidence Passing for Effect Handlers" (ICFP 2021)
