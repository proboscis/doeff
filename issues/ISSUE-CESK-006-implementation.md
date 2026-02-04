# ISSUE: Implement SPEC-CESK-006 Layered Algebraic Effects

## Related Spec
`specs/cesk-architecture/SPEC-CESK-006-two-level-effects.md`

## Summary

Implement the layered algebraic effects architecture as defined in SPEC-CESK-006. This is a rewrite of `step.py` (v1) with proper three-level architecture.

## Current Status

**109 tests passing** in `tests/cesk_v3/`

### Completed
- [x] Phase 1: Module Structure & Types
- [x] Phase 2: Level 1 CESK Machine (`cesk_step`)
- [x] Phase 3: Level 2 WithHandler
- [x] Phase 4: Level 2 Resume, Forward
- [x] Phase 5: Level 2 Dispatch (DispatchingFrame)
- [x] Phase 6: Simple `run()` loop
- [x] Phase 7: Nested handlers, implicit abandonment
- [x] Phase 8: Scheduling Primitives (GetContinuation, ResumeContinuation)
- [x] Phase 9: Fiber Creation (GetHandlers, CreateContinuation)

### In Progress
- [ ] Phase 10: Async Execution Architecture
- [ ] Phase 11: Run Functions (sync_run, async_run)
- [ ] Phase 12: Handler Presets
- [ ] Phase 13: Async Integration Tests

### Pending
- [ ] Phase 14: Level 3 Core Effects (full implementation)
- [ ] Phase 15: Handler Migration from v1
- [ ] Phase 16: Cutover & Cleanup

---

## Phase 10: Async Execution Architecture

**Goal**: Add `PythonAsyncSyntaxEscape` as a Level 2 control primitive and step result.

**Reference**: SPEC-CESK-006 "Async Execution Architecture" section, ADR-15

**Key insight**: `PythonAsyncSyntaxEscape` is a Python syntax workaround, NOT a fundamental primitive. sync_run is the clean implementation; async_run exists for asyncio integration.

### Step 1 - Add PythonAsyncSyntaxEscape primitive

File: `doeff/cesk_v3/level2_algebraic_effects/primitives.py`

```python
@dataclass(frozen=True)
class PythonAsyncSyntaxEscape(ControlPrimitive):
    """Escape to Python's async event loop.
    
    WHY THIS EXISTS: Python's asyncio APIs require async def context.
    Handlers run during step() which is synchronous. This escape lets
    handlers say "please run this in an async context".
    
    - Handler yields with VALUE-returning action
    - level2_step wraps to STATE-returning action
    - async_run awaits and gets CESKState directly
    """
    action: Callable[[], Awaitable[Any]]
```

### Step 2 - Add handle_async_escape to Level 2

File: `doeff/cesk_v3/level2_algebraic_effects/handlers.py`

```python
def handle_async_escape(
    escape: PythonAsyncSyntaxEscape, state: CESKState
) -> PythonAsyncSyntaxEscape:
    """Wrap handler's value-returning action to return CESKState."""
    C, E, S, K = state.C, state.E, state.S, state.K
    original_action = escape.action
    
    async def wrapped_action() -> CESKState:
        value = await original_action()
        return CESKState(C=Value(value), E=E, S=S, K=K)
    
    return PythonAsyncSyntaxEscape(action=wrapped_action)
```

### Step 3 - Update level2_step return type

File: `doeff/cesk_v3/level2_algebraic_effects/step.py`

```python
def level2_step(state: CESKState) -> CESKState | Done | Failed | PythonAsyncSyntaxEscape:
    # ... existing code ...
    
    if isinstance(C, EffectYield):
        yielded = C.yielded
        
        # ... existing primitives ...
        
        if isinstance(yielded, PythonAsyncSyntaxEscape):
            return handle_async_escape(yielded, state)
```

### Step 4 - Tests

File: `tests/cesk_v3/level2/test_async_escape.py`

- Handler yields escape with value-returning action
- level2_step wraps and returns state-returning escape
- Action captures correct E, S, K

**Exit criteria**: PythonAsyncSyntaxEscape flows from handler through level2_step with correct wrapping.

