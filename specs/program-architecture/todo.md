# Refactoring Todo: Program → Effect | KleisliProgramCall

## Comparison: Spec vs Current Implementation

### Current State

| Component | Current Implementation | Spec Proposal |
|-----------|----------------------|---------------|
| **Program class** | Concrete dataclass with `generator_func` field | Protocol or removed entirely |
| **Effect** | Standalone ADT, not a Program | Implements Program protocol, executable directly |
| **KleisliProgram** | Holds `Callable[P, Program[T]]` | **Unchanged** (still holds generator-creating function) |
| **KleisliProgramCall** | Doesn't exist | **NEW**: Concrete class holding generator instance + call metadata |
| **Interpreter** | Handles `Effect \| Program` | Handles `Effect \| KleisliProgramCall` |
| **Program.pure()** | Returns `Program` wrapping generator | Returns `PureEffect` |
| **Program.from_effect()** | Removed (effects are already programs) | Removed |
| **KleisliProgram.__call__()** | Returns `Program` | Returns `KleisliProgramCall` |

**Key Distinction:**
- `KleisliProgram`: Unbound (holds `Callable[P, Generator[Program, Any, T]]`)
- `KleisliProgramCall`: Bound (holds SAME function + args, like partial application)
- **Same function, different binding!** `to_generator()` creates the actual generator.
- **Generator yields**: `Program` where `Program = Effect | KPCall`

| **_InterceptedProgram** | Subclass of `Program` | Subclass of `KleisliProgramCall` |
| **map/flat_map/intercept** | Methods on `Program` class | Methods on Protocol, implemented by both Effect and KPCall |

### Files to Change

| File | Current Role | Changes Needed |
|------|-------------|----------------|
| `doeff/program.py` | Defines `Program` class | Convert to Protocol, add `KleisliProgramCall` class |
| `doeff/types.py` | Defines `EffectBase` | Add `map/flat_map/intercept` methods |
| `doeff/effects/__init__.py` | Exports effects | Add `PureEffect` |
| `doeff/kleisli.py` | Defines `KleisliProgram` | Update `__call__` to return `KleisliProgramCall` |
| `doeff/do.py` | Defines `@do` decorator | Update to return `KleisliProgramCall` instead of `Program` |
| `doeff/interpreter.py` | Execution engine | Update to handle `KleisliProgramCall`, track call stack |
| `doeff/handlers/__init__.py` | Effect handlers | Add handler for `PureEffect` |
| All test files | Test current behavior | Update to use new types |

## Refactoring Phases

### Phase 0: Preparation (No Breaking Changes)

**Goal**: Add new types alongside existing ones, enable dual-mode.

#### Tasks

- [ ] **0.1: Add PureEffect**
  - Location: `doeff/effects/pure.py` (new file)
  - Create `PureEffect(EffectBase)` with `value: Any` field
  - Add handler in `ResultEffectHandler` to return `effect.value`
  - Export from `doeff/effects/__init__.py`
  - Test: `test_pure_effect.py`

- [ ] **0.2: Add KleisliProgramCall class**
  - Location: `doeff/program.py` (add to existing file)
  - Define `KleisliProgramCall` dataclass with:
    - `generator_func: Callable[P, Generator[Program, Any, T]]` - The generator-creating function
    - `args: tuple` - Bound arguments
    - `kwargs: dict` - Bound keyword arguments
    - `kleisli_source: KleisliProgram | None` - Which KP created this (link back)
    - `function_name: str` - Name of the source KP
    - `created_at: EffectCreationContext | None` - Where it was called
  - Add `to_generator()` method:
    ```python
    def to_generator(self) -> Generator[Program, Any, T]:
        return self.generator_func(*self.args, **self.kwargs)
    ```
  - Add classmethod constructors:
    - `create_from_kleisli(generator_func, kleisli, args, kwargs)` - Called by KP.__call__
    - `create_anonymous(generator_func, args, kwargs)` - For map/flat_map
    - `create_derived(generator_func, args, kwargs, parent)` - Preserve metadata
  - Don't implement `map/flat_map` yet (Phase 1)
  - Test: `test_kleisli_program_call.py`

  **Critical Note**:
  - `generator_func` is THE SAME function as `KleisliProgram.func`
  - KPCall = partial application (function + bound args)
  - `to_generator()` does the actual function call to create generator
  - Generator yields `Program` where `Program = Effect | KPCall`

- [ ] **0.3: Add CallFrame to ExecutionContext**
  - Location: `doeff/types.py`
  - Define `CallFrame` dataclass
  - Add `program_call_stack: list[CallFrame]` to `ExecutionContext`
  - Test: Check `ExecutionContext.copy()` preserves call stack

