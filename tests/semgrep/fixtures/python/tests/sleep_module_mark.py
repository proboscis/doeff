"""Polarity fixture for doeff-no-sleep-in-tests -- the module declaration pytestmark = pytest.mark.realtime.

A sleep after the declaration does not fire. The one line before the declaration fires (a sign that the rule reaches this file).
Since the name does not start with test_, pytest does not collect it.
"""

import asyncio
import time

import pytest


def _before_declaration():
    time.sleep(0.1)  # fires


pytestmark = pytest.mark.realtime


async def _helper(duration):
    await asyncio.sleep(duration)


def test_module_declared():
    time.sleep(0.1)
