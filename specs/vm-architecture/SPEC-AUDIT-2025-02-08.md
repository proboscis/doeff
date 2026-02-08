# Spec Audit Report: SPEC-008 + SPEC-009 + SPEC-TYPES-001

**Date:** 2025-02-08
**Audited specs:**
- `specs/vm-architecture/SPEC-008-rust-vm.md` (Rev 11)
- `specs/vm-architecture/SPEC-009-rust-vm-migration.md` (Rev 6)
- `specs/SPEC-TYPES-001-program-effect-separation.md` (Rev 9)

**Implementation:** `packages/doeff-vm/src/*.rs`, `doeff/*.py`, `doeff/effects/*.py`

**Method:** 8 parallel review agents, each assigned a spec section + implementation files. Cross-referenced and deduplicated.

**Stats:** 50 items total — 4 contradictions, 10 critical, 11 moderate, 12 minor, 13 discussion

---

## How to use this file

For each item, fill in the **Resolution** column:

- `fix-code` — Fix the implementation to match the spec (default for G-items)
- `fix-spec` — Update the spec to match the implementation
- `defer` — Acknowledge but defer to a future milestone
- `add-to-spec` — Code is correct, document it in the spec (for Q-items)
- `remove-from-code` — Code has extra behavior that should be removed (for Q-items)
- `discuss` — Needs further discussion before deciding
- `skip` — Not worth fixing / no action needed

When done, bring this file back and I'll proceed with the TDD plan for all non-skipped items.

---

## C: Spec Contradictions (resolve before fixing code)

These are spec-internal bugs. The implementation is correct in all 4 cases.
Default resolution: `fix-spec`.

| ID | Description | Spec Lines | Impl Correct? | Resolution |
|----|-------------|------------|----------------|------------|
| C1 | **Handler-return RustReturn frame.** Spec pseudocode (L2650) comments out `push_frame(Frame::RustReturn{cb: handler_return_cb})` but handler-return semantics (L2777-2780) require exactly that frame to intercept handler completion. Impl correctly pushes it at `vm.rs:780-793`. | 008:2650 vs 008:2777 | Yes | fix-spec DONE |
| C2 | **PythonCall gen ownership.** Spec pseudocode (L2552-2583) shows generator carried in PythonCall variants (GenSend/GenNext/GenThrow), but D1 Phase 2 design note says gen should live in PendingPython. Impl follows D1 Phase 2 (gen in PendingPython, not PythonCall). | 008:2552 vs D1 note | Yes | fix-spec DONE |
| C3 | **Gather/Race use-after-move.** Spec pseudocode passes `k_user` by move to `wait_on_all`/`wait_on_any` then uses `k_user` again in `transfer_next_or`. This is a use-after-move in Rust. Impl correctly clones at `scheduler.rs:652-653, 664-665`. | 008:1613-1625 | Yes | fix-spec DONE |
| C4 | **with_local doesn't clean new keys.** Spec pseudocode for `RustStore::with_local` only restores old bindings but doesn't remove newly-added env keys. Impl correctly tracks `new_keys` and removes them at `vm.rs:90-107`. | 008:2036-2056 | Yes | fix-spec DONE |

---

## G: Gaps — Critical

Default resolution: `fix-code` (implement what spec requires).

### G1: Standard effect #[pyclass] structs missing

| Field | Value |
|-------|-------|
| **Spec says** | `PyGet`, `PyPut`, `PyAsk`, `PyTell`, `PyModify` as `#[pyclass(frozen)]` Rust structs (SPEC-008 R11-A, lines 788-825) |
| **Impl does** | Effects are plain Python classes. No Rust pyclass structs exist. Test-only `Effect` enum in `effect.rs:39-46`. |
| **Files** | `effect.rs` (missing), `_types_internal.py`, `effects/state.py`, `effects/reader.py`, `effects/writer.py` |
| **Enforcement** | semgrep (type must exist and extend PyEffectBase) |
| **Resolution** | |
| **Notes** | Part of R11 migration. Large change — affects handler dispatch. |

### G2: Scheduler effect #[pyclass] structs missing

| Field | Value |
|-------|-------|
| **Spec says** | `PySpawn`, `PyGather`, `PyRace`, `PyCreatePromise`, `PyCompletePromise`, `PyFailPromise`, `PyCreateExternalPromise`, `PyTaskCompleted` as `#[pyclass(frozen)]` Rust structs (SPEC-008 R11-A, lines 829-873) |
| **Impl does** | Scheduler effects are plain Python objects. Parsed via `parse_scheduler_python_effect()` duck-typing. |
| **Files** | `effect.rs` (missing), `scheduler.rs` |
| **Enforcement** | semgrep |
| **Resolution** | |
| **Notes** | Part of R11 migration. Coupled with G1. |