**Validation**: All existing tests pass, new types exist but aren't used yet.

---

### Phase 1: Make Effect Executable (Breaking: EffectBase API)

**Goal**: Add `map/flat_map/intercept` to `EffectBase` so Effects implement Program protocol.

#### Tasks

- [ ] **1.1: Define Program Protocol**
  - Location: `doeff/program.py`
  - Create `Program` Protocol with:
    ```python
    class Program(Protocol[T]):
        def map(self, f: Callable[[T], U]) -> Program[U]: ...
        def flat_map(self, f: Callable[[T], Program[U]]) -> Program[U]: ...
        def intercept(self, transform: Callable[[Effect], Effect | Program]) -> Program[T]: ...
    ```
  - Keep old `Program` class as `_LegacyProgram` for compatibility
  - Test: Type checker accepts Protocol

- [ ] **1.2: Implement map/flat_map/intercept on EffectBase**
  - Location: `doeff/types.py` (EffectBase class)
  - Implement:
    ```python
    def map(self, f):
        def mapped_gen():
            value = yield self
            return f(value)
        return KleisliProgramCall.create_anonymous(mapped_gen)

    def flat_map(self, f):
        def flatmapped_gen():
            value = yield self
            next_prog = f(value)
            result = yield next_prog
            return result
        return KleisliProgramCall.create_anonymous(flatmapped_gen)

    def intercept(self, transform):
        transformed = transform(self)
        # Return transformed effect or program
        return transformed
    ```
  - Test: `test_effect_monadic_methods.py`

- [ ] **1.3: Implement map/flat_map/intercept on KleisliProgramCall**
  - Location: `doeff/program.py` (KleisliProgramCall class)
  - Copy implementations from current `Program` class
  - Use `create_derived` to preserve metadata
  - Test: `test_kpcall_monadic_methods.py`

**Validation**: Effects can be mapped/chained like Programs.

---

### Phase 2: Update KleisliProgram to Return KPCall (Breaking: Return Type)

**Goal**: KleisliProgram.__call__ returns KleisliProgramCall instead of Program.

#### Tasks

- [ ] **2.1: Update KleisliProgram.__call__**
  - Location: `doeff/kleisli.py:219-275`
  - After unwrapping Program arguments, change to:
    ```python
    # Don't call the generator function yet!
    # Just bind the arguments
    return KleisliProgramCall.create_from_kleisli(
        generator_func=self.func,  # Pass the function itself
        kleisli=self,
        args=unwrapped_args,  # The unwrapped arguments
        kwargs=unwrapped_kwargs
    )
    ```
  - **Key**: Pass `self.func` (the generator-creating function), not a call to it
  - Test: `test_kleisli_returns_kpcall.py`

- [ ] **2.2: Update @do decorator**
  - Location: `doeff/do.py:107-133`
  - Change `return Program(generator_wrapper)` to:
    ```python
    return KleisliProgramCall.create_anonymous(generator_wrapper)
    ```
  - Test: Existing `test_do_decorator.py` should still pass

**Validation**: Calling a `@do` function returns `KleisliProgramCall`, existing code works via Protocol.

---

### Phase 3: Migrate Helper Functions (Breaking: Helper Return Types)

**Goal**: `Program.pure` returns `PureEffect`; effects can be used directly (no `from_effect`).

#### Tasks

- [ ] **3.1: Migrate Program.pure**
  - Location: `doeff/program.py:121-128`
  - Change to:
    ```python
    @staticmethod
    def pure(value: T) -> PureEffect:
        return PureEffect(value)
    ```
  - Find all usages: `grep -r "Program.pure"` (25 occurrences expected)
  - Update tests expecting `Program` type
  - Test: `test_program_pure_returns_effect.py`

- [x] **3.2: Remove Program.from_effect**
  - Location: `doeff/program.py:147-154`
  - Change to:
    ```python
    @staticmethod
    def from_effect(effect: Effect) -> Effect:
        return effect  # Just return the effect directly!
    ```
  - Find usages, update tests
  - Test: `test_from_effect_identity.py`

- [ ] **3.3: Migrate Program.lift**
  - Location: `doeff/program.py:136-144`
  - Update to handle new types:
    ```python
    @staticmethod
    def lift(value: Program[U] | U) -> Program[U]:
        if isinstance(value, KleisliProgramCall):
            return value
        if isinstance(value, Effect):
            return value
        return PureEffect(value)
    ```
  - Test: `test_program_lift.py`

- [ ] **3.4: Migrate sequence, traverse, list, tuple, set, dict**
  - Location: `doeff/program.py:205-265`
  - Update to return `KleisliProgramCall.create_anonymous(...)`
  - Test: `test_program_combinators.py`

