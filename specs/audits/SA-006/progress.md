# SA-006 Progress

| ID | Status | Test/Rule | Fix PR |
|---|---|---|---|
| SA-006-C01 | pending-resolution | - | - |
| SA-006-C02 | pending-resolution | - | - |
| SA-006-G01 | pending | - | - |
| SA-006-G02 | pending | - | - |
| SA-006-G03 | pending | - | - |
| SA-006-G04 | pending | - | - |
| SA-006-G05 | pending | - | - |
| SA-006-G06 | pending | - | - |
| SA-006-G07 | pending | - | - |
| SA-006-G08 | pending | - | - |
| SA-006-G09 | pending | - | - |
| SA-006-G10 | pending | - | - |
| SA-006-Q01 | pending-resolution | - | - |
| SA-006-Q02 | pending-resolution | - | - |
| SA-006-Q03 | pending-resolution | - | - |

## Dependency/Status Ledger

- fact: SPEC-008 says Effect enum removed while later text retains enum-style references -> issue: SA-006-C01 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: []
- fact: TYPES-001 presents conflicting KPC metadata extraction loci (classifier vs handler) -> issue: SA-006-C02 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: []
- fact: effect pyclass payload/class contract diverges from fielded spec contract -> issue: SA-006-G01 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-C01]
- fact: continuation conversion exposes internals instead of opaque K -> issue: SA-006-G02 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: handler identity preservation breaks via `rust_program_handler` placeholder -> issue: SA-006-G03 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G02]
- fact: Python wrapper performs program normalization prohibited by SPEC-009 boundary contract -> issue: SA-006-G04 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: DoCtrl acceptance path and boundary error messaging diverge from spec contract -> issue: SA-006-G05 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G04]
- fact: validation matrix requirements for boundary checks/constructor checks are not enforced -> issue: SA-006-G06 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G04]
- fact: Program alias/hierarchy model differs from TYPES-001 contract -> issue: SA-006-G07 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-C02]
- fact: DoExpr root composability contract absent in implementation type root -> issue: SA-006-G08 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G07]
- fact: callstack effect bridge does not follow unified DoCtrl path described in TYPES-001 -> issue: SA-006-G09 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-C02]
- fact: annotation alias normalization for `Thunk` diverges from spec example set -> issue: SA-006-G10 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G07]
- fact: scheduler ready-waiters queue behavior exists without explicit spec statement -> issue: SA-006-Q01 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: []
- fact: extra exported handler sentinels are visible publicly with unclear policy boundary -> issue: SA-006-Q02 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: []
- fact: test requirement location/scope policy for public API validation is underspecified vs existing tests -> issue: SA-006-Q03 -> auto-resolve/discussion: discussion-required -> action: discuss -> dependencies: []
