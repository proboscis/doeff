"""The orders and counts that the figures of docs/articles/time-and-clocks.md show, measured with doeff: what passes paper on
its way to recorded in paper-passes-tick-on, the Delays and Spawns the virtual clock sees in virtual-clock-jumps, and the effects
the strategy's stack yields under each clock in wall-clock-waits. The programs are the article's (examples/effects_tour/trading.py),
with a listening handler put between two of its layers.

Usage (from the repository root): uv run python tools/docs_site/figures_measure/time_and_clocks.py
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "examples" / "effects_tour"))

from doeff_core_effects import await_handler, scheduled
from trading import (
    BARS,
    OPEN,
    Delay,
    Log,
    Order,
    Tick,
    bar_clock,
    finished_at,
    paper,
    quiet,
    recorded,
    sma_cross,
    virtual_clock,
    wall_clock,
)

from doeff import Pass, Resume, Spawn, handler, run


@dataclass
class Heard:
    """What a listening handler heard, in order."""

    mut_events: tuple[str, ...] = ()

    def add(self, event: str) -> None:
        self.mut_events = (*self.mut_events, event)


def answers(where: str, heard: Heard):
    """A handler that writes down each Tick and Order it sees with its answer, and passes it on."""

    @handler
    def listen(effect: Tick | Order, k):
        answer = yield effect
        heard.add(f"{where}: {effect!r} -> {answer!r}")
        return (yield Resume(k, answer))

    return listen


def kinds(heard: Heard):
    """A handler that writes down the name of every Tick, Order, Log and Delay that passes it."""

    @handler
    def listen(effect: Tick | Order | Log | Delay, k):
        heard.add(type(effect).__name__)
        return (yield Pass(effect, k))

    return listen


def spawns(heard: Heard):
    """A handler outside the clock that writes down each Spawn on its way to the scheduler."""

    @handler
    def listen(effect: Spawn, k):
        heard.add(type(effect).__name__)
        return (yield Pass(effect, k))

    return listen


def traced_backtest(bars, seconds: float, heard: Heard):
    """timed_backtest from trading.py, with kinds() between finished_at and bar_clock."""
    strategy = kinds(heard)(bar_clock(seconds)(sma_cross(5, 20)))
    return recorded(bars)(paper(0.0005)(finished_at(strategy)))


if __name__ == "__main__":
    print(
        "-- paper-passes-tick-on: printed(recorded(BARS)(paper(0.0005)(sma_cross(5, 20)))), quiet in place of printed"
    )
    heard = Heard()
    run(
        quiet(
            recorded(BARS)(
                answers("outside paper", heard)(
                    paper(0.0005)(answers("inside paper", heard)(sma_cross(5, 20)))
                )
            )
        )
    )
    for event in heard.mut_events[:4]:
        print(" ", event)
    print(" ", next(event for event in heard.mut_events if "Order" in event))
    print(
        "  an Order outside paper:",
        any(e.startswith("outside") and "Order" in e for e in heard.mut_events),
    )
    print(f"  the close of bar 20: {BARS[20].close}, the fill: {BARS[20].close * 1.0005}")

    print(
        "-- virtual-clock-jumps: scheduled(virtual_clock(OPEN)(stamped(timed_backtest(BARS, 60)))), quiet in place of stamped"
    )
    seen, spawned = Heard(), Heard()
    started = time.monotonic()
    pnl, ended = run(
        scheduled(spawns(spawned)(virtual_clock(OPEN)(quiet(traced_backtest(BARS, 60, seen)))))
    )
    print(f"  pnl {pnl} at {ended:%H:%M}, after {time.monotonic() - started:.3f} s")
    print(
        f"  {seen.mut_events.count('Delay')} Delay, {seen.mut_events.count('Tick')} Tick; the clock spawned {len(spawned.mut_events)} task"
    )
    print("  the first effects:", seen.mut_events[:4])

    print("-- wall-clock-waits: the same 60 bars, 10 ms apart, on each clock")
    on_wall, on_virtual = Heard(), Heard()
    started = time.monotonic()
    wall, _ = run(
        scheduled(await_handler(wall_clock(quiet(traced_backtest(BARS[:60], 0.01, on_wall)))))
    )
    waited = time.monotonic() - started
    virtual, clock_end = run(
        scheduled(virtual_clock(OPEN)(quiet(traced_backtest(BARS[:60], 0.01, on_virtual))))
    )
    print(f"  wall clock: pnl {wall} after {waited:.3f} s")
    print(
        f"  virtual clock: pnl {virtual}, {(clock_end - OPEN).total_seconds():.2f} s on its clock"
    )
    counts = {name: on_wall.mut_events.count(name) for name in ("Delay", "Tick", "Order", "Log")}
    print(f"  the same effects in the same order: {on_wall.mut_events == on_virtual.mut_events}; {counts}")
