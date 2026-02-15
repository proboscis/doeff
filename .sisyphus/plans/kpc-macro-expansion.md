# KPC True Macro Expansion — Call DoCtrl with Lazy Arg Evaluation

## TL;DR

> **Quick Summary**: Implement the actual macro model: `KleisliProgram.__call__()` produces a `Call` DoCtrl with DoExpr args. VM evaluates args sequentially before invoking kernel. Then delete ALL KPC code from the VM (~750 lines Rust, ~80 lines Python). Hard-drop, no shims.
>
> **Deliverables**:
> - `KleisliProgram.__call__()` returns `Call(Pure(kernel), [DoExpr args], kwargs, metadata)` — a proper DoCtrl, not a PyKPC effect
> - VM's `DoCtrl::Call` handler evaluates `f`, args, kwargs as DoExpr nodes before calling kernel
> - `CallArg` type in Rust (lifted from existing `KpcArg` pattern) for typed arg classification
> - Auto-unwrap strategy cached at decoration time on `KleisliProgram`
> - Complete deletion: PyKPC, KpcHandlerFactory, KpcHandlerProgram, ConcurrentKpcHandlerProgram, kpc/concurrent_kpc sentinels, _effective_runtime_handlers
> - All tests green, no KPC references remaining
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 3 phases with parallel sub-tasks
> **Critical Path**: Phase A (Python macro) → Phase B (VM Call evaluator) → Phase C (KPC deletion)

---

## Context

### Original Request

User requested implementation of the KPC macro model per SPEC-KPC-001. The previous implementation attempt was catastrophically wrong — it only made surface-level changes (removing `kpc` from public defaults, adding a PyKPC inline hack) while leaving the entire KPC-as-effect architecture intact. All 91 KPC references in Rust code remained. The core macro expansion (`__call__` → `Call` DoCtrl) was never implemented.

### Interview Summary

**Key Decisions**:
- **Hard-drop confirmed**: no backward compat shim for KPC-as-effect behavior
- **Call DoCtrl approach**: VM's Call handler extended to evaluate DoExpr args (Option A)
- **Kernel is DoExpr**: `f` in `Call(f, args, kwargs, meta)` is itself a DoExpr the VM evaluates
- **Sequential-only**: no concurrent arg evaluation (ConcurrentKpc is removed, not replaced)
- **Strategy caching**: auto-unwrap strategy computed once at decoration time, cached on KleisliProgram

**Research Findings**:
- `Call` DoCtrl exists (`PyCall` at pyvm.rs:1647, tag=1) but current VM handling doesn't evaluate DoExpr args
- `_build_auto_unwrap_strategy()` already exists in program.py:195-247 — complete and tested
- KPC removal inventory: 15 files, ~830 lines to delete/modify
- `KpcHandlerProgram::advance_running` (handler.rs:706-805) is a working reference for phased arg evaluation

### Metis Review

**Identified Gaps (addressed in this plan)**:
- `DoCtrl::Call` args type must change from `Vec<Value>` to `Vec<CallArg>` where `CallArg = Value(Value) | Expr(PyShared)` — impacts classify_yielded, do_ctrl.rs clone_ref, vm.rs match arms
- `classify_yielded` for Call must detect DoExpr args and emit `CallArg::Expr` vs `CallArg::Value`
- Circular import risk: `kleisli.py` constructing `Call`, `Pure`, `Perform` from `doeff_vm` — needs validation
- `Perform` must be constructible from Python (confirmed: `PyPerform` at pyvm.rs:1717)
- Handler return path: if a Python handler returns a Call DoCtrl (result of calling a @do function), the VM must handle it via standard DoCtrl classify path, not the old PyKPC inline hack
- `doeff-pinjected/bridge.py`, `doeff/effects/_validators.py`, `doeff/cli/discovery.py` all import `KleisliProgramCall`

---

## Work Objectives

### Core Objective

Replace the KPC-as-effect dispatch mechanism with true call-time macro expansion that produces `Call` DoCtrl nodes. Extend VM's Call handler to evaluate DoExpr arguments. Then delete all KPC code from the codebase.

### Concrete Deliverables

1. Python macro expansion (`doeff/kleisli.py`, `doeff/program.py`)
2. VM Call evaluator (`packages/doeff-vm/src/vm.rs`, `do_ctrl.rs`, `pyvm.rs`)
3. KPC deletion across all files (see removal inventory)
4. Test updates and new regression tests

### Definition of Done

- [ ] `KleisliProgram.__call__()` returns a `Call` DoCtrl (isinstance check: `doeff_vm.Call`)
- [ ] `Call` args are DoExpr nodes: `Perform(effect)` for unwrap-yes+effect, `Pure(val)` for plain values
- [ ] VM evaluates Call f/args/kwargs as DoExpr before invoking kernel
- [ ] Zero KPC references in Rust code (`grep -r "PyKPC\|KpcHandler\|ConcurrentKpc" packages/doeff-vm/src/`)
- [ ] Zero KPC shims in Python (`grep -r "_effective_runtime_handlers\|KleisliProgramCall\|PyKPC" doeff/ packages/ tests/ --include="*.py" --include="*.rs"`)
- [ ] Auto-unwrap strategy cached at decoration time
- [ ] doeff-13 hang path still covered by bounded regression tests
- [ ] `cargo build && cargo test` passes
- [ ] `uv run pytest` passes (full suite)

### Must Have

- TDD-first workflow: RED tests → implementation → GREEN
- Hard verification gate between each phase
- Phased arg evaluation in VM (not concurrent — sequential only)
- Strategy caching at decoration time (not per-call computation)

### Must NOT Have (Guardrails)

