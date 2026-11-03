# spire.py — a small deck-building battle (in the spirit of Slay the Spire), as one doeff program.
# The same `battle` runs under: a scripted player, a random bot, a Monte Carlo bot that simulates
# the rest of the fight by running `battle` itself, a tape that replays a recorded fight exactly,
# a thousand parallel fights for a win rate, and a trace of every card played.
from __future__ import annotations

import random
from dataclasses import dataclass, replace

from doeff import EffectBase, Gather, Pass, Resume, Spawn, do, handler, run
from doeff_core_effects.scheduler import scheduled


# ---- Effects: everything the fight needs from the outside world.
@dataclass(frozen=True)
class Roll(EffectBase):  # "give me a random number below n"
    n: int


@dataclass(frozen=True)
class Choose(EffectBase):  # "player: which card, or None to end the turn?"
    state: Fight
    hand: tuple[str, ...]
    energy: int


@dataclass(frozen=True)
class Show(EffectBase):  # "draw this on the screen"
    text: str


@dataclass(frozen=True)
class Played(EffectBase):  # "a card was played" (for statistics)
    card: str
    damage: int


# ---- The game: plain data and one @do program.
CARDS = {"Strike": (1, 6, 0), "Defend": (1, 0, 5), "Bash": (2, 8, 0)}  # cost, damage, block
DECK = ("Strike",) * 5 + ("Defend",) * 4 + ("Bash",)


@dataclass(frozen=True)
class Fight:
    hp: int = 40
    block: int = 0
    enemy_hp: int = 44
    vulnerable: int = 0  # turns the enemy takes +50% damage
    enemy_strength: int = 0
    draw: tuple[str, ...] = DECK
    discard: tuple[str, ...] = ()
    turn: int = 1


@do
def draw_hand(s: Fight, n: int = 5):
    hand = []
    for _ in range(n):
        if not s.draw:
            s = replace(s, draw=s.discard, discard=())
        if not s.draw:
            break
        i = yield Roll(len(s.draw))  # shuffle by drawing a random card
        hand.append(s.draw[i])
        s = replace(s, draw=s.draw[:i] + s.draw[i + 1 :])
    return s, tuple(hand)


def hit(s: Fight, card: str) -> tuple[Fight, int]:
    cost, dmg, blk = CARDS[card]
    dmg = dmg * 3 // 2 if s.vulnerable else dmg
    s = replace(s, enemy_hp=s.enemy_hp - dmg, block=s.block + blk)
    if card == "Bash":
        s = replace(s, vulnerable=2)
    return s, dmg


@do
def battle(s: Fight = Fight()):
    while s.hp > 0 and s.enemy_hp > 0:
        s = replace(s, block=0)
        s, hand = yield draw_hand(s)
        energy = 3
        while energy > 0 and hand:
            yield Show(f"T{s.turn} you {s.hp} ({s.block} block) | enemy {s.enemy_hp} | {energy} energy | {' '.join(hand)}")
            pick = yield Choose(s, hand, energy)
            if pick is None or CARDS[hand[pick]][0] > energy:
                break
            card = hand[pick]
            hand = hand[:pick] + hand[pick + 1 :]
            energy -= CARDS[card][0]
            s, dmg = hit(s, card)
            s = replace(s, discard=s.discard + (card,))
            yield Played(card, dmg)
            if s.enemy_hp <= 0:
                return "win", s.turn
        s = replace(s, discard=s.discard + hand)
        intent = yield Roll(3)  # the enemy: 0 chomp 11, 1 thrash 7, 2 bellow (+3 strength)
        if intent == 2:
            s = replace(s, enemy_strength=s.enemy_strength + 3)
            yield Show("   enemy bellows")
        else:
            raw = (11 if intent == 0 else 7) + s.enemy_strength
            taken = max(0, raw - s.block)
            s = replace(s, hp=s.hp - taken)
            yield Show(f"   enemy hits for {raw}, you take {taken}")
        s = replace(s, vulnerable=max(0, s.vulnerable - 1), turn=s.turn + 1)
    return ("win" if s.enemy_hp <= 0 else "lose"), s.turn


