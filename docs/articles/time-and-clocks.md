# Time is an effect: one trading strategy on a virtual clock and on the wall clock

*A strategy that never calls `time.sleep` or `datetime.now` runs a five-hour session in well under a second, and the same strategy, unchanged, waits for every bar on the wall clock. The clock is a handler around the program, and the virtual one holds each waiting `k` until its time comes.*

This is the third post on algebraic effects in Python with doeff. [The first one](algebraic-effects-in-python.md) stacked handlers around one card fight, and [the second](yield-and-async.md) compared `@do` and `yield` with `async def` and `await`. Both left time for later: this post is about it.

Code that calls `time.sleep(60)` waits a minute in a test too, and code that calls `datetime.now()` gives a different answer on every run. With effects, waiting and asking the time are two more operations a program yields, and a clock is a handler that gives them their meaning. The program here is a small trading strategy. Its first layers are a backtest, a risk stop, a sweep and a replay, in the style of the first post; then a clock goes around it, first a virtual one and then the wall clock, and in the last stack the strategy trades over an async exchange client.

## The strategy and its effects

`sma_cross` is a moving-average crossover: it buys when the average of the last `fast` closes rises above the average of the last `slow` closes, and sells when it falls below. It yields three kinds of effect: `Tick` for the next bar, `Order` to buy or sell, and `Log` to write a line. It does not yield `Delay` or `GetTime`: those two are for the clock layers further down. Nothing in it says where the bars come from, who fills the orders, or how long anything takes. In every stack below, `sma_cross` is the same function.

<details markdown="1">
<summary>trading.py (excerpt): the effects and <code>sma_cross</code></summary>

~~~python
# trading.py (excerpt)
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
~~~

</details>

## One program, layer by layer

### Layer 1: a backtest over recorded bars

`recorded(BARS)` answers `Tick` from a list of 300 one-minute bars, made by a random walk with a fixed seed, so every run prints the same lines. `paper(0.0005)` fills each `Order` at the last close plus 0.05% slippage. It knows the last close because it also takes `Tick`: it passes the `Tick` on to the feed outside with `yield effect`, looks at the bar that comes back, and resumes `k` with it.

<details markdown="1">
<summary>The handlers of Layer 1, the run and what it prints</summary>

~~~python
# trading.py (excerpt)
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

@handler
def printed(effect: Log, k):  # log lines to the screen
    print("   ", effect.line)
    return (yield Resume(k, None))

BARS = random_walk(seed=1, n=300)  # 300 one-minute bars
~~~

~~~python
print("  -> pnl", run(printed(recorded(BARS)(paper(0.0005)(sma_cross(5, 20))))))
~~~

~~~text
    t=20 buy  102.49
    t=57 sell 109.35
    t=60 buy  109.50
    t=66 sell 107.00
    t=76 buy  108.89
    t=80 sell 106.45
    t=118 buy  100.77
    t=187 sell 118.23
    t=197 buy  121.40
    t=217 sell 122.38
    t=223 buy  126.35
    t=228 sell 124.01
    t=230 buy  126.27
    t=231 sell 124.94
    t=232 buy  127.63
  -> pnl 50.79
~~~

</details>

### Layer 2: a risk stop that drops `k`

`risk(2.0)` passes each `Order` on to the exchange and looks at the fill. When one trade has lost more than 2.0, it returns a sentence without resuming `k`, and the strategy stops at that `yield`: no flag in the strategy, no exception through it. `quiet` takes the place of `printed`.

<details markdown="1">
<summary><code>risk</code>, the run and what it prints</summary>

~~~python
# trading.py (excerpt)
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

@handler
def quiet(effect: Log, k):  # no log: many backtests per second
    return (yield Resume(k, None))
~~~

~~~python
print("  ->", run(quiet(recorded(BARS)(paper(0.0005)(risk(2.0)(sma_cross(5, 20)))))))
~~~

~~~text
  -> risk stop: lost 2.51 on one trade
~~~

</details>

### Layer 3: a parameter sweep in parallel tasks

`sweep` yields one `Spawn` per pair of averages and then `Gather`, and each task carries its own feed and exchange. `scheduled` is the handler of `Spawn` and `Gather`; the clock layers below lean on it too.

<details markdown="1">
<summary><code>sweep</code>, the run and what it prints</summary>