---

## Phase 11: Run Functions (sync_run, async_run)

**Goal**: Replace simple `run()` with `sync_run()` and `async_run()` returning `RunResult`.

**Reference**: SPEC-CESK-006 "Async Execution Architecture" section

### Step 1 - Define RunResult

File: `doeff/cesk_v3/result.py`

```python
@dataclass
class RunResult(Generic[T]):
    """Result of running a program, with CESK state access."""
    value: T | None = None
    error: BaseException | None = None
    final_store: dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_ok(self) -> bool:
        return self.error is None
    
    def unwrap(self) -> T:
        if self.error is not None:
            raise self.error
        return cast(T, self.value)
```

### Step 2 - Implement sync_run

File: `doeff/cesk_v3/run.py`

```python
def sync_run(
    program: Program[T],
    handlers: list[Handler],
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> RunResult[T]:
    """Run synchronously. Raises TypeError on PythonAsyncSyntaxEscape."""
    wrapped = _wrap_with_handlers(program, handlers)
    state = CESKState(C=ProgramControl(wrapped), E=env or {}, S=store or {}, K=[])
    
    while True:
        result = level2_step(state)
        
        if isinstance(result, Done):
            return RunResult(value=result.value, final_store=state.S)
        if isinstance(result, Failed):
            return RunResult(error=result.error, final_store=state.S)
        if isinstance(result, PythonAsyncSyntaxEscape):
            raise TypeError("sync_run received PythonAsyncSyntaxEscape. Use async_run.")
        
        state = result
```

### Step 3 - Implement async_run

```python
async def async_run(
    program: Program[T],
    handlers: list[Handler],
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> RunResult[T]:
    """Run asynchronously. Awaits PythonAsyncSyntaxEscape actions."""
    wrapped = _wrap_with_handlers(program, handlers)
    state = CESKState(C=ProgramControl(wrapped), E=env or {}, S=store or {}, K=[])
    
    while True:
        result = level2_step(state)
        
        if isinstance(result, Done):
            return RunResult(value=result.value, final_store=state.S)
        if isinstance(result, Failed):
            return RunResult(error=result.error, final_store=state.S)
        if isinstance(result, PythonAsyncSyntaxEscape):
            state = await result.action()
            await asyncio.sleep(0)
            continue
        
        state = result
        await asyncio.sleep(0)
```

### Step 4 - Tests

File: `tests/cesk_v3/test_run.py`

- sync_run returns RunResult with value
- sync_run returns RunResult with error
- sync_run raises TypeError on escape
- async_run awaits escapes and continues
- async_run returns RunResult

**Exit criteria**: Both run functions work, return RunResult, handle escapes correctly.

---

## Phase 12: Handler Presets

**Goal**: Create sync_handlers_preset and async_handlers_preset.

**Reference**: SPEC-CESK-006 "Handler Presets" section, ADR-16

### Step 1 - Implement sync_await_handler

File: `doeff/cesk_v3/level3_core_effects/asyncio_bridge.py`

Handler that handles `AwaitEffect` by running async in thread pool (no escape).

```python
@do
def sync_await_handler(effect: EffectBase) -> Program[Any]:
    if isinstance(effect, AwaitEffect):
        # Run in thread pool, block until complete
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(effect.awaitable)
        finally:
            loop.close()
        return (yield Resume(result))
    return (yield Forward(effect))
```

### Step 2 - Implement python_async_syntax_escape_handler

File: `doeff/cesk_v3/level3_core_effects/asyncio_bridge.py` (same file)

Handler that handles `AwaitEffect` by yielding `PythonAsyncSyntaxEscape`.

```python
@do
def python_async_syntax_escape_handler(effect: EffectBase) -> Program[Any]:
    if isinstance(effect, AwaitEffect):
        promise = yield CreateExternalPromise()
        
        async def fire_task():
            try:
                result = await effect.awaitable
                promise.complete(result)
            except BaseException as e:
                promise.fail(e)
        
        yield PythonAsyncSyntaxEscape(
            action=lambda: asyncio.create_task(fire_task())
        )
        
        result = yield Wait(promise.future)
        return (yield Resume(result))
    
    return (yield Forward(effect))
```

