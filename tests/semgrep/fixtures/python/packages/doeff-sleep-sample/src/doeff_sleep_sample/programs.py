"""doeff-no-sleep-in-programs の検体 — `# 鳴る` の行だけが当たる(tests/semgrep/test_no_sleep_in_programs_rule.py)。"""

import asyncio
import time
from time import sleep

import doeff
from doeff import Await, Pass, Resume, do
from doeff_time import Delay


@do
def waits_with_time_sleep():
    time.sleep(1.0)  # 鳴る
    return "done"


@do
def waits_with_awaited_asyncio_sleep():
    yield Await(asyncio.sleep(2.0))  # 鳴る
    return "done"


@do
def waits_with_imported_sleep():
    sleep(0.5)  # 鳴る
    return "done"


@doeff.do
def waits_under_qualified_decorator():
    yield Await(asyncio.sleep(1.0))  # 鳴る
    return "done"


@do
def waits_in_a_nested_helper():
    def settle() -> None:
        time.sleep(0.1)  # 鳴る

    settle()
    return "done"


@do
def waits_with_the_time_effect():
    yield Delay(1.0)
    return "done"


@do
def yields_to_the_loop_once():
    yield Await(asyncio.sleep(0))
    value = yield Await(asyncio.sleep(0, result="value"))
    time.sleep(0)
    return value


@do
def wall_clock_handler(effect, k):
    yield Await(asyncio.sleep(1.0))
    return (yield Resume(k, None))


class WallClockRuntime:
    @do
    def handle(self, effect, k):
        time.sleep(1.0)
        yield Pass(effect, k)


def plain_polling_loop() -> None:
    time.sleep(1.0)
