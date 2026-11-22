# clocks.py — two kinds of time in one card game, each kept by its own handler.
# A card effect waits for the end of a turn (TurnEnds); an animation waits for seconds (Delay). Both are
# plain loops, and neither knows what a turn or a second is:
#   - the turn clock is the game's own, 20 lines below: it holds the k of every card effect that waits,
#     and resumes them all when a turn ends;
#   - the seconds are doeff-time's sim_time_handler, a virtual clock: an animation is a task, and the
#     clock moves on to the next wake-up when every task waits.
# The two are stacked around one program, so a poison that lasts five turns and a number that rises for
# 0.4 s run side by side, on one thread, with no lock or event for either. On the virtual clock the
# fight prints at once, the same in every run. Needs `pip install doeff doeff-time`.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from doeff import EffectBase, Resume, Spawn, do, handler, run
from doeff_core_effects import scheduled
from doeff_time import Delay, GetTime, sim_time_handler


# ---- Effects of the game (the seconds are doeff-time's Delay and GetTime).
@dataclass(frozen=True)
class TurnEnds(EffectBase):  # "wait for the end of the turn"; the fight gets what the lasting effects dealt
    pass


@dataclass(frozen=True)
class Lasting(EffectBase):  # "run this card effect over the next turns"
    program: object


@dataclass(frozen=True)
class Hurt(EffectBase):  # a lasting effect deals damage to the enemy
    n: int


@dataclass(frozen=True)
class Draw(EffectBase):  # one line on the screen
    text: str


@dataclass(frozen=True)
class Choose(EffectBase):  # "player: which card this turn?"
    pass


# ---- The game: the fight, a card effect and an animation, each a plain loop.
@do
def poison(n: int, turns: int):  # a card effect: n damage at the end of each of the next turns
    for _ in range(turns):
        yield TurnEnds()
        yield Hurt(n)


@do
def rising(text: str):  # an animation: the number rises for four frames, 0.1 s apart
    for height in range(4):
        yield Draw(f"{' ' * 24}{text}{'^' * height}")
        yield Delay(0.1)


@do
def battle(enemy: int = 30):
    """Play a card a turn until the enemy is down. Returns the turn it ended on."""
    turn = 0
    while enemy > 0:
        turn += 1
        card = yield Choose()
        if card == "Strike":
            enemy -= 6
            yield Spawn(rising("-6"), daemon=True)
        elif card == "Poison":
            yield Lasting(poison(3, turns=5))
        yield Draw(f"turn {turn}: {card}, enemy {enemy}")
        dealt = yield TurnEnds()
        if dealt:
            enemy -= dealt
            yield Spawn(rising(f"-{dealt}"), daemon=True)
    return turn


# ---- The turn clock: it holds the k of each card effect that waits, and resumes them when a turn ends.
def turns():
    waiting, dealt = (), 0

    @handler
    def lasting(effect: TurnEnds | Hurt, k):  # around each card effect
        nonlocal waiting, dealt
        if isinstance(effect, Hurt):
            dealt += effect.n
            return (yield Resume(k, None))
        waiting = (*waiting, k)  # it waits for the end of the turn: keep its k, and go back to the fight

    @handler
    @do(non_tail=True)  # it goes on after each card effect it resumes
    def turn_clock(effect: Lasting | TurnEnds, k):
        nonlocal waiting, dealt
        if isinstance(effect, Lasting):
            yield lasting(effect.program)  # the card effect runs up to its first wait
            return (yield Resume(k, None))
        ready, waiting, dealt = waiting, (), 0
        for card_effect in ready:  # the turn ends: each card effect runs up to its next wait
            yield Resume(card_effect, None)
        return (yield Resume(k, dealt))

    return turn_clock


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
    print(f"{(now - START).total_seconds():4.2f}s  {effect.text}")
    return (yield Resume(k, None))


def play(plays, seconds: float = 0.25):
    """The fight under both clocks: the turn clock inside, the virtual clock of seconds outside."""
    fight = turns()(battle())
    return run(scheduled(sim_time_handler(start_time=START)(printed(player(plays, seconds)(fight)))))


if __name__ == "__main__":
    ended = play(["Poison", "Strike", "Strike", "Strike", "Strike"])
    print(f"the enemy is down on turn {ended}")
