"""doeff_interpreter fixture for docs/adr — the runtime for doeff-adr deftest enforcement.

Responsibility boundary (doeff-adr policy): collection is owned by the doeff-adr pytest plugin,
and the runtime fixture is owned by the consuming repository (here, doeff itself).

This fixture doubles as the reference implementation of ADR-DOE-HY-002 R3:
- A deftest's :env is always applied through the reader handler (never silently ignored).
- Errors are re-raised (no false green from RunResult absorbing them).
- The records an ADR deftest fetches with (<- spec (GetAdr ADR-ID)) and the id list it reads with (<- ids (AdrIds))
  are answered from the registry by doeff-adr's adr_registry().
"""

import pytest


@pytest.fixture
def doeff_interpreter():
    def run_program(program, *, env=None):
        from doeff import run
        from doeff_adr import adr_registry

        program = adr_registry()(program)
        if env:
            from doeff_core_effects import reader

            program = reader(dict(env))(program)
        return run(program)

    return run_program
