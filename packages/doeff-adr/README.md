# doeff-adr

`doeff-adr` turns ADRs into executable contracts.

The package provides Hy macros for:

- `defadr`: register an ADR as structured data and emit a pytest contract check.
- `defsemgrep`: register an installed Semgrep rule with hit/clean fixtures, or
  an inline Semgrep rule body, and emit a pytest check.
- `deftest`: re-exported from `doeff-hy` for ADR-local executable examples.

Accepted ADRs must carry at least one executable enforcement.
