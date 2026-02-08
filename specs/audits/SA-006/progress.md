# SA-006 Progress (Round 2)

| ID | Status | Test/Rule | Fix PR |
|---|---|---|---|
| SA-006-G01 | pending | - | - |
| SA-006-G02 | pending | - | - |
| SA-006-G03 | pending | - | - |
| SA-006-G04 | pending | - | - |
| SA-006-G05 | pending | - | - |
| SA-006-G06 | pending | - | - |
| SA-006-G07 | pending | - | - |
| SA-006-G08 | pending | - | - |

## Dependency/Status Ledger

- fact: `run/async_run` still normalize program inputs and accept duck-typed generators -> issue: SA-006-G01 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: boundary validation matrix requirements are not fully enforced with spec-required error messaging -> issue: SA-006-G02 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G01]
- fact: primitive constructors defer validation until classify/dispatch path -> issue: SA-006-G03 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G01]
- fact: Rev10 requires binary hierarchy and `Program = DoExpr` while implementation keeps `DoThunk` and `ProgramBase` alias -> issue: SA-006-G04 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: composition remains generator-based rather than DoCtrl AST node composition -> issue: SA-006-G05 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G04]
- fact: callstack API still exposed as legacy effect path instead of canonical `GetCallStack` DoCtrl route -> issue: SA-006-G06 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: [SA-006-G04]
- fact: `GetHandlers` identity preservation remains broken for Rust sentinels -> issue: SA-006-G07 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
- fact: `WithHandler` field naming contract in spec vs implementation remains mismatched -> issue: SA-006-G08 -> auto-resolve/discussion: auto-fix-code -> action: fix-code -> dependencies: []
