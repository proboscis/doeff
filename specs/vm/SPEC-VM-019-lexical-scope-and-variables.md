# SPEC-VM-019: Lexical Scope and Scoped Variables (Rev 2)

**Status:** Draft
**Created:** 2026-03-18
**Revised:** 2026-03-18
**Motivation:** Handler duplication in Spawn (#342, PR #350), RustStore violation, ScopeStore leaking into VM segments

## Problem Statement

The doeff VM lacks a unified concept of "lexical scope." Currently:

1. **Handler chain** is managed via segments (prompt boundaries)
2. **Interceptor state** is copied between segments ad-hoc
3. **Scope bindings** (Local/Ask) live in `ScopeStore` on segments — VM knows about LazyAskHandler internals
4. **State/Writer/Ask** have dedicated fields in `RustStore` — VM knows about handler-specific state

This causes:
- Handler duplication in Spawn: `ResumeContinuation` inherits parent's handler segments because `CreateContinuation` defers segment construction to resume time
- `RustStore` has handler-specific fields (`state`, `writer`, `ask`) violating abstraction
- `ScopeStore` on segments is LazyAskHandler implementation detail leaking into VM
- `CreateContinuation` stores handler data but doesn't build segments — `ResumeContinuation` must build them, inheriting from `self.current_segment` (wrong parent)

## Design Principle: Segments Are the Scope

### Analogy with OCaml 5

In OCaml 5, effect handlers are stack delimiters. The call stack IS the scope:

```
OCaml 5 fiber stack:

  [stack frames]         ← user code
  ─── delimiter ───      ← match ... with effect E k ->
  [stack frames]
  ─── delimiter ───      ← another handler
  [stack frames]
  ─── delimiter ───      ← outermost handler

- perform E → walk up stack to find delimiter → capture frames = continuation k
- continue k v → splice frames back, handler is still surrounding (deep)
- No "scope object" — the stack IS the scope
```

doeff's segment chain is the same structure, just represented as explicit data:

```
OCaml 5                         doeff
───────────────────             ────────────────────
stack frame                 ≈   Frame (one @do generator's state)
handler delimiter           ≈   Segment boundary (PromptBoundary)
chunk between delimiters    ≈   Segment (frames + handler config)
fiber stack                 ≈   Segment chain (caller → parent → root)
fiber                       ≈   Task
captured stack segment      ≈   Continuation (Arc<Segment> snapshot)
```

**Therefore: segments form the scope. No separate Scope struct is needed.**

The segment chain is the single source of truth for:
- Handler visibility (walk up chain to find `PromptBoundary`)
- Interceptor state (segments carry `InterceptorBoundary`)
- Variable bindings (segments carry variable tables — replacing ScopeStore/RustStore)

### Current Segment Dual-Purpose Problem

A Segment currently mixes two concerns:

```
Segment {
    // --- Scope (handler context) ---
    kind: PromptBoundary { handler, types },  // which handler
    caller: Option<SegmentId>,                // parent in handler chain
    scope_store: ScopeStore,                  // Local/Ask bindings
    interceptor_eval_depth: usize,
    interceptor_skip_stack: Vec<...>,

    // --- Execution state ---
    frames: Vec<Frame>,                       // call stack
    mode: Mode,                               // Deliver/Throw/HandleYield/Return
    dispatch_id: Option<DispatchId>,
    pending_python: Option<PendingPython>,
}
```

`segment.caller` serves two different purposes:
1. **Scope chain**: finding handlers and variables in enclosing scopes
2. **Return continuation**: where to return after this segment completes

These align in simple cases but **diverge in Spawn**: the spawned task's return
continuation is the scheduler, but its scope chain should be the yield site's handlers.

This conflation is the root cause of handler duplication. `ResumeContinuation` sets
`caller = self.current_segment` to get the scope chain, but that inherits the
scheduler's context, duplicating handlers.

### Fix: Separate `caller` (return) from `scope_parent` (scope chain)

```
Segment {
    // --- Scope ---
    kind: SegmentKind,
    scope_parent: Option<SegmentId>,          // handler/variable lookup chain
    variables: HashMap<VarId, Value>,         // scoped variables (replaces ScopeStore + RustStore fields)

    // --- Execution ---
    caller: Option<SegmentId>,                // return continuation (where to go when done)
    frames: Vec<Frame>,
    mode: Mode,
    dispatch_id: Option<DispatchId>,
    ...
}
```

Key properties:
- `scope_parent` is the **lexical** parent — follows handler installation order
- `caller` is the **dynamic** parent — follows execution/return flow
- For `WithHandler(h, body)`: scope_parent = parent segment, caller = parent segment (same)
- For `Spawn(task)`: scope_parent = yield site's segment, caller = scheduler's segment (different!)
- Handler lookup walks `scope_parent` chain
- Return/throw walks `caller` chain
- No duplication possible — scope chain is set once at creation time

### CreateContinuation Builds Segments at Creation Time

The current bug: `CreateContinuation` stores handler data without building segments.
`ResumeContinuation` builds segments using `self.current_segment` as parent — wrong.

**Fix:** `CreateContinuation` builds the full segment chain immediately.

For **unstarted continuations** (Spawn path):
```
CreateContinuation(program, outside_seg_id):
    1. Create body segment for program
    2. Set body_segment.scope_parent = outside_seg_id   // yield site's scope
    3. Set body_segment.caller = <to be set at resume>  // scheduler determines return path
    4. Store segment in continuation
    → Continuation now owns its scope. Resume is trivial.
```

For **captured continuations** (handler dispatch path):
```
Continuation::capture(segment):
    Already correct — captures Arc<Segment> snapshot including scope_parent.
    Resume materializes snapshot. scope_parent is preserved from capture time.
```

`ResumeContinuation` becomes trivial:
```
ResumeContinuation(k, value):
    1. Activate k's pre-built segments
    2. Deliver value
    → No handler installation. No scope inheritance from resume site.
```

### What Happens to GetHandlers?

`GetHandlers` walked the caller chain to collect handlers. With `scope_parent`,
this is no longer needed for Spawn. The scheduler can pass the yield site's
segment ID directly to `CreateContinuation`.

The yield site's segment ID is available from the continuation `k` that the
scheduler receives when handling the Spawn effect:

```
Scheduler receives Spawn(task) with continuation k:
    outside_seg_id = k.segment_id()           // yield site's segment
    new_k = CreateContinuation(task, outside_seg_id)  // scope_parent set here
    Resume(k, task_handle)                     // return to spawner
    // later:
    ResumeContinuation(new_k, value)           // trivial resume
```

`GetHandlers` may be retained for introspection/debugging but is removed from
the Spawn critical path.

## Scoped Variables

### Motivation

Currently, handler state leaks into the VM:
- `RustStore.state` — StateHandler's Get/Put state
- `RustStore.env` — LazyAskHandler's Ask bindings
- `RustStore.log` — WriterHandler's Tell log
- `ScopeStore.scope_bindings` — LazyAskHandler's Local shadow bindings

These are handler implementation details that the VM shouldn't know about. Instead,
the VM provides generic **scoped variables** that handlers use to manage their state.

### Variable Semantics

A scoped variable is a named storage cell that lives in a segment. Variables follow
Python-like scoping rules:

- **Read**: walks the `scope_parent` chain from inner to outer
- **Write (default)**: writes to the **current segment** (shadow — like Python local)
- **Write (nonlocal)**: writes to the **segment where the variable was allocated** (mutate — like Python `nonlocal`)

```
Segment B (scope_parent → A): { x: 20 }     ← WriteVar(x, 20) shadows A's x
Segment A:                     { x: 10 }     ← original allocation

ReadVar(x) in B → 20 (found in B, stops)
ReadVar(x) in A → 10

WriteVar(x, 30) in B → B: { x: 30 }, A unchanged
WriteVarNonlocal(x, 30) in B → A: { x: 30 }
```

### DoCtrl Primitives for Variables

```
AllocVar(initial_value) → VarId
    Allocates a new variable in the current segment's variable table.
    Returns an opaque VarId.
    Variable lifetime is tied to the segment.

ReadVar(VarId) → Value
    Reads the variable. Walks scope_parent chain if not in current segment.

WriteVar(VarId, Value) → ()
    Writes to the current segment (shadow semantics).

WriteVarNonlocal(VarId, Value) → ()
    Writes to the segment where VarId was originally allocated (mutate semantics).
```

### How Handlers Use Scoped Variables

All handlers are **user-space effect handlers** — they use the VM's DoCtrl
scope/variable primitives to manage their state. The VM knows nothing about
State, Writer, Local, or Ask specifically.

#### StateHandler (Get/Put effects)

State is a mutable variable — `Put` in any scope mutates the same variable
(nonlocal semantics).

```python
class StateHandlerFactory:
    def create(self, initial_value):
        var = yield AllocVar(initial_value)   # variable in handler's segment
        return StateHandler(var)

class StateHandler:
    def __init__(self, var: VarId):
        self.var = var

    def handle(self, effect, k):
        if isinstance(effect, Get):
            value = yield ReadVar(self.var)
            yield Resume(k, value)
        elif isinstance(effect, Put):
            yield WriteVarNonlocal(self.var, effect.value)  # mutates handler segment
            yield Resume(k, None)
        else:
            yield Pass()
```

#### WriterHandler (Tell/slog effects)

Writer is an append-only log. Writes are nonlocal.

```python
class WriterHandlerFactory:
    def create(self):
        log = yield AllocVar([])
        return WriterHandler(log)

class WriterHandler:
    def __init__(self, log: VarId):
        self.log = log

    def handle(self, effect, k):
        if isinstance(effect, Tell):
            current = yield ReadVar(self.log)
            yield WriteVarNonlocal(self.log, current + [effect.message])
            yield Resume(k, None)
        else:
            yield Pass()
```

#### LazyAskHandler (Local/Ask effects)

Local creates a new scope with shadow bindings. Ask walks the scope chain.

```python
class LazyAskHandler:
    def handle(self, effect, k):
        if isinstance(effect, Local):
            # EvalInScope runs body in a child segment with bindings
            result = yield EvalInScope(effect.body, effect.overrides)
            yield Resume(k, result)
        elif isinstance(effect, Ask):
            value = yield ReadVar(effect.key)  # walks scope_parent chain
            yield Resume(k, value)
        else:
            yield Pass()
```

Note: `EvalInScope(body, bindings)` is a VM primitive that:
1. Creates a child segment (scope_parent = current)
2. Allocates variables from bindings in the child segment
3. Executes body in the child segment
4. On completion, returns to parent segment (child's variables are dropped)

This replaces the separate `PushScope`/`PopScope` DoCtrl — the scope lifecycle
is tied to the segment lifecycle, which is tied to `EvalInScope` / `WithHandler`.

#### Scheduler (Spawn/Gather effects)

```python
class SchedulerHandler:
    def handle(self, effect, k):
        if isinstance(effect, Spawn):
            # k.segment_id is the yield site's segment — its scope_parent chain
            # has all handlers, interceptors, and variables
            task_k = yield CreateContinuation(
                effect.task,
                outside_seg=k.segment_id()  # yield site's scope chain
            )
            task_id = self.register_task(task_k)
            yield Resume(k, task_id)
        elif isinstance(effect, Gather):
            yield Resume(k, results)
        else:
            yield Pass()
```

## Migration Plan

### Phase 1: Add `scope_parent` to Segment

- Add `scope_parent: Option<SegmentId>` field to `Segment`
- Initially, `scope_parent = caller` for all segments (no behavior change)
- `WithHandler` sets both `scope_parent` and `caller` to parent segment
- Handler lookup (`find_matching_handler`) walks `scope_parent` instead of `caller`
- All existing tests must pass — this is a refactor, not a behavior change

### Phase 2: Fix CreateContinuation for Spawn

- `CreateContinuation` accepts `outside_seg_id` (yield site's segment)
- Builds body segment with `scope_parent = outside_seg_id`
- `ResumeContinuation` sets `caller` only (for return flow), does not touch `scope_parent`
- This fixes handler duplication: scope chain comes from creation, not resume
- Test: spawned task sees correct handler count (no duplication)

### Phase 3: Add Scoped Variables

- Add `variables: HashMap<VarId, Value>` to `Segment`
- Add `AllocVar`, `ReadVar`, `WriteVar`, `WriteVarNonlocal` DoCtrl variants
- `ReadVar` walks `scope_parent` chain
- `WriteVarNonlocal` writes to the segment where `VarId` was allocated
- Remove `ScopeStore` from `Segment`
- Remove handler-specific fields from `RustStore` (`state`, `env`, `log`)
- Migrate StateHandler, WriterHandler, LazyAskHandler to use scoped variables

### Phase 4: Clean Up

- Evaluate whether `GetHandlers` is still needed (likely introspection-only)
- Remove `EvalInScope` if superseded or consolidate with new semantics
- Remove `RustStore.with_local` (replaced by EvalInScope + AllocVar)

## TDD Test Plan

Tests should be written BEFORE implementation. They target the segment-based
scope model and variable primitives.

### Scope Chain Tests

```python
# T1: scope_parent chain is correctly set by WithHandler
def test_handler_scope_parent_chain():
    # WithHandler(A, WithHandler(B, body))
    # Inside body: scope_parent chain is body_seg → B_seg → A_seg → root
    # Handler lookup finds A, B, and any root handlers

# T2: scope_parent vs caller diverge in Spawn
def test_spawn_scope_parent_differs_from_caller():
    # Spawn(task) inside WithHandler(A, ...)
    # task's scope_parent → yield site (has handler A)
    # task's caller → scheduler (return path)
    # task can find handler A via scope_parent
```

### Variable Shadowing Tests

```python
# T3: AllocVar + ReadVar basic
def test_alloc_and_read_var():
    var = yield AllocVar(42)
    val = yield ReadVar(var)
    assert val == 42

# T4: Shadow semantics — WriteVar in child segment does not affect parent
def test_shadow_write():
    var = yield AllocVar(10)
    result = yield EvalInScope(shadow_body(var), {})
    # Inside shadow_body: WriteVar(var, 20) → shadows in child segment
    # After EvalInScope: ReadVar(var) → 10 (parent unchanged)

# T5: Nonlocal write — WriteVarNonlocal mutates parent segment
def test_nonlocal_write():
    var = yield AllocVar(10)
    yield EvalInScope(nonlocal_body(var), {})
    # Inside nonlocal_body: WriteVarNonlocal(var, 20)
    # After EvalInScope: ReadVar(var) → 20 (parent mutated)

# T6: ReadVar walks scope_parent chain
def test_read_walks_scope_parent():
    var = yield AllocVar(10)
    # EvalInScope creates child segment with scope_parent → current
    result = yield EvalInScope(read_var_body(var), {})
    assert result == 10  # found via scope_parent chain

# T7: Three-level shadow chain
def test_three_level_shadow():
    var = yield AllocVar(10)
    # level 1 → WriteVar(var, 20)
    # level 2 → WriteVar(var, 30)
    # ReadVar at each level returns correct shadow

# T8: Multiple variables in same segment
def test_multiple_vars():
    x = yield AllocVar(1)
    y = yield AllocVar(2)
    assert (yield ReadVar(x)) == 1
    assert (yield ReadVar(y)) == 2
```

### Spawn + Scope Tests (Critical — tests the handler duplication fix)

```python
# T9: Spawned task sees yield site's handler chain (not duplicated)
def test_spawn_no_handler_duplication():
    # GetHandlers inside spawned task returns N handlers
    # Same N as direct call (not 2*N)

# T10: Spawned task sees yield site's variables
def test_spawn_inherits_yield_site_vars():
    var = yield AllocVar(42)
    task_handle = yield Spawn(read_var_task(var))
    result = yield Gather(task_handle)
    assert result == 42

# T11: Spawned task variable shadow does not affect parent
def test_spawn_var_shadow_isolation():
    var = yield AllocVar(10)
    task_handle = yield Spawn(write_and_return(var, 99))
    yield Gather(task_handle)
    assert (yield ReadVar(var)) == 10  # parent unchanged

# T12: N=500 spawned tasks — no O(N²) from scope
def test_spawn_500_no_quadratic():
    # Spawn 500 tasks, each reads a variable
    # Memory O(N), not O(N²)

# T13: Multiple spawned tasks share parent's scope_parent chain
def test_multiple_spawn_share_scope():
    var = yield AllocVar(42)
    t1 = yield Spawn(read_var_task(var))
    t2 = yield Spawn(read_var_task(var))
    r1, r2 = yield Gather(t1, t2)
    assert r1 == 42 and r2 == 42
```

### Local/Ask on Scoped Variables Tests

```python
# T14: Local creates child segment, Ask reads from it
def test_local_ask_basic():
    result = yield Local({"key": "value"}, ask_program("key"))
    assert result == "value"

# T15: Nested Local shadow
def test_local_shadow():
    result = yield Local({"x": 10},
        Local({"x": 20}, ask_program("x"))
    )
    assert result == 20

# T16: Local scope ends — outer value restored
def test_local_scope_restore():
    @do
    def program():
        yield Local({"x": 20}, noop())
        return (yield Ask("x"))
    result = yield Local({"x": 10}, program())
    assert result == 10

# T17: Ask walks scope_parent chain to outer Local
def test_ask_walks_to_outer():
    result = yield Local({"x": 10},
        Local({"y": 20}, ask_program("x"))  # x not in inner, found in outer
    )
    assert result == 10
```

### State/Writer on Scoped Variables Tests

```python
# T18: State Get/Put as nonlocal variable operations
def test_state_as_scoped_var():
    yield Put(10)
    # Put in nested scope mutates the state variable (nonlocal)
    assert (yield Get()) == 10

# T19: Writer Tell as nonlocal append
def test_writer_as_scoped_var():
    yield Tell("a")
    yield Tell("b")
    # writer log should be ["a", "b"]
```

## Open Questions

1. **Variable identity across segments**: When `WriteVar` shadows a variable, is it
   the same `VarId` with a new value in the current segment? Yes — same VarId,
   different segment in the scope_parent chain.

2. **Performance**: `ReadVar` walks `scope_parent` chain — O(depth). For hot paths
   (Ask inside spawned tasks), consider caching or segment-local lookup tables.

3. **Segment lifetime and variables**: Variables are dropped when their segment is
   deallocated. Need to ensure segment lifetime rules are clear — Arc snapshots
   in continuations keep segments alive.

4. **EvalInScope vs PushScope/PopScope**: Rev 1 had standalone PushScope/PopScope.
   Rev 2 uses EvalInScope which ties scope lifecycle to segment lifecycle (cleaner).
   Standalone Push/Pop may still be needed if handlers need mid-execution scope
   manipulation — evaluate during implementation.
