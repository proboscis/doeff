# IMPL-VM-PROTOCOL: Implementation Plan

Companion to [SPEC-VM-PROTOCOL.md](SPEC-VM-PROTOCOL.md).

This document inventories current violations of the VM↔Python typed protocol spec and describes the changes needed to achieve compliance.

---

## 1. Current Violation Inventory

### 1.1 Dunder Attribute Violations (spec C1)

| Attribute | File | Direction |
|-----------|------|-----------|
| `__doeff_traceback_data__` | pyvm.rs:104 | VM → Python (write) |
| `__doeff_traceback_data__` | pyvm.rs:113 | VM → Python (read via `hasattr`) |
| `__doeff_traceback__` | pyvm.rs:1470 | Python → VM (read) |
| `__doeff_traceback__` | pyvm.rs:1515 | Python → VM (read) |

Test-only fixtures also use `__doeff_scheduler_*` and `__doeff_state_*` class attributes (pyvm.rs:2202, 2284, 2308, 2332, 2367, 2484, 2508). These are in embedded Python test classes, not VM core logic.

### 1.2 Untyped Python Object Access (spec C2)

The VM reads `__code__` and `__name__` from Python handler objects for trace display:

| Location | What is accessed | Purpose |
|----------|-----------------|---------|
| vm.rs:336-358 | `handler.__code__.co_filename`, `co_firstlineno` (with `__call__.__code__` fallback) | `python_handler_source()` — handler file/line for trace entries |
| vm.rs:322-333 | `handler.__name__`, `__qualname__` (with `__call__.__name__` fallback) | `python_handler_name()` — handler name for trace entries |

Both are called from `handler_trace_info` (vm.rs:360-373), which populates trace event metadata for the handler chain.

**Resolution**: WithHandler wrapping (spec §3.8) captures handler metadata at registration time. Extend `Handler::Python` to carry this metadata:

```rust
Handler::Python {
    callable: PyShared,            // the wrapped handler
    handler_name: String,          // from handler_fn.__qualname__
    handler_file: Option<String>,  // from handler_fn.__code__.co_filename
    handler_line: Option<u32>,     // from handler_fn.__code__.co_firstlineno
}
```

The VM uses these fields for `handler_trace_info`. `python_handler_source()` and `python_handler_name()` are eliminated.

### 1.3 Python Module Import Violations (spec C6)

| Import | File |
|--------|------|
| `import("doeff.traceback")` | pyvm.rs:116 |

Called from the traceback attachment helper that attaches doeff-formatted tracebacks to exceptions. After migration, traceback data is delivered via `RunResult.traceback_data` (spec §4), making this import unnecessary.

### 1.4 Silent Fallback Violations (spec C7)

| Location | Fallback | Required change |
|----------|----------|-----------------|
| pyvm.rs:1284 | `"<anonymous>"` for missing function_name | Error: `PyCall.meta` must be present |
| pyvm.rs:1286 | `"<unknown>"` for missing source_file | Error: `PyCall.meta` must be present |
| pyvm.rs:1299-1317 | `__code__` introspection when no meta | Error: `PyCall.meta` must be present |
| pyvm.rs:1320 | `CallMetadata::anonymous()` as final fallback | Error with diagnostic |
| vm.rs:433 | `unwrap_or(metadata.source_line)` for failed line probe | Error with diagnostic info |
| vm.rs:1500 | `metadata: None` for handler generators | Error: must be DoeffGenerator |
| vm.rs:2160 | `CallMetadata::anonymous()` for `map` callback | Map DoExpr must carry mapper metadata from Python (see §4.4) |
| vm.rs:2203 | `CallMetadata::anonymous()` for `flat_map` binder | FlatMap DoExpr must carry binder metadata from Python (see §4.4) |

### 1.5 Direct Generator Introspection

| Location | What is accessed | Purpose |
|----------|-----------------|---------|
| vm.rs:415-423 | `generator.gi_frame.f_lineno` | `generator_current_line()` — live line number for trace |
| vm.rs:433 | Call site: `resume_location_from_frames` | Resume location event |
| vm.rs:674 | Call site: `supplement_with_live_state` | Trace assembly |

After migration, all live location info goes through `DoeffGenerator.get_frame` callback (spec §3.4). `generator_current_line()` is eliminated.

---

## 2. Generator Frame Push Sites (Current State)

Every place the VM pushes `Frame::PythonGenerator` onto the frame stack:

| Site | File:line | Generator source | Current metadata | Required change |
|------|-----------|------------------|------------------|-----------------|
| Entry program | pyvm.rs:647 (`start_with_generator`) | `to_generator_strict` result | `None` | Must receive DoeffGenerator |
| Call result (program) | vm.rs:1404 (`StartProgramFrame`) | `to_generator_strict` result | From `DoCtrl::Call` | Must receive DoeffGenerator |
| Call result (func) | vm.rs:1428 (`CallFuncReturn`) | Function return value | From `DoCtrl::Call` | Must receive DoeffGenerator |
| Generator re-push | vm.rs:1454 (`StepUserGenerator`) | Re-push after yield | Carried from prior frame | Already wrapped |
| Handler generator | vm.rs:1500 (`CallPythonHandler`) | `handler(effect, k)` result | **`None` — no metadata** | **Must receive DoeffGenerator** (critical gap) |

