"""Collection.flatten の self の型の検体: Collection の Collection だけに呼べ、答えは内側の値の型。"""

from typing import reveal_type

from doeff_traverse.collection import Collection


def nested(c: Collection[Collection[int]]) -> Collection[int]:
    reveal_type(c.flatten())
    return c.flatten()


def flat(c: Collection[int]) -> None:
    c.flatten()
