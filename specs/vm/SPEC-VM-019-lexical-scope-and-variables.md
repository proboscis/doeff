# SPEC-VM-019: Lexical Scope and Scoped Variables

**Status:** Draft
**Created:** 2026-03-18
**Motivation:** Handler duplication in Spawn (#342, PR #350), RustStore violation, ScopeStore leaking into VM segments

## Problem Statement

The doeff VM lacks a unified concept of "lexical scope." Currently:

1. **Handler chain** is managed via segments (prompt boundaries)
2. **Interceptor state** is copied between segments ad-hoc
3. **Scope bindings** (Local/Ask) live in `ScopeStore` on segments — VM knows about LazyAskHandler internals
4. **State/Writer/Ask** have dedicated fields in `RustStore` — VM knows about handler-specific state

This causes:
- Handler duplication in Spawn: `ResumeContinuation` inherits parent's handler segments because it needs scope/interceptor state from `self.current_segment`
- `RustStore` has handler-specific fields (`state`, `writer`, `ask`) violating abstraction
- `ScopeStore` on segments is LazyAskHandler implementation detail leaking into VM
- `CreateContinuation` defers segment construction to `ResumeContinuation`, causing implicit parent inheritance

## Design

### Core Concept: Scope

A **Scope** is the lexical execution environment. It holds:
- Handler chain (which effects get caught where)
- Interceptor state (WithIntercept guards)
- Variable table (scoped variables)

Scopes form a chain (inner → outer), like Python's scope chain.

```
┌──────────────┐
│ Scope C      │  (innermost — current WithHandler/Local block)
│  variables   │
│  handlers    │
│  interceptors│
│  parent ─────┼──→ ┌──────────────┐
└──────────────┘    │ Scope B      │
                    │  parent ─────┼──→ ┌──────────────┐
                    └──────────────┘    │ Scope A      │  (outermost)
                                       │  parent: None │
                                       └──────────────┘
```

### Core Concept: Scoped Variable

A **scoped variable** is a named storage cell that lives in a scope. Variables follow Python-like scoping rules:

- **Read**: walks the scope chain from inner to outer (lookup)
- **Write (default)**: writes to the **current scope** (shadow — like Python local variable)
- **Write (nonlocal)**: writes to the **scope where the variable was allocated** (mutate — like Python `nonlocal`)

```
Scope B: { x: 20 }        ← WriteVar(x, 20) shadows Scope A's x
    ↓ parent
Scope A: { x: 10 }        ← original allocation

ReadVar(x) in Scope B → 20 (found in B, stops)
ReadVar(x) in Scope A → 10

WriteVar(x, 30) in Scope B → Scope B: { x: 30 }, Scope A unchanged
WriteVar(x, 30, nonlocal) in Scope B → Scope A: { x: 30 }
```

### DoCtrl Primitives

#### Scope Management

```
PushScope
  Creates a new scope with the current scope as parent.
  Used internally by WithHandler, Local, WithIntercept.

PopScope
  Exits the current scope, returning to parent.
  Variables in the popped scope are dropped.
```

#### Variable Operations

```
AllocVar(initial_value) → VarId
  Allocates a new variable in the current scope.
  Returns an opaque VarId.
  Variable lifetime is tied to the scope — when scope is popped, variable is dropped.

ReadVar(VarId) → Value
  Reads the variable value.
  Walks scope chain from current to outer if variable is shadowed.

WriteVar(VarId, Value) → ()
  Writes to the current scope's layer (shadow semantics).
  Does not affect outer scopes.

WriteVarNonlocal(VarId, Value) → ()
  Writes to the scope where VarId was originally allocated (mutate semantics).
  Equivalent to Python's `nonlocal` assignment.
```

### How Existing Handlers Map to This

All handlers below are **user-space effect handlers** — they use VM's DoCtrl
scope/variable primitives to manage their state. The VM knows nothing about
State, Writer, Local, or Ask specifically.

#### StateHandler (Get/Put effects)

State is a mutable variable shared across scopes — `Put` in any scope mutates the
same variable. This maps to `WriteVarNonlocal`.

```python
# --- Handler factory (called when WithHandler installs the handler) ---
# The factory allocates a scoped variable to hold state.
# VarId is stored in the handler instance (closure / struct field).

class StateHandlerFactory:
    def create(self, initial_value):
        var = yield AllocVar(initial_value)   # DoCtrl — var lives in handler's scope
        return StateHandler(var)

# --- Handler (processes Get/Put effects) ---

class StateHandler:
    def __init__(self, var: VarId):
        self.var = var

    def handle(self, effect, k):
        if isinstance(effect, Get):
            value = yield ReadVar(self.var)            # DoCtrl
            yield Resume(k, value)

        elif isinstance(effect, Put):
            yield WriteVarNonlocal(self.var, effect.value)  # DoCtrl — nonlocal: mutates
            yield Resume(k, None)                           #   the scope where var was allocated

        else:
            yield Pass()

# --- Behavior ---
# yield Put(10)     → WriteVarNonlocal(var, 10) — mutates handler scope's variable
# yield Get()       → ReadVar(var) → 10
# PushScope + Put(20) + PopScope → var is still 20 (nonlocal write persists)
```

#### WriterHandler (Tell/slog effects)

Writer is an append-only log. Like State, writes are nonlocal — Tell appends to the
same log regardless of scope depth.

```python
# --- Handler factory ---

class WriterHandlerFactory:
    def create(self):
        log = yield AllocVar([])              # DoCtrl — empty log
        return WriterHandler(log)

# --- Handler ---

class WriterHandler:
    def __init__(self, log: VarId):
        self.log = log

    def handle(self, effect, k):
        if isinstance(effect, Tell):
            current = yield ReadVar(self.log)              # DoCtrl
            yield WriteVarNonlocal(self.log, current + [effect.message])  # DoCtrl
            yield Resume(k, None)

        else:
            yield Pass()

# --- Behavior ---
# yield Tell("a")   → log = ["a"]
# yield Tell("b")   → log = ["a", "b"]
# After handler scope ends, log is dropped (or returned as handler result)
```

#### LazyAskHandler (Local/Ask effects)

Ask reads a variable by key, walking the scope chain. Local creates a new scope with
shadow bindings. This is the handler that most directly uses scope semantics.

Unlike State/Writer, Local/Ask uses **shadow** writes — `Local` creates a new scope
and `AllocVar` in that scope shadows the parent's variable. When the Local scope ends,
the shadow is dropped and the parent's value is restored.

```python
# --- Handler ---
# No factory-time AllocVar needed — variables are allocated per Local call.

class LazyAskHandler:
    def handle(self, effect, k):
        if isinstance(effect, Local):
            yield PushScope                               # DoCtrl — new scope
            for key, value in effect.overrides.items():
                yield AllocVar(key, value)                # DoCtrl — shadow in new scope
            result = yield EvalInCurrentScope(effect.body) # DoCtrl — run body in new scope
            yield PopScope                                # DoCtrl — drop shadows
            yield Resume(k, result)

        elif isinstance(effect, Ask):
            value = yield ReadVar(effect.key)             # DoCtrl — walks scope chain
            yield Resume(k, value)

        else:
            yield Pass()

# --- Behavior ---
# yield Local({"x": 10}, body):
#   1. PushScope → new scope (parent: current)
#   2. AllocVar("x", 10) → x = 10 in new scope
#   3. body executes, yield Ask("x") → ReadVar("x") → 10
#   4. PopScope → new scope dropped, x = 10 shadow gone
#
# Nested Local({"x": 20}, ...) inside Local({"x": 10}, ...):
#   Ask("x") → 20 (inner shadow)
#   After inner Local ends → Ask("x") → 10 (outer restored)
#
# Ask("x") with no Local for "x":
#   ReadVar walks scope chain to root, not found → handler raises MissingEnvKeyError

# --- Lazy evaluation (current LazyAskHandler feature) ---
# When a Local value is a thunk (callable/program), LazyAskHandler evaluates it
# lazily on first Ask and caches the result. This is handler-level logic using
# the same VM primitives:
#   1. AllocVar(key, thunk) — store thunk as initial value
#   2. On Ask(key): ReadVar → get thunk → evaluate → WriteVar(key, result) — cache
#   3. Subsequent Ask(key): ReadVar → get cached result
# The lazy eval + caching is entirely in handler code, not VM primitives.
```

#### Scheduler (Spawn/Gather effects)

The scheduler uses scope primitives to create tasks that inherit the yield site's
environment.

```python
# --- Handler ---

class SchedulerHandler:
    def handle(self, effect, k):
        if isinstance(effect, Spawn):
            scope = yield GetScopeOf(k)                        # DoCtrl — yield site's scope
            task_k = yield CreateContinuation(effect.task, scope)  # DoCtrl — segments built now
            task_id = self.register_task(task_k)
            yield Resume(k, task_id)                           # return task handle to parent
            # Later, when scheduling:
            # yield ResumeContinuation(task_k, value)          # DoCtrl — trivial resume

        elif isinstance(effect, Gather):
            # ... wait for tasks, collect results ...
            yield Resume(k, results)

        else:
            yield Pass()

# --- Behavior ---
# Spawned task sees the yield site's full scope:
#   - all handlers (Writer, State, Cache, Scheduler itself)
#   - all interceptors
#   - all variables (Ask bindings, State vars, etc.)
# No GetHandlers needed — scope includes everything.
# No handler duplication — scope is shared reference, not re-installed.
```

### Spawn Semantics

With lexical scope as a first-class concept, Spawn becomes simple:

```
# Scheduler handler processes Spawn(task):

# Current scope already contains everything:
#   handler chain, interceptors, variables
# The continuation K from yield site carries scope reference.

yield EvalInCurrentScope(task)
# → task executes in the current scope
# → all handlers, interceptors, variables are visible
# → no GetHandlers needed
# → no handler re-installation needed
# → no duplication possible
```

#### GetScopeOf / CreateContinuation / ResumeContinuation

```
GetScopeOf(k) → ScopeRef
  Extracts the scope from continuation k.
  k holds a reference to the yield site's scope — the scope that was active
  when the user code yielded the effect.
  Returns an opaque ScopeRef that can be passed to CreateContinuation.

CreateContinuation(program, scope) → Continuation
  Creates a new continuation that will execute program in the given scope.
  Builds segments at creation time referencing the scope.
  The continuation is a self-contained execution unit.
  scope comes from GetScopeOf(k) — the yield site's environment.

ResumeContinuation(k, value) → ()
  Jumps to k's pre-built segments and delivers value.
  No scope inheritance from call site.
  No handler installation.
  Trivial operation.
```

#### Spawn Flow with New Primitives

```
Scheduler handler receives Spawn(task) with continuation k:

  scope = yield GetScopeOf(k)                    # yield site's full scope
  new_k = yield CreateContinuation(task, scope)   # segments built now
  yield ResumeContinuation(new_k, value)           # trivial resume

  # No GetHandlers — scope includes handler chain
  # No handler re-installation — segments reference scope directly
  # No duplication — scope is shared, not copied
```

#### GetHandlers (Deprecated)

`GetHandlers` is superseded by `GetScopeOf(k)`. The scope includes the handler chain,
interceptor state, and variables — everything `GetHandlers` provided and more.

`GetHandlers` may be retained for introspection/debugging but is no longer part of
the Spawn critical path. The handler duplication bug existed because `GetHandlers`
returned the handler list without scope context, forcing `ResumeContinuation` to
re-install handlers on top of `self.current_segment` (which already had them).

### Migration from Current Implementation

#### Phase 1: Introduce Scope and Variable Primitives
- Add `Scope` struct that unifies handler chain + interceptors + variable table
- Add `AllocVar`, `ReadVar`, `WriteVar`, `WriteVarNonlocal` DoCtrl variants
- Segments reference scopes instead of carrying ScopeStore/interceptor state directly

#### Phase 2: Fix CreateContinuation
- `CreateContinuation` captures current scope and builds segments at creation time
- `ResumeContinuation` becomes trivial resume (no handler installation)
- Remove handler duplication bug

#### Phase 3: Migrate Handlers to Scoped Variables
- StateHandler: replace `RustStore.state` with scoped variable
- WriterHandler: replace `RustStore.writer` with scoped variable
- LazyAskHandler: replace `ScopeStore.scope_bindings` with scoped variables
- Remove `RustStore` handler-specific fields
- Remove `ScopeStore` from segments

#### Phase 4: Clean Up
- Evaluate whether `GetHandlers` is still needed
- Remove `EvalInScope` if superseded by `PushScope` + `EvalInCurrentScope`
- Update scheduler to use simplified Spawn path

## Scope vs Segment Architecture

### Current: Scope and Execution State are Mixed

Currently, a `Segment` serves two purposes:

```
Segment (current) {
    // --- Scope (static structure) ---
    kind: PromptBoundary { handler, types },  // handler lives here
    caller: Option<SegmentId>,                // parent scope chain
    scope_store: ScopeStore,                  // Local/Ask bindings
    interceptor_eval_depth: usize,            // WithIntercept
    interceptor_skip_stack: Vec<...>,         // WithIntercept

    // --- Execution state (dynamic) ---
    frames: Vec<Frame>,                       // dispatch/eval stack
    mode: Mode,                               // Deliver/Throw/HandleYield/Return
    dispatch_id: Option<DispatchId>,          // current dispatch
}
```

This mixing is why `CreateContinuation` cannot capture scope without also capturing
execution state, and why `ResumeContinuation` must use `self.current_segment`
(to inherit scope) which causes handler duplication.

### Proposed: Separate Scope from Segment

```
Scope {
    parent: Option<ScopeId>,
    handler: Option<(Marker, Handler, Types)>,   // PromptBoundary info
    interceptor_state: InterceptorState,
    variables: HashMap<VarId, Value>,
}

Segment {
    scope: ScopeId,              // reference to scope (not owned)
    frames: Vec<Frame>,          // execution state only
    mode: Mode,
    dispatch_id: Option<DispatchId>,
}
```

Key properties:
- **Scope** is the static environment — handler chain, interceptors, variables
- **Segment** is the dynamic execution state — what's currently running
- Multiple segments can reference the same scope (e.g., Spawned tasks share parent scope)
- `CreateContinuation` captures `ScopeId` — trivial, no cloning
- `ResumeContinuation` creates new segment referencing K's scope — no parent inheritance
- Scope chain (`parent`) replaces segment chain (`caller`) for scope lookup
- Handler duplication is structurally impossible — scope is shared, not re-installed

### Scope Lifecycle

Note: `Local` and `Spawn` are **effects handled by user-space handlers**, not VM
primitive DoCtrl. The VM only provides the scope/variable primitives. Handlers use
those primitives to implement their semantics.

```
WithHandler(handler, body):                        ← DoCtrl (VM primitive)
    1. Create new Scope { parent: current_scope, handler, variables: {} }
    2. Create new Segment { scope: new_scope }
    3. Execute body
    4. On body completion: pop back to parent scope

Local(overrides, body):                            ← Effect (user-space handler)
    LazyAskHandler handles the Local effect using VM primitives:
    1. yield PushScope                             ← DoCtrl
    2. for k, v in overrides:
         yield AllocVar(k, v)                      ← DoCtrl (shadow in new scope)
    3. result = yield EvalInCurrentScope(body)      ← DoCtrl
    4. yield PopScope                              ← DoCtrl
    5. yield Resume(k, result)

Spawn(task):                                       ← Effect (user-space handler)
    Scheduler handler handles the Spawn effect using VM primitives:
    1. k = yield CreateContinuation(task)          ← DoCtrl
       → K captures current scope (the yield site's scope)
       → segments are built referencing that scope
    2. yield ResumeContinuation(k, value)           ← DoCtrl
       → new segment created, referencing K's scope
       → no handler installation, no parent inheritance from resume site
```

### Segment `caller` vs Scope `parent`

Currently `segment.caller` serves two purposes:
1. **Scope chain**: finding handlers and variables in enclosing scopes
2. **Return continuation**: where to return after execution completes

After separation:
- `scope.parent` handles scope chain (handler/variable lookup)
- `segment.caller` handles only return continuation (execution flow)

These are different concepts that happen to align in simple cases but diverge in
Spawn (the return continuation is the scheduler, but the scope chain is the yield site).

## TDD Test Plan

Tests should be written BEFORE implementation, targeting the DoCtrl-level scope/variable primitives.

### Scope Chain Tests

```python
# T1: Basic scope creation and nesting
# PushScope creates a child scope, PopScope returns to parent
def test_push_pop_scope():
    yield PushScope
    # inside child scope
    yield PopScope
    # back to parent scope

# T2: Scope nesting depth
# Multiple PushScope creates a chain
def test_nested_scopes():
    yield PushScope       # scope A
    yield PushScope       # scope B (parent: A)
    yield PushScope       # scope C (parent: B)
    yield PopScope        # back to B
    yield PopScope        # back to A
    yield PopScope        # back to root
```

### Variable Shadowing Tests

```python
# T3: AllocVar + ReadVar basic
def test_alloc_and_read_var():
    var = yield AllocVar(42)
    val = yield ReadVar(var)
    assert val == 42

# T4: Shadow semantics — inner scope write does not affect outer
def test_shadow_write():
    var = yield AllocVar(10)          # outer scope: var = 10
    yield PushScope
    yield WriteVar(var, 20)           # inner scope: shadows var = 20
    val = yield ReadVar(var)
    assert val == 20                  # inner sees 20
    yield PopScope
    val = yield ReadVar(var)
    assert val == 10                  # outer still 10

# T5: Nonlocal write — inner scope mutates outer
def test_nonlocal_write():
    var = yield AllocVar(10)          # outer scope: var = 10
    yield PushScope
    yield WriteVarNonlocal(var, 20)   # mutates outer scope
    yield PopScope
    val = yield ReadVar(var)
    assert val == 20                  # outer is now 20

# T6: ReadVar walks scope chain
def test_read_walks_chain():
    var = yield AllocVar(10)          # scope A: var = 10
    yield PushScope                   # scope B: no shadow
    val = yield ReadVar(var)
    assert val == 10                  # found in scope A via chain lookup
    yield PopScope

# T7: Variable dropped on scope pop
def test_var_dropped_on_pop():
    yield PushScope
    var = yield AllocVar(42)          # var in inner scope
    yield PopScope                    # inner scope dropped
    # ReadVar(var) should fail — var no longer exists

# T8: Multiple variables in same scope
def test_multiple_vars():
    x = yield AllocVar(1)
    y = yield AllocVar(2)
    assert (yield ReadVar(x)) == 1
    assert (yield ReadVar(y)) == 2

# T9: Shadow chain — three levels
def test_three_level_shadow():
    var = yield AllocVar(10)          # scope A: 10
    yield PushScope
    yield WriteVar(var, 20)           # scope B: 20
    yield PushScope
    yield WriteVar(var, 30)           # scope C: 30
    assert (yield ReadVar(var)) == 30
    yield PopScope
    assert (yield ReadVar(var)) == 20
    yield PopScope
    assert (yield ReadVar(var)) == 10
```

### Scope + Handler Tests

```python
# T10: WithHandler creates a scope — handler's AllocVar lives in that scope
def test_handler_var_in_handler_scope():
    # Handler allocates a variable during init
    # Variable is visible inside handler scope
    # Variable is dropped when handler scope ends

# T11: Nested handlers — inner handler can read outer handler's var via chain
def test_nested_handler_var_visibility():
    # Outer handler allocates var_a
    # Inner handler can ReadVar(var_a) via scope chain

# T12: Handler var not visible outside handler scope
def test_handler_var_not_visible_outside():
    # WithHandler(MyHandler, body) — MyHandler allocates var
    # After WithHandler completes, var is gone
```

### Spawn + Scope Tests (Critical — tests the handler duplication fix)

```python
# T13: Spawned task sees yield site's handler chain
def test_spawn_inherits_yield_site_handlers():
    # WithHandler(A, WithHandler(B, WithHandler(Scheduler,
    #   yield Spawn(task)
    # )))
    # task should see handlers [A, B, Scheduler]

# T14: Spawned task sees yield site's variables
def test_spawn_inherits_yield_site_vars():
    var = yield AllocVar(42)
    task_handle = yield Spawn(read_var_task(var))
    result = yield Gather(task_handle)
    assert result == 42

# T15: Spawned task variable shadow does not affect parent
def test_spawn_var_shadow_isolation():
    var = yield AllocVar(10)
    task_handle = yield Spawn(write_and_return(var, 99))
    yield Gather(task_handle)
    assert (yield ReadVar(var)) == 10  # parent unchanged

# T16: Handler chain NOT duplicated in Spawn
def test_spawn_no_handler_duplication():
    # GetHandlers inside spawned task returns N handlers
    # Same N as direct call (not 2*N)

# T17: Multiple spawned tasks share parent scope (read)
def test_multiple_spawn_share_scope():
    var = yield AllocVar(42)
    t1 = yield Spawn(read_var_task(var))
    t2 = yield Spawn(read_var_task(var))
    r1, r2 = yield Gather(t1, t2)
    assert r1 == 42 and r2 == 42

# T18: N=500 spawned tasks — no O(N²) memory from scope
def test_spawn_500_no_quadratic_memory():
    # Spawn 500 tasks, each reads a variable
    # Memory should be O(N), not O(N²)
    # Regression test for #342
```

### Local/Ask on Scoped Variables Tests

```python
# T19: Local creates scope, Ask reads from it
def test_local_ask_basic():
    result = yield Local({"key": "value"}, ask_program("key"))
    assert result == "value"

# T20: Local shadow — inner Local overrides outer
def test_local_shadow():
    result = yield Local({"x": 10},
        Local({"x": 20}, ask_program("x"))
    )
    assert result == 20

# T21: Local scope ends — outer value restored
def test_local_scope_restore():
    @do
    def program():
        yield Local({"x": 20}, noop())
        return (yield Ask("x"))
    result = yield Local({"x": 10}, program())
    assert result == 10

# T22: Ask walks scope chain to outer Local
def test_ask_walks_to_outer():
    result = yield Local({"x": 10},
        Local({"y": 20}, ask_program("x"))  # x not in inner, found in outer
    )
    assert result == 10
```

### State/Writer on Scoped Variables Tests

```python
# T23: State Get/Put as nonlocal variable operations
def test_state_as_scoped_var():
    # State handler uses WriteVarNonlocal
    # Put in inner scope mutates the state variable
    yield Put(10)
    yield PushScope
    yield Put(20)
    yield PopScope
    assert (yield Get()) == 20  # nonlocal write persists

# T24: Writer Tell as nonlocal append
def test_writer_as_scoped_var():
    yield Tell("a")
    yield PushScope
    yield Tell("b")
    yield PopScope
    yield Tell("c")
    # writer log should be ["a", "b", "c"]
```

## Open Questions

1. **Spawn and variable sharing**: When a task is spawned, should variables be snapshot-copied or reference-shared? This should be configurable by the scheduler handler, not hardcoded in the VM.

2. **Variable identity across scopes**: When `WriteVar` shadows a variable, is it the same `VarId` with a new value in the current scope, or a new `VarId`? Python creates a new local — but `VarId` based access suggests same-id-different-scope.

3. **AllocVar caller**: Should `AllocVar` be called by handler factories during scope creation, or by handlers during dispatch? Factory-time allocation is cleaner but requires a factory protocol.

4. **Performance**: Scope chain lookup for `ReadVar` is O(depth). For hot paths (Ask inside Spawned tasks), this needs to be efficient. Consider caching or flattening.

5. **EvalInCurrentScope vs CreateContinuation**: Are both needed? `EvalInCurrentScope` is synchronous (Koka-style), `CreateContinuation` is deferred. The scheduler may need both — `EvalInCurrentScope` for direct execution, `CreateContinuation` for task queuing.