**Validation**: All Program static methods return Effects or KPCalls.

---

### Phase 4: Update Interpreter (Breaking: Internal Loop Logic)

**Goal**: Interpreter handles Effect | KleisliProgramCall, builds call stack.

#### Tasks

- [ ] **4.1: Add call stack tracking**
  - Location: `doeff/interpreter.py:176-206`
  - In `run_async`, initialize `program_call_stack` if not present
  - Test: Stack initialized correctly

- [ ] **4.2: Update _execute_program_loop signature**
  - Location: `doeff/interpreter.py:208-258`
  - Change signature to accept `Program` (protocol)
  - Add fast path for Effects:
    ```python
    if isinstance(program, Effect):
        value = await self._handle_effect(program, ctx)
        return RunResult(ctx, Ok(value))
    ```
  - Test: Single effects execute directly

- [ ] **4.3: Add KPCall tracking**
  - Location: `doeff/interpreter.py:208-258`
  - Push CallFrame when entering KPCall with `kleisli_source`
  - Pop CallFrame on exit (try/finally)
  - Test: `test_call_stack_tracking.py`

- [ ] **4.4: Update loop to handle KPCall**
  - Location: `doeff/interpreter.py:221-258`
  - Change generator creation:
    ```python
    # OLD: gen = program.generator_func()
    # NEW: gen = program.to_generator()  # Calls with bound args
    ```
  - Change `isinstance(current, Program)` to `isinstance(current, KleisliProgramCall)`
  - Test: Nested KPCalls tracked correctly

- [ ] **4.5: Update _record_effect_usage**
  - Location: `doeff/interpreter.py:260-290`
  - Capture `call_stack_snapshot` from `ctx.program_call_stack`
  - Add to EffectObservation
  - Test: Effect observations include call stack

**Validation**: Interpreter builds call stack, all tests pass.

---

### Phase 5: Update _InterceptedProgram (Breaking: Internal Implementation)

**Goal**: Make _InterceptedProgram a KleisliProgramCall subclass.

#### Tasks

- [ ] **5.1: Refactor _InterceptedProgram**
  - Location: `doeff/program.py:279-415`
  - Change base class from `Program` to `KleisliProgramCall`
  - Store `base_program` and `transforms` as metadata
  - Update `compose` to work with KPCall
  - Test: Interception still works

- [ ] **5.2: Update intercept on KPCall**
  - Location: `doeff/program.py` (KleisliProgramCall class)
  - Ensure `intercept` returns `_InterceptedProgram`
  - Test: Multiple intercepts compose correctly

- [ ] **5.3: Verify metadata preservation**
  - Ensure `_InterceptedProgram.compose` preserves `kleisli_source`, `args`, etc.
  - Test: `test_intercept_preserves_metadata.py`

**Validation**: Interception preserves metadata, call tracking works through intercepts.

---

### Phase 6: Build Effect Call Tree (New Feature)

**Goal**: Expose call tree structure from observations.

#### Tasks

- [ ] **6.1: Add EffectObservation.call_stack_snapshot**
  - Location: `doeff/types.py:564-571`
  - Add `call_stack_snapshot: tuple[CallFrame, ...] = ()` field
  - Test: Observations capture stack

- [ ] **6.2: Create EffectCallTree builder**
  - Location: `doeff/analysis/call_tree.py` (new file)
  - Implement `EffectCallTree.from_observations()`
  - Build tree from call stack snapshots
  - Test: `test_call_tree_builder.py`

- [ ] **6.3: Add visualization**
  - Add `EffectCallTree.visualize_ascii()`
  - Format as tree showing program calls → effects
  - Test: `test_call_tree_visualization.py`

- [ ] **6.4: Add to RunResult.display()**
  - Location: `doeff/types.py` (RunResult class)
  - Add section showing effect call tree
  - Test: Display includes call tree

**Validation**: Users can see which KleisliProgram calls led to which effects.

---

### Phase 7: Deprecate Old Program Class (Breaking: Remove Legacy)

**Goal**: Remove `_LegacyProgram`, finalize migration.

#### Tasks

- [ ] **7.1: Remove _LegacyProgram**
  - Location: `doeff/program.py`
  - Delete old `Program` class (renamed to `_LegacyProgram` in Phase 1)
  - Ensure `Program` is only the Protocol
  - Test: All tests pass without legacy class

- [ ] **7.2: Update type stubs**
  - Location: `doeff/core.pyi`
  - Update `Program` definition to reflect protocol
  - Add `KleisliProgramCall` exports
  - Test: Type checker accepts new structure

- [ ] **7.3: Update documentation**
  - Update all docs mentioning `Program` class
  - Explain Effect | KleisliProgramCall abstraction
  - Add examples of call tracking
  - Test: Docs build without warnings

