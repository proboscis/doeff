# test_trading.py
from datetime import timedelta

from doeff import handler, run
from doeff_core_effects import await_handler, scheduled

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
