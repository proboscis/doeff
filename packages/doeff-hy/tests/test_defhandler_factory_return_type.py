"""Check on the return type of a defhandler with arguments (a handler factory).

A defhandler with arguments builds a function that returns a handler when called. Previously the factory had no return type, so the public API inventory
(tools/public_api_inventory.py) and inspect.signature read the factory as a "function with no return type", and 53 names returning handlers were left unclassified.
The runtime expansion annotates the factory's return type as doeff's ProgramHandler. The factory in a class body and the expansion for type checking are not annotated (see the note in handle.hy).
"""

from __future__ import annotations

import ast
import importlib
import inspect

import hy
from doeff.program import ProgramHandler

PROBE = """
(require doeff-hy.handle [defhandler])
(import doeff [EffectBase])
(defhandler plain (EffectBase [] (reperform effect)))
(defhandler factory [x] (EffectBase [] (reperform effect)))
(defn outer [] (defhandler inner [y] (EffectBase [] (reperform effect))) inner)
(defclass Holder [] (defhandler method-factory [self z] (EffectBase [] (reperform effect))))
"""


def _eval(source: str) -> dict[str, object]:
    importlib.import_module("doeff_hy")  # prepare the Hy importer and macros (import for side effects)
    namespace: dict[str, object] = {"__name__": "probe"}
    hy.eval(hy.read_many(source), namespace)
    return namespace


def _expand(source: str) -> str:
    importlib.import_module("doeff_hy")
    return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))


def _return_type(value: object) -> object:
    assert callable(value), value
    return inspect.signature(value).return_annotation


def test_a_factory_on_the_module_level_returns_a_program_handler() -> None:
    namespace = _eval(PROBE)
    assert _return_type(namespace["factory"]) is ProgramHandler
    # The value the factory returns is, as before, an installer carrying the handler mark.
    assert getattr(_call(namespace["factory"], 1), "_doeff_is_handler_fn", None) is True


def test_a_factory_inside_a_function_returns_a_program_handler() -> None:
    namespace = _eval(PROBE)
    inner = _call(namespace["outer"])
    assert _return_type(inner) is ProgramHandler


def test_a_factory_in_a_class_body_is_not_annotated_and_adds_no_class_attribute() -> None:
    namespace = _eval(PROBE)
    holder = namespace["Holder"]
    assert isinstance(holder, type)
    assert _return_type(holder.__dict__["method_factory"]) is inspect.Signature.empty
    assert "_doeff_ProgramHandler" not in vars(holder)


def test_a_handler_without_parameters_stays_a_marked_handler_value() -> None:
    namespace = _eval(PROBE)
    assert getattr(namespace["plain"], "_doeff_is_handler_fn", None) is True


def test_the_static_expansion_does_not_annotate_the_factory() -> None:
    from doeff_hy._static_view import static_view

    runtime = _expand(PROBE)
    with static_view():
        static = _expand(PROBE)
    assert "def factory(x) -> _doeff_ProgramHandler:" in runtime, runtime
    assert "_doeff_ProgramHandler" not in static, static


def _call(function: object, *arguments: object) -> object:
    assert callable(function), function
    return function(*arguments)