# ---- Handlers: each gives one effect its meaning.
def dice(seed: int):  # Roll answered by a seeded generator
    rng = random.Random(seed)

    @handler
    def roll(effect: Roll, k):
        return (yield Resume(k, rng.randrange(effect.n)))

    return roll


@handler
def printed(effect: Show, k):
    print("   ", effect.text)
    return (yield Resume(k, None))


@handler
def silent(effect: Show, k):  # no screen: thousands of fights per second
    return (yield Resume(k, None))


@handler
def quiet_stats(effect: Played, k):  # nobody is counting
    return (yield Resume(k, None))


def scripted(moves: list):  # a player who follows a script (a test, or a replayed bug report)
    @handler
    def choose(effect: Choose, k):
        return (yield Resume(k, moves.pop(0) if moves else None))

    return choose


@handler
def random_bot(effect: Choose, k):  # plays a random affordable card
    options = [i for i, c in enumerate(effect.hand) if CARDS[c][0] <= effect.energy]
    i = yield Roll(len(options) + 1)  # +1: end the turn
    return (yield Resume(k, options[i] if i < len(options) else None))


def monte_carlo(playouts: int):  # tries each card by running the rest of the fight `playouts` times
    @handler
    def choose(effect: Choose, k):
        best, best_score = None, -1.0
        options = [i for i, c in enumerate(effect.hand) if CARDS[c][0] <= effect.energy]
        for i in options + [None]:
            wins = 0
            for n in range(playouts):
                s = effect.state if i is None else hit(effect.state, effect.hand[i])[0]
                if s.enemy_hp <= 0:
                    wins += 1
                    continue
                # finish this turn's hand with a random bot, then the rest of the fight
                result, _ = run(quiet_stats(silent(dice(n)(random_bot(battle(replace(s, turn=s.turn)))))))
                wins += result == "win"
            if wins / playouts > best_score:
                best, best_score = i, wins / playouts
        return (yield Resume(k, best))

    return choose


def recording(tape: list):  # keep every Roll and Choose answer
    @handler
    def record(effect: Roll | Choose, k):
        answer = yield effect
        tape.append(answer)
        return (yield Resume(k, answer))

    return record


def replaying(tape: list):  # answer Roll and Choose from the tape: no dice, no player
    tape = list(tape)

    @handler
    def replay(effect: Roll | Choose, k):
        return (yield Resume(k, tape.pop(0)))

    return replay


def damage_stats(totals: dict):  # how much damage each card did
    @handler
    def count(effect: Played, k):
        totals[effect.card] = totals.get(effect.card, 0) + effect.damage
        return (yield Resume(k, None))

    return count


@do
def win_rate(player, fights: int):  # the same battle, `fights` times, in parallel tasks
    tasks = []
    for seed in range(fights):
        tasks.append((yield Spawn(silent(dice(seed)(player(battle()))))))
    results = yield Gather(*tasks)
    return sum(r == "win" for r, _ in results) / fights


if __name__ == "__main__":
    print("1. a scripted player on seed 7, on screen")
    moves = [0, 0, 0, None] * 20
    print("  ->", run(quiet_stats(printed(dice(7)(scripted(moves)(battle()))))))

    print("2. record a fight, then replay it with no dice and no player")
    tape: list = []
    first = run(quiet_stats(silent(dice(3)(random_bot(recording(tape)(battle()))))))
    again = run(quiet_stats(silent(replaying(tape)(battle()))))
    print("  ->", first, again, "same:", first == again, "tape:", len(tape), "answers")

    print("3. 100 fights in parallel: random bot vs Monte Carlo bot")
    print("  -> random bot wins", run(scheduled(quiet_stats(win_rate(random_bot, 100)))))
    print("  -> monte carlo wins", run(scheduled(quiet_stats(win_rate(monte_carlo(20), 100)))))

    print("4. damage by card over 100 random fights")
    totals: dict = {}
    run(scheduled(damage_stats(totals)(win_rate(random_bot, 100))))
    print("  ->", totals)
