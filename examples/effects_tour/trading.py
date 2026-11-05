# trading.py — one trading strategy as a doeff program, run as a backtest over recorded bars,
# under a risk layer that stops it, with retries against a flaky exchange, as a parameter sweep
# in parallel tasks, as an exact replay of a recorded session, on a virtual clock (a day of
# one-minute bars in an instant), on the wall clock (the same bars, really waited for), and live
# on asyncio. The bars come from a fixed seed, so every line printed is the same on every run.
from __future__ import annotations

import asyncio
import heapq
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import count

from doeff import Await, EffectBase, Gather, Pass, Resume, Spawn, do, handler, run
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import (
    PRIORITY_IDLE,
    CompletePromise,
    CreatePromise,
    Wait,
    scheduled,
)


# ---- Effects: what a strategy needs from the outside world.
@dataclass(frozen=True)
class Bar:  # one price bar
    t: int
    close: float


@dataclass(frozen=True)
class Tick(EffectBase):  # "the next bar, or None when the market data ends"
    pass


@dataclass(frozen=True)
class Order(EffectBase):  # "buy or sell this many; tell me the fill price"
    side: str
    qty: int


@dataclass(frozen=True)
class Log(EffectBase):  # "write this line"
    line: str


@dataclass(frozen=True)
class Delay(EffectBase):  # "wait this many seconds"
    seconds: float


@dataclass(frozen=True)
class GetTime(EffectBase):  # "what time is it?"
    pass


# ---- The strategy: a moving-average crossover. It knows nothing about data feeds or exchanges.
@do
def sma_cross(fast: int, slow: int):
    closes: list[float] = []
    position = 0
    cash = 0.0
    while (bar := (yield Tick())) is not None:
        closes.append(bar.close)
        if len(closes) < slow:
            continue
        f = sum(closes[-fast:]) / fast
        s = sum(closes[-slow:]) / slow
        if f > s and position == 0:
            price = yield Order("buy", 1)
            position, cash = 1, cash - price
            yield Log(f"t={bar.t} buy  {price:.2f}")
        elif f < s and position == 1:
            price = yield Order("sell", 1)
            position, cash = 0, cash + price
            yield Log(f"t={bar.t} sell {price:.2f}")
    if position:
        cash += yield Order("sell", 1)
    return round(cash, 2)


# ---- Handlers: each gives one effect its meaning.
def random_walk(seed: int, n: int) -> list[Bar]:  # synthetic market data for the demo
    rng = random.Random(seed)
    price, bars = 100.0, []
    for t in range(n):
        price *= 1 + rng.gauss(0.0005, 0.01)
        bars.append(Bar(t, round(price, 2)))
    return bars


def recorded(bars: list[Bar]):  # Tick answered from recorded bars: the backtest's data feed
    it = iter(bars)

    @handler
    def tick(effect: Tick, k):
        return (yield Resume(k, next(it, None)))

    return tick


def paper(slippage: float):  # Order filled at the last close, plus slippage: the backtest's exchange
    last = [None]

    @handler
    def fill(effect: Tick | Order, k):
        if isinstance(effect, Tick):
            bar = yield effect  # the data feed outside answers
            last[0] = bar.close if bar else last[0]
            return (yield Resume(k, bar))
        price = last[0] * (1 + slippage if effect.side == "buy" else 1 - slippage)
        return (yield Resume(k, price))

    return fill


def risk(max_loss: float):  # stop the strategy after one trade loses more than max_loss
    entry = [None]

    @handler
    def guard(effect: Order, k):
        price = yield effect  # the exchange outside fills it
        if effect.side == "buy":
            entry[0] = price
        elif entry[0] is not None and price - entry[0] < -max_loss:
            return f"risk stop: lost {entry[0] - price:.2f} on one trade"  # drop k: the strategy ends
        return (yield Resume(k, price))

    return guard


class RateLimited(Exception):
    pass


def flaky(failures: int):  # an exchange that answers 429 to the first few orders
    left = [failures]

    @handler
    def exchange(effect: Order, k):
        if left[0] > 0:
            left[0] -= 1
            raise RateLimited("429")
        return (yield Pass(effect, k))

    return exchange


def retrying(times: int):  # ask the exchange again on 429
    @handler
    def retry(effect: Order, k):
        for attempt in range(times):
            try:
                price = yield effect
                break
            except RateLimited:
                if attempt == times - 1:
                    raise
                yield Log(f"retry {attempt + 1} after 429")
        return (yield Resume(k, price))

    return retry


@handler
def printed(effect: Log, k):  # log lines to the screen
    print("   ", effect.line)
    return (yield Resume(k, None))


@handler
def quiet(effect: Log, k):  # no log: many backtests per second
    return (yield Resume(k, None))


def recording(tape: list):  # keep every bar and fill of a session
    @handler
    def record(effect: Tick | Order, k):
        answer = yield effect
        tape.append(answer)
        return (yield Resume(k, answer))

    return record


def replaying(tape: list):  # answer bars and fills from the tape: no feed, no exchange
    tape = list(tape)

    @handler
    def replay(effect: Tick | Order, k):
        return (yield Resume(k, tape.pop(0)))

    return replay


# ---- Time: the strategy never looks at a clock. A clock layer waits for the next bar and tells
# the time; the strategy runs unchanged on either the virtual clock or the wall clock.
def bar_clock(seconds: float):  # a bar arrives `seconds` after the previous one
    @handler
    def wait_for_bar(effect: Tick, k):
        yield Delay(seconds)  # the clock outside decides what waiting means
        return (yield Resume(k, (yield effect)))

    return wait_for_bar


