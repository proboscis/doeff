# SPEC-VM-019: Pure Stack Machine and Scoped Variables (Rev 3)

**Status:** Draft
**Created:** 2026-03-18
**Revised:** 2026-03-19
**Motivation:** Handler duplication in Spawn (#342), dispatch complexity, RustStore violation, ScopeStore leaking into VM

**ADR:** [DEC-VM-012 Pure Stack Machine Dispatch](../../doeff-VAULT/Decisions/DEC-VM-012-pure-stack-machine-dispatch.md)

## Problem Statement

The doeff VM has accumulated accidental complexity in its dispatch mechanism. Comparing
with OCaml 5's effect handler runtime reveals that doeff's core mechanics are structurally
identical but obscured by layered concerns:

1. **Dispatch state in special frames** — `HandlerDispatch` and `DispatchOrigin` frames
   encode tracing, protocol enforcement, and error enrichment directly in the frame stack.
   These concerns can be handled by flags on existing objects and on-demand computation.

2. **Handler duplication in Spawn** — spawned tasks get cloned handler segments instead
   of sharing the parent's handlers via the `caller` chain. In OCaml 5, spawned fibers
   share parent handlers naturally because effects delegate up the fiber chain.

3. **Handler-specific fields in RustStore** — `state`, `env`, `log` are handler
   implementation details leaking into the VM. Should be generic scoped variables.

4. **ScopeStore on segments** — LazyAskHandler implementation detail leaking into VM.

## Design Principle: OCaml 5-like Pure Stack Machine

### OCaml 5 Architecture (from PLDI 2021 paper)

OCaml 5 implements effect handlers with **fibers** — small heap-allocated stack chunks:

```
Fiber = stack chunk containing:
  - Stack frames (function calls)
  - Handler delimiter (handler closure + environment)
  - Link to parent fiber

Program stack = linked list of fibers

perform E:
  1. Walk fiber chain to find matching handler delimiter
  2. Detach fibers between perform site and handler → this IS continuation k
     (just pointer manipulation, no copying — one-shot)
  3. Run handler code on the handler's fiber

continue k v:
  1. Reattach k's fiber chain
  2. Deliver value v
  3. Execution resumes from where perform suspended
```

Key properties:
- **No dispatch state frames** — handler's own stack frames ARE the dispatch state
- **Shared handlers** — spawned fibers delegate effects up the fiber chain to the same
  handler instances. All tasks share one StateHandler, one WriterHandler, etc.
- **One-shot enforcement** — flag on continuation object, checked at `continue` time
- **Tracing** — separate DWARF-based debugging, not inline frames

### doeff Mapping

```
OCaml 5                         doeff (target)
───────────────────             ────────────────────
fiber (stack chunk)         =   Segment
stack frame                 =   Frame
handler delimiter           =   SegmentKind::PromptBoundary
fiber chain (linked list)   =   caller chain
program stack               =   segment chain via caller
captured continuation       =   Continuation (Arc<Segment> snapshot)
perform                     =   yield Effect → VM dispatch
continue k v                =   VM processes Resume(k, v)
```

doeff generators yield DoCtrl commands where OCaml handlers call runtime primitives
directly. This is a surface difference — the VM's internal architecture can still be
a pure stack machine.

## Changes

### 1. Remove Special Dispatch Frames

**Remove `HandlerDispatch` frame.** Its responsibilities move to:
- **Return path**: Already handled by `caller` chain — when handler segment finishes,
  execution returns to caller.
- **One-shot enforcement**: `consumed: bool` flag on `Continuation` object. Checked
  when VM processes `Resume(k, v)`.
- **Tracing**: Separate trace observer records dispatch start/complete events.

**Remove `DispatchOrigin` frame.** Its responsibilities move to:
- **Error enrichment**: Assemble active chain from segment chain on demand at throw
  time. The segment chain already contains all handler info.
- **Effect tracking**: The handler generator holds the effect as a parameter. No need
  for the VM to separately track it in a frame.

### 2. Simplify Dispatch

The dispatch path becomes:

```
yield Effect:
  1. Walk caller chain from current segment
  2. For each PromptBoundary: check if handler matches effect
  3. Skip the currently-active handler's prompt (self-dispatch exclusion)
  4. When found: capture k = snapshot current segment
  5. Set k.consumed = false
  6. Invoke handler's kleisli with (effect, k)
  7. Handler generator yields commands (Resume, Pass, etc.)
  8. VM processes each command using the stack

Resume(k, v):
  1. Assert !k.consumed (one-shot check)
  2. Set k.consumed = true
  3. Restore k's segment (reattach to stack)
  4. Set caller → handler's segment (return path)
  5. Deliver v
```

This is OCaml-level simplicity. Self-dispatch exclusion (step 3) prevents a handler
from catching its own re-performed effects — same semantics as OCaml where `perform`
searches above the current delimiter.

### 3. Spawn Uses Shared Handlers (OCaml Model)

In OCaml 5, spawned fibers share parent handlers. Effects from child tasks delegate
up to the same handler instances:

```
WithHandler(StateHandler,
    WithHandler(Scheduler,
        body → Spawn(task)
    )
)

Parent task:                    Spawned task:
┌──────────┐                    ┌──────────┐
│ body seg │                    │ task seg │
│ caller ──┼──┐                 │ caller ──┼──┐
└──────────┘  │                 └──────────┘  │
              ▼                               │
         ┌──────────┐                         │
         │Scheduler │ ◄───────────────────────┘
         │ caller ──┼──┐
         └──────────┘  │
                       ▼
         ┌──────────────┐
         │ StateHandler │  ← SAME instance serves both tasks
         └──────────────┘
```

**Spawn implementation:**

```python
class SchedulerHandler:
    def handle(self, effect, k):
        if isinstance(effect, Spawn):
            # CreateContinuation just creates a body segment
            # with caller → scheduler's segment
            task_k = yield CreateContinuation(effect.task)
            task_id = self.register_task(task_k)
            yield Resume(k, task_id)
```

`CreateContinuation(program)` creates a continuation whose body segment has
`caller = current segment` (the scheduler's handler segment). When the task
yields an effect:

1. Walk up from task body segment via caller
2. Hit scheduler's segment — doesn't handle Get → continue up
3. Hit StateHandler's segment — handles Get → dispatch

No `GetHandlers`, no `clone_spawn_scope_chain`, no `scope_parent`. Effects
delegate up the caller chain naturally. All tasks share the same handler
instances — same `AllocVar`'d variables, same state.

### 4. Scoped Variables Replace RustStore Fields

Handler state moves from handler-specific RustStore fields to generic scoped
variables that live in segments:

```
AllocVar(initial_value) → VarId
    Allocates a variable in the current segment.
    Returns opaque VarId. Lifetime tied to segment.

ReadVar(VarId) → Value
    Reads variable. Walks caller chain if not in current segment.

WriteVar(VarId, Value) → ()
    Writes to current segment (shadow semantics).

WriteVarNonlocal(VarId, Value) → ()
    Writes to the segment where VarId was allocated (nonlocal/mutate semantics).
```

Handler factories allocate variables once via `AllocVar`:

```python
class StateHandlerFactory:
    def create(self, initial_value):
        var = yield AllocVar(initial_value)  # in handler's segment
        return StateHandler(var)

class StateHandler:
    def handle(self, effect, k):
        if isinstance(effect, Get):
            value = yield ReadVar(self.var)       # walks caller chain
            yield Resume(k, value)
        elif isinstance(effect, Put):
            yield WriteVarNonlocal(self.var, effect.value)  # mutates handler's segment
            yield Resume(k, None)
```

Since spawned tasks share the handler via caller chain, `ReadVar` from a spawned
task walks up to the StateHandler's segment and reads the same variable. `Put` from
any task writes to the same variable via `WriteVarNonlocal`. This is exactly OCaml's
model where handler state is a mutable ref shared by all tasks.

**Remove from RustStore:** `state`, `env`, `log` fields.
**Remove from Segment:** `ScopeStore`, `scope_store` field.

### 5. EvalInScope for Local/Ask

`Local(bindings, body)` creates a child segment with shadow variables:

```python
class LazyAskHandler:
    def handle(self, effect, k):
        if isinstance(effect, Local):
            result = yield EvalInScope(effect.body, effect.overrides)
            yield Resume(k, result)
        elif isinstance(effect, Ask):
            value = yield ReadVar(effect.key)  # walks caller chain
            yield Resume(k, value)
```

`EvalInScope(body, bindings)` is a VM primitive that:
1. Creates a child segment with `caller = current segment`
2. Allocates variables from bindings in the child segment
3. Executes body in the child segment
4. On completion, pops child segment (variables dropped)
5. Returns result to caller

`ReadVar` from inside the body walks the caller chain: child segment → handler
segment → parent handlers. Shadow variables in the child segment are found first.

## Open Questions

### Handler Re-entrancy

When handler A is handling E1 and resumes k, and k yields E2 also handled by A:

```
1. body yields E1 → A handles it, yields Resume(k1, v)
2. VM resumes k1 → body yields E2 → walks up → finds A again
3. A needs to handle E2, but A's generator from step 1 may still be alive
```

In OCaml deep handlers, this is natural — the handler's `match` block is re-entered.
In doeff, the handler is a generator. If step 1's generator is done (Resume was final
yield), A's segment is clean. If not, there's a conflict.

**Current approach:** Each dispatch creates a new handler program invocation. The
previous invocation's state is captured as part of k. Need to verify this works
cleanly with the simplified dispatch.

### Variable Sharing Semantics in Spawn

Shared handlers mean shared variables (OCaml model). This means:
- `Put(42)` in task A → `Get()` in task B sees 42
- `Tell("msg")` in task A → visible to parent's writer log

This is correct for cooperative scheduling (one task at a time). For future
parallel execution, may need explicit isolation via `WithHandler` wrapping.

### Performance of Caller Chain Walking

`ReadVar` walks the caller chain — O(depth). For hot paths (Ask in deeply nested
handlers), may need caching. OCaml's fiber chain walk is also O(depth) but with
hardware-friendly memory layout (contiguous stack chunks).

## Migration Plan

### Phase 1: Remove Special Dispatch Frames

- Add `consumed: bool` to `Continuation`
- Move one-shot check to Resume/Transfer processing
- Remove `HandlerDispatch` frame type
- Remove `DispatchOrigin` frame type
- Move error enrichment to throw-time assembly
- Move tracing to separate observer
- All existing tests must pass

### Phase 2: Simplify Spawn (Shared Handlers)

- `CreateContinuation(program)` creates body segment with `caller = current`
- Remove `clone_spawn_scope_chain`
- Remove `scope_parent` (caller chain is sufficient)
- Remove `GetHandlers` from spawn path
- Spawned task effects delegate up caller chain to parent's handlers
- Test: handler count in spawned task = handler count in parent (no duplication)
- Test: State/Writer shared between parent and spawned tasks

### Phase 3: Scoped Variables

- Add `variables: HashMap<VarId, Value>` to Segment
- Add `AllocVar`, `ReadVar`, `WriteVar`, `WriteVarNonlocal` DoCtrl
- `ReadVar` walks caller chain
- Migrate StateHandler, WriterHandler, LazyAskHandler to scoped variables
- Remove `RustStore.state`, `RustStore.env`, `RustStore.log`
- Remove `ScopeStore` from Segment

### Phase 4: Clean Up

- Remove unused dispatch infrastructure (DispatchId, dispatch modes)
- Simplify `Mode` enum (may not need `HandleYield`)
- Evaluate `start_dispatch` simplification
- Remove accumulated special cases

## TDD Test Plan

### Dispatch Simplification Tests

```python
# T1: Basic dispatch still works without special frames
def test_basic_effect_dispatch():
    result = yield WithHandler(StateHandler(0), get_put_program())
    assert result == expected

# T2: One-shot enforcement via Continuation.consumed flag
def test_one_shot_enforcement():
    # Handler that tries to resume k twice → error

# T3: Nested handlers dispatch correctly
def test_nested_handler_dispatch():
    # WithHandler(A, WithHandler(B, body))
    # body yields effect for A → skips B → reaches A

# T4: Self-dispatch exclusion
def test_self_dispatch_exclusion():
    # Handler A performs effect that A handles → should NOT catch itself
    # Effect should propagate to outer handler
```

### Shared Handler Tests (Spawn)

```python
# T5: Spawned task sees parent's handler
def test_spawn_delegates_to_parent_handler():
    # Spawn(task) where task yields Get
    # Get handled by StateHandler ABOVE scheduler
    # Same handler instance as parent

# T6: State shared between parent and spawned task
def test_spawn_shared_state():
    yield Put(42)
    task = yield Spawn(get_program())
    result = yield Gather(task)
    assert result == 42  # child sees parent's state

# T7: Spawned task Put visible to parent
def test_spawn_put_visible_to_parent():
    yield Put(0)
    task = yield Spawn(put_program(42))
    yield Gather(task)
    assert (yield Get()) == 42  # parent sees child's Put

# T8: No handler duplication in spawn
def test_spawn_no_handler_duplication():
    # Handler count inside spawned task = handler count in direct call

# T9: N=500 spawn no quadratic
def test_spawn_500_no_quadratic():
    # 500 spawned tasks, each does Get
    # All share same StateHandler — O(N) not O(N²)
```

### Scoped Variable Tests

```python
# T10: AllocVar + ReadVar basic
def test_alloc_and_read_var():
    var = yield AllocVar(42)
    assert (yield ReadVar(var)) == 42

# T11: ReadVar walks caller chain
def test_read_var_walks_caller():
    # Variable in outer handler segment
    # Read from inner segment → found via caller chain

# T12: WriteVarNonlocal mutates original segment
def test_nonlocal_write():
    var = yield AllocVar(10)
    # EvalInScope body: WriteVarNonlocal(var, 20)
    assert (yield ReadVar(var)) == 20  # mutated

# T13: Shadow write in child segment
def test_shadow_write():
    var = yield AllocVar(10)
    # EvalInScope body: WriteVar(var, 20) → shadows
    assert (yield ReadVar(var)) == 10  # parent unchanged

# T14: Variable dropped on segment pop
def test_var_dropped_on_pop():
    # AllocVar in EvalInScope → after scope ends, var gone
```

### Local/Ask on Scoped Variables

```python
# T15: Local creates scope, Ask reads
def test_local_ask():
    result = yield Local({"key": "value"}, ask_program("key"))
    assert result == "value"

# T16: Nested Local shadow
def test_nested_local_shadow():
    result = yield Local({"x": 10}, Local({"x": 20}, ask_program("x")))
    assert result == 20

# T17: Ask walks caller chain to outer Local
def test_ask_walks_to_outer():
    result = yield Local({"x": 10}, Local({"y": 20}, ask_program("x")))
    assert result == 10  # x not in inner, found in outer
```
