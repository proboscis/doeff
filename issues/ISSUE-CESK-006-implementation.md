# ISSUE: Implement SPEC-CESK-006 Layered Algebraic Effects

## Related Spec
`specs/cesk-architecture/SPEC-CESK-006-two-level-effects.md`

## Summary

Implement the layered algebraic effects architecture as defined in SPEC-CESK-006. This is a rewrite of `step.py` (v1) with proper three-level architecture.

## Implementation Approach

This is **v3** of the CESK implementation. We keep v1 tests intact and migrate them incrementally.

### Test Migration Strategy

```
tests/cesk/           # v1 tests (existing) - keep intact, mark as v1
tests/cesk_v3/        # v3 tests (new) - migrated + new tests per spec
```

**For each phase:**
1. Identify v1 tests that cover similar functionality
2. Copy relevant tests to `tests/cesk_v3/` 
3. Adapt to v3 API (new imports, new primitives)
4. Write additional tests based on spec (for new behavior)
5. Implement v3 code until all v3 tests pass
6. v1 tests remain intact (can run separately)

**Why this approach:**
- Don't lose valuable test cases from v1
- v1 tests serve as reference for expected behavior
- Incremental migration reduces risk
- Can run v1 and v3 tests independently during transition
- After full migration, v1 tests can be removed

### Directory Structure During Development

```
doeff/cesk/                    # v1 implementation (existing, keep until Phase 8)
doeff/cesk_v3/                 # v3 implementation (new, per this spec)
    ├── level1_cesk/
    ├── level2_algebraic_effects/
    ├── level3_user_effects/
    └── run.py

tests/cesk/                    # v1 tests (existing, keep as reference)
tests/cesk_v3/                 # v3 tests (new + migrated from v1)
    ├── level1_cesk/
    ├── level2_algebraic_effects/
    ├── level3_user_effects/
    ├── integration/
    └── invariants/
```

### Running Tests

```bash
# During development - run only v3 tests
pytest tests/cesk_v3/

# Check v1 still works (optional, for reference)
pytest tests/cesk/

# After Phase 8 cutover - v3 becomes main
pytest tests/cesk/
```

## Phases

### Phase 1: Module Structure & Types (Foundation)

**Goal**: Create module structure and all type definitions. No logic yet.

**Step 1 - Migrate/create tests**:

Check v1 tests for relevant cases:
```bash
# Find existing type-related tests
ls tests/cesk/test_*.py | xargs grep -l "CESKState\|ReturnFrame\|HandlerFrame"
```

Create v3 test structure:
```
tests/cesk_v3/
├── conftest.py           # Shared fixtures for v3
├── level1_cesk/
│   ├── conftest.py
│   └── test_types.py     # Type construction, immutability, frozen dataclass
│
├── level2_algebraic_effects/
│   ├── conftest.py
│   └── test_state.py     # HandlerEntry methods, AlgebraicEffectsState methods
│
└── level3_user_effects/
    ├── conftest.py
    └── test_base.py      # EffectBase inheritance
```

**Step 2 - Implementation**:
```
doeff/cesk/
├── level1_cesk/
│   ├── __init__.py
│   ├── state.py          # CESKState, Value, Error, EffectYield, ProgramControl, Done, Failed
│   ├── frames.py         # ReturnFrame, WithHandlerFrame
│   └── types.py          # Kontinuation, Environment, Store type aliases
│
├── level2_algebraic_effects/
│   ├── __init__.py
│   ├── state.py          # AlgebraicEffectsState, HandlerEntry, DOEFF_INTERNAL_AE
│   ├── primitives.py     # ControlPrimitive, WithHandler, Resume, Abort, GetContinuation, etc.
│   └── handle.py         # ContinuationHandle, Handler type alias
│
├── level3_user_effects/
│   ├── __init__.py
│   └── base.py           # EffectBase
│
└── errors.py             # UnhandledEffectError, etc.
```

**Exit criteria**: All types importable, pyright passes, all Phase 1 tests pass.

---

### Phase 2: Level 1 CESK Machine (Pure Stepper)

**Goal**: Implement `cesk_step()` - pure CESK machine with no effect knowledge.