### G3: KPC is Python dataclass, not Rust #[pyclass]

| Field | Value |
|-------|-------|
| **Spec says** | `KleisliProgramCall` is a `#[pyclass(frozen, extends=PyEffectBase)]` Rust struct (`PyKPC`) with fields: `kleisli_source`, `args`, `kwargs`, `function_name`, `execution_kernel`, `created_at` (SPEC-TYPES-001 Rev 9, SPEC-008 R11-A) |
| **Impl does** | KPC is a Python `@dataclass(frozen=True)` in `program.py:465-502`. Handler uses `getattr`-based parsing (`handler.rs:186-232`). |
| **Files** | `program.py:465-502`, `handler.rs:186-232` |
| **Enforcement** | test + semgrep |
| **Resolution** | |
| **Notes** | Core TYPES-001 Rev 9 requirement. Blocked by G4 (KPC parent class). |

### G4: KPC extends ProgramBase, not EffectBase

| Field | Value |
|-------|-------|
| **Spec says** | KPC is an Effect subtype (extends EffectBase). KPC is NOT a DoThunk — no `to_generator()`. (SPEC-TYPES-001 §1.3, §2) |
| **Impl does** | `KleisliProgramCall` extends `ProgramBase` (`program.py:466`). Uses `__doeff_kpc__ = True` marker for detection. |
| **Files** | `program.py:466` |
| **Enforcement** | semgrep |
| **Resolution** | |
| **Notes** | This is the core conflation TYPES-001 aims to fix. Coupled with G3. |

### G5: classify_yielded uses duck-typing instead of three isinstance checks

| Field | Value |
|-------|-------|
| **Spec says** | Three C-level pointer comparisons: `is_instance_of::<PyDoCtrlBase>()` then `is_instance_of::<PyEffectBase>()` then `is_instance_of::<PyDoThunkBase>()`. No Python imports, no getattr, no hasattr, no string matching. (SPEC-008 R11-C, R11-F; SPEC-TYPES-001 §7) |
| **Impl does** | ~240-line classifier (`pyvm.rs:608-849`) that checks individual Rust pyclasses, then duck-types with getattr for WithHandler/Resume/etc., then calls `is_effect_object()` (which includes Python import fallback + `__doeff_effect_base__` marker), then `hasattr("to_generator")`, then string type name matching. |
| **Files** | `pyvm.rs:608-849` |
| **Enforcement** | test + semgrep (ban hasattr/getattr/string-matching in classify) |
| **Resolution** | |
| **Notes** | Blocked by G1, G12, G13 (types must extend bases first). This is the capstone of the R11 migration. |

### G6: run() installs default handlers when handlers=None

| Field | Value |
|-------|-------|
| **Spec says** | `handlers` defaults to `[]`. API-1: No handlers installed by default. If program yields `Get("x")` with no state handler, VM raises `UnhandledEffect`. (SPEC-009 §1, L149, L837) |
| **Impl does** | When `handlers=None` (the default), Python shim calls `default_handlers()` which returns `[state, reader, writer]`. (`rust_vm.py:56`) |
| **Files** | `doeff/rust_vm.py:46-56` |
| **Enforcement** | test |
| **Resolution** | |
| **Notes** | Breaking change for existing users if fixed. Consider: fix code AND provide `default_handlers()` as explicit convenience? |

### G7: run() always installs KPC handler

| Field | Value |
|-------|-------|
| **Spec says** | `run()` installs no handlers by default (API-1). If `handlers=[]`, zero handlers. (SPEC-009 §1) |
| **Impl does** | Rust-side `run()` always wraps with `KpcHandlerFactory` as innermost handler (`pyvm.rs:1822-1835`). Also `PyVM::new()` at lines 68-80 auto-installs it. |
| **Files** | `pyvm.rs:1822-1835`, `pyvm.rs:68-80` |
| **Enforcement** | test |
| **Resolution** | |
| **Notes** | Without KPC handler, `@do` decorated programs won't work (KPC yields become UnhandledEffect). Spec may need updating to acknowledge KPC handler as implicit. Or: require users to install it explicitly. |

### G8: Import paths for Resume/Delegate/Transfer/WithHandler broken