### Step 3 - Define presets

File: `doeff/cesk_v3/run.py` (alongside sync_run/async_run)

```python
sync_handlers_preset: list[Handler] = [
    state_handler({}),
    reader_handler({}),
    writer_handler(),
    sync_await_handler,
    # ... other handlers
]

async_handlers_preset: list[Handler] = [
    state_handler({}),
    reader_handler({}),
    writer_handler(),
    python_async_syntax_escape_handler,
    # ... other handlers
]
```

### Step 4 - Tests

File: `tests/cesk_v3/test_presets.py`

- sync_handlers_preset works with sync_run
- async_handlers_preset works with async_run
- Await effect works in both contexts

**Exit criteria**: Both presets defined, work with respective run functions.

---

## Phase 13: Async Integration Tests

**Goal**: Comprehensive tests for async execution.

### Test Files

```
tests/cesk_v3/
├── test_run.py                  # sync_run, async_run basics
├── test_presets.py              # Handler preset tests
├── test_async_integration.py    # Full async integration
└── level2/
    └── test_async_escape.py     # PythonAsyncSyntaxEscape Level 2 tests
```

### Test Cases

**File: `tests/cesk_v3/test_run.py`**
- sync_run returns RunResult with value
- sync_run returns RunResult with error
- sync_run raises TypeError on escape
- async_run awaits escapes and continues
- async_run returns RunResult

**File: `tests/cesk_v3/test_presets.py`**
- sync_handlers_preset works with sync_run
- async_handlers_preset works with async_run
- Both presets handle State/Reader/Writer identically

**File: `tests/cesk_v3/test_async_integration.py`**

1. **sync_run with sync_handlers_preset**
   - Pure programs work
   - State/Reader/Writer effects work
   - Await effect via thread pool works

2. **async_run with async_handlers_preset**
   - Pure programs work
   - State/Reader/Writer effects work
   - Await effect via escape works
   - Multiple awaits in sequence
   - Concurrent awaits (Gather pattern)

3. **Error handling**
   - Exception in awaitable propagates
   - sync_run raises TypeError on escape
   - Error in handler propagates to RunResult

4. **Integration with existing scheduling**
   - Spawn + Await combination
   - Cooperative scheduling with async effects

**Exit criteria**: All async integration tests pass.

---

## Phase 14: Level 3 Core Effects (Full Implementation)

**Goal**: Implement remaining core effects from spec.

### Effects to implement

Per SPEC-CESK-006 Level 3 section:

| Category | Effects | Handler |
|----------|---------|---------|
| Writer | WriterTellEffect, WriterListenEffect | writer_handler |
| Cache | CacheGet, CachePut, CacheDelete, CacheExists | cache_handler |
| Atomic | AtomicGet, AtomicUpdate | atomic_handler |
| Result | ResultSafeEffect | result_handler |
| Promise | CreatePromise, CompletePromise, FailPromise | promise_handler |
| Scheduler | Spawn, Wait, Gather, Race | scheduler_handler |
| Debug | Graph, Intercept, Debug, Callstack | various |

### Files to create

```
doeff/cesk_v3/level3_core_effects/
│
│   # Fundamental Effects (pure, closure-based handlers)
├── state.py            # ✅ Done - Get, Put, Modify + state_handler
├── reader.py           # ✅ Done (partial) - Ask + reader_handler
├── writer.py           # 🔄 In progress - Tell, Listen + writer_handler
├── cache.py            # ⏳ Pending - CacheGet/Put/Delete/Exists + cache_handler
├── atomic.py           # ⏳ Pending - AtomicGet/Update + atomic_handler
├── result.py           # ⏳ Pending - Safe + result_handler
│
│   # Cooperative Scheduling Effects
├── promise.py          # ⏳ Pending - CreatePromise/Complete/Fail + promise_handler
├── scheduler.py        # ⏳ Pending - Spawn/Wait/Gather/Race + scheduler_handler
│
│   # External Integration Effects
├── asyncio_bridge.py   # ⏳ Pending (Phase 12) - AwaitEffect
│                       #   + sync_await_handler (thread pool)
│                       #   + python_async_syntax_escape_handler (escapes)
├── external_promise.py # ⏳ Pending - CreateExternalPromise
│                       #   + sync_external_wait_handler
│                       #   + async_external_wait_handler
│
│   # Debugging/Introspection Effects
├── graph.py            # ⏳ Pending - GraphStep/Annotate/Snapshot/Capture + graph_handler
├── intercept.py        # ⏳ Pending - InterceptEffect + intercept_handler
├── debug.py            # ⏳ Pending - GetDebugContext + debug_handler
└── callstack.py        # ⏳ Pending - ProgramCallFrame/Stack + callstack_handler
```

