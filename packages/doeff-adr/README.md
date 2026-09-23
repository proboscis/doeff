# doeff-adr

`doeff-adr` turns ADRs into executable contracts.

The package provides Hy macros for:

- `defadr`: register an ADR as structured data and emit a pytest contract check.
- `defsemgrep`: register an installed Semgrep rule with hit/clean fixtures, or
  an inline Semgrep rule body, and emit a pytest check.
- `deftest`: re-exported from `doeff-hy` for ADR-local executable examples.
- pytest plugin: collect `defadr_*.hy` / `test_defadr_*.hy` executable ADR
  files and run generated `test_*` functions.
- wiring self-check: compare every executable ADR in the repository with the
  files that the current pytest invocation actually collected.

Accepted ADRs must carry at least one executable enforcement.

## Where an installed `defsemgrep` reads its rule

The installed form names a rule id and carries hit / clean fixtures:

```hy
(defsemgrep worker-files-rule
  "worker-business-code-touches-files-only-through-effects"
  [{"relative-path" "controllers/worker/lab/keeper.hy" "source" "…"}]    ; must fire
  [{"relative-path" "controllers/worker/lab/keeper.hy" "source" "…"}]    ; must stay silent
  :config "worker.semgrep.yaml")
```

- `:config` absent: the nearest `.semgrep.yaml` found by walking up from
  pytest's working directory (the original behavior).
- `:config "relative/path.yaml"`: resolved against the directory of the file
  that declares the `defsemgrep`, so a rule file can live next to the ADR that
  owns it and does not depend on where pytest was started.
- `:config "/absolute/path.yaml"`: used as is.

`doeff_adr.registry.resolved_config_path(spec)` returns the file a spec reads.

Run executable ADR files directly:

```bash
uv run pytest docs/adr/defadr_0018_hypha_app_in_doeff_hy.hy
```

## Collection wiring gate

Include every executable ADR directory in the pytest collection roots used by
CI. For the conventional layout:

```toml
[tool.pytest.ini_options]
testpaths = ["tests", "docs/adr"]
```

Then add the one-line fail-closed gate before the test run:

```bash
uv run doeff-adr verify-wiring
```

The command runs pytest collection in `strict` mode and exits nonzero while any
matching executable ADR is absent from the effective collection. Normal pytest
runs default to `warn` so targeted local runs remain usable. Set
`doeff_adr_wiring = "strict"` in pytest configuration when every invocation
should fail closed, or pass `--doeff-adr-wiring=off` only for an intentional
local opt-out. `defsemgrep` enforcement separately fails, rather than skips,
when the `semgrep` executable is unavailable.

Wiring is a property of the collection scope, not of the selection: the
collected files are measured before `-k` / `-m` / `--deselect` drop items, so a
default-scope run narrowed with `-m 'not e2e'` still verifies.

### In-session gate test

A repository whose canonical gate is the default pytest run itself can read the
verdict from inside that run instead of spawning a second collection (a nested
`pytest --collect-only` is O(suite) work under a per-test deadline):

```python
from pathlib import Path

import pytest
from doeff_adr.pytest_plugin import (
    NotDefaultScope,
    WiringVerified,
    default_scope_wiring,
    wiring_failure_message,
)


def test_all_executable_adrs_are_collected(request: pytest.FixtureRequest) -> None:
    verdict = default_scope_wiring(request.session)
    if isinstance(verdict, NotDefaultScope):
        pytest.skip(f"session collected {list(verdict.args)}, not the default scope")
    if not isinstance(verdict, WiringVerified):
        pytest.fail(wiring_failure_message(verdict, Path(request.config.rootpath)))
```

`default_scope_wiring` answers `WiringVerified`, `WiringUncollected`, or
`WiringWalkAborted` for a default invocation (no path arguments), reusing the
one measurement the plugin already took at collection finish. A session aimed
at explicit paths answers `NotDefaultScope`: it cannot speak for the default
scope in either direction (narrower paths miss ADRs the default scope reaches;
wider paths reach ADRs the default scope leaves silent), so the test must not
report it as a pass.

For proboscis-ema and agent-control-plane follow-ups, add their existing
`docs/adr` directory to the pytest roots used by the real CI test command,
install `semgrep` when those ADRs contain `defsemgrep`, and run
`uv run doeff-adr verify-wiring` as a required step. The gate reports every
remaining `defadr_*.hy` path, so migration can be completed without maintaining
a second hand-written ADR file list.

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
