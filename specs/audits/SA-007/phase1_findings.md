# SA-007 Phase 1 Findings (Parallel Review)

Session: `SA-007`
Spec scope:
- `specs/vm/SPEC-008-rust-vm.md`
- `specs/vm/SPEC-009-rust-vm-migration.md`
- `specs/core/SPEC-TYPES-001-program-effect-separation.md`

Review units executed in parallel: `P1-U1` .. `P1-U9`

## Unit summaries

| Unit | Matches | Divergences | Missing | Discussion | Contradictions |
|---|---:|---:|---:|---:|---:|
| P1-U1 | 14 | 10 | 6 | 3 | 3 |
| P1-U2 | 15 | 7 | 3 | 1 | 3 |
| P1-U3 | 14 | 6 | 4 | 3 | 3 |
| P1-U4 | 14 | 6 | 4 | 3 | 5 |
| P1-U5 | 15 | 9 | 4 | 2 | 4 |
| P1-U6 | 23 | 7 | 3 | 3 | 2 |
| P1-U7 | 19 | 8 | 4 | 10 | 0 |
| P1-U8 | 12 | 10 | 3 | 1 | 1 |
| P1-U9 | 44 | 71 | 30 | 3 | 2 |

## Cross-referenced recurring findings (deduplicated)

1. Entry boundary drift:
   - `doeff/rust_vm.py` still normalizes/wraps top-level input (`_normalize_program`, `_TopLevelDoExpr`) where SPEC-009 requires a thin pass-through boundary.
2. Type-validation drift:
   - Construction-time validation for `Resume`, `Transfer`, `Delegate`, and `WithHandler` is not enforced at constructor boundaries.
3. TYPE-001 architectural drift:
   - `DoThunk`/`to_generator` pipeline still active.
   - `Program` is not strict `DoExpr` alias.
   - `Pure/Map/FlatMap` control-node model is not implemented.
4. Identity and visibility drift:
   - `GetHandlers` path can emit placeholders (`"rust_program_handler"`) instead of preserving identity semantics.
5. Classifier and effect model drift:
   - Classifier/control-flow paths still rely on concrete class checks and compatibility behaviors inconsistent with strict base-only/type-separation narrative.
6. Spec internal contradictions were repeatedly reported across units (naming and model-level conflicts).

## Phase 1 gate note

All assigned section units produced results with evidence references. Duplicate and cross-unit overlaps were merged into the recurring findings list above for Phase 2 classification.
