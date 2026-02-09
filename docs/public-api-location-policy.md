# Public API Location Policy

## Status

Active from 2026-02-09.

Related issues:
- ISSUE-CORE-489 (policy)
- ISSUE-CORE-490 (tests/public_api migration)
- ISSUE-CORE-491 (docs/examples + semgrep guardrails)

## Contract

Public API location is fixed at top-level `doeff`.

Consumer-facing code MUST import from:

```python
from doeff import ...
```

Consumer-facing code MUST NOT import from subpackages such as:

- `doeff.program`
- `doeff.types`
- `doeff.rust_vm`
- `doeff.do`
- `doeff.effects`

Exception: internal implementation modules and internal tests may still use
subpackage imports when needed for white-box/runtime verification.

## Migration Scope

In scope now:
- `tests/public_api/**/*.py`
- `examples/**/*.py`
- consumer-facing docs

CESK user-facing surfaces have been removed under ISSUE-CORE-492.

## Enforcement

Semgrep guardrails are required for in-scope paths to prevent regression.

Validation command:

```bash
uv run semgrep --config .semgrep.yaml tests/public_api examples docs
```