The handler generator site (vm.rs:1500) is the critical gap: handler generators are pushed with zero location info. After WithHandler wrapping (spec §3.8), the VM receives `DoeffGenerator` from every `PythonCall::CallHandler` invocation.

---

## 3. Eliminations

### 3.1 `wrap_expr_as_generator` and `wrap_return_value_as_generator`

The VM currently contains two internal helpers that create synthetic Python generators:

| Helper | Definition | What it does |
|--------|-----------|-------------|
| `wrap_expr_as_generator` | pyvm.rs:828 | Creates `def _wrap(e): v = yield e; return v` via `PyModule::from_code` — a generator that yields a DoExpr and returns whatever is sent back |
| `wrap_return_value_as_generator` | pyvm.rs:837 | Creates `def _ret(v): if False: yield v; return v` via `PyModule::from_code` — a generator that immediately returns a plain value |

**Call sites:**

| Site | File:line | Context |
|------|-----------|---------|
| Handler returns effect-like | pyvm.rs:718 | `CallHandler` path wraps DoExpr-like return in generator |
| Handler returns plain value | pyvm.rs:727 | `CallHandler` path wraps non-generator return |
| `to_generator_strict` for DoExprBase | pyvm.rs:815 | Wraps `PyDoExprBase` objects into generators |

**Why they must be eliminated:**

1. They create raw Python generators inside the VM — violating C2 and the DoeffGenerator invariant (spec §3.7).
2. They use `PyModule::from_code` — creating Python code from string literals inside Rust, which is fragile and defeats typing.
3. Handler return normalization belongs in WithHandler (spec §3.8).

**After elimination:**

- `wrap_expr_as_generator` — removed entirely
- `wrap_return_value_as_generator` — removed entirely
- `CallHandler` handler return path — simplified to DoeffGenerator extraction only
- `to_generator_strict` for `PyDoExprBase` — delegates to Python-side `to_generator()` method

### 3.2 Effect `created_at` / `EffectCreationContext`

Every effect creation currently calls `create_effect_with_trace()` (~59 call sites in core `doeff/effects/` across 17 files, plus additional sites in extension packages `doeff-events`, `doeff-time`, `doeff-sim`), which runs `capture_creation_context()` on **every `yield`** in user code.

`capture_creation_context()` (doeff/utils.py:106) performs on every call:
1. `sys._getframe(skip_frames)` — frame lookup
2. `linecache.getline(filename, line)` — file I/O (cached)
3. Walk **8-12 parent frames**, each with: `f_code.co_filename`, `f_lineno`, `co_name`, plus `linecache.getline()`
4. Build `list[dict]` of stack data
5. Allocate `EffectCreationContext` frozen dataclass
6. `dataclasses.replace(effect, created_at=ctx)` — allocate a copy of the entire frozen effect

The `get_frame` callback (spec §3.4) replaces `created_at` as the source of live line information:

| | `created_at` (current) | `get_frame` callback (spec) |
|---|---|---|
| **When it runs** | Every effect creation (hot path) | Only during trace assembly or error (cold path) |
| **Cost per invocation** | Frame walk + linecache + allocations | Single Python call reading `gi_frame.f_lineno` |
| **Allocations on hot path** | `EffectCreationContext` + effect copy per yield | None |

For the trace system, the generator's suspended line (`gi_frame.f_lineno` via callback) and the effect's creation site (`created_at`) report the same line in the common case: `value = yield Get("key")` — the effect is created and yielded on the same line.

The edge case where they differ — `effect = Get("key"); ...; value = yield effect` — is uncommon and the generator suspend line (where the `yield` is) is arguably more useful for tracing than where the effect object was instantiated.

**Migration steps:**

1. Remove `created_at` field from `EffectBase` and all effect dataclasses
2. Remove `create_effect_with_trace()` wrapper from all ~59+ call sites — effects are constructed directly
3. Remove `capture_creation_context()` helper
4. Remove `EffectCreationContext` dataclass
5. Update `EffectFailureError` (`_types_internal.py:532-536`) and trace rendering to use VM-provided line info instead of `created_at`
6. Update error display paths that read `effect.created_at` to use trace data from `RunResult.traceback_data`
7. Update `cache.py:279-290` — currently reads `created_at` from `ProgramCallStack` frames for cache call-site attribution; must use alternative source (VM trace data or explicit call-site parameter)

This is a Python-side cleanup. The VM is not involved — it never read `created_at`.

### 3.3 Handler Trace Metadata

