"""The contract `(: x FrozenJson)` and the typed binding `(<- x FrozenJson e)` pass at runtime.

The contract and binding map the written type with `doeff_hy._type_forms.runtime_type_form` to a form that can be passed to isinstance, but only literal generic types
(`(get ...)`, `(| ...)`) are mapped; a name (a type alias) is passed to isinstance as is. If the runtime name FrozenJson of doeff_hy._frozen is
a union that contains generic types such as `FrozenMap[object] | tuple[object, ...] | ...`, every value gave
`TypeError: isinstance() argument 2 cannot be a parameterized generic` (2026-10-11: the agora reception returned 500 --
users avoided it by expanding to the literal form). The runtime name is made a union of outer types (only the outer type is checked at runtime -- the decision of #1790).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

SOURCE = """\
(require doeff-hy.macros [defk <-])
(import doeff_hy._frozen [FrozenJson FrozenMap])

(defk accepted [value]
  {:pre [(: value FrozenJson)] :post [(: % bool)]}
  True)

(defk field-of [event key]
  {:pre [(: event (get FrozenMap FrozenJson)) (: key str)] :post [(: % FrozenJson)]}
  (.get event key))

(defk bound-field [event key]
  {:pre [(: event (get FrozenMap FrozenJson)) (: key str)] :post [(: % FrozenJson)]}
  (<- got FrozenJson (field-of event key))
  got)
"""


@pytest.fixture(scope="module")
def mod(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    import doeff_hy  # noqa: F401 - register the Hy import hook

    root: Path = tmp_path_factory.mktemp("doeff_hy_frozen_json_contract")
    (root / "frozen_json_contract_probe.hy").write_text(SOURCE, encoding="utf-8")
    sys.path.insert(0, str(root))
    dont_write_before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        return importlib.import_module("frozen_json_contract_probe")
    finally:
        sys.path.remove(str(root))
        sys.dont_write_bytecode = dont_write_before


def _run(program: object) -> object:
    """Run the Program of a loaded Hy module (a module attribute is object in the types, so narrow it to Program)."""
    from doeff import Program, run

    match program:
        case Program():
            return run(program)
        case _:
            raise TypeError(f"tried to run a value that is not a Program: {program!r}")


def test_a_contract_naming_frozen_json_accepts_every_json_value(mod: ModuleType) -> None:
    from doeff_hy._frozen import freeze_json_text

    for value in (freeze_json_text('{"a": [1, "x", null]}'), freeze_json_text("[1, 2]"), "x", 1, 1.5, True, None):
        assert _run(mod.accepted(value)) is True, value


def test_a_typed_bind_and_a_mapping_contract_of_frozen_json_pass(mod: ModuleType) -> None:
    from doeff_hy._frozen import freeze_json_text

    event = freeze_json_text('{"start_ms": 3, "items": [1]}')
    assert _run(mod.bound_field(event, "start_ms")) == 3
    assert _run(mod.bound_field(event, "items")) == (1,)
    assert _run(mod.bound_field(event, "absent")) is None


def test_a_contract_naming_frozen_json_still_refuses_values_outside_frozen_json(mod: ModuleType) -> None:
    # Mappings and sequences that are not frozen are rejected by the contract (an AssertionError that names them, not a TypeError).
    for value in ({"a": 1}, [1], object()):
        with pytest.raises(AssertionError, match="expected FrozenJson"):
            _run(mod.accepted(value))


def test_the_runtime_name_still_parameterizes_a_frozen_map_field() -> None:
    # The runtime name is a name for the field annotation `(get FrozenMap FrozenJson)` (#4257) -- a union of outer types can still be placed as a type argument.
    from doeff_hy._frozen import FrozenJson, FrozenMap

    assert FrozenMap[FrozenJson].__origin__ is FrozenMap