- No backward compat shim for `KleisliProgramCall` type
- No `_effective_runtime_handlers` or hidden kpc injection
- No PyKPC inline hack in pyvm.rs execute_python_call
- No concurrent arg evaluation (ConcurrentKpc is deleted, not replaced)
- No changes to Perform, Eval, Map, FlatMap, WithHandler DoCtrl handling
- No changes to PartiallyAppliedKleisliProgram (it delegates to KleisliProgram.__call__)
- No handler.rs refactoring beyond KPC deletion
- No CallMetadata struct changes
- No modification to scheduler or safe handlers

### Deferred/Out of Scope

- Concurrent arg resolution (future VM-level strategy, per spec)
- Documentation sweep beyond minimal docstring updates
- Performance optimization of strategy caching (correctness first)
- `_InterceptedProgram` — doesn't exist in codebase, no action needed

---

## Verification Strategy (MANDATORY)

### Test Decision

- **Infrastructure exists**: YES
- **User wants tests**: YES (TDD)
- **Framework**: pytest + pytest-asyncio (strict), cargo test (Rust)

### Phase Gates

Each phase has a hard verification gate:
- **Phase A gate**: `@do` function call returns `Call` DoCtrl with correct DoExpr args (Python-only unit tests)
- **Phase B gate**: `cargo build && cargo test && uv run pytest tests/core/test_do_methods.py -x` (end-to-end through VM)
- **Phase C gate**: `uv run pytest -q` (full suite) + grep verification (zero KPC artifacts)

---

## Execution Strategy

### Three Phases with Hard Gates

```
Phase A: VM Foundation + Python Macro Expansion
  Task 1  Revert bad changes from previous attempt
  Task 2  Introduce CallArg type, modify DoCtrl::Call, relax PyCall validation, update classify_yielded
  Task 3  RED: macro expansion unit tests (assert Call with Pure(kernel) as f)
  Task 4  GREEN: strategy caching + __call__ rewrite (emit Call(Pure(kernel), [DoExpr args], ...))
  ── GATE A: Python tests pass, Call DoCtrl returned with Pure(kernel) as f ──

Phase B: VM Call Evaluator
  Task 5  RED: end-to-end tests (Call with DoExpr args through VM)
  Task 6  Implement phased arg evaluation in VM step_handle_yield
  Task 7  GREEN: end-to-end tests pass through new Call evaluator
  ── GATE B: cargo test + e2e pytest pass ──

Phase C: KPC Deletion
  Task 8  Delete KPC from Rust (effect.rs, handler.rs, pyvm.rs, lib.rs, vm.rs)
  Task 9  Delete KPC from Python (program.py, rust_vm.py, handlers.py, __init__.py, etc.)
  Task 10 Update/rewrite all affected tests
  Task 11 Final verification sweep
  ── GATE C: full suite green + zero KPC grep hits ──
```

NOTE: Task 2 (CallArg type + PyCall validation relaxation) is moved to Phase A BEFORE the Python macro expansion. This resolves the sequencing issue: `PyCall::new` must accept `Pure(kernel)` as `f` before `KleisliProgram.__call__()` can construct `Call(Pure(kernel), ...)`.

### Dependency Matrix

| Task | Depends On | Blocks | Phase |
|------|------------|--------|-------|
| 1 | None | 2 | A |
| 2 | 1 | 3,4 | A |
| 3 | 2 | 4 | A |
| 4 | 3 | 5,6 | A |
| 5 | 4 | 7 | B |
| 6 | 4 | 7 | B |
| 7 | 5,6 | 8,9 | B |
| 8 | 7 | 10,11 | C |
| 9 | 7 | 10,11 | C (parallel with 8) |
| 10 | 8,9 | 11 | C |
| 11 | 10 | None | C |

---

## TODOs