`python_handler_source` (vm.rs:336-358) and `python_handler_name` (vm.rs:322-333) read `__code__` and `__name__` from Python handler functions for trace entries.

**Resolution**: see §1.2 above. `Handler::Python` carries metadata from WithHandler registration. Both functions are eliminated.

### 3.4 Map/FlatMap Callback Metadata

`handle_map` (vm.rs:2160) and `handle_flat_map` (vm.rs:2203) synthesize internal `DoCtrl::Call` instructions for the mapper/binder callback. Currently these use `CallMetadata::anonymous()`.

**Context**: `program.map(f)` and `program.flat_map(f)` are Python-side combinators. The VM evaluates the source program, then calls `f(result)` by synthesizing a `DoCtrl::Call`. The mapper `f` is a plain Python callable — not a `@do` function, not wrapped in DoeffGenerator. Its result is typically a plain value (map) or a DoExpr (flat_map), not a generator.

**Resolution**: Map and FlatMap DoExpr variants carry metadata about the mapper/binder, populated by the Python side at DoExpr construction time:

```rust
DoCtrl::Map {
    source: PyShared,
    mapper: PyShared,
    mapper_meta: CallMetadata,  // NEW: from mapper.__code__ at construction time
}
```

The VM uses `mapper_meta` when synthesizing the internal `DoCtrl::Call` instead of `anonymous()`. This keeps metadata extraction on the Python side (at DoExpr construction, not at VM step time) and eliminates `anonymous()` from this runtime path.

---

## 4. Eliminated Boundary Mechanisms (Summary)

| Old mechanism | Direction | Spec replacement |
|---------------|-----------|------------------|
| `__doeff_traceback_data__` dunder on exception | VM → Python | `DoeffTracebackData` PyClass on `RunResult` (spec §4) |
| `__doeff_traceback__` dunder on exception | Python ↔ Python | Python consumer manages this post-VM (spec §4.4) |
| `gi_frame.f_lineno` direct probe | VM reads Python | `DoeffGenerator.get_frame` callback (spec §3.4) |
| `__doeff_inner__` chain on generator | VM reads Python | Eliminated — callback encapsulates navigation (spec §3.3) |
| `import("doeff.do")` from VM | VM imports Python | Eliminated entirely (spec C6) |
| `import("doeff.traceback")` from VM | VM imports Python | Eliminated entirely (spec C6) |
| `__code__` introspection in `call_metadata_from_pycall` | VM reads Python | `PyCall.meta` mandatory (spec C7, §5.3) |
| `__code__` introspection in `python_handler_source` | VM reads Python | Handler metadata from WithHandler registration (§3.3) |
| `wrap_expr_as_generator` (synthetic generator in VM) | VM creates Python | Eliminated — handler normalization in WithHandler (spec §3.8) |
| `wrap_return_value_as_generator` (synthetic generator in VM) | VM creates Python | Eliminated — handlers must return generators (spec §3.9) |
| Handler non-generator return normalization in VM | VM wraps Python | Moved to WithHandler wrapper (spec §3.8, §3.9) |
| `effect.created_at` / `EffectCreationContext` | Python hot path | Eliminated — `get_frame` callback on cold path only (spec §3.4) |
| `CallMetadata::anonymous()` in map/flat_map | VM synthesizes | Map/FlatMap DoExpr carry mapper metadata from Python (§3.4) |

---

## 5. Handler-Level Dunder Audit (Out of Scope)

These dunders exist outside the VM core. They are not protocol violations but are noted for a separate cleanup:

| Dunder | Set | Read | Purpose | Verdict |
|--------|-----|------|---------|---------|
| `__doeff_do_decorated__` | `do.py:85` on KleisliProgram | `kleisli.py:196` | Gates handler annotation validation. If absent, validation skipped. | Can be replaced by `isinstance(handler, KleisliProgram)`. |
| `__doeff_do_wrapper__` | `decorators.py:20` on factory func | **Never read at runtime** | Tooling/static analysis marker only. | Dead at runtime. |
| `__doeff_effect_base__` | `_types_internal.py:602` on EffectBase | `doeff-agentic/opencode.py:1016` | Type detection: is value a lazy effect? | Can be replaced by `isinstance(value, PyEffectBase)`. |
| `__doeff_do_expr_base__` | Not found as setter | `doeff-agentic/opencode.py:1015` | Type detection: is value a lazy DoExpr? | Can be replaced by `isinstance(value, PyDoExprBase)`. |
| `__doeff_handler_validation_patched__` | `doeff_vm/__init__.py:47` | `doeff_vm/__init__.py:19` | One-time monkey-patch guard. | Module-init bookkeeping. Can be a module-level bool. |
| `__doeff_scheduler_*` (9 variants) | `effect.rs` classattrs | **Not read by scheduler** (uses isinstance) | Vestigial. Scheduler uses `downcast::<PySpawn>()` etc. | Dead code. Safe to remove. |
