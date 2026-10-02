"""doeff-no-sleep-in-tests の極性の検体 — module の宣言 pytestmark = pytest.mark.realtime(agora-redesign #2882)。

宣言より後の sleep は鳴らない。宣言より前の 1 行は鳴る(規則がこの file に届いていることの印)。
名が test_ で始まらないので pytest は集めない。
"""

import asyncio
import time

import pytest


def _before_declaration():
    time.sleep(0.1)  # 鳴る


pytestmark = pytest.mark.realtime


async def _helper(duration):
    await asyncio.sleep(duration)


def test_module_declared():
    time.sleep(0.1)
