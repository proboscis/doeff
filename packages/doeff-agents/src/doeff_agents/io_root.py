"""composition root が持つ「I/O を果たす家」の型と、答えの絞り込み。

段 7 lane 7c(agora-redesign・決定 1.3): program を回した答えは、doeff の
`Program` が結果の型を運ばないので静的には `object` である。ここはその
`object` を要求の約束した型へ**実行時に絞る** 1 点で、型逃げ(Any・cast)を
使わずに不整合を loud に落とす。

この module は I/O を 1 つも持たない(driver 層の規律 = ADR-DOE-AGENTS-013)。
"""

from collections.abc import Callable, Generator
from typing import TypeAlias, TypeVar

import hy  # noqa: F401  # .hy import hook — ProcessOutcome は Hy の module に住む
from doeff import Program

from doeff_agents.io_effects import ProcessOutcome

_Result = TypeVar("_Result")

#: composition root が選んだ「I/O を果たす家」。program を受け、答えを返す。
IoRoot: TypeAlias = Callable[[Program], object]

#: `@do` の本体(generator)の型。要求を yield し、答えを受け、`_Result` を返す。
#: 家の答えは静的には `object` なので、使う側は `as_*` で絞る。
IoGenerator: TypeAlias = Generator[Program, object, _Result]


def _mismatch(value: object, expected: str) -> TypeError:
    return TypeError(f"I/O の答えが {expected} ではない: {type(value).__name__}({value!r})")


def as_bool(value: object) -> bool:
    """答えを真偽へ絞る。"""
    if not isinstance(value, bool):
        raise _mismatch(value, "bool")
    return value


def as_str(value: object) -> str:
    """答えを文字列へ絞る。"""
    if not isinstance(value, str):
        raise _mismatch(value, "str")
    return value


def as_optional_str(value: object) -> str | None:
    """答えを「文字列か不在」へ絞る。"""
    if value is not None and not isinstance(value, str):
        raise _mismatch(value, "str | None")
    return value


def as_int(value: object) -> int:
    """答えを整数へ絞る。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _mismatch(value, "int")
    return value


def as_float(value: object) -> float:
    """答えを実数へ絞る。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _mismatch(value, "float")
    return float(value)


def as_str_tuple(value: object) -> tuple[str, ...]:
    """答えを文字列の並びへ絞る。"""
    if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
        raise _mismatch(value, "tuple[str, ...]")
    return value


def as_process_outcome(value: object) -> ProcessOutcome:
    """答えを子 process の結果へ絞る。"""
    if not isinstance(value, ProcessOutcome):
        raise _mismatch(value, "ProcessOutcome")
    return value


__all__ = [
    "IoGenerator",
    "IoRoot",
    "as_bool",
    "as_float",
    "as_int",
    "as_optional_str",
    "as_process_outcome",
    "as_str",
    "as_str_tuple",
]