**Step 1 - Tests first**:
- `tests/cesk/level1_cesk/test_cesk_step.py` - State transitions
- `tests/cesk/level1_cesk/test_generator_protocol.py` - next/send/throw
- `tests/cesk/level1_cesk/test_terminal_states.py` - Done/Failed conditions

**Step 2 - Implementation**:
```
doeff/cesk/level1_cesk/
└── step.py               # cesk_step() implementation
```

**Behavior to implement**:
- `ProgramControl` → start generator, push ReturnFrame
- `Value` + ReturnFrame → send to generator
- `Error` + ReturnFrame → throw to generator  
- `Value` + empty K → `Done`
- `Error` + empty K → `Failed`
- Assert: K[0] must be ReturnFrame when processing

**Exit criteria**: Level 1 can step pure generator programs (no effects) to completion. All Phase 2 tests pass.

---

### Phase 3: Level 2 Step & WithHandler (Handler Installation)

**Goal**: Implement `level2_step()` that wraps Level 1, handle `WithHandler` and `WithHandlerFrame`.

**Step 1 - Tests first**:
- `tests/cesk/level2_algebraic_effects/test_level2_step.py` - Wrapping behavior
- `tests/cesk/level2_algebraic_effects/test_with_handler.py` - Handler installation
- `tests/cesk/invariants/test_l1_never_sees_whf.py` - WHF interception

**Step 2 - Implementation**:
```
doeff/cesk/level2_algebraic_effects/
├── step.py               # level2_step() - wraps cesk_step
└── translate.py          # translate_control_primitive() - WithHandler only
```

**Behavior to implement**:
- PRE-STEP: If `C=Value` and `K[0]=WHF` → pop handler, continue
- DELEGATE: Call `cesk_step()`
- POST-STEP: If `EffectYield(ControlPrimitive)` → translate
- `WithHandler` translation: push handler (at END), push WHF, start program

**Exit criteria**: Can install handlers, WHF correctly pops handlers on scope end. All Phase 3 tests pass.

---

### Phase 4: Level 2 Resume & Abort (Continuation Management)

**Goal**: Implement `Resume` and `Abort` control primitives.

**Step 1 - Tests first**:
- `tests/cesk/level2_algebraic_effects/test_resume.py` - K concatenation, value flow
- `tests/cesk/level2_algebraic_effects/test_abort.py` - Cleanup, warning
- `tests/cesk/level2_algebraic_effects/test_one_shot.py` - Consumption tracking
- `tests/cesk/invariants/test_one_shot_violations.py` - Double-resume error

**Step 2 - Implementation**:
- Update `translate.py` with Resume, Abort cases
- One-shot tracking in AlgebraicEffectsState

**Behavior to implement**:
- `Resume`: Get captured_k from active handler, concatenate K, send value
- `Abort`: Clean up captured_k (close generators), warn, continue with handler's K only
- One-shot enforcement: Track consumed k_ids, raise on double-resume

**Exit criteria**: Resume/Abort work correctly, one-shot enforced. All Phase 4 tests pass.

---

### Phase 5: Level 3 User Effect Dispatch

**Goal**: Implement `translate_user_effect()` - dispatch effects to handlers.

**Step 1 - Tests first**:
- `tests/cesk/level3_user_effects/test_handler_dispatch.py` - Finding handlers
- `tests/cesk/level3_user_effects/test_effect_forwarding.py` - active_handler_index
- `tests/cesk/invariants/test_control_primitive_intercept.py` - ControlPrimitive blocked

**Step 2 - Implementation**:
```
doeff/cesk/level3_user_effects/
└── translate.py          # translate_user_effect()
```

**Behavior to implement**:
- Assert not ControlPrimitive
- Find handler (search from END, innermost first)
- Capture K into handler's slot (per-handler captured_k)
- Invoke handler, set K=[]

**Exit criteria**: Effects dispatched to correct handler, forwarding works. All Phase 5 tests pass.

---

### Phase 6: Main Loop & Basic Handlers

**Goal**: Wire up `run()` and implement basic handlers (state, reader).

**Step 1 - Tests first**:
- `tests/cesk/integration/test_full_stack.py` - End-to-end execution
- `tests/cesk/integration/test_state_handler.py` - Get/Put effects
- `tests/cesk/integration/test_reader_handler.py` - Ask effect

