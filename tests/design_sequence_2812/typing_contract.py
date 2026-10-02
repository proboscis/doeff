"""提案の T/E が既存 Program 型で表せることを確認する静的仕様。"""
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, assert_type

from test_sequence_contract import reference_sequence

from doeff import EffectBase, Program, do


@dataclass(frozen=True)
class ReadNumber(EffectBase[int]):
    pass


@do
def collect_numbers(first: Program[int, ReadNumber], second: Program[int, ReadNumber]) -> Generator[
    ReadNumber, Any, tuple[int, ...]
]:
    result = yield from reference_sequence(first, second)
    assert_type(result, tuple[int, ...])
    return result


def preserves_effects(
    first: Program[int, ReadNumber], second: Program[int, ReadNumber]
) -> Program[tuple[int, ...], ReadNumber]:
    return reference_sequence(first, second)
