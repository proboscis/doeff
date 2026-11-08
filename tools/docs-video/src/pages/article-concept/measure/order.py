"""The small function of the video article-concept, run under the handlers the video draws.

order() asks for a price and a confirmation as effects and never says who answers. The handlers answer Price from a
table, and answer Confirm by resuming k (the order goes on) or by not resuming it (the order stops at the yield).
Every code line of the video's ORDER_CODE is a line of this file (check_code.py); every result is a line printed here.

Usage (from the repository root): uv run python tools/docs-video/src/pages/article-concept/measure/order.py > tools/docs-video/src/pages/article-concept/measure/order.log
"""
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Price(EffectBase):  # "what does this item cost?"
    item: str


@dataclass(frozen=True)
class Confirm(EffectBase):  # "may I pay this much?"
    price: int


@do
def order(item: str):
    price = yield Price(item)
    ok = yield Confirm(price)
    return ("bought" if ok else "skipped"), price


@handler
def prices(effect: Price, k):  # answer from a table and resume k
    return (yield Resume(k, {"tea": 3, "cake": 5}[effect.item]))


@handler
def yes(effect: Confirm, k):  # resume k with True: the rest of order runs
    return (yield Resume(k, True))


@handler
def stop(effect: Confirm, k):  # never resume k: order stops at its Confirm
    return "stopped"


if __name__ == "__main__":
    print("resume k:", run(prices(yes(order("tea")))))
    print("drop k:  ", run(prices(stop(order("tea")))))
