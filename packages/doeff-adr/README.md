# doeff-adr

`doeff-adr` turns ADRs into executable contracts.

The package provides Hy macros for:

- `defadr`: register an ADR as structured data and emit a pytest contract check.
- `defsemgrep`: register an installed Semgrep rule with hit/clean fixtures, or
  an inline Semgrep rule body, and emit a pytest check.
- `deftest`: re-exported from `doeff-hy` for ADR-local executable examples.
- pytest plugin: collect `defadr_*.hy` / `test_defadr_*.hy` executable ADR
  files and run generated `test_*` functions.

Accepted ADRs must carry at least one executable enforcement.

Run executable ADR files directly:

```bash
uv run pytest docs/adr/defadr_0018_hypha_app_in_doeff_hy.hy
```

If an ADR uses `deftest`, the consuming project must provide the
`doeff_interpreter` pytest fixture in its own `conftest.py` or test shim.
`doeff-adr` intentionally does not choose an interpreter, scheduler, handler
stack, env source, or runtime policy for the project.

Minimal project-side fixture example:

```python
from typing import Any

import pytest


@pytest.fixture
def doeff_interpreter() -> Any:
    def run_program(program: Any, *, env: dict[Any, Any] | None = None) -> Any:
        from doeff import run

        if env:
            raise ValueError("this ADR test runtime does not use env overrides")
        return run(program)

    return run_program
```
