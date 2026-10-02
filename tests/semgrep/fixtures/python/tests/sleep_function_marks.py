"""doeff-no-sleep-in-tests の極性の検体 — 関数の宣言と sleep(0)(agora-redesign #2882)。

`# 鳴る` を付けた行だけが当たる。名が test_ で始まらないので pytest は集めない。
"""

import asyncio
import threading
import time

import pytest


def test_plain_sleep():
    time.sleep(0.1)  # 鳴る


async def test_plain_async_sleep():
    await asyncio.sleep(0.1)  # 鳴る


def test_helper_in_plain_test():
    def worker():
        time.sleep(0.05)  # 鳴る

    threading.Thread(target=worker).start()


@pytest.mark.slow
def test_other_mark_is_not_a_declaration():
    time.sleep(0.1)  # 鳴る


def _module_helper():
    time.sleep(0.1)  # 鳴る


def test_zero_sleep_yields_only():
    time.sleep(0)


async def test_zero_async_sleep_yields_only():
    await asyncio.sleep(0)


@pytest.mark.realtime
def test_declared_realtime():
    time.sleep(0.1)


@pytest.mark.realtime
async def test_declared_realtime_async():
    await asyncio.sleep(0.1)


@pytest.mark.realtime
def test_declared_realtime_nested_helper():
    def worker():
        time.sleep(0.05)

    threading.Thread(target=worker).start()


@pytest.mark.timeout(5)
@pytest.mark.realtime
def test_declared_among_other_marks():
    time.sleep(0.1)


class TestInClass:
    @pytest.mark.realtime
    def test_declared_method(self):
        time.sleep(0.1)

    def test_plain_method(self):
        time.sleep(0.1)  # 鳴る
