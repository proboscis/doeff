# retry.py
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class FetchPrice(EffectBase):
    sku: str


class Flaky(Exception):
    pass


@do
def order_total(sku: str, qty: int):
    price = yield FetchPrice(sku)
    return price * qty


def retrying(times: int):
    @do
    def retry(effect: FetchPrice, k):
        for attempt in range(1, times + 1):
            try:
                price = yield effect  # ask the handler further out again
                break
            except Flaky:
                if attempt == times:
                    raise
        return (yield Resume(k, price))

    return handler(retry)


def flaky_api(failures: int):  # stand-in for an API that returns 503 a few times
    left = [failures]

    def api(effect: FetchPrice, k):
        if left[0] > 0:
            left[0] -= 1
            raise Flaky("503")
        return Resume(k, 9.5)

    return handler(api)


if __name__ == "__main__":
    print(run(flaky_api(failures=2)(retrying(3)(order_total("tea", 4)))))
