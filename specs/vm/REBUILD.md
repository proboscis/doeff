# doeff VM Rebuild — OCaml 5 Alignment

Branch: `rebuild/drop-stale-trace`

## Architecture (done)

- VM: 5 registers (segments, var_store, mode, pending_external, current_segment)
- Fiber: 3 fields (frames, parent, handler)
- Continuation: head + last_fiber, one-shot via take()
- 5 OCaml 5 operations: match_with, perform, continue_k, reperform, fiber_return
- Parent pointers as source of truth
- No ContId, no clone, no copy machine

## DoExpr Nodes (done)

Pure Python classes with `tag` attribute. No Rust base classes.

| Tag | Name | Description |
|-----|------|-------------|
| 0 | Pure | Return a value |
| 5 | Perform | Perform an effect |
| 6 | Resume | Resume continuation (non-tail) |
| 7 | Transfer | Resume continuation (tail) |
| 8 | Delegate | Forward effect, append handler to k |
| 16 | Apply | Call f(args) |
| 17 | Expand | Eval inner to Stream, push as frame |
| 19 | Pass | Forward effect to outer handler |
| 20 | WithHandler | Install handler, run body |
| 22 | (TransferThrow) | Rust-only, not exposed to Python |
| 23 | GetTraceback | Non-consuming traceback query |

## Rust Exports (done)

`doeff_vm` exports: `PyVM`, `K`, `Callable`, `EffectBase`, `Ok`, `Err`

## Python API (done)

`doeff` exports: DoExpr nodes + Rust exports + `program()` helper

## Traceback (done)

- `IRStream::source_location()` — live func_name, source_file, source_line
- `PythonGeneratorStream` reads gi_code + gi_frame.f_lineno
- `VM::collect_traceback(fiber_id)` — walks parent chain
- `GetTraceback(k)` DoExpr — handler queries without consuming k

## Done (this session)

- [x] `@do` decorator — wraps generator fn, returns DoExpr tree
- [x] `run(doexpr)` — takes a single DoExpr, creates PyVM, runs it
- [x] `WithHandler` DoExpr (tag=20) + classify
- [x] Nested WithHandler works for single Pass
- [x] Perform from handler finds outer handler (skip-self fix)

## In Progress

- [ ] Pass topology bug: after outer handler resumes, inner handler boundary lost for subsequent effects (2 xfail tests)
- [ ] Core effects (Ask, Get, Put, Tell) as EffectBase subclasses
- [ ] Handler implementations for core effects
- [ ] Error traceback rendering (format per SPEC-TRACE-001)
- [ ] Dead code cleanup (old doeff/*.py, old Rust files)

## Tests

- 25 Rust VM core tests
- 26 Python bridge tests (test_new_vm.py) — 25 pass, 2 xfail (Pass topology)
- 13 architecture violation tests
- Total: 38 passing, 2 xfailed
