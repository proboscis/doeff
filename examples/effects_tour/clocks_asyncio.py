# clocks_asyncio.py — a small fight with a Poison and rising numbers, written with asyncio, to compare with clocks.py.
# asyncio has one clock, the machine's: an animation's sleep really waits, and the turns are not a clock
# at all. So the turn is an object of its own, an asyncio.Condition that every card effect is handed and
# the fight notifies, with a count of turns so that an effect does not miss one; and the damage the card
# effects deal goes through a shared object too. The fight takes its real 1 s on every run.
from __future__ import annotations

import asyncio
import time


class Turns:  # the end of a turn, as asyncio sees it: a condition, a count and the damage dealt
    def __init__(self):
        self.ended = asyncio.Condition()
        self.count = 0
        self.dealt = 0
        self.effects = ()

    async def wait(self):
        async with self.ended:
            seen = self.count
            await self.ended.wait_for(lambda: self.count > seen)

    async def end(self):
        self.dealt = 0  # noqa: DOEFF002 - the turn of the asyncio version is one shared object
        async with self.ended:
            self.count += 1  # noqa: DOEFF002 - the same
            self.ended.notify_all()
        await asyncio.sleep(0)  # let every card effect take its turn before the fight reads the damage
        return self.dealt


async def poison(turns: Turns, n: int, times: int):
    for _ in range(times):
        await turns.wait()
        turns.dealt += n


async def rising(screen, text: str):
    for height in range(4):
        screen(f"{' ' * 24}{text}{'^' * height}")
        await asyncio.sleep(0.1)


async def battle(choose, screen, enemy: int = 27):
    turns, animations = Turns(), set()
    turn = 0
    while enemy > 0:
        turn += 1
        card = await choose()
        if card == "Strike":
            enemy -= 6
            animations.add(asyncio.create_task(rising(screen, "-6")))
        elif card == "Poison":
            turns.effects = (*turns.effects, asyncio.create_task(poison(turns, 3, 3)))
            await asyncio.sleep(0)  # let the poison start waiting before the turn ends
        screen(f"turn {turn}: {card}, enemy {enemy}")
        dealt = await turns.end()
        if dealt:
            enemy -= dealt
            animations.add(asyncio.create_task(rising(screen, f"-{dealt}")))
    for effect in turns.effects:
        effect.cancel()
    return turn


def play(plays, seconds: float = 0.25):
    cards = iter(plays)
    start = time.monotonic()

    async def choose():
        await asyncio.sleep(seconds)
        return next(cards)

    def screen(text):
        print(f"{time.monotonic() - start:4.2f}s  {text}")

    return asyncio.run(battle(choose, screen))


if __name__ == "__main__":
    ended = play(["Poison", "Strike", "Strike", "Strike", "Strike"])
    print(f"the enemy is down on turn {ended}")