| Field | Value |
|-------|-------|
| **Spec says** | `from doeff import Resume, Delegate, Transfer` (§8 handler authors). `from doeff import WithHandler` (§8 user code). (SPEC-009 §8, L709, L725) |
| **Impl does** | These are NOT re-exported from `doeff/__init__.py`. Available only via `doeff.rust_vm.__getattr__` lazy proxy. `from doeff import Resume` raises `ImportError`. |
| **Files** | `doeff/__init__.py` |
| **Enforcement** | test (import assertions) |
| **Resolution** | fix-code DONE |

### G9: doeff.handlers module doesn't exist

| Field | Value |
|-------|-------|
| **Spec says** | `from doeff.handlers import state` (SPEC-009 §8, L611-614). `from doeff.handlers import scheduler` (L676). |
| **Impl does** | No `doeff/handlers/` directory or `doeff/handlers.py` module. Handler sentinels only available via `doeff_vm.state` or `doeff.rust_vm.__getattr__`. |
| **Files** | missing `doeff/handlers/` module |
| **Enforcement** | test |
| **Resolution** | fix-code DONE |

### G10: Modify handler returns new_value instead of old_value

| Field | Value |
|-------|-------|
| **Spec says** | Modify handler `resume()` yields `Resume { continuation: k, value: old_value }` — returns the OLD value to the caller. (SPEC-008 L1210-1218) |
| **Impl does** | Returns `new_value` (the modifier's result). Test at `handler.rs:1224-1225` confirms `value.as_int(), Some(20)` which is new_value. |
| **Files** | `handler.rs:672-686` |
| **Enforcement** | test |
| **Resolution** | |
| **Notes** | Behavioral bug. Which semantics do you want? Haskell's `StateT` returns new value; some algebraic effect libraries return old value. |

---

## G: Gaps — Moderate

Default resolution: `fix-code`.

| ID | Description | Spec Lines | Impl Location | Enforcement | Resolution |
|----|-------------|------------|---------------|-------------|------------|
| G11 | **auto_unwrap_strategy on KPC.** Spec says NOT stored on KPC; handler computes from kleisli_source annotations at dispatch time. Impl stores it on KPC at creation time (`program.py:480,494`). Handler reads it via getattr (`handler.rs:200`). | TYPES-001 Rev 9 | `program.py:480`, `handler.rs:200` | semgrep | |
| G12 | **DoCtrl pyclasses don't extend PyDoCtrlBase.** PyWithHandler, PyResume, PyTransfer, PyDelegate are standalone pyclasses. classify_yielded checks them individually instead of one `is_instance_of::<PyDoCtrlBase>()`. | 008:R11-F L932-933 | `pyvm.rs` pyclasses | semgrep | |
| G13 | **DoThunk types don't extend PyDoThunkBase.** No concrete type uses `extends=PyDoThunkBase`. Base class exists but is unused. | 008:R11-F L936-937 | `pyvm.rs:49-50` | semgrep | |
| G14 | **Handler traits use DispatchEffect not Bound\<PyAny\>.** `start()` takes `DispatchEffect`, `can_handle()` takes `&DispatchEffect`. Spec says `py: Python<'_>, effect: &Bound<'_, PyAny>`. | 008:R11-D L1093-1107 | `handler.rs:43-57` | semgrep | |
| G15 | **Dual EffectBase.** Rust `PyEffectBase` (empty marker, `pyvm.rs:43`) vs Python `EffectBase` (rich dataclass, `_types_internal.py:567`). Python EffectBase is NOT a subclass of Rust PyEffectBase. isinstance checks don't work across the boundary. | 008:R11-F | `pyvm.rs:43`, `_types_internal.py:567` | test + semgrep | |
| G16 | **to_generator_strict too permissive.** Accepts callables, KPCs, Effects, DoExprs via fallback wrapping. Spec says only DoThunk accepted; raw generators rejected except via `start_with_generator()`. | 008:L2984-2996 | `pyvm.rs:517-580` | test | |
| G17 | **Scheduler silently swallows errors.** Two places: unexpected resume type returns `Value::None` instead of `Throw(TypeError)` (L719); unexpected resume in Idle phase returns `Value::None` instead of `Throw(RuntimeError)` (L754-756). | 008:L1668-1702 | `scheduler.rs:719, 754-756` | test | fix-code DONE |
| G18 | **Two RunResult types.** Rust `PyRunResult` (matches spec) vs Python `RunResult` (`_types_internal.py:838`, no `.raw_store`, no `.error`). `from doeff import RunResult` gives the Python version. | 009:§2 | `pyvm.rs:1083`, `_types_internal.py:838` | test | fix-code DONE |
| G19 | **PyResultOk/Err != doeff Ok/Err.** `result.result` returns Rust `PyResultOk`/`PyResultErr`, not doeff `Ok`/`Err`. `isinstance(result.result, Ok)` fails. | 009:L229-237 | `pyvm.rs:1039-1081` | test | fix-code DONE |
| G20 | **transfer_next_or skips store save/load.** Spec's `transfer_task()` saves current task store, loads new task store, sets `current_task` before Transfer. Impl's `transfer_next_or()` only pops ready queue — no store context-switching. | 008:L1434-1447 | `scheduler.rs:521-536` | test | fix-code DONE |
| G21 | **doeff.presets module missing.** `from doeff.presets import sync_preset, async_preset` fails. | 009:L684-688 | missing module | test | |

---

## G: Gaps — Minor

Default resolution: `fix-code` (or `fix-spec` where noted).

| ID | Description | Spec Lines | Impl Location | Enforcement | Resolution |
|----|-------------|------------|---------------|-------------|------------|
| G22 | **Base classes missing `frozen`.** `PyEffectBase`, `PyDoCtrlBase`, `PyDoThunkBase` lack `frozen` attribute. | 008:R11-F L907-920 | `pyvm.rs:43-49` | semgrep | fix-code DONE |
| G23 | **PyShared vs Py\<PyAny\> (systematic).** All `Py<PyAny>` in spec are `PyShared` in impl. Intentional for GIL-free safety / free-threaded Python 3.14t. | 008:throughout | all .rs files | — | |
| G24 | **Reader/Writer resume returns Value not unreachable!().** Spec says `unreachable!("never yields mid-handling")`. Impl returns the value (more defensive). | 008:L1257, L1296 | `handler.rs:763, 842` | test | fix-code DONE |
| G25 | **Tell param named `message` not `value`.** `WriterTellEffect(message=...)` vs spec's `Tell(value=...)`. | 009:L353-355 | `writer.py:13-18` | test | |
| G26 | **Ask key type Hashable not str.** `AskEffect(key: EnvKey)` where `EnvKey = Hashable`. Spec says `str`. | 009:L351 | `reader.py:17-25` | test | |
| G27 | **Callback type +Sync.** Spec: `Box<dyn FnOnce(...) + Send>`. Impl: `+ Send + Sync`. | 008:L474 | `vm.rs:25` | semgrep | |
| G28 | **current_segment is Option.** Spec says `SegmentId` (non-optional). Impl uses `Option<SegmentId>` for robustness. | 008:L2149 | `vm.rs:193` | — | |
| G29 | **CreateContinuation field naming.** `expr` vs `program`, extra `handler_identities` field. | 008:L3502-3508 | `step.rs:122-126` | — | |
| G30 | **Continuation constructor naming.** `create_unstarted` vs spec's `create`. Parameter `expr` vs `program`. | 008:L634 | `continuation.rs:91` | — | |
| G31 | **Crate file layout.** Spec suggests `do_ctrl.rs`, `handlers/mod.rs`, `rust_store.rs`, etc. Impl has `step.rs`, `handler.rs`, `arena.rs`, `ids.rs`, `py_shared.rs`. | 008:L4559-4587 | various | — | |
| G32 | **HashMap vs SlotMap for callbacks.** Spec says `SlotMap<CallbackId, Callback>`. Impl uses `HashMap`. | 008:L4274-4283 | `vm.rs:188` | — | |
| G33 | **task_cont returns Option.** Spec says panics if not found. Impl returns `Option<Continuation>`. | 008:L1501-1507 | `scheduler.rs:416-421` | — | |

---

## Q: Discussion Items (spec silent, code has extra behavior)

For each: `add-to-spec` or `remove-from-code`.

| ID | Description | Impl Location | Why it might be needed | Resolution |
|----|-------------|---------------|----------------------|------------|
| Q1 | **handler_identities on Continuation.** `Vec<Option<PyShared>>` field preserves Rust sentinel identity across continuation round-trips to Python. | `continuation.rs:48` | ADR-14 handler identity preservation | add-to-spec DONE |
| Q2 | **KpcHandlerFactory/KpcHandlerProgram.** Large handler implementation (~200 lines) for KPC dispatch with Eval-based arg resolution. Not covered in SPEC-008 handler section. | `handler.rs:337-540` | Required for @do to work | add-to-spec DONE |
| Q3 | **to_generator_strict fallback paths.** Wraps KPC objects and bare Effects in synthetic generators. Accepts callables as programs. | `pyvm.rs:538-558` | Migration bridge / ergonomics | remove-from-code |
| Q4 | **is_effect_object multi-path detection.** Besides `is_instance_of::<PyEffectBase>()`, also tries Python module import + `__doeff_effect_base__` marker + `__doeff_kpc__` marker. | `pyvm.rs:864-892` | Bridge until Python EffectBase inherits from Rust PyEffectBase | remove-from-code |
| Q5 | **PyException enum with lazy variants.** `RuntimeError{message}` and `TypeError{message}` allow constructing exceptions without GIL. Richer than spec's `Py<PyAny>`. | `step.rs:14-26` | GIL-free error construction | add-to-spec DONE |
| Q6 | **continuation_registry HashMap.** Maps `ContId -> Continuation` for Python-side K lookup. | `vm.rs:198` | Required for handler K->Continuation mapping | add-to-spec DONE |
| Q7 | **NestingStep/NestingGenerator.** ADR-13 mechanism: `run(handlers=[...])` yields WithHandler chain via a synthetic generator. | `pyvm.rs:1271-1341` | Implements run() handler nesting | add-to-spec DONE |
| Q8 | **PyRustHandlerSentinel.** Wraps Rust handler factories as Python-visible objects with identity. | `pyvm.rs:1246-1262` | ADR-14 Rust handler identity in Python | add-to-spec DONE |
| Q9 | **default_handlers() as public API.** Exported from `doeff.__init__.py`. Returns `[state, reader, writer]`. | `rust_vm.py:33-42` | Convenience for users. Related to G6 — if G6 is fixed, this becomes the explicit way to get defaults. | add-to-spec DONE |
| Q10 | **Legacy exports in \_\_init\_\_.py.** Exports `ProgramRunResult`, `run_program`, `KleisliProgram`, `EffectCallTree`, cache utilities, graph utilities, etc. SPEC-009 §9 says these are NOT part of the public API. | `doeff/__init__.py` | Backward compat for existing users | remove-from-code (keep `KleisliProgram`, `EffectCallTree` as public API; remove `ProgramRunResult`, `run_program`, cache/graph utils) |
| Q11 | **Extra ID types.** `RunnableId(u64)`, `TaskId(u64)`, `PromiseId(u64)` not in SPEC-008 Data Structures section. | `ids.rs:35-55` | Scheduler needs them | add-to-spec DONE |
| Q12 | **DoCtrl::Eval/GetCallStack/Call handling details.** These are handled in `vm.rs` but the spec's step rules section doesn't detail their handling. | `vm.rs:628-667` | Described in TYPES-001 but not in 008 step rules | add-to-spec (already present in handle_do_ctrl) |
| Q13 | **lazy_pop_completed before DoCtrl.** Called in `step_handle_yield` before processing DoCtrl variants. Spec only mentions lazy popping in dispatch context. | `vm.rs:589` | Correct dispatch cleanup timing | add-to-spec DONE |

---

## Dependency Graph (for TDD planning)

```
G1 (effect pyclasses) ──┐
G2 (scheduler pyclasses)┤
G12 (DoCtrl extends)    ├──> G5 (simplify classify_yielded)
G13 (DoThunk extends)   │
G15 (unify EffectBase) ─┘
G3 (KPC Rust pyclass) ──> G4 (KPC extends EffectBase) ──> G11 (auto-unwrap in handler)
G8 (import paths) ──> G9 (handlers module)
G6 (default handlers) } independent
G7 (KPC auto-install) } independent
G10 (Modify semantics) } independent
G17 (scheduler errors)  } independent
G18 (RunResult unify)   } independent
G19 (Ok/Err unify)      ──> G18
G20 (store save/load)   } independent
G21 (presets module)     } independent
```

---

## Quick Decision Template

Copy-paste and fill in for rapid triage:

```
C1:
C2:
C3:
C4:
G1:
G2:
G3:
G4:
G5:
G6:
G7:
G8:
G9:
G10:
G11:
G12:
G13:
G14:
G15:
G16:
G17:
G18:
G19:
G20:
G21:
G22:
G23:
G24:
G25:
G26:
G27:
G28:
G29:
G30:
G31:
G32:
G33:
Q1:
Q2:
Q3:
Q4:
Q5:
Q6:
Q7:
Q8:
Q9:
Q10:
Q11:
Q12:
Q13:
```