~~~python
# trading.py (excerpt)
@do
def sweep(bars: list[Bar], grid: list[tuple[int, int]]):  # every parameter pair, in parallel tasks
    tasks = []
    for fast, slow in grid:
        tasks.append((yield Spawn(quiet(recorded(bars)(paper(0.0005)(sma_cross(fast, slow)))))))
    results = yield Gather(*tasks)
    return sorted(zip(results, grid), reverse=True)
~~~

~~~python
for pnl, (fast, slow) in run(scheduled(sweep(BARS, [(3, 10), (3, 20), (5, 10), (5, 20), (8, 20), (8, 40)]))):
    print(f"   fast={fast:>2} slow={slow:>2} pnl={pnl:>7.2f}")
~~~

~~~text
   fast= 8 slow=40 pnl=  59.72
   fast= 8 slow=20 pnl=  55.94
   fast= 3 slow=20 pnl=  51.83
   fast= 5 slow=20 pnl=  50.79
   fast= 3 slow=10 pnl=  43.98
   fast= 5 slow=10 pnl=  42.89
~~~

</details>

### Layer 4: record and replay

`recording(tape)` passes each `Tick` and `Order` on and keeps the answer; `replaying(tape)` answers from the tape, with no feed and no exchange. The 317 answers are the whole session: 301 bars, counting the `None` that ends the data, and 16 fills.

<details markdown="1">
<summary><code>recording</code> and <code>replaying</code>, the runs and what they print</summary>

~~~python
# trading.py (excerpt)
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
~~~

~~~python
tape: list = []
first = run(quiet(recorded(BARS)(paper(0.0005)(recording(tape)(sma_cross(5, 20))))))
again = run(quiet(replaying(tape)(sma_cross(5, 20))))
print("  ->", first, again, "same:", first == again, "tape:", len(tape), "answers")
~~~

~~~text
  -> 50.79 50.79 same: True tape: 317 answers
~~~

</details>

## A clock is a handler

So far a bar comes the moment the strategy asks for one. A market sends one a minute, and a backtest that should behave like the market has to make the strategy wait. The strategy does not change for that. A layer goes between it and the feed: `bar_clock(60)` takes each `Tick`, yields `Delay(60)` of its own, then passes the `Tick` on to the feed and resumes `k` with the bar. What waiting a minute means is up to the clock outside, and there are two of them: a virtual clock and the wall clock.

### Layer 5: a virtual clock

`virtual_clock(OPEN)` answers `GetTime` with a time it keeps, starting at 09:00, and it does not resume `k` at once on `Delay`. It creates a promise, puts it on a heap of sleepers ordered by the time to wake them, and yields `Wait` on it, so the task that asked waits in the scheduler with its `k`. If no `advance` task is running, it also spawns one at idle priority, so the scheduler runs it only when every other task is waiting. At that point nothing can happen until some sleeper wakes, so `advance` takes the earliest one off the heap, sets the time to its wake-up time and completes its promise. The time jumps from one wake-up to the next, and five hours of market go by in well under a second.

Two more layers ask the clock the time. `stamped` writes each log line with the time, by yielding `GetTime` of its own while it handles `Log`, and `finished_at` returns the strategy's result together with the time it ended. Neither knows which clock is outside.

<details markdown="1">
<summary>The clock layers, the run and what it prints</summary>

~~~python
# trading.py (excerpt)
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
            yield Spawn(advance(), priority=TaskPriority.IDLE, daemon=True)
        yield Wait(promise.future)
        return (yield Resume(k, None))

    return clock

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

OPEN = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)  # the market opens at 09:00
~~~

~~~python
pnl, ended = run(scheduled(virtual_clock(OPEN)(stamped(timed_backtest(BARS, 60)))))
print(f"  -> pnl {pnl} at {ended:%H:%M}")
~~~

~~~text
    09:21 t=20 buy  102.49
    09:58 t=57 sell 109.35
    10:01 t=60 buy  109.50
    10:07 t=66 sell 107.00
    10:17 t=76 buy  108.89
    10:21 t=80 sell 106.45
    10:59 t=118 buy  100.77
    12:08 t=187 sell 118.23
    12:18 t=197 buy  121.40
    12:38 t=217 sell 122.38
    12:44 t=223 buy  126.35
    12:49 t=228 sell 124.01
    12:51 t=230 buy  126.27
    12:52 t=231 sell 124.94
    12:53 t=232 buy  127.63
  -> pnl 50.79 at 14:01