**Step 2 - Implementation**:
```
doeff/cesk/
├── run.py                # Main loop: run()
└── handlers/
    ├── __init__.py
    ├── state.py          # Get/Put handler using new primitives
    └── reader.py         # Ask handler using new primitives
```

**Behavior to implement**:
- `run()`: Loop calling level2_step, translate user effects
- State handler: `yield AskStore()`, `yield ModifyStore()`, `yield Resume()`
- Reader handler: `yield AskEnv()`, `yield Resume()`

**Exit criteria**: Can run programs with Get/Put/Ask effects end-to-end. All Phase 6 tests pass.

---

### Phase 7: Nested Handlers & Edge Cases

**Goal**: Verify nested handler dispatch works correctly (the two-stack model).

**Step 1 - Tests first** (these test the hardest scenarios):
- `tests/cesk/integration/test_nested_handlers.py` - Basic nesting
- `tests/cesk/integration/test_handler_yields_with_handler.py` - Handler installing nested handler
- `tests/cesk/integration/test_handler_errors.py` - Exception propagation
- `tests/cesk/invariants/test_index_stability.py` - active_handler_index stays valid
- `tests/cesk/invariants/test_paired_push_pop.py` - HandlerEntry/WHF pairing

**Step 2 - Fix any issues found by tests**

**Exit criteria**: All nested handler scenarios from spec work correctly. All Phase 7 tests pass.

---

### Phase 8: Handler Migration & Cleanup

**Goal**: Migrate remaining handlers, remove old code.

**Step 1 - Migrate remaining v1 tests to v3**:
- Copy remaining relevant tests from `tests/cesk/` to `tests/cesk_v3/`
- Adapt to v3 API
- Ensure all migrated tests pass

**Step 2 - Migrate handlers**:
1. Migrate each existing handler to new primitives:
   - writer handler
   - cache handler  
   - scheduler handlers
   - async handlers

**Step 3 - Cutover**:
1. Replace `doeff/cesk/` with v3 implementation
2. Move `tests/cesk_v3/` to `tests/cesk/` (replace old tests)
3. Remove deprecated files: `step.py` (old v1), `step_v2.py`, `handler_frame.py`
4. Update `__init__.py` exports
5. Run full test suite

**Exit criteria**: All handlers migrated, v3 is now the main implementation, full test suite passes.

---

### Phase 9: Enforcement & Documentation

**Goal**: Add static analysis rules, finalize documentation.

**Step 1 - Add semgrep rules** (test by running on codebase):
- Block direct access to `__doeff_internal_ae__` outside Level 2
- Warn on multiple Resume in handler
- Block ControlPrimitive subclasses outside Level 2

**Step 2 - Documentation**:
- Update docstrings in all new modules
- Type stubs if needed for public API
- Update README if needed

**Exit criteria**: `make lint` passes with new rules, docs complete.

---

## Phase Summary

| Phase | Focus | Key Deliverable | Est. Effort |
|-------|-------|-----------------|-------------|
| 1 | Foundation | Module structure, all types | 2-3h |
| 2 | Level 1 | `cesk_step()` - pure CESK | 3-4h |
| 3 | Level 2 (install) | `level2_step()`, WithHandler | 3-4h |
| 4 | Level 2 (resume) | Resume, Abort, one-shot | 3-4h |
| 5 | Level 3 | `translate_user_effect()` | 2-3h |
| 6 | Integration | `run()`, basic handlers | 3-4h |
| 7 | Edge cases | Nested handlers, errors | 2-3h |
| 8 | Migration | Handler migration, cleanup | 4-6h |
| 9 | Polish | Semgrep, docs | 2-3h |

**Total estimated effort**: ~3-4 days

## Acceptance Criteria

- [ ] All phases completed with tests passing
- [ ] `make lint` passes (including new semgrep rules)
- [ ] `make test` passes (all existing + new tests)
- [ ] Old files removed (`step_v2.py`, hacks)
- [ ] Spec invariants enforced (runtime assertions + semgrep)

## Dependencies

- SPEC-CESK-006 (this implements it)

## Notes

- Each phase should be a separate PR for easier review
- Phase 1-6 can potentially be combined if moving fast
- Phase 8 (migration) may reveal edge cases requiring spec updates
