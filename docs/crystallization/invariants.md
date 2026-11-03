# Catalogue of the doeff VM's implicit invariants

Written: 2026-06-11. Implementation: `packages/doeff-vm-core/src/vm/invariants.rs` (cargo feature `invariant-checks`).
Before this session, these consistency conditions **existed only as the shape of the code**. Each condition is given evidence of "which code silently assumes/absorbs this condition".

## How to run

```bash
cd packages/doeff-vm-core
PYO3_PYTHON=$(git rev-parse --show-toplevel)/.venv/bin/python \
  cargo test --features "python_bridge invariant-checks"
```

- `assert_invariants_after_step()` runs **after every transition** of `VM::step()` (it lists all violations and panics)
- Zero cost when the feature is disabled (not compiled)
- `VM::check_invariants()` (fatal violations only) and `VM::invariant_report()` (violations + known tensions) can also be called directly
- Note: every vm-core module sits behind the `python_bridge` feature, so `invariant-checks` alone does not compile the checker

## Severity convention

- **violation (fatal)**: a structural break that the VM's own operations assume "never happens". Firing = bug. It panics
- **tension**: a known contradiction between spec and code that the current code absorbs with a fallback. Reported only. Resolving it is a crystallization decision, not the checker's

## Condition catalogue

### I1. Arena slot hygiene (violation)
free_list has no duplicates, is within range, and every entry points to a None slot.
**Evidence**: `arena.rs alloc/free` — free_list consistency is a precondition for alloc being correct. `attach_chain` turns free_list contamination into a runtime error (arena.rs:74-79), but nobody checked the hygiene of free_list itself.

### I2. current_segment exists (violation)
If `current_segment = Some(id)`, id is a live fiber in the arena.
**Evidence**: every path in `step.rs` uses `self.segments.get_mut(seg_id)` assuming success. Failure is handled by scattered individual `else` branches.

### I3. Parent chain is acyclic and non-dangling (violation)
Following parent from any fiber in the arena stops without cycling and without pointing to a nonexistent fiber.
**Evidence**: the `seen.insert` guard at `vm/var_store.rs:22` and the `:19 else break` assume this condition in a form that **silently absorbs** cycles and dangling pointers. `collect_traceback` (vm.rs:82) likewise absorbs it with a break. A violation would silently make scope resolution and tracebacks incomplete.

### I4. Handler boundary consistency (violation)
(a) The Handler of a boundary fiber has **exactly one role** among prompt/intercept/mask. (b) The placeholder marker (0) is not installed in the arena. (c) Markers are unique within the arena.
**Evidence**: the 3 constructors at `segment.rs:64-113` set only one role (the structural intent), but the fields are pub so a multi-role configuration can be built. Marker uniqueness is a precondition for `find_prompt_boundary_by_marker` (vm/handler.rs:83) being correct.

### I5. Internal consistency of detached chains (violation)
For every DetachedFiberChain visible from the VM (pending_handler_chain_backup, Frame::Program.chain_backup, Value::Continuation in VarStore): head/last_fiber exist among the owned fibers, there are no duplicate ids, the parent chain from head reaches last_fiber and covers all owned fibers, and the tail's parent is None.
**Evidence**: `arena.rs detach_chain` (:40-62) guarantees this at construction, but whether it is maintained after later operations such as `DetachedFiberChain::append` (continuation.rs:88) was not checked.

### I6. Single-location law (violation) — mechanization of the K1 coupling core
**"A continuation (the fibers it owns) exists in exactly one place at any point in time"** (a runtime check of SPEC-VM-021:282). Concretely: the arena slot of a fiber owned by a detached chain is `VacantReserved` (empty but not in free_list = reserved), and no fiber is owned by two chains.
**Evidence**: `attach_chain` (arena.rs:74-88) partially checks this on reattachment, but nobody checked held continuations. This fixes the root cause of the R3 rebuild (postmortem.md §3) as a law.

### I7. EvalReturn references exist (violation)
The `head_fiber` referenced by an `EvalReturn{ResumeToContinuation|ReturnToContinuation|EvalInScopeReturn}` frame of a fiber in the arena exists in the arena or in a detached chain visible to the VM.
**Evidence**: `vm/var_store.rs:31-41` follows this reference to resolve scope, but silently skips it when missing (= scope is silently lost).

### I8. Var cell owner is alive (**tension** — known tension B14) — settled, scheduled for removal
The `owner_segment` of each VarId in `var_store.cells` has not been freed (VacantFree).
**Reality**: **it fires in the existing test `test_alloc_read_write_var`** (the cell survives after the fiber completes). It is invisible in production because the unconditional fallback in `read_scoped_var_from` (vm/var_store.rs:144) absorbs it.
**Meaning**: proof that the contradiction (constraint-graph.md §4-④) between SPEC-VM-020:191 "VarId.owner_segment should not exist (cells are pointed to by pointer)" and SPEC-VM-019 Rev 2 "segments are the scope" **actually exists in a form observable at run time**.
**Settled (2026-06-12, D15)**: the whole Var machinery turned out to be dead infrastructure and its full removal was decided (Python API already removed, no bridge exposure, the only user is one Rust test). The tension resolves by its subject disappearing. The I8 checker is scheduled for removal as part of the removal issue (constraint-graph.md §4-④) — until then it keeps the current "report only" behaviour.

## Limits of what the checker observes

1. **Continuations held only by Python-side PyK are invisible** (unreachable from the VM struct). Full verification of the single-location law needs cooperation from the bridge layer
2. **Continuations buried inside DoCtrl** (e.g. `DoCtrl::Resume{k}` pushed on an Apply-family frame of EvalReturn) are not scanned (little to find relative to the recursion depth; can be extended if needed)
3. Since it checks only **after** a `step()` transition, transient inconsistencies during a transition are not observed (intentional: invariants need not hold mid-transition)

## Implementation changes (reference)

- New: `src/vm/invariants.rs` (the checker + 8 unit tests)
- `Cargo.toml`: added feature `invariant-checks`
- `src/vm.rs`: module registration (cfg-gated)
- `src/vm/step.rs`: hook at the end of `step()` (cfg-gated, no logic change)
- `src/continuation.rs`: `cell_addr`/`inspect_chain` helpers (cfg-gated, crate-internal)
- `src/arena.rs`: `SlotStatus`/`slot_status`/`free_list_violations` (cfg-gated, crate-internal)

Verification result (2026-06-11): with the feature enabled 36 passed / 1 ignored (28 existing + 8 new); with the feature disabled 28 passed (no regression).
