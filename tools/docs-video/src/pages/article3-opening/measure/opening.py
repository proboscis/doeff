"""The runs behind the article3-opening video: every number and order of events it shows, from the example of the
article "Time is an effect" (examples/effects_tour/trading.py).

Usage (from the repository root): uv run python tools/docs-video/src/pages/article3-opening/measure/opening.py > tools/docs-video/src/pages/article3-opening/measure/opening.log
"""

import sys
import time
from pathlib import Path

# The article's example (trading.py) is imported through this path.
sys.path.insert(0, str(Path(__file__).resolve().parents[6] / "examples" / "effects_tour"))

from doeff_core_effects import await_handler, scheduled
from trading import (
    BARS,
    OPEN,
    Delay,
    GetTime,
    Log,
    Tick,
    bar_clock,
    paper,
    quiet,
    recorded,
    sma_cross,
    stamped,
    timed_backtest,
    virtual_clock,
    wall_clock,
)

from doeff import Pass, handler, run

# o8-o9: the closes the chart draws, and the two averages from the 20th bar on.
closes = [bar.close for bar in BARS[:30]]
print("o8 closes 0-29:", closes)
print("o9 fast(5) 19-29:", [round(sum(closes[t - 4 : t + 1]) / 5, 2) for t in range(19, 30)])
print("o9 slow(20) 19-29:", [round(sum(closes[t - 19 : t + 1]) / 20, 2) for t in range(19, 30)])

# o16-o18: the backtest, every log line it writes and its pnl.
lines: list[str] = []


@handler
def kept(effect: Log, k):
    lines.append(effect.line)
    return (yield Pass(effect, k))


pnl = run(quiet(kept(recorded(BARS)(paper(0.0005)(sma_cross(5, 20))))))
print("o18 trades:", len(lines))
for line in lines:
    print("o18   ", line)
print("o18 pnl:", pnl)

# o20-o21: what bar_clock yields for one Tick, in order.
seen: list[str] = []


@handler
def watched(effect: Tick | Delay, k):
    seen.append(
        type(effect).__name__ + (f"({effect.seconds:g})" if isinstance(effect, Delay) else "()")
    )
    return (yield Pass(effect, k))


run(
    scheduled(
        virtual_clock(OPEN)(quiet(recorded(BARS[:2])(watched(bar_clock(60)(sma_cross(5, 20))))))
    )
)
print("o20 bar_clock yields, in order:", seen[:4])

# o22-o26: the virtual clock, its stamped lines, the end and the wall time it took.
stamps: list[str] = []


@handler
def kept_stamps(effect: Log, k):
    now = yield GetTime()
    stamps.append(f"{now:%H:%M} {effect.line}")
    return (yield Pass(effect, k))


started = time.monotonic()
pnl, ended = run(scheduled(virtual_clock(OPEN)(quiet(kept_stamps(timed_backtest(BARS, 60))))))
took = time.monotonic() - started
print("o25 first stamped line:", stamps[0])
print("o25 stamped lines:", len(stamps), "last:", stamps[-1])
print(f"o26 pnl {pnl} at {ended:%H:%M}, wall time {took:.3f} s")
started = time.monotonic()
run(scheduled(virtual_clock(OPEN)(stamped(timed_backtest(BARS, 60)))))
print(f"o26 the article's stack (stamped prints) took {time.monotonic() - started:.3f} s")

# o27-o31: the wall clock over 60 bars 10 ms apart, and the virtual clock over the same bars.
started = time.monotonic()
wall, _ = run(scheduled(await_handler(wall_clock(quiet(timed_backtest(BARS[:60], 0.01))))))
took = time.monotonic() - started
print(f"o30 wall clock: pnl {wall}, wall time {took:.3f} s, over 0.6 s: {took >= 0.6}")
started = time.monotonic()
virtual, at = run(scheduled(virtual_clock(OPEN)(quiet(timed_backtest(BARS[:60], 0.01)))))
print(
    f"o31 virtual clock: pnl {virtual}, ends at {at:%H:%M:%S.%f}, wall time {time.monotonic() - started:.3f} s"
)