def virtual_clock(start: datetime):  # waits take no time: the clock jumps to the next wake-up
    now = start
    sleepers = []  # (wake-up time, order, promise), earliest first
    order = count()
    advancing = False

    @do
    def advance():  # idle priority: runs while every other task waits
        nonlocal now, advancing
        while sleepers:
            now, _, promise = heapq.heappop(sleepers)
            yield CompletePromise(promise, None)
        advancing = False

    @handler
    def clock(effect: Delay | GetTime, k):
        nonlocal advancing
        if isinstance(effect, GetTime):
            return (yield Resume(k, now))
        promise = yield CreatePromise()
        heapq.heappush(sleepers, (now + timedelta(seconds=effect.seconds), next(order), promise))
        if not advancing:
            advancing = True
            yield Spawn(advance(), priority=PRIORITY_IDLE, daemon=True)
        yield Wait(promise.future)
        return (yield Resume(k, None))

    return clock


@handler
def wall_clock(effect: Delay | GetTime, k):  # waits on asyncio, the time is the machine's
    if isinstance(effect, GetTime):
        return (yield Resume(k, datetime.now(timezone.utc)))
    yield Await(asyncio.sleep(effect.seconds))
    return (yield Resume(k, None))


@handler
def stamped(effect: Log, k):  # log lines with the time of the clock outside
    now = yield GetTime()
    print(f"    {now:%H:%M} {effect.line}")
    return (yield Resume(k, None))


@do
def finished_at(program):  # the program's answer and the time it ended
    pnl = yield program
    return pnl, (yield GetTime())


def timed_backtest(bars: list[Bar], seconds: float):  # the backtest with a bar every `seconds`
    strategy = bar_clock(seconds)(sma_cross(5, 20))
    return recorded(bars)(paper(0.0005)(finished_at(strategy)))


@do
def sweep(bars: list[Bar], grid: list[tuple[int, int]]):  # every parameter pair, in parallel tasks
    tasks = []
    for fast, slow in grid:
        tasks.append((yield Spawn(quiet(recorded(bars)(paper(0.0005)(sma_cross(fast, slow)))))))
    results = yield Gather(*tasks)
    return sorted(zip(results, grid), reverse=True)


class FakeExchange:  # stand-in for an async exchange client: a bar every 10 ms
    def __init__(self, bars: list[Bar]):
        self.bars = iter(bars)

    async def next_bar(self):
        await asyncio.sleep(0.01)
        return next(self.bars, None)

    async def place(self, side: str, qty: int, last: float):
        await asyncio.sleep(0.01)
        return last


def live(exchange: FakeExchange):  # Tick and Order over the async client, on the wall clock
    last = [None]

    @handler
    def trade(effect: Tick | Order, k):
        if isinstance(effect, Tick):
            bar = yield Await(exchange.next_bar())
            last[0] = bar.close if bar else last[0]
            return (yield Resume(k, bar))
        price = yield Await(exchange.place(effect.side, effect.qty, last[0]))
        return (yield Resume(k, price))

    return trade


BARS = random_walk(seed=1, n=300)  # 300 one-minute bars
OPEN = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)  # the market opens at 09:00

if __name__ == "__main__":
    print("1. backtest: recorded bars, paper fills with slippage")
    print("  -> pnl", run(printed(recorded(BARS)(paper(0.0005)(sma_cross(5, 20))))))

    print("2. the same strategy under a risk layer: stop after one trade loses more than 2.0")
    print("  ->", run(quiet(recorded(BARS)(paper(0.0005)(risk(2.0)(sma_cross(5, 20)))))))

    print("3. the exchange answers 429 twice; the strategy retries")
    print("  -> pnl", run(printed(recorded(BARS)(paper(0.0005)(flaky(2)(retrying(3)(sma_cross(5, 20))))))))

    print("4. parameter sweep: 6 backtests in parallel tasks")
    for pnl, (fast, slow) in run(scheduled(sweep(BARS, [(3, 10), (3, 20), (5, 10), (5, 20), (8, 20), (8, 40)]))):
        print(f"   fast={fast:>2} slow={slow:>2} pnl={pnl:>7.2f}")

    print("5. record a session, then replay it without the feed or the exchange")
    tape: list = []
    first = run(quiet(recorded(BARS)(paper(0.0005)(recording(tape)(sma_cross(5, 20))))))
    again = run(quiet(replaying(tape)(sma_cross(5, 20))))
    print("  ->", first, again, "same:", first == again, "tape:", len(tape), "answers")

    print("6. a virtual clock: 300 one-minute bars, 5 hours of market time, no waiting")
    pnl, ended = run(scheduled(virtual_clock(OPEN)(stamped(timed_backtest(BARS, 60)))))
    print(f"  -> pnl {pnl} at {ended:%H:%M}")

    print("7. the wall clock: the same strategy, 60 bars 10 ms apart, really waited for")
    started = time.monotonic()
    pnl, _ = run(scheduled(await_handler()(wall_clock(quiet(timed_backtest(BARS[:60], 0.01))))))
    print(f"  -> pnl {pnl} after at least 0.6 s: {time.monotonic() - started >= 0.6}")
    same, _ = run(scheduled(virtual_clock(OPEN)(quiet(timed_backtest(BARS[:60], 0.01)))))
    print(f"  -> the virtual clock gives pnl {same}, the same: {pnl == same}")

    print("8. live: the same strategy over an async exchange on the wall clock (60 bars)")
    print("  -> pnl", run(scheduled(await_handler()(printed(live(FakeExchange(BARS[:60]))(sma_cross(5, 20)))))))
