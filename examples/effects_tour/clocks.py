# clocks.py — the article's fight, unchanged, in real time: two kinds of time, each kept by its own handler.
# A card effect waits for the end of a turn (TurnEnds); a number that rises on screen waits for seconds
# (Delay). Both are plain loops, and neither knows what a turn or a second is:
#   - the turns are the game's own: spire.py's `turns()` keeps the k of each card effect that waits, and
#     resumes it when the fight ends the turn. The game needs no scheduler for that;
#   - the seconds are doeff-time's sim_time_handler, a virtual clock, and doeff's scheduler `scheduled`
#     runs each rising number as a task of its own, so it goes on while the player thinks.
# `battle` is spire.py's, and its stack only grows on the outside: the seconds come in with the screen.
# On the virtual clock the fight prints at once, the same in every run. Needs `pip install doeff doeff-time`.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from doeff import EffectBase, Pass, Resume, Spawn, do, handler, run
from doeff_core_effects import scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from spire import Choose, Played, Show, battle, dice, scripted, turns  # the article's fight and its turn clock


# ---- An animation: a damage number rises for four frames, 0.1 s apart.
@dataclass(frozen=True)
class Draw(EffectBase):  # "draw this number at this height above the line"
    text: str
    height: int


@do
def rising(text: str):
    for height in range(4):
        yield Draw(text, height)
        yield Delay(0.1)


# ---- The real-time screen: three handlers around the same `battle`.
def thinking(seconds: float):
    """The player takes `seconds` to pick a card; the numbers go on rising meanwhile."""

    @handler
    def thinks(effect: Choose, k):
        yield Delay(seconds)
        return (yield Pass(effect, k))  # the card itself comes from the player further out

    return thinks


@handler
def animated(effect: Played, k):
    """Each hit starts a rising number, a task of its own: the fight does not wait for it."""
    if effect.damage:
        yield Spawn(rising(f"-{effect.damage}"), daemon=True)
    return (yield Resume(k, None))


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


@handler
def screen(effect: Show | Draw, k):
    """Print each line and each frame of a number, at the second of the virtual clock."""
    now = (yield GetTime()) - START
    line = effect.text if isinstance(effect, Show) else f"{' ' * 30}{effect.text}{'^' * effect.height}"
    print(f"{now.total_seconds():4.2f}s  {line}")
    return (yield Resume(k, None))


def in_real_time(moves, seed: int = 7, seconds: float = 0.25):
    """The fight of `seed` in real time: the turn clock inside, the screen and its seconds outside."""
    fight = dice(seed)(scripted(moves)(thinking(seconds)(turns()(battle()))))
    return run(scheduled(sim_time_handler(start_time=START)(screen(animated(fight)))))


if __name__ == "__main__":
    print("->", in_real_time([1, 1, 0, 0, 0, 0, 0, None, 0, 0, 0]))