~~~

</details>

The trades and the result are those of Layer 1, now with times. Bar 20 comes at 09:21, after its own minute of waiting, and the session ends at 14:01, not 14:00, because the `Tick` that finds no 301st bar waits its minute too. The virtual clock is not a mode of the library: it is the handler above, about 30 lines in the same `(effect, k)` form as `printed`, built on the scheduler's promises.

### Layer 6: the wall clock

`wall_clock` answers `Delay` by awaiting `asyncio.sleep`, and `GetTime` with the machine's time. `await_handler` runs those awaitables on an asyncio loop. Everything else in the stack is what the virtual clock ran: the same `timed_backtest`, the same `bar_clock`, the same strategy. Here it gets 60 bars 10 ms apart, so the run really waits for at least 0.6 seconds, and then the same bars go through the virtual clock for comparison.

<details markdown="1">
<summary><code>wall_clock</code>, the runs and what they print</summary>

~~~python
# trading.py (excerpt)
@handler
def wall_clock(effect: Delay | GetTime, k):  # waits on asyncio, the time is the machine's
    if isinstance(effect, GetTime):
        return (yield Resume(k, datetime.now(timezone.utc)))
    yield Await(asyncio.sleep(effect.seconds))
    return (yield Resume(k, None))
~~~

~~~python
started = time.monotonic()
pnl, _ = run(scheduled(await_handler(wall_clock(quiet(timed_backtest(BARS[:60], 0.01))))))
print(f"  -> pnl {pnl} after at least 0.6 s: {time.monotonic() - started >= 0.6}")
same, _ = run(scheduled(virtual_clock(OPEN)(quiet(timed_backtest(BARS[:60], 0.01)))))
print(f"  -> the virtual clock gives pnl {same}, the same: {pnl == same}")
~~~

~~~text
  -> pnl 6.85 after at least 0.6 s: True
  -> the virtual clock gives pnl 6.85, the same: True
~~~

</details>

The two clocks give the same answer because the strategy cannot tell them apart: it never asked either of them anything. What it yields, and in what order, is the same under both.

### Production: live over an async exchange

`live(exchange)` answers `Tick` and `Order` by awaiting an async exchange client, here `FakeExchange`, which stands in for one and sends a bar every 10 ms. In this stack the time comes from the exchange: `next_bar` waits until the next bar is there, so there is no `bar_clock`. The strategy is still the one Layer 1 backtested. Its result, 6.96 against 6.85 in Layer 6, differs by the slippage: the stand-in fills at the last close, while `paper` charged 0.05% on each fill.

<details markdown="1">
<summary><code>live</code>, the run and what it prints</summary>

~~~python
# trading.py (excerpt)
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
~~~

~~~python
print("  -> pnl", run(scheduled(await_handler(printed(live(FakeExchange(BARS[:60]))(sma_cross(5, 20)))))))
~~~

~~~text
    t=20 buy  102.44
    t=57 sell 109.40
  -> pnl 6.96
~~~

</details>

### The stacks side by side

| Run | Layers around `sma_cross`, from the inside out | What changes |
|---|---|---|
| backtest | `paper(0.0005)`, `recorded(BARS)`, `printed` | 300 recorded bars, fills with slippage, on the screen |
| risk stop | `risk(2.0)` added inside `paper`, `quiet` in place of `printed` | the strategy stops at the first trade that loses more than 2.0 |
| sweep | each backtest a task, under `scheduled` | six pairs of averages, side by side |
| recorded | `recording(tape)` added inside `paper` | every bar and every fill is kept |
| replayed | `replaying(tape)` in place of the feed and the exchange | the same session with no feed and no exchange |
| virtual clock | `bar_clock(60)` around the strategy and `finished_at` inside the backtest; `stamped` and `virtual_clock(OPEN)` outside it, under `scheduled` | a bar a minute; five hours of market time, no waiting |
| wall clock | `wall_clock` and `await_handler` in place of `virtual_clock(OPEN)` | the same bars, really waited for |
| production | `live(exchange)` in place of the feed, the exchange and the clock, under `await_handler` | trades over an async client |

## Why the clock is not an argument