**Validation**: Clean abstraction, no legacy code remains.

---

### Phase 8: Performance & Polish

**Goal**: Optimize and refine the implementation.

#### Tasks

- [ ] **8.1: Benchmark performance**
  - Compare old vs new interpreter performance
  - Ensure call stack tracking overhead is minimal
  - Test: No significant regression

- [ ] **8.2: Optimize call stack operations**
  - Use pre-allocated lists if needed
  - Consider copy-on-write for stack snapshots
  - Test: Benchmarks improve

- [ ] **8.3: Add profiling helpers**
  - Expose call tree as profiling data
  - Add hot path detection
  - Test: Can identify bottlenecks

- [ ] **8.4: Final polish**
  - Clean up any remaining TODOs
  - Ensure all deprecation warnings removed
  - Add migration guide
  - Test: Full test suite passes

**Validation**: Production-ready, performant, well-documented.

---

## Testing Strategy

### Compatibility Tests (run continuously during migration)

```python
# Test old code works with new types via Protocol
def test_protocol_compatibility():
    effect: Program[int] = Ask("x")  # Effect satisfies Program
    kpcall: Program[int] = my_kleisli(5)  # KPCall satisfies Program
    assert_type_checks(effect, Program)
    assert_type_checks(kpcall, Program)

# Test existing code paths
def test_legacy_behavior():
    # These should still work
    prog = Program.pure(5)
    assert isinstance(prog, Effect)  # New behavior

    result = engine.run(prog)
    assert result.value == 5  # Same result
```

### New Feature Tests

```python
def test_call_stack_tracking():
    @do
    def outer():
        x = yield Ask("x")
        y = yield inner(x)
        return y

    @do
    def inner(x):
        return x + 1

    result = engine.run(outer())
    assert len(result.context.program_call_stack) == 0  # Cleaned up

    # Check observations have stack
    for obs in result.effect_observations:
        assert len(obs.call_stack_snapshot) > 0

def test_effect_call_tree():
    result = engine.run(complex_program())
    tree = EffectCallTree.from_observations(result.effect_observations)

    assert "outer" in tree.visualize_ascii()
    assert "inner" in tree.visualize_ascii()
    assert "Ask(x)" in tree.visualize_ascii()
```

## Migration Guide for Users

### Breaking Changes

1. **Program.pure** now returns `PureEffect` instead of `Program`
   - **Impact**: Type annotations expecting `Program` still work (Protocol)
   - **Action**: No code changes needed in most cases

2. **Program.from_effect** removed; effects are already Programs
   - **Impact**: Same as above
   - **Action**: No code changes needed

3. **Direct Program instantiation** no longer supported
   - **Impact**: Code doing `Program(generator_func)` breaks
   - **Action**: Use `@do` decorator or `KleisliProgramCall.create_anonymous`

### Upgrade Path

```python
# Before
def my_func():
    return Program(lambda: (yield Ask("x")))

# After - Option 1: Use @do
@do
def my_func():
    return (yield Ask("x"))

# After - Option 2: Use KPCall explicitly
def my_func():
    def gen():
        return (yield Ask("x"))
    return KleisliProgramCall.create_anonymous(gen)
```

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Breaking user code | High | High | Gradual migration, keep Protocol compatibility |
| Performance regression | Medium | Medium | Benchmark each phase, optimize call stack |
| Type checker issues | Medium | Low | Update stubs incrementally, test mypy |
| Complex refactoring errors | High | High | Small phases, extensive testing, can rollback |
| Documentation drift | Medium | Low | Update docs in parallel with code |

## Success Metrics

- [ ] All existing tests pass
- [ ] New call tracking tests pass
- [ ] Type checker validates new structure
- [ ] Performance within 5% of baseline
- [ ] Documentation updated
- [ ] Migration guide validated with real code

## Timeline Estimate

- Phase 0: 1-2 days (foundation)
- Phase 1: 2-3 days (Protocol + Effect methods)
- Phase 2: 1-2 days (KleisliProgram update)
- Phase 3: 2-3 days (Helper migration + test updates)
- Phase 4: 2-3 days (Interpreter refactor)
- Phase 5: 1-2 days (_InterceptedProgram)
- Phase 6: 2-3 days (Call tree feature)
- Phase 7: 1 day (Cleanup)
- Phase 8: 1-2 days (Polish)

**Total: ~15-23 days** (assumes full-time work, adjust for part-time)

## Next Steps

1. Review this plan with team
2. Get approval for breaking changes
3. Start Phase 0
4. Create feature branch
5. Implement incrementally
6. Merge when all phases complete
