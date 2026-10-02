"""sequential() が Traverse 1 回に内側の handler を読むのは、件を 1 つ目に走らせる時の 1 度だけである事の検(agora-redesign #2958)。

Traverse の件は、Traverse を出した所と sequential() の間に入っている handler(内側の handler)の下で走る。sequential() は
それを継続から読み(GetHandlers)、件ごとに入れ直す。前は件が 1 つも走らない Traverse(件 0・失敗した件だけの Collection)
でも読み、読みは @do の関数 get_inner_handlers の呼び 1 段を経ていた(件 0 の Traverse 1 回の固定費 約 5.8 µs のうち
約 1.1 µs — #2927)。「最後の 1 つ = 受けた handler 自身を外す」の規則は doeff.handler_utils.inner_of の 1 か所にあり、
数えはその呼びで見る。
"""

from collections.abc import Iterable

import pytest
from doeff_traverse.collection import Collection, ItemResult
from doeff_traverse.effects import Traverse
from doeff_traverse.handlers import sequential
from doeff_vm import EffectBase

from doeff import Resume, do, handler_utils, run
from doeff import handler as program_handler


class Ask(EffectBase):
    """件の中から内側の handler に問う effect。"""


@do
def answer(effect: Ask, k):
    return (yield Resume(k, 7))


@do
def plus_ask(x: int):
    asked = yield Ask()
    return x + asked


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """inner_of の呼び(= 内側の handler の読み)ごとに、読んだ handler の数を積む。"""
    seen: list[int] = []
    inner_of = handler_utils.inner_of

    def counting(handlers: list[object]) -> list[object]:
        seen.append(len(handlers))
        return inner_of(handlers)

    monkeypatch.setattr(handler_utils, "inner_of", counting)
    return seen


def traverse_under_answer(items: Iterable[int]) -> Collection[int]:
    """sequential → answer(内側の handler)→ Traverse の順に入れて走らせる。"""

    @do
    def body():
        return (yield Traverse(plus_ask, items))

    return run(sequential()(program_handler(answer)(body())))


def test_inner_of_drops_the_catching_handler() -> None:
    assert handler_utils.inner_of(["inner", "middle", "catcher"]) == ["inner", "middle"]
    assert handler_utils.inner_of([]) == []


def test_a_traverse_reads_the_inner_handlers_once_for_all_its_items(reads: list[int]) -> None:
    col = traverse_under_answer([1, 2, 3])
    assert col.valid_values == [8, 9, 10], "件は内側の handler(answer)の下で走る"
    assert len(reads) == 1, "件が 3 つでも読むのは 1 度"


def test_a_traverse_with_no_item_reads_no_handlers(reads: list[int]) -> None:
    col = traverse_under_answer([])
    assert len(col) == 0
    assert reads == [], "走らせる件が無ければ内側の handler を読まない"


def test_a_traverse_of_failed_items_only_reads_no_handlers(reads: list[int]) -> None:
    failed: Collection[int] = Collection([ItemResult(index=0, value=ValueError("earlier"), failed=True)])
    col = traverse_under_answer(failed)
    assert len(col.failed_items) == 1, "失敗した件はそのまま運ぶ"
    assert reads == [], "失敗した件だけなら走らせる件が無いので読まない"
