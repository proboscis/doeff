# Draft: KPC True Macro Expansion

## Requirements (confirmed)
- KleisliProgram.__call__() must produce a Call DoCtrl — NOT a PyKPC effect
- VM must have ZERO knowledge of KPC
- Hard-drop: no backward compat shim, no _effective_runtime_handlers secret injection
- PyKPC, KpcHandlerFactory, KpcHandlerProgram, ConcurrentKpcHandlerProgram — all deleted
- Auto-unwrap strategy cached at decoration time, applied at call time (macro expansion)

## Research Findings

### Call DoCtrl Shape (VM)
- `PyCall(f, args, kwargs, meta)` — extends PyDoCtrlBase, tag=1
- VM dual-path dispatch:
  - PATH A (no args): StartProgram → to_generator_strict(f) → push generator frame
  - PATH B (has args): CallFunc → func.call(args, kwargs) → deliver result as value
- Args are Vec<Value> (already-resolved), NOT DoExpr nodes to evaluate

### Auto-Unwrap Strategy
- `_build_auto_unwrap_strategy()` already exists in program.py:195-247
- Annotation classifiers exist: _annotation_text_is_program_kind(), _is_program_annotation_kind()
- Currently computed per-execution in Rust handler (no caching)
- NO caching exists anywhere yet

### KPC Removal Inventory
- ~750 lines Rust to delete (effect.rs, handler.rs, pyvm.rs, vm.rs, lib.rs)
- ~80 lines Python to delete/modify (program.py, rust_vm.py, handlers.py, kleisli.py, __init__.py, etc.)
- 15 files total affected

## Critical Design Decision: How Call Evaluates Args

SPEC-KPC-001 says:
> All args in the resulting Call are DoExpr nodes. The VM evaluates each arg
> sequentially left-to-right before invoking the kernel.

Current VM Call handling does NOT evaluate DoExpr args — it passes them as plain values.

### Option A: Extend VM Call to evaluate DoExpr args
- VM's Call handler gets a new phase: evaluate each arg as DoExpr, collect results, then call kernel
- Similar to how KpcHandlerProgram worked, but generic (not KPC-specific)
- Preserves Call metadata for tracing
- Matches spec exactly
- Requires Rust changes to vm.rs Call handling

### Option B: Generator-based macro expansion (no VM changes)
- __call__ produces a GeneratorProgram that yields effects/programs to resolve args, then yield-froms kernel
- VM sees a normal generator — zero changes needed
- Simpler, but loses Call-level trace metadata
- Deviates from spec wording (returns GeneratorProgram, not Call DoCtrl)

## DECIDED: Option A — Extend VM Call to evaluate DoExpr args
- Kernel (f) is ALSO a DoExpr — VM evaluates it too
- Full Call shape: Call(Pure(kernel), [DoExpr args], {DoExpr kwargs}, metadata)
- VM evaluates: f → resolved_callable, then args L-to-R → resolved_values, then kwargs → resolved_kw
- Then calls resolved_callable(*resolved_values, **resolved_kw) → generator → push frame
- This is a GENERIC VM improvement, not KPC-specific
- ~50-80 lines of Rust in vm.rs Call handling (phased arg evaluator)

## Scope Boundaries
- INCLUDE: Full KPC removal, macro expansion, strategy caching, test updates, revert bad changes
- EXCLUDE: Concurrent arg resolution (removed per spec), non-KPC perf work, doc sweep