**Exit criteria**: All core effects implemented with tests.

---

## Phase 15: Handler Migration from v1

**Goal**: Migrate v1 handlers to v3 API, ensure feature parity.

### v1 handlers to migrate

```
doeff/cesk/handlers/
├── state_handler.py           → use v3 state.py
├── writer_handler.py          → use v3 writer.py
├── cache_handler.py           → use v3 cache.py
├── atomic_handler.py          → use v3 atomic.py
├── graph_handler.py           → use v3 graph.py
├── core_handler.py            → integrate into v3
├── scheduler_state_handler.py → use v3 scheduler.py
├── task_scheduler_handler.py  → use v3 scheduler.py
├── sync_await_handler.py      → use v3 sync_await.py
├── async_external_wait_handler.py → use v3 async handlers
└── python_async_syntax_escape_handler.py → use v3 async_escape.py
```

### Migration approach

1. Compare v1 handler behavior with v3 implementation
2. Port any missing features/edge cases
3. Ensure v1 tests pass against v3 handlers
4. Mark v1 handlers as deprecated

**Exit criteria**: v3 handlers have feature parity with v1.

---

## Phase 16: Cutover & Cleanup

**Goal**: Make v3 the main implementation, remove v1.

### Steps

1. **Update imports**
   - `doeff/__init__.py` exports v3 run functions
   - `doeff/cesk/__init__.py` deprecated, points to v3

2. **Move tests**
   - `tests/cesk_v3/` → becomes main test location
   - v1-specific tests marked/removed

3. **Remove deprecated code**
   - `doeff/cesk/step.py` (old v1)
   - `doeff/cesk/step_v2.py`
   - `doeff/cesk/handler_frame.py`
   - Any other v1-only files

4. **Documentation**
   - Update docs to reference v3 API
   - Migration guide for v1 → v3

5. **Final validation**
   - `make lint` passes
   - `make test` passes (full suite)
   - Example programs work

**Exit criteria**: v3 is the only CESK implementation, all tests pass.

---

## Phase Summary

| Phase | Focus | Status | Est. Effort |
|-------|-------|--------|-------------|
| 1-9 | Level 1-2-3 Foundation | ✅ Done | - |
| 10 | PythonAsyncSyntaxEscape | ⏳ Next | 2-3h |
| 11 | sync_run / async_run | ⏳ Pending | 2-3h |
| 12 | Handler Presets | ⏳ Pending | 3-4h |
| 13 | Async Integration Tests | ⏳ Pending | 2-3h |
| 14 | Level 3 Core Effects | ⏳ Pending | 4-6h |
| 15 | Handler Migration | ⏳ Pending | 4-6h |
| 16 | Cutover & Cleanup | ⏳ Pending | 2-3h |

**Remaining effort**: ~3-4 days

## Commands

```bash
# Run v3 tests
uv run pytest tests/cesk_v3/ -v

# Type check v3
uv run pyright doeff/cesk_v3/

# Full lint
make lint
```

## Acceptance Criteria

- [ ] All phases completed with tests passing
- [ ] `make lint` passes
- [ ] `make test` passes (all existing + new tests)
- [ ] sync_run and async_run work correctly
- [ ] Handler presets provide expected behavior
- [ ] Old v1 code removed

## Dependencies

- SPEC-CESK-006 (this implements it)
- SPEC-CESK-005 (PythonAsyncSyntaxEscape design)