- [x] 1. Revert bad changes from previous attempt

  **What to do**:
  - Remove `_effective_runtime_handlers()` function and its call sites in `doeff/rust_vm.py:90-97,221,247`
  - Restore `run()` and `async_run()` to pass `handlers` directly (not through `_effective_runtime_handlers`)
  - Remove PyKPC inline hack at `packages/doeff-vm/src/pyvm.rs:700-764` (the `if result.is_instance_of::<PyKPC>()` block in `execute_python_call`)
  - Rebuild Rust: `uv run maturin develop --manifest-path packages/doeff-vm/Cargo.toml`

  **Must NOT do**:
  - Do not delete KPC handler code yet (that's Phase C)
  - Do not change KleisliProgram.__call__ yet (that's Task 3)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Phase A start
  - **Blocks**: 2, 3
  - **Blocked By**: None

  **References**:
  - `doeff/rust_vm.py:90-97` — `_effective_runtime_handlers()` function (DELETE)
  - `doeff/rust_vm.py:221` — `effective_handlers = _effective_runtime_handlers(handlers)` in `run()` (REVERT to `handlers`)
  - `doeff/rust_vm.py:247` — same in `async_run()` (REVERT)
  - `packages/doeff-vm/src/pyvm.rs:700-764` — PyKPC inline hack in execute_python_call (DELETE block)
  - WHY: These are the bad changes from the previous attempt that paper over the problem

  **Acceptance Criteria**:
  - [ ] `_effective_runtime_handlers` function deleted from rust_vm.py
  - [ ] `run()` and `async_run()` pass `handlers` directly to VM (via `_run_call_kwargs`)
  - [ ] PyKPC inline block removed from pyvm.rs execute_python_call
  - [ ] `cargo build` passes after pyvm.rs change
  - [ ] `uv run maturin develop --manifest-path packages/doeff-vm/Cargo.toml` completes
  - [ ] NOTE: some tests will FAIL after this — that's expected (KPC path is now broken without the hack). This proves the hack was load-bearing.

  **Commit**: YES
  - Message: `revert(runtime): remove bad KPC shims from previous attempt`
  - Files: `doeff/rust_vm.py`, `packages/doeff-vm/src/pyvm.rs`

---

- [x] 2. RED: macro expansion unit tests

  **What to do**:
  - Create `tests/public_api/test_kpc_macro_expansion.py` with failing tests asserting:
    - `@do` function call returns `isinstance(result, Call)` (from doeff_vm)
    - Args are classified as DoExpr nodes: `Perform(effect)` for unwrap-yes+EffectBase, `Pure(val)` for plain values
    - Program-annotated args wrapped as `Pure(program)` (not unwrapped)
    - Effect-annotated args wrapped as `Pure(effect)` (not unwrapped)
    - Kernel accessible from Call.f (wrapped in Pure)
    - Strategy is cached on KleisliProgram instance

  **Must NOT do**:
  - No production code changes in RED step

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`, `doeff-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 1)
  - **Blocks**: 3
  - **Blocked By**: 1

  **References**:
  - `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md:42-84` — macro expansion semantics and example
  - `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md:88-158` — auto-unwrap classification rules
  - `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md:207-242` — return type and composability
  - `doeff/kleisli.py:63-73` — current __call__ (target for Task 3 rewrite)
  - `doeff/program.py:195-247` — existing _build_auto_unwrap_strategy (already implemented)
  - WHY: RED tests define the exact contract before implementation

  **Acceptance Criteria**:
  - [ ] `tests/public_api/test_kpc_macro_expansion.py` exists with ≥6 test cases
  - [ ] `uv run pytest tests/public_api/test_kpc_macro_expansion.py -q` → all tests FAIL (RED)
  - [ ] Failure messages reference assertion targets (isinstance Call, strategy cached, etc.)

  **Commit**: YES
  - Message: `test(kpc): add RED macro expansion unit tests`
  - Files: `tests/public_api/test_kpc_macro_expansion.py`

---

- [x] 3. GREEN: strategy caching + KleisliProgram.__call__() rewrite

  **What to do**:
  - Cache auto-unwrap strategy at decoration time in `KleisliProgram.__post_init__()` (`kleisli.py:38`):
    ```python
    from doeff.program import _build_auto_unwrap_strategy
    strategy = _build_auto_unwrap_strategy(self)
    object.__setattr__(self, "_auto_unwrap_strategy", strategy)
    ```
  - Rewrite `KleisliProgram.__call__()` to perform macro expansion:
    1. Read cached strategy
    2. For each positional arg: if `should_unwrap=True` and `isinstance(arg, EffectBase)` → `Perform(arg)`; if `should_unwrap=True` and `isinstance(arg, DoCtrlBase)` → emit arg directly; else → `Pure(arg)`
    3. Same for kwargs
    4. Build `CallMetadata` from self (function_name, source_file, source_line)
    5. Return `Call(f=Pure(self.func), args=classified_args, kwargs=classified_kwargs, meta=metadata_obj)`
  - Import `Call`, `Pure`, `Perform` from `doeff_vm` inside `__call__` (lazy import to avoid circular deps)
  - Also cache strategy in `DoYieldFunction.__init__()` (`do.py:32`) since it subclasses KleisliProgram and overrides init
  - **CRITICAL: Call.f validation change required first**: `PyCall::new` at pyvm.rs:1670 currently enforces `f.is_callable()`. But `Pure(kernel)` is NOT callable (it's a DoCtrl). Before Task 3 can work, Task 5 must either:
    - Relax the `Call` constructor validation to accept DoExpr objects for `f` (preferred — change `is_callable()` check to `is_callable() || is_instance_of::<PyDoExprBase>()`)
    - OR: Task 3 must use a temporary workaround (e.g., pass raw kernel as `f` and have classify_yielded wrap it in CallArg::Value)
  - **Decision**: Task 5 runs BEFORE Task 3 can produce Call DoCtrl with DoExpr f. Reorder: Task 5 moves to Phase A (before Task 3), or Task 3 uses raw callable for `f` initially and Task 5 upgrades it to DoExpr.
  - **Chosen approach**: Task 3 passes `self.func` directly as `Call.f` (it IS callable, so current validation passes). Task 5 then changes `f` type to `CallArg` and relaxes validation. Task 3 is updated after Task 5 to emit `Pure(self.func)` instead.

  **Must NOT do**:
  - Do not change VM Call handling yet (that's Phase B)
  - Do not delete KPC code yet (that's Phase C)
  - Do not change PartiallyAppliedKleisliProgram (it delegates to self._base.__call__)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`, `doeff-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: 4, 5
  - **Blocked By**: 2

  **References**:
  - `doeff/kleisli.py:38-56` — __post_init__ (add strategy caching here)
  - `doeff/kleisli.py:63-73` — __call__ (REWRITE this)
  - `doeff/do.py:32-84` — DoYieldFunction.__init__ (cache strategy here too)
  - `doeff/program.py:195-247` — _build_auto_unwrap_strategy (use this)
  - `doeff/program.py:45-175` — annotation classifiers (used by strategy builder)
  - `doeff/_types_internal.py` — EffectBase (for isinstance check in arg classification)
  - `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md:42-84` — exact expansion semantics
  - `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md:148-158` — arg classification table
  - `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md:164-197` — CallMetadata population
  - WHY: This is the core macro expansion — the entire point of the spec

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/public_api/test_kpc_macro_expansion.py -q` → all tests PASS (GREEN)
  - [ ] `KleisliProgram.__call__()` returns a `Call` DoCtrl instance
  - [ ] `hasattr(kleisli_instance, '_auto_unwrap_strategy')` is True after construction
  - [ ] NOTE: end-to-end `run()` tests may FAIL because VM doesn't evaluate Call DoExpr args yet. That's expected and addressed in Phase B.

  **Commit**: YES
  - Message: `feat(kleisli): implement call-time macro expansion producing Call DoCtrl`
  - Files: `doeff/kleisli.py`, `doeff/do.py`

---

- [x] 4. RED: end-to-end tests (Call with DoExpr args through VM)

  **What to do**:
  - Create `tests/core/test_call_doexpr_evaluation.py` with tests asserting:
    - `run(@do_func(plain_value))` produces correct result (Pure arg path)
    - `run(@do_func(Ask("key")))` with env resolves the effect before calling kernel
    - `run(@do_func(Get("counter")))` with store resolves the effect
    - `run(@do_func(inner_program()))` where inner is a @do function — resolves the inner Call
    - Nested: `run(outer(inner(Ask("key"))))` — multi-level Call DoExpr evaluation
    - Program-annotated args are NOT unwrapped (passed as-is to kernel)
  - These tests call `run()` end-to-end — they exercise the full VM path

  **Must NOT do**:
  - No VM code changes in RED step

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`, `doeff-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: NO (sequential after Phase A gate)
  - **Blocks**: 7
  - **Blocked By**: 3

  **References**:
  - `doeff/rust_vm.py:208-231` — run() function (entry point for tests)
  - `doeff/rust_vm.py:141-150` — default_handlers() (provides state/reader/writer/etc.)
  - `tests/core/test_do_methods.py` — existing end-to-end patterns to follow
  - `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md:66-84` — macro expansion example
  - WHY: RED tests define the end-to-end contract before VM changes

  **Acceptance Criteria**:
  - [ ] `tests/core/test_call_doexpr_evaluation.py` exists with ≥6 test cases
  - [ ] `uv run pytest tests/core/test_call_doexpr_evaluation.py -q` → tests FAIL (RED)
  - [ ] Failures are due to VM not evaluating DoExpr args (not import errors or syntax issues)

  **Commit**: YES
  - Message: `test(vm): add RED end-to-end Call DoExpr evaluation tests`
  - Files: `tests/core/test_call_doexpr_evaluation.py`

---

- [x] 5. Introduce CallArg type, modify DoCtrl::Call, update classify_yielded

  **What to do**:
  - In `packages/doeff-vm/src/do_ctrl.rs`:
    - Add `CallArg` enum (modeled on existing `KpcArg` at effect.rs:392):
      ```rust
      pub enum CallArg {
          Value(Value),
          Expr(PyShared),  // DoExpr to be evaluated by VM
      }
      ```
    - Change `DoCtrl::Call` to:
      ```rust
      Call {
          f: CallArg,                       // kernel — CallArg::Expr(Pure(kernel)) or CallArg::Value
          args: Vec<CallArg>,               // positional args
          kwargs: Vec<(String, CallArg)>,    // keyword args
          metadata: CallMetadata,
      }
      ```
      NOTE: `f` is ALSO a `CallArg` — this is critical. The kernel itself is a DoExpr (typically `Pure(kernel_callable)`) that the VM evaluates first, before evaluating args.
    - Implement `clone_ref` for `CallArg`
  - In `packages/doeff-vm/src/pyvm.rs` `classify_yielded` (`DoExprTag::Call` arm at line 949):
    - For `Call.f`: check if it's a `PyDoExprBase` or `PyEffectBase` instance
      - If yes → `CallArg::Expr(PyShared::new(obj))`
      - If no (plain callable) → `CallArg::Value(Value::Python(obj))`
    - For each arg in `PyCall.args`: check if it's a `PyDoExprBase` or `PyEffectBase` instance
      - If yes → `CallArg::Expr(PyShared::new(obj))`
      - If no → `CallArg::Value(Value::from_pyobject(obj))`
    - Same for kwargs values
  - Update ALL `DoCtrl::Call` match arms in:
    - `vm.rs:1184` — `step_handle_yield` (temporary: keep old behavior for `CallArg::Value`, placeholder for `CallArg::Expr`)
    - `do_ctrl.rs` — `clone_ref` implementation
    - Any other match arms found via `cargo build` errors

  **Must NOT do**:
  - Do not implement the full phased evaluation yet (that's Task 6)
  - Do not delete any KPC code yet

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: 6
  - **Blocked By**: 3

  **References**:
  - `packages/doeff-vm/src/do_ctrl.rs:63-68` — current DoCtrl::Call variant definition
  - `packages/doeff-vm/src/effect.rs:391-395` — existing KpcArg enum (PATTERN to follow for CallArg)
  - `packages/doeff-vm/src/pyvm.rs:949-966` — current classify_yielded Call arm (REWRITE)
  - `packages/doeff-vm/src/pyvm.rs:206-210` — PyDoCtrlBase struct (used for isinstance detection)
  - `packages/doeff-vm/src/vm.rs:1184-1198` — current Call match in step_handle_yield (update to compile)
  - WHY: This lays the type foundation for the phased evaluator without changing runtime behavior yet

  **Acceptance Criteria**:
  - [ ] `CallArg` enum defined in `do_ctrl.rs`
  - [ ] `DoCtrl::Call` uses `Vec<CallArg>` for args and kwargs
  - [ ] `classify_yielded` correctly classifies DoExpr args as `CallArg::Expr`
  - [ ] `cargo build` passes
  - [ ] `cargo test` passes (existing Call tests still work for Value args)

  **Commit**: YES
  - Message: `feat(vm): introduce CallArg type and DoExpr-aware classify_yielded for Call`
  - Files: `packages/doeff-vm/src/do_ctrl.rs`, `packages/doeff-vm/src/pyvm.rs`, `packages/doeff-vm/src/vm.rs`

---

- [x] 6. Implement phased arg evaluation in VM step_handle_yield

  **What to do**:
  - In `packages/doeff-vm/src/vm.rs`, replace the current `DoCtrl::Call` handler (line 1184) with a phased evaluator:
    1. **Phase: Evaluate f**: `f` is `CallArg`. If `CallArg::Expr(expr)`, push `Eval(expr)` continuation and wait for result. When result arrives, store as `resolved_kernel: PyShared`. If `CallArg::Value(val)`, extract immediately as resolved kernel.
    2. **Phase: Evaluate args**: For each `CallArg::Expr` in args (left-to-right), push `Eval(expr)` continuation and wait. Collect resolved values into `Vec<Value>`. Skip `CallArg::Value` (already resolved).
    3. **Phase: Evaluate kwargs**: Same for kwargs values. Collect into `Vec<(String, Value)>`.
    4. **Phase: Call kernel**: All args resolved. Emit `PythonCall::CallFunc { func: resolved_kernel, args: resolved_values, kwargs: resolved_kw_values }`. This calls the kernel with resolved args. NOTE: even for zero-arg calls, use `CallFunc` (not `StartProgram`), because `StartProgram` goes through `to_generator_strict` which expects a generator/program object, NOT a plain callable. The kernel IS a callable (generator factory function), so `CallFunc` with empty args is the correct dispatch.
    5. **Phase: Handle kernel result**: The kernel call returns a generator (since `@do` functions are generator factories). `execute_python_call` for `CallFunc` already detects generators (pyvm.rs:692-698) and returns them as `PyCallOutcome::Value(Value::Python(gen))`. The `receive_python_result` for `CallFuncReturn` currently delivers the value. **Change needed**: when the result is a generator, it should be pushed as a new frame (like `StartProgramFrame` does at vm.rs:1279-1299) with metadata from `CallMetadata`. Add new `PendingPython::CallFuncStartFrame { metadata }` variant or detect generators in the CallFuncReturn handler.
  - NOTE: The key insight is that `f` uses the SAME `CallArg` type as args/kwargs. The evaluator handles `f` first, then args left-to-right, then kwargs. This is a single unified evaluation loop, not separate code paths for f vs args.
  - This requires a new `PendingPython` variant (e.g., `PendingPython::CallArgEvaluation { ... }`) or equivalent state tracking.
  - Follow the pattern from `KpcHandlerProgram::advance_running` (handler.rs:706-805) — this is a known-working reference for phased arg evaluation.

  **Must NOT do**:
  - Do not add concurrent/parallel arg evaluation
  - Do not change Perform, Eval, Map, FlatMap handling
  - Do not delete KPC code yet

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain`
  - **Skills**: [`python-coding-style`]
    - Reason: This is the most complex Rust code change — a state machine for phased evaluation inside the VM step function

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: 7
  - **Blocked By**: 5

  **References**:
  - `packages/doeff-vm/src/vm.rs:1184-1198` — current Call handler (REPLACE)
  - `packages/doeff-vm/src/handler.rs:706-805` — KpcHandlerProgram::advance_running (PATTERN — phased eval reference implementation)
  - `packages/doeff-vm/src/handler.rs:689-694` — KpcResolution enum (PATTERN for tracking resolution state)
  - `packages/doeff-vm/src/vm.rs:1279-1308` — receive_python_result for StartProgramFrame and CallFuncReturn (existing paths to reuse)
  - `packages/doeff-vm/src/python_call.rs:14-48` — PythonCall and PendingPython enums (add new variant)
  - `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md:156-158` — "VM evaluates each arg sequentially left-to-right before invoking the kernel"
  - WHY: This is the core VM change that makes Call a proper "apply with lazy args" construct

  **Acceptance Criteria**:
  - [ ] `cargo build` passes
  - [ ] `cargo test` passes (including any new Rust unit tests for phased evaluation)
  - [ ] `uv run maturin develop --manifest-path packages/doeff-vm/Cargo.toml` completes

  **Commit**: YES
  - Message: `feat(vm): implement phased DoExpr arg evaluation for Call DoCtrl`
  - Files: `packages/doeff-vm/src/vm.rs`, `packages/doeff-vm/src/python_call.rs`

---

- [x] 7. GREEN: end-to-end tests pass through new Call evaluator

  **What to do**:
  - Run the RED tests from Task 4 — they should now pass:
    ```bash
    uv run pytest tests/core/test_call_doexpr_evaluation.py -q
    ```
  - Run the macro expansion tests from Task 2:
    ```bash
    uv run pytest tests/public_api/test_kpc_macro_expansion.py -q
    ```
  - Run existing @do tests to verify no regressions:
    ```bash
    uv run pytest tests/core/test_do_methods.py -q
    ```
  - If any tests fail: debug and fix in vm.rs or kleisli.py (GREEN phase)
  - Verify hang regression tests still pass:
    ```bash
    uv run pytest tests/public_api/test_doeff13_hang_regression.py -q
    ```

  **Must NOT do**:
  - Do not skip or xfail tests to force green
  - Do not delete KPC code yet (KPC path still exists alongside new Call path)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`, `doeff-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: NO (gate between Phase B and Phase C)
  - **Blocks**: 8, 9
  - **Blocked By**: 4, 6

  **References**:
  - `tests/core/test_call_doexpr_evaluation.py` — end-to-end tests from Task 4
  - `tests/public_api/test_kpc_macro_expansion.py` — macro expansion tests from Task 2
  - `tests/core/test_do_methods.py` — existing @do method tests
  - `tests/public_api/test_doeff13_hang_regression.py` — hang regression tests

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/core/test_call_doexpr_evaluation.py -q` → PASS
  - [ ] `uv run pytest tests/public_api/test_kpc_macro_expansion.py -q` → PASS
  - [ ] `uv run pytest tests/core/test_do_methods.py -q` → PASS
  - [ ] `uv run pytest tests/public_api/test_doeff13_hang_regression.py -q` → PASS
  - [ ] `cargo test` → PASS

  **Commit**: YES
  - Message: `feat(vm): Call DoCtrl lazy arg evaluation end-to-end verified`
  - Files: any fixes needed

---

- [x] 8. Delete KPC from Rust

  **What to do**:
  - `packages/doeff-vm/src/effect.rs`:
    - Delete `PyKPC` struct (lines 50-66)
    - Delete `impl PyKPC` block (lines 186-212)
    - Delete `KpcArg` enum and `KpcCallEffect` struct (lines 391-404)
  - `packages/doeff-vm/src/handler.rs`:
    - Delete `parse_kpc_python_effect` (lines 473-522)
    - Delete `extract_kpc_call_metadata` (lines 524-578)
    - Delete `fallback_kpc_args_repr` (lines 580-598)
    - Delete `kpc_strategy_should_unwrap_positional` (lines 600-611)
    - Delete `kpc_strategy_should_unwrap_keyword` (lines 613-624)
    - Delete `extract_kpc_arg` (lines 626-631)
    - Remove `PyKPC` from `is_do_expr_candidate` check (line 636)
    - Delete `KpcHandlerFactory` + `KpcHandlerProgram` + all supporting types (lines 639-888)
    - Delete `ConcurrentKpcHandlerFactory` + `ConcurrentKpcHandlerProgram` + all supporting types (lines 1604-1899)
    - Delete KPC tests (lines 1936-1986)
    - Remove KPC imports at top of file (line 14)
  - `packages/doeff-vm/src/pyvm.rs`:
    - Remove `PyKPC` from import line 9
    - Remove `KpcHandlerFactory`, `ConcurrentKpcHandlerFactory` from import line 76
    - Remove `kpc` and `concurrent_kpc` sentinel registrations (lines 2932-2945)
    - Remove `KleisliProgramCall` alias (lines 2930-2931)
    - Remove `m.add_class::<PyKPC>()` (line 2921)
    - Delete KPC-related tests (lines 2309-2328, 2531-2550)
  - `packages/doeff-vm/src/lib.rs`:
    - Remove `PyKPC` from re-export (line 44)
  - `packages/doeff-vm/src/vm.rs`:
    - Delete KPC test (lines 3192-3217)

  **Must NOT do**:
  - Do not delete Python-side KPC code in this task (that's Task 9)
  - Do not restructure handler.rs beyond KPC deletion

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 9)
  - **Parallel Group**: Phase C
  - **Blocks**: 10, 11
  - **Blocked By**: 7

  **References**:
  - See removal inventory from explore agent (exhaustive line ranges above)
  - WHY: Eliminate all KPC knowledge from the VM per user requirement

  **Acceptance Criteria**:
  - [ ] `cargo build` passes with zero KPC-related code
  - [ ] `cargo test` passes
  - [ ] `grep -r "PyKPC\|KpcHandler\|ConcurrentKpc\|KpcCallEffect\|KpcArg\|parse_kpc_python" packages/doeff-vm/src/` → zero matches
  - [ ] `uv run maturin develop --manifest-path packages/doeff-vm/Cargo.toml` completes

  **Commit**: YES
  - Message: `refactor(vm): delete all KPC effect/handler code from Rust VM`
  - Files: `packages/doeff-vm/src/effect.rs`, `handler.rs`, `pyvm.rs`, `lib.rs`, `vm.rs`

---

- [x] 9. Delete KPC from Python

  **What to do**:
  - `doeff/rust_vm.py`:
    - Delete `_effective_runtime_handlers` if not already done in Task 1
    - Remove `"kpc"` and `"concurrent_kpc"` from `__getattr__` name set if present
  - `doeff/program.py`:
    - Delete `from doeff_vm import KleisliProgramCall as KleisliProgramCall` (line 508)
    - Delete `_CompatDataclassField` class (lines 511-515)
    - Delete `_format_kpc_args_repr` function (lines 518-537)
    - Delete `_kpc_create_from_kleisli` function (lines 540-556)
    - Delete `_kpc_and_then_k` function (lines 559-564)
    - Delete all three `setattr(KleisliProgramCall, ...)` calls (lines 567-582)
    - Remove `"KleisliProgramCall"` from `__all__` (line 591)
  - `doeff/__init__.py`:
    - Remove `KleisliProgramCall` from import (line 116) and `__all__` (line 269)
  - `doeff/handlers.py`:
    - Remove `"kpc"` from `_HANDLER_SENTINELS` (line 17)
    - Update docstring (lines 5-6)
  - `packages/doeff-vm/doeff_vm/__init__.py`:
    - Delete `kpc = _ext.kpc` (line 40)
    - Delete `concurrent_kpc = _ext.concurrent_kpc` (line 41)
    - Delete `KleisliProgramCall = _ext.KleisliProgramCall` (line 42)
    - Delete `PyKPC = _ext.PyKPC` (line 54)
    - Remove from `__all__`: `"KleisliProgramCall"` (97), `"PyKPC"` (110), `"concurrent_kpc"` (147), `"kpc"` (148)
  - `doeff/effects/_validators.py`:
    - Remove `KleisliProgramCall` from import (line 24) and isinstance check (line 25)
  - `doeff/cli/discovery.py`:
    - Remove `KleisliProgramCall` import (line 319) and isinstance checks (lines 326, 328, 332)
  - `doeff/do.py`:
    - Update stale comments at lines 24-25 and 69
  - `doeff/kleisli.py`:
    - Remove stale "KPC effect" mention in error message (line 91)
  - `doeff/program.py:263-272`:
    - `_is_rust_program_subclass()` references `doeff_vm.KleisliProgramCall` in issubclass check (line 270)
    - Change to: `return issubclass(subclass, (doeff_vm.DoExpr,))` — remove `KleisliProgramCall`
    - This is critical: `Call` extends `DoExpr`, so `isinstance(call_result, ProgramBase)` will still work
  - `packages/doeff-pinjected/src/doeff_pinjected/bridge.py`:
    - Remove `KleisliProgramCall` from import (line 15)
    - Replace `isinstance(prog, (Program, KleisliProgramCall))` (line 43) with `isinstance(prog, Program)`
    - Update comment about KleisliProgramCall `.intercept()` fallback (line 58)

  **Must NOT do**:
  - Do not restructure any module beyond deletion
  - Do not change PartiallyAppliedKleisliProgram

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 8)
  - **Parallel Group**: Phase C
  - **Blocks**: 10, 11
  - **Blocked By**: 7

  **References**:
  - See removal inventory from explore agent (exhaustive file:line listing)
  - `doeff/program.py:263-272` — `_is_rust_program_subclass` references `doeff_vm.KleisliProgramCall` (line 270)
  - `doeff/effects/_validators.py:20-25` — isinstance KleisliProgramCall check
  - `doeff/cli/discovery.py:319-332` — isinstance KleisliProgramCall checks
  - `packages/doeff-vm/doeff_vm/__init__.py:40-54,97,110,147-148` — all KPC exports
  - `packages/doeff-pinjected/src/doeff_pinjected/bridge.py:15,43,58` — KleisliProgramCall import and isinstance
  - WHY: Eliminate all KPC references from Python layer (including dependent packages)

  **Acceptance Criteria**:
  - [ ] `grep -r "KleisliProgramCall\|_effective_runtime_handlers\|PyKPC" doeff/ packages/doeff-vm/doeff_vm/ packages/doeff-pinjected/ --include="*.py"` → zero matches (excluding test files and this plan)
  - [ ] `uv run python -c "import doeff; import doeff_vm; assert not hasattr(doeff_vm, 'kpc'); assert not hasattr(doeff_vm, 'KleisliProgramCall')"` → exit 0

  **Commit**: YES
  - Message: `refactor(python): delete all KPC/KleisliProgramCall references`
  - Files: `doeff/program.py`, `doeff/rust_vm.py`, `doeff/handlers.py`, `doeff/__init__.py`, `doeff/do.py`, `doeff/kleisli.py`, `doeff/effects/_validators.py`, `doeff/cli/discovery.py`, `packages/doeff-vm/doeff_vm/__init__.py`

---

- [x] 10. Update/rewrite all affected tests

  **What to do**:
  - Rewrite `tests/public_api/test_types_001_kpc.py` (19 KPC references):
    - All KPC-as-effect assertions become KPC-as-Call-DoCtrl assertions
    - `isinstance(result, KleisliProgramCall)` → `isinstance(result, Call)`
    - Auto-unwrap tests verify DoExpr arg classification (Perform/Pure)
  - Update `tests/public_api/test_kpc_macro_runtime_contract.py` (7 KPC references):
    - Assertions about default_handlers excluding kpc remain valid
    - Rewrite `KleisliProgramCall` type assertions to `Call` DoCtrl assertions
  - Update `tests/public_api/test_types_001_hierarchy.py` (8 KPC references at lines 3,17,69,86-90):
    - Remove `KleisliProgramCall` import and all issubclass/isinstance checks
    - Replace with `Call` DoCtrl hierarchy assertions
  - Update `tests/core/test_sa001_spec_gaps.py` (5 KPC references at lines 224-227,235-237,273-275):
    - Delete tests asserting KleisliProgramCall importable from doeff_vm
    - Delete tests asserting KPC dataclass fields
    - Delete tests asserting KPC extends EffectBase
  - Update `tests/core/test_sa002_spec_gaps.py` (1 KPC reference at line 52):
    - Remove assertion about `KleisliProgramCall` class in program.py source
  - Update `tests/core/test_doexpr_hierarchy.py` (2 KPC references at lines 7,20):
    - Remove `KleisliProgramCall` import and isinstance check
    - Replace with `Call` DoCtrl isinstance check
  - Update `tests/core/test_sa008_runtime_probes_formalized.py` (1 KPC reference at line 28):
    - Replace `doeff_vm.KleisliProgramCall` isinstance check with `Call`
  - Update `tests/core/test_do_methods.py` (10 KPC references at lines 13,18,23,56,58,89,92,105,111,122,132):
    - Replace all `KleisliProgramCall` imports and isinstance checks with `Call` or `ProgramBase`
    - Update execution kernel extraction logic
  - Update `tests/core/test_rust_vm_api_strict.py`:
    - Remove kpc from handler sentinel expectations
  - Update `tests/core/test_spec_gaps.py`:
    - Remove KPC handler-specific assertions
  - Delete KPC tests in `packages/doeff-vm/tests/test_pyvm.py` (8 KPC references at lines 1779-1877):
    - Delete tests that import `PyKPC`, `kpc`, `concurrent_kpc`
    - These test KPC handler dispatch which no longer exists
  - Verify hang regression tests still pass:
    - `tests/public_api/test_doeff13_hang_regression.py` — should work since Call DoCtrl doesn't use handler dispatch

  **Must NOT do**:
  - Do not skip/xfail to force green — fix or properly rewrite
  - Do not add tests for concurrent arg evaluation

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`python-coding-style`, `doeff-patterns`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: 11
  - **Blocked By**: 8, 9

  **References**:
  - `tests/public_api/test_types_001_kpc.py` — main KPC test file (REWRITE)
  - `tests/public_api/test_kpc_macro_runtime_contract.py` — runtime contract tests (UPDATE)
  - `tests/public_api/test_doeff13_hang_regression.py` — hang tests (VERIFY)
  - `tests/core/test_sa001_spec_gaps.py` — spec gap tests (UPDATE)

  **Acceptance Criteria**:
  - [ ] `uv run pytest tests/public_api/ -q` → all pass
  - [ ] `uv run pytest tests/core/ -q` → all pass
  - [ ] `grep -r "KleisliProgramCall\|PyKPC" tests/ packages/doeff-vm/tests/ --include="*.py"` → zero matches
  - [ ] No test imports `kpc` or `concurrent_kpc` from doeff_vm

  **Commit**: YES
  - Message: `test(kpc): rewrite tests for Call DoCtrl macro model`
  - Files: `tests/public_api/test_types_001_kpc.py`, `tests/public_api/test_kpc_macro_runtime_contract.py`, `tests/core/test_sa001_spec_gaps.py`, `tests/core/test_sa002_spec_gaps.py`, `tests/core/test_rust_vm_api_strict.py`, `tests/core/test_spec_gaps.py`

---

- [x] 11. Final verification sweep

  **What to do**:
  - Full test suite:
    ```bash
    uv run pytest -q
    ```
  - Rust compilation and tests:
    ```bash
    cd packages/doeff-vm && cargo build && cargo test
    ```
  - KPC artifact grep (must be zero):
    ```bash
    grep -r "PyKPC\|KpcHandler\|ConcurrentKpc\|KleisliProgramCall\|_effective_runtime_handlers" doeff/ packages/doeff-vm/src/ packages/doeff-vm/doeff_vm/ --include="*.py" --include="*.rs"
    ```
  - Bounded hang check:
    ```bash
    uv run python -c "import subprocess,sys; r=subprocess.run(['pytest','tests/public_api/test_doeff13_hang_regression.py','-q'], timeout=3.0); sys.exit(r.returncode)"
    ```
  - Macro expansion type check:
    ```bash
    uv run python -c "from doeff import do; from doeff_vm import Call; f = do(lambda: 42); result = f(); assert isinstance(result, Call), type(result)"
    ```
  - Record evidence to `.sisyphus/evidence/kpc-macro-final-verification.md`

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`python-coding-style`]

  **Parallelization**:
  - **Can Run In Parallel**: NO (final gate)
  - **Blocks**: None
  - **Blocked By**: 10

  **Acceptance Criteria**:
  - [x] `uv run pytest -q` → all pass, no new skips
  - [x] `cargo build && cargo test` → pass
  - [x] KPC grep → zero matches
  - [x] Hang check → passes within 3.0s
  - [x] Macro expansion → Call isinstance True
  - [x] Evidence file created

  **Commit**: YES
  - Message: `chore(kpc): final verification — KPC fully replaced by Call DoCtrl macro`
  - Files: `.sisyphus/evidence/kpc-macro-final-verification.md`

---

## Commit Strategy

| After Task | Message | Files |
|------------|---------|-------|
| 1 | `revert(runtime): remove bad KPC shims from previous attempt` | doeff/rust_vm.py, pyvm.rs |
| 2 | `test(kpc): add RED macro expansion unit tests` | tests/public_api/test_kpc_macro_expansion.py |
| 3 | `feat(kleisli): implement call-time macro expansion producing Call DoCtrl` | doeff/kleisli.py, doeff/do.py |
| 4 | `test(vm): add RED end-to-end Call DoExpr evaluation tests` | tests/core/test_call_doexpr_evaluation.py |
| 5 | `feat(vm): introduce CallArg type and DoExpr-aware classify_yielded for Call` | do_ctrl.rs, pyvm.rs, vm.rs |
| 6 | `feat(vm): implement phased DoExpr arg evaluation for Call DoCtrl` | vm.rs, python_call.rs |
| 7 | `feat(vm): Call DoCtrl lazy arg evaluation end-to-end verified` | any fixes |
| 8 | `refactor(vm): delete all KPC effect/handler code from Rust VM` | effect.rs, handler.rs, pyvm.rs, lib.rs, vm.rs |
| 9 | `refactor(python): delete all KPC/KleisliProgramCall references` | many Python files |
| 10 | `test(kpc): rewrite tests for Call DoCtrl macro model` | test files |
| 11 | `chore(kpc): final verification — KPC fully replaced by Call DoCtrl macro` | evidence |

---

## Success Criteria

### Verification Commands

```bash
# Full Python test suite
uv run pytest -q

# Rust build + tests
cd packages/doeff-vm && cargo build && cargo test

# Zero KPC artifacts (covers core runtime + all packages)
grep -r "PyKPC\|KpcHandler\|ConcurrentKpc\|KleisliProgramCall\|_effective_runtime_handlers" doeff/ packages/doeff-vm/src/ packages/doeff-vm/doeff_vm/ packages/doeff-pinjected/ --include="*.py" --include="*.rs"

# Bounded hang check
uv run python -c "import subprocess,sys; r=subprocess.run(['pytest','tests/public_api/test_doeff13_hang_regression.py','-q'], timeout=3.0); sys.exit(r.returncode)"

# Macro expansion type verification
uv run python -c "from doeff import do; from doeff_vm import Call; f = do(lambda: 42); result = f(); assert isinstance(result, Call), type(result)"
```

### Final Checklist

- [x] `KleisliProgram.__call__()` returns `Call` DoCtrl (not PyKPC effect)
- [x] Auto-unwrap strategy cached at decoration time
- [x] VM evaluates Call f/args/kwargs as DoExpr before invoking kernel
- [x] Zero KPC references in Rust code
- [x] Zero KPC shims in Python code
- [x] Full test suite green
- [x] Hang regression covered
- [x] `cargo build && cargo test` passes

---

## Decision Resolution

- **Architecture**: Option A — extend VM Call to evaluate DoExpr args (kernel is DoExpr too)
- **Compatibility**: Hard drop — no KleisliProgramCall backward compat
- **Arg evaluation**: Sequential left-to-right only (no concurrent)
- **Strategy caching**: At decoration time on KleisliProgram instance
- **Phase ordering**: Python macro first → VM evaluator → KPC deletion last (delete after replacement proven)
