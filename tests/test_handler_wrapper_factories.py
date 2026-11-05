"""The 8 names that used to wrap a program with a handler are now factories (or handler values) applied as h1(h2(p)), like other handlers.

Rule: a handler-returning factory is a function that, called with minimal arguments, returns a handler carrying the marker
_doeff_is_handler_fn (ADR-DOE-API-001 R3). Functions that wrap a program are moved to the factory shape (R6), and handlers are
applied to a program as h1(h2(p)) (R16). A handler with neither configuration nor per-call state is a marked value, not a
factory (ADR-DOE-API-002 R1). Where each name went:

- doeff-conductor's make_blocking_scheduled_handler and make_async_scheduled_handler: kept as factories under the same names
  (take fn and return a handler that answers every effect with fn(effect); the async one emits a DeprecationWarning).
- doeff-notify's testing_handler and build_testing_handler (two names for the same function): kept as factories under the
  same names (take the capture destination for records).
- doeff-core-effects' run_apart: no configuration, so kept under the same name as a marked handler value: (run-apart program).
- doeff-agentic's with_agentic_effectful_handlers(p, ...): removed, moved to the existing factory agentic_effectful_handlers(...)(p).
- doeff-agentic's with_handlers(p, handlers) (doeff_agentic.runtime): removed, moved to doeff's stacked_handlers(*handlers)(p)
  (the submodule doeff_agentic.runtime was also removed).
- doeff-records' ready_handlers: not a function that installs handlers but a program that prepares the check shell (returns the
  handler for the writer's name as is), so it was moved to the internal _ready_handlers instead of becoming a factory.

1. The inventory (tools/public_api_inventory.py) reads the factory names as handlers that "return a handler when called" and
   run_apart as a handler carrying the marker, and does not count the removed names or the names moved inside as public names.
2. Calling a factory returns a marked handler, and applying that handler (and run_apart) to a program gives the same answer as the
   original function (doeff-notify is not in the root environment, so packages/doeff-notify/tests/test_handlers.py checks the same thing).

The doeff-conductor, doeff-agentic and doeff-records parts (the 3 lines above) are checked by each package's own tests, so that
the root tests do not import internal packages (2026-11-04): packages/doeff-conductor/tests/test_scheduled_handler_factories.py,
packages/doeff-agentic/tests/test_handler_factory_shape.py, packages/doeff-records/tests/test_ready_handlers_inside.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from doeff import do, run

ROOT = Path(__file__).resolve().parents[1]
#: Time limit in seconds for the inventory child process to read one package (same as tests/test_public_api_known_violations.py).
_PROBE_SECONDS = 100


def _load_tool() -> ModuleType:
    """Load the inventory tool under tools/ (shares one module with the known-violations table test under the same name)."""
    loaded = sys.modules.get("public_api_inventory")
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(
        "public_api_inventory", ROOT / "tools" / "public_api_inventory.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["public_api_inventory"] = module
    spec.loader.exec_module(module)
    return module


#: Names that became factories under the same name, and destinations moved to an existing factory: (package dir, entry-point module, name).
FACTORIES = (
    ("doeff-notify", "doeff_notify", "testing_handler"),
    ("doeff-notify", "doeff_notify", "build_testing_handler"),
)
#: Names that became marked handler values rather than factories because they have no configuration: (package dir, entry-point module, name).
VALUES = (("doeff-core-effects", "doeff_core_effects", "run_apart"),)
PACKAGES = sorted({package for package, _module, _name in (*FACTORIES, *VALUES)})


@pytest.fixture(scope="module")
def inventory_rows() -> dict[str, list[dict[str, str]]]:
    """Inventory rows of the 2 packages (per package dir)."""
    tool = _load_tool()
    packages = tool.inventory(ROOT, PACKAGES, jobs=len(PACKAGES), timeout=_PROBE_SECONDS)
    for package in packages:
        assert package["error"] is None, f"could not read {package['dir']}: {package['error']}"
    return {package["dir"]: package["exports"] for package in packages}


@pytest.mark.timeout(_PROBE_SECONDS + 20)
@pytest.mark.parametrize(("package", "module", "name"), FACTORIES)
def test_the_inventory_reads_each_factory_as_a_handler_it_called(
    inventory_rows: dict[str, list[dict[str, str]]], package: str, module: str, name: str
) -> None:
    """The inventory reads the entry-point name as a handler and records that calling it with minimal arguments returned a marked handler (R3 check)."""
    [row] = [
        row
        for row in inventory_rows[package]
        if row["module"] == module and row["name"] == name
    ]
    assert row["kind"] == "handler", row
    assert row["reason"].endswith("calling it returns a handler"), row


@pytest.mark.timeout(_PROBE_SECONDS + 20)
@pytest.mark.parametrize(("package", "module", "name"), VALUES)
def test_the_inventory_reads_each_value_as_a_marked_handler(
    inventory_rows: dict[str, list[dict[str, str]]], package: str, module: str, name: str
) -> None:
    """The inventory reads the entry-point name as a handler value carrying the marker (ADR-DOE-API-002 R1)."""
    [row] = [
        row
        for row in inventory_rows[package]
        if row["module"] == module and row["name"] == name
    ]
    assert (row["kind"], row["form"]) == ("handler", "handler"), row


def _marked(value: object) -> bool:
    return getattr(value, "_doeff_is_handler_fn", False) is True


def test_run_apart_is_a_handler_that_runs_the_program_apart() -> None:
    from doeff_core_effects import run_apart, scheduled

    @do
    def computed():
        return 41 + 1

    assert _marked(run_apart)
    assert run(scheduled(run_apart(computed()))) == 42
