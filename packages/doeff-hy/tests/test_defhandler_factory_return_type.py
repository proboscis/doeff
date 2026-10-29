"""引数の在る defhandler(handler の工場)の戻りの型の検(card ki-01270742001e)。

引数の在る defhandler は呼ぶと handler を返す関数を作る。前は工場に戻りの型が無く、公開 API の目録
(tools/public_api_inventory.py)と inspect.signature が工場を「戻りの型が無い関数」と読み、handler を返す名 53 個が未分類になった。
実行時の展開は工場の戻りの型を doeff の ProgramHandler と注記する。class の本体の工場と型検査のための展開は注記しない(handle.hy の註)。
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
    importlib.import_module("doeff_hy")  # Hy の importer と macro を用意する(副作用のための import)
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
    # 工場が返す値は今までどおり handler の印を持つ installer。
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
