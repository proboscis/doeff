"""Polarity sample for doeff-no-sleep-in-tests -- function declarations, sleep(0), asyncio.sleep(...) without await,
and threading.Event().wait(seconds), a paraphrase of sleep that waits for a signal nobody sets.

Only lines with the "fires" marker comment are hit. The names do not start with test_, so pytest does not collect them.
"""

import asyncio
import threading
import time

import pytest

from doeff import Await, do


def test_plain_sleep():
    time.sleep(0.1)  # fires


async def test_plain_async_sleep():
    await asyncio.sleep(0.1)  # fires


def test_helper_in_plain_test():
    def worker():
        time.sleep(0.05)  # fires

    threading.Thread(target=worker).start()


@pytest.mark.slow
def test_other_mark_is_not_a_declaration():
    time.sleep(0.1)  # fires


def _module_helper():
    time.sleep(0.1)  # fires


def test_fresh_event_wait_spells_a_sleep():
    threading.Event().wait(0.1)  # fires
    threading.Event().wait(timeout=0.1)  # fires


def test_waiting_for_an_event_someone_sets_is_not_a_sleep():
    ready = threading.Event()
    threading.Thread(target=ready.set).start()
    ready.wait(timeout=5)


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
        time.sleep(0.1)  # fires


def test_program_awaits_a_sleep():
    @do
    def program():
        yield Await(asyncio.sleep(0.1))  # fires

    return program


def test_program_yields_to_the_loop_once():
    @do
    def program():
        yield Await(asyncio.sleep(0))
        return (yield Await(asyncio.sleep(0, result="value")))

    return program


@pytest.mark.realtime
def test_declared_realtime_program():
    @do
    def program():
        yield Await(asyncio.sleep(0.1))

    return program
