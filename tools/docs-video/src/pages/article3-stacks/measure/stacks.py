"""The runs behind the article3-stacks video: every number and order of events it shows, from the example of the
article "Time is an effect" (examples/effects_tour/trading.py and test_trading.py).

Usage (from the repository root): uv run python tools/docs-video/src/pages/article3-stacks/measure/stacks.py > tools/docs-video/src/pages/article3-stacks/measure/stacks.log
"""

import sys
from pathlib import Path

# The article's example (trading.py) is imported through this path.
sys.path.insert(0, str(Path(__file__).resolve().parents[6] / "examples" / "effects_tour"))

from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled
from trading import (
    BARS,
    OPEN,
    FakeExchange,
    GetTime,
    Log,
    Order,
    live,
    paper,
    quiet,
    recorded,
    recording,
    replaying,
    risk,
    sma_cross,
    sweep,
    timed_backtest,
    virtual_clock,
)

from doeff import Pass, Resume, handler, run

# s5-s10: the orders risk passes on, the fills the strategy gets back, and where it stops.
orders: list[str] = []
fills: list[float] = []


@handler
def watched(effect: Order, k):
    orders.append(effect.side)
    price = yield effect
    fills.append(round(price, 2))
    return (yield Resume(k, price))


stopped = run(quiet(recorded(BARS)(paper(0.0005)(watched(risk(2.0)(sma_cross(5, 20)))))))
print("s9 orders risk passed on:", orders)
print("s9 fills from the exchange:", fills)
print("s10 the run returns:", stopped)

# s11-s13: the sweep, sorted best first.
for pnl, (fast, slow) in run(
    scheduled(sweep(BARS, [(3, 10), (3, 20), (5, 10), (5, 20), (8, 20), (8, 40)]))
):
    print(f"s12 fast={fast} slow={slow} pnl={pnl:.2f}")

# s14-s16: record, then replay.
tape: list = []
first = run(quiet(recorded(BARS)(paper(0.0005)(recording(tape)(sma_cross(5, 20))))))
again = run(quiet(replaying(tape)(sma_cross(5, 20))))
print("s16", first, again, "same:", first == again, "tape:", len(tape), "answers")

# s17-s20: live over the fake exchange.
lines: list[str] = []


@handler
def kept(effect: Log, k):
    lines.append(effect.line)
    return (yield Pass(effect, k))


pnl = run(scheduled(await_handler(quiet(kept(live(FakeExchange(BARS[:60]))(sma_cross(5, 20)))))))
print("s20 live trades:", lines, "pnl", pnl)


# s21-s23: the test handler of test_trading.py.
@handler
def first_trade(effect: Log, k):  # drop k: the run ends at the first log line
    return (yield GetTime()) - OPEN, effect.line


print("s23", run(scheduled(virtual_clock(OPEN)(first_trade(timed_backtest(BARS, 60))))))
