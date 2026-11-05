"""Turn post-import pytest marks into savable records. Values are not re-evaluated (#1459)."""

import inspect
import math
from collections.abc import Callable
from dataclasses import dataclass

from _pytest.mark.structures import Mark as PytestMark
from _pytest.mark.structures import ParameterSet, get_unpacked_marks
from doeff_hy._pytest_items import (
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
    """The meaning of collection cannot be reproduced from records alone, so keep the conventional import collection."""


@dataclass(frozen=True, eq=False)
class OpaqueParam:
    """A placeholder used only for collection. At setup it is replaced with the real value at the saved position."""

    position: int

    def __repr__(self) -> str:
        return f"<replaced with the real value after import #{self.position}>"


def parameter_value(value: object, explicit_id: str | None) -> ParamValue:
    """A literal becomes its real value; a counterpart object with an explicit id becomes a placeholder replaced at setup."""
    if value is None or isinstance(value, (str, int, bool)):
        return LiteralValue(value)
    if isinstance(value, float) and math.isfinite(value):
        return LiteralValue(value)
    if explicit_id is not None or isinstance(value, (list, tuple, dict, set, Keyword, OpaqueParam)):
        return OpaqueValue()
    raise UnrecordableError(f"value whose id cannot be reproduced: {type(value).__name__}")


def parametrize_record(mark: PytestMark) -> Parametrize:
    """Read the real values and explicit ids of a single argument. Unsupported shapes such as indirect fixtures and per-item marks are not saved."""
    if len(mark.args) != 2 or set(mark.kwargs) - {"ids"}:
        raise UnrecordableError("unsupported parametrize arguments")
    argname: object = mark.args[0]
    raw_values: object = mark.args[1]
    if not isinstance(argname, str) or "," in argname or not isinstance(raw_values, (list, tuple)):
        raise UnrecordableError("parametrize saves only one argument and real values in a list/tuple")
    raw_ids: object = mark.kwargs.get("ids")
    if raw_ids is not None and (not isinstance(raw_ids, (list, tuple)) or len(raw_ids) != len(raw_values)):
        raise UnrecordableError("parametrize ids are saved only as a list/tuple of the same length as the values")
    values: list[ParamValue] = []
    ids: list[str | None] = []
    for position, raw in enumerate(raw_values):
        explicit_id: object = None if raw_ids is None else raw_ids[position]
        value: object = raw
        if isinstance(raw, ParameterSet):
            if raw.marks or len(raw.values) != 1:
                raise UnrecordableError("per-item marks and multiple arguments of pytest.param are not saved")
            value = raw.values[0]
            if raw.id is not None:
                explicit_id = raw.id
        if explicit_id is not None and not isinstance(explicit_id, str):
            raise UnrecordableError("parametrize id is not a string")
        ids.append(explicit_id)
        values.append(parameter_value(value, explicit_id))
    return Parametrize(argname, tuple(values), tuple(ids) if any(i is not None for i in ids) else None)


def runtime_function_record(name: str, function: Callable[..., object]) -> FunctionItem:
    """Save the already-evaluated arguments and marks of a function deferred by Dynamic."""
    decorators: list[Decorator] = []
    for mark in get_unpacked_marks(function):
        if mark.name == "parametrize":
            decorators.append(parametrize_record(mark))
        elif mark.name == "skipif":
            decorators.append(SkipIf())
        elif not mark.args and not mark.kwargs:
            decorators.append(Mark(mark.name))
        else:
            raise UnrecordableError(f"marks with arguments are not saved: {mark.name}")
    return FunctionItem(name, tuple(inspect.signature(function).parameters), tuple(reversed(decorators)))
