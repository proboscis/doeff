# clocks.py — two kinds of time in one card game, each kept by its own handler.
# A card effect waits for the end of a turn (TurnEnds); an animation waits for seconds (Delay). Both are
# plain loops, each a task of its own, and neither knows what a turn or a second is:
#   - the turn clock is spire.py's `turns()`: a card effect waits on the promise of the turn, and the
#     fight completes that promise when the turn ends;
#   - the seconds are doeff-time's sim_time_handler, a virtual clock: it moves on to the next wake-up
#     when every task waits.
# Both stand on doeff's scheduler, `scheduled`, which keeps every task that waits. So a poison that
# lasts three turns and a number that rises for 0.4 s run side by side, on one thread, with no lock or
# event for either. On the virtual clock the fight prints at once, the same in every run.
# Needs `pip install doeff doeff-time`.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from doeff import EffectBase, Resume, Spawn, do, handler, run
from doeff_core_effects import scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from spire import EndTurn, poison, turns  # the card effect and the turn clock of the article's fight


# ---- Effects of the game (the turns are spire.py's TurnEnds and EndTurn, the seconds doeff-time's Delay and GetTime).
@dataclass(frozen=True)
class Draw(EffectBase):  # a line on the screen, or a number at a height above it
    text: str
    height: int | None = None


@dataclass(frozen=True)
class Choose(EffectBase):  # "player: which card this turn?"
    pass


# ---- The game: the fight and an animation, each a plain loop (the card effect `poison` is spire.py's).
@do
def rising(text: str):  # an animation: the number rises for four frames, 0.1 s apart
    for height in range(4):
        yield Draw(text, height)
        yield Delay(0.1)


@do
def battle(enemy: int = 27):
    """Play a card a turn until the enemy is down. Returns the turn it ended on."""
    turn = 0
    while enemy > 0:
        turn += 1
        card = yield Choose()
        if card == "Strike":
            enemy -= 6
            yield Spawn(rising("-6"), daemon=True)
        elif card == "Poison":
            yield Spawn(poison(3, turns=3), daemon=True)
        yield Draw(f"turn {turn}: {card}, enemy {enemy}")
        dealt = yield EndTurn()
        if dealt:
            enemy -= dealt
            yield Spawn(rising(f"-{dealt}"), daemon=True)
    return turn


# ---- The outside world: a player who takes 0.25 s to pick a card, and a screen that prints.
def player(plays, seconds: float = 0.25):
    cards = iter(plays)

    @handler
    def thinks(effect: Choose, k):
        yield Delay(seconds)  # the animations go on while the player looks at the screen
        return (yield Resume(k, next(cards)))

    return thinks


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


@handler
def printed(effect: Draw, k):
    now = yield GetTime()
    line = effect.text if effect.height is None else f"{' ' * 24}{effect.text}{'^' * effect.height}"
    print(f"{(now - START).total_seconds():4.2f}s  {line}")
    return (yield Resume(k, None))


def play(plays, seconds: float = 0.25):
    """The fight under both clocks: the turn clock inside, the virtual clock of seconds outside."""
    fight = turns()(battle())
    return run(scheduled(sim_time_handler(start_time=START)(printed(player(plays, seconds)(fight)))))


if __name__ == "__main__":
    ended = play(["Poison", "Strike", "Strike", "Strike", "Strike"])
    print(f"the enemy is down on turn {ended}")
