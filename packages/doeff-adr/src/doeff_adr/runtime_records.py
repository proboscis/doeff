"""import後のpytestの印を保存可能な記録へ直す。値を再評価しない(#1459)。"""

import inspect
import math
from collections.abc import Callable
from dataclasses import dataclass

from _pytest.mark.structures import Mark as PytestMark
from _pytest.mark.structures import ParameterSet, get_unpacked_marks
from doeff_hy.pytest_items import (
    Decorator,
    FunctionItem,
    LiteralValue,
    Mark,
    OpaqueValue,
    Parametrize,
    ParamValue,
    SkipIf,
)
from hy.models import Keyword


class UnrecordableError(ValueError):
    """収集の意味を記録だけでは再現できないので、従来のimport収集を保つ。"""


@dataclass(frozen=True, eq=False)
class OpaqueParam:
    """収集だけに使う仮の値。setupでは保存した位置の実値に置き換える。"""

    position: int

    def __repr__(self) -> str:
        return f"<import の後に実物の値へ替わる #{self.position}>"


def parameter_value(value: object, explicit_id: str | None) -> ParamValue:
    """literalは実値、明示idのある相手役等はsetupで置き換える仮の値にする。"""
    if value is None or isinstance(value, (str, int, bool)):
        return LiteralValue(value)
    if isinstance(value, float) and math.isfinite(value):
        return LiteralValue(value)
    if explicit_id is not None or isinstance(value, (list, tuple, dict, set, Keyword, OpaqueParam)):
        return OpaqueValue()
    raise UnrecordableError(f"idを再現できない値: {type(value).__name__}")


def parametrize_record(mark: PytestMark) -> Parametrize:
    """1引数の実値と明示idを読む。間接fixture・個別の印など未対応の形は保存しない。"""
    if len(mark.args) != 2 or set(mark.kwargs) - {"ids"}:
        raise UnrecordableError("parametrizeの引数が未対応")
    argname: object = mark.args[0]
    raw_values: object = mark.args[1]
    if not isinstance(argname, str) or "," in argname or not isinstance(raw_values, (list, tuple)):
        raise UnrecordableError("parametrizeは1引数とlist/tupleの実値だけを保存する")
    raw_ids: object = mark.kwargs.get("ids")
    if raw_ids is not None and (not isinstance(raw_ids, (list, tuple)) or len(raw_ids) != len(raw_values)):
        raise UnrecordableError("parametrizeのidsは値と同じ長さのlist/tupleだけを保存する")
    values: list[ParamValue] = []
    ids: list[str | None] = []
    for position, raw in enumerate(raw_values):
        explicit_id: object = None if raw_ids is None else raw_ids[position]
        value: object = raw
        if isinstance(raw, ParameterSet):
            if raw.marks or len(raw.values) != 1:
                raise UnrecordableError("pytest.paramの個別の印・複数引数は保存しない")
            value = raw.values[0]
            if raw.id is not None:
                explicit_id = raw.id
        if explicit_id is not None and not isinstance(explicit_id, str):
            raise UnrecordableError("parametrizeのidが文字列でない")
        ids.append(explicit_id)
        values.append(parameter_value(value, explicit_id))
    return Parametrize(argname, tuple(values), tuple(ids) if any(i is not None for i in ids) else None)


def runtime_function_record(name: str, function: Callable[..., object]) -> FunctionItem:
    """Dynamicで保留された関数の、既に評価された引数・印を保存する。"""
    decorators: list[Decorator] = []
    for mark in get_unpacked_marks(function):
        if mark.name == "parametrize":
            decorators.append(parametrize_record(mark))
        elif mark.name == "skipif":
            decorators.append(SkipIf())
        elif not mark.args and not mark.kwargs:
            decorators.append(Mark(mark.name))
        else:
            raise UnrecordableError(f"引数つきの印は保存しない: {mark.name}")
    return FunctionItem(name, tuple(inspect.signature(function).parameters), tuple(reversed(decorators)))
