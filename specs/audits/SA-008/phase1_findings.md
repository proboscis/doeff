# SA-008 Phase 1 Findings (Subagent Parallel Review)

Session: `SA-008`

Specs reviewed:
- `specs/vm-architecture/SPEC-008-rust-vm.md`
- `specs/vm-architecture/SPEC-009-rust-vm-migration.md`
- `specs/SPEC-TYPES-001-program-effect-separation.md`

Review units executed: `P1-U1` .. `P1-U9`

## Unit summary (cross-referenced)

| Unit | Matches | Divergences | Missing | Discussion | Contradictions |
|---|---:|---:|---:|---:|---:|
| P1-U1 | 11 | 2 | 0 | 4 | 0 |
| P1-U2 | 11 | 1 | 0 | 0 | 0 |
| P1-U3 | 8 | 0 | 0 | 2 | 0 |
| P1-U4 | 12 | 1 | 1 | 2 | 0 |
| P1-U5 | 11 | 2 | 0 | 2 | 0 |
| P1-U6 | 7 | 3 | 2 | 1 | 0 |
| P1-U7 | 14 | 1 | 0 | 1 | 0 |
| P1-U8 | 10 | 2 | 0 | 1 | 1 |
| P1-U9 | 9 | 7 | 3 | 2 | 1 |

## Deduplicated findings for Phase 2 classification

1. `Yielded::Unknown` fallback category remains in runtime classification path while R10 narrative expects strict binary DoExpr taxonomy.
2. `classify_yielded` remains concrete-type/extract driven with fallback branch; binary base-only/tag-only classifier intent is not fully realized.
3. `DoThunk` compatibility alias remains exported from Python API, conflicting with R10 “eliminated” direction.
4. `GeneratorProgram` remains central for composition pipelines in Python layer; `.map/.flat_map` still create generator-backed programs.
5. Rust `DoCtrl::Map` and `DoCtrl::FlatMap` runtime path is not semantically implemented (throws runtime error path).
6. Standard and scheduler effect handling paths still rely on marker/getattr parsing patterns in handler/scheduler pipelines rather than strict typed effect pyclass extraction narrative.
7. KPC parse/unwrap logic still includes Python-side strategy/shape assumptions that diverge from strict Rust-owned classifier architecture.
8. Public `RunResult` surface remains split (Rust run result wrappers vs Python-side protocol expectations), causing ambiguity in type-level guarantees.
9. Unhandled-effect error semantics are generic runtime/type errors rather than dedicated domain-specific public error type.
10. Internal/runtime-only methods and objects are mostly hidden but remain discoverable through extension-level imports; policy boundary is unclear.
11. SPEC-TYPES-001 has internal drift: R10 “DoThunk eliminated” direction vs remaining DoThunk-centric public test requirement language.
12. SPEC-TYPES-001 classifier section contains mixed messaging around fallback/unknown behavior vs strict binary model.

## False-positive filtering notes

- “missing test coverage for TYPES-001” claims were rejected; relevant tests exist under `tests/public_api/test_types_001_*.py`.
- “run() auto-installs handlers by default” claims were rejected for `doeff.rust_vm.run`; default handler installation happens only when explicitly passed (or through higher-level wrappers), not at function default signature level.