A clock can also be an object passed in: give the strategy a `clock` with `sleep` and `now`, and hand it a different object in tests. That covers `GetTime`. Making five hours pass at once is harder, because a virtual clock may move the time forward only when nothing else can run, and that is a fact about the scheduler and every task in it, not about the code that called `sleep`. Here the virtual clock gets it from the scheduler itself: `advance` runs at idle priority, and `Wait` leaves each sleeping task's `k` with the scheduler until the clock completes its promise.

A handler also sees the program from outside. `bar_clock` puts the minute between bars around a strategy that has no parameter for it; `stamped` asks for the time while it handles another effect; `finished_at` reads the time after the program has returned. None of them is in the strategy's signature, and the clock outside is chosen once, by whoever runs the stack.

| The program yields | Handlers in this post |
|---|---|
| `Tick()` | `recorded(bars)` answers from a list, `live(exchange)` from the async client, `replaying(tape)` from the tape; `paper`, `bar_clock` and `recording(tape)` pass it on and look at the answer |
| `Order(side, qty)` | `paper(slippage)`, `live(exchange)`, `replaying(tape)`; `risk(max_loss)` and `recording(tape)` pass it on |
| `Log(line)` | `printed`, `quiet`, `stamped` |
| `Delay(seconds)` | `virtual_clock(start)` keeps `k` waiting until the clock jumps to its time, `wall_clock` awaits `asyncio.sleep` |
| `GetTime()` | `virtual_clock(start)` answers with the time it keeps, `wall_clock` with the machine's time |
| `Spawn`, `Gather`, `Wait`, promises | `scheduled` |

## Tests are one of the stacks

A test runs a stack with the virtual clock, so a test about minutes and hours takes milliseconds and gives the same answer on every run. The second test below installs its own handler for `Log`, which drops `k` at the first log line and returns the time it asked for: the run ends at the first trade, and the result is when it happened.

<details markdown="1">
<summary>test_trading.py: three tests on the clocks, nothing patched</summary>

~~~python
# test_trading.py
from datetime import timedelta

from doeff import handler, run
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled

from trading import BARS, OPEN, GetTime, Log, quiet, timed_backtest, virtual_clock, wall_clock


def test_five_hours_of_bars_end_five_hours_and_a_minute_after_the_open():
    # 300 bars a minute apart, and the Tick that finds no 301st bar waits its minute too
    pnl, ended = run(scheduled(virtual_clock(OPEN)(quiet(timed_backtest(BARS, 60)))))
    assert (pnl, ended) == (50.79, OPEN + timedelta(hours=5, minutes=1))


def test_the_first_trade_happens_at_the_minute_of_its_bar():
    @handler
    def first_trade(effect: Log, k):  # drop k: the run ends at the first log line
        return (yield GetTime()) - OPEN, effect.line

    first = run(scheduled(virtual_clock(OPEN)(first_trade(timed_backtest(BARS, 60)))))
    assert first == (timedelta(minutes=21), "t=20 buy  102.49")


def test_the_wall_clock_gives_what_the_virtual_clock_gives():
    bars = BARS[:60]
    on_the_wall, _ = run(scheduled(await_handler(wall_clock(quiet(timed_backtest(bars, 0.001))))))
    virtual, _ = run(scheduled(virtual_clock(OPEN)(quiet(timed_backtest(bars, 0.001)))))
    assert on_the_wall == virtual == 6.85
~~~

</details>

## Limits

- The virtual clock here is about 30 lines written to show how one works. The package doeff-time has a fuller one, `sim_time_handler`, with more time effects (`WaitUntil`, `ScheduleAt`, a monotonic time) and wall-clock handlers for asyncio and for blocking code.
- A clock moves only the time that is asked for through its effects: code that calls `time.sleep` or `datetime.now` directly still uses the machine's clock.
- The virtual clock needs `scheduled`: the waiting task's `k` is kept by the scheduler, and `advance` runs as an idle task.
- A wait on the wall clock goes through asyncio, so `wall_clock` and `live` need `await_handler` in the stack.
- The bars are a random walk with a fixed seed, and the strategy is an example of the mechanism, not a strategy to trade.

## Try it

~~~bash
python trading.py
pytest test_trading.py
~~~

Every example in this post runs as shown with doeff built from the main branch, plus pytest for the tests. The strategy and its stacks are [examples/effects_tour/trading.py](https://github.com/proboscis/doeff/blob/main/examples/effects_tour/trading.py), which also has a stack this post leaves out: retries against an exchange that answers 429. Where does this break down for you? Issues are open at https://github.com/proboscis/doeff/issues.
