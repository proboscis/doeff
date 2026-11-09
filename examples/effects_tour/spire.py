# spire.py — a small deck-building battle (in the spirit of Slay the Spire), as one doeff program.
# `battle` says only what it needs from the outside world: Roll (a random number), Choose (the
# player's move), Show (the screen) and Played (a statistic). Each layer below gives those effects
# a meaning, and the same `battle` runs under every one of them:
#   1. a scripted player and a screen        4. a Monte Carlo bot: its handler runs `battle` itself
#   2. a random bot                          5. 100 fights, one task each, for a win rate
#   3. record and replay of a fight          6. a damage count per card
#   +  production: a person at a terminal, choosing with input()      python spire.py play
#   +  production: the same person in a window (tkinter)              python spire.py window
# Every seed is fixed, so the output is the same on every run. Needs only `pip install doeff`.
from __future__ import annotations

import random
import sys
from dataclasses import dataclass, replace

from doeff import EffectBase, Gather, Resume, Spawn, do, handler, run
from doeff_core_effects import scheduled


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
    """Draw n cards; the deck is shuffled by asking for a random position (Roll)."""
    hand = []
    for _ in range(n):
        if not s.draw:
            s = replace(s, draw=s.discard, discard=())
        if not s.draw:
            break
        i = yield Roll(len(s.draw))
        hand.append(s.draw[i])
        s = replace(s, draw=s.draw[:i] + s.draw[i + 1 :])
    return s, tuple(hand)


def hit(s: Fight, card: str) -> tuple[Fight, int]:
    """Play one card on the state: returns the new state and the damage dealt (a pure function)."""
    cost, dmg, blk = CARDS[card]
    dmg = dmg * 3 // 2 if s.vulnerable else dmg
    s = replace(s, enemy_hp=s.enemy_hp - dmg, block=s.block + blk)
    if card == "Bash":
        s = replace(s, vulnerable=2)
    return s, dmg


@do
def battle(s: Fight = Fight()):
    """Fight to the end. Returns ("win" | "lose", the turn it ended on)."""
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


def affordable(effect: Choose) -> list[int]:
    """Positions in the hand of the cards the player has the energy for."""
    return [i for i, c in enumerate(effect.hand) if CARDS[c][0] <= effect.energy]


# ---- Layer 1: a scripted player and a screen.
def dice(seed: int):
    """Answer Roll from a generator seeded with `seed`: the same seed, the same fight."""
    rng = random.Random(seed)

    @handler
    def roll(effect: Roll, k):
        return (yield Resume(k, rng.randrange(effect.n)))

    return roll


def scripted(moves: list):
    """A player who follows a script (a test, or a replayed bug report); None ends the turn."""
    moves = list(moves)

    @handler
    def choose(effect: Choose, k):
        return (yield Resume(k, moves.pop(0) if moves else None))

    return choose


@handler
def printed(effect: Show, k):
    """The screen: print each line."""
    print("   ", effect.text)
    return (yield Resume(k, None))


@handler
def silent(effect: Show, k):
    """No screen: thousands of fights per second."""
    return (yield Resume(k, None))


@handler
def quiet_stats(effect: Played, k):
    """Nobody is counting."""
    return (yield Resume(k, None))


# ---- Layer 2: a random bot.
@handler
def random_bot(effect: Choose, k):
    """Play a random affordable card, or end the turn (the extra number is "end the turn")."""
    options = affordable(effect)
    i = yield Roll(len(options) + 1)
    return (yield Resume(k, options[i] if i < len(options) else None))


# ---- Layer 3: record and replay.
def record(tape: list):
    """Keep every Roll and Choose answer on the tape, passing the effect on to the handlers outside."""

    @handler
    def recorder(effect: Roll | Choose, k):
        answer = yield effect
        tape.append(answer)
        return (yield Resume(k, answer))

    return recorder


def replay(tape: list):
    """Answer Roll and Choose from the tape: no dice, no player, the same fight."""
    tape = list(tape)

    @handler
    def replayer(effect: Roll | Choose, k):
        return (yield Resume(k, tape.pop(0)))

    return replayer


# ---- Layer 4: a Monte Carlo bot.
def monte_carlo(playouts: int):
    """Try each move by running a new `battle` from the state after it, `playouts` times.

    The handler runs `battle` itself, under dice and a random bot, and picks the move with the
    most wins. The paused fight's `k` is not run by the playouts: it is resumed once, with that
    move. A playout starts from the next turn's draw, which is a cheap approximation of "the
    rest of this turn".
    """

    @handler
    def choose(effect: Choose, k):
        best, best_score = None, -1.0
        for i in [*affordable(effect), None]:
            wins = 0
            for n in range(playouts):
                s = effect.state if i is None else hit(effect.state, effect.hand[i])[0]
                if s.enemy_hp <= 0:
                    wins += 1
                    continue
                result, _ = run(quiet_stats(silent(dice(n)(random_bot(battle(s))))))
                wins += result == "win"
            if wins / playouts > best_score:
                best, best_score = i, wins / playouts
        return (yield Resume(k, best))

    return choose


# ---- Layer 5: 100 fights, one task each.
@do
def win_rate(player, fights: int):
    """The same battle `fights` times, one task per seed; returns the share of wins."""
    tasks = []
    for seed in range(fights):
        tasks.append((yield Spawn(silent(dice(seed)(player(battle()))))))
    results = yield Gather(*tasks)
    return sum(r == "win" for r, _ in results) / fights


# ---- Layer 6: a damage count per card.
def damage_stats(totals: dict):
    """Add up the damage each card did into `totals`."""

    @handler
    def count(effect: Played, k):
        totals[effect.card] = totals.get(effect.card, 0) + effect.damage
        return (yield Resume(k, None))

    return count


# ---- Production: a person at a terminal.
@handler
def human(effect: Choose, k):
    """Ask at the terminal which card to play; an empty line (or a bad number) ends the turn."""
    options = affordable(effect)
    menu = "  ".join(f"[{i}] {effect.hand[i]}" for i in options)
    answer = input(f"   play which? {menu}  [enter = end turn] > ").strip()
    pick = int(answer) if answer.isdigit() and int(answer) in options else None
    return (yield Resume(k, pick))


def play() -> None:
    """A fight against the enemy, played by a person; the dice are seeded by the system."""
    seed = random.SystemRandom().randrange(10**6)
    print(f"seed {seed}")
    print("->", run(quiet_stats(printed(dice(seed)(human(battle()))))))


def at_terminal(seed: int) -> None:
    """The fight of `seed` at the terminal: printed answers Show, human answers Choose."""
    print("->", run(quiet_stats(printed(dice(seed)(human(battle()))))))


# ---- Production again: the same person in a window. Two handlers change; `battle` does not.
def window():
    """Open a window; return a handler for Show, a handler for Choose, and `end` for the result.

    tkinter comes with Python, and it is imported here so that the other layers need no display.
    `wait_variable` runs the window until a click sets it, so `clicked` waits the way `input()` waits.
    """
    import tkinter as tk

    colors = {"Strike": "#b5483f", "Defend": "#3f6fb5", "Bash": "#b5803f"}
    end_turn, closed = -1, -2
    full, lines = Fight(), []
    last = full
    root = tk.Tk()
    root.title("spire")
    board = tk.Canvas(root, width=800, height=260, bg="#1b1f27", highlightthickness=0)
    board.pack()
    answer = tk.IntVar()
    root.protocol("WM_DELETE_WINDOW", lambda: answer.set(closed))

    def write(x, y, text, anchor="w", size=12, color="white"):
        board.create_text(x, y, anchor=anchor, text=text, fill=color, font=("Helvetica", size))

    def bar(y, name, value, most, color, note):
        write(16, y, name)
        board.create_rectangle(80, y - 9, 380, y + 9, fill="#343a46", outline="")
        if value:
            board.create_rectangle(80, y - 9, 80 + 300 * value // most, y + 9, fill=color, outline="")
        write(392, y, f"{value}{note}")

    def button(i, x, width, color, title, *details):
        tag = f"button{i}"
        board.create_rectangle(x, 76, x + width, 186, fill=color, outline="#d8dee9", tags=tag)
        board.create_text(x + width / 2, 98, text=title, fill="white", font=("Helvetica", 14, "bold"), tags=tag)
        for row, detail in enumerate(details):
            board.create_text(x + width / 2, 130 + 22 * row, text=detail, fill="white", font=("Helvetica", 11), tags=tag)
        board.tag_bind(tag, "<Button-1>", lambda _: answer.set(i))

    def draw(s: Fight, hand=(), energy=0, options=()):
        board.delete("all")
        block = f"  ({s.block} block)" if s.block else ""
        bar(22, "you", max(s.hp, 0), full.hp, "#4caf6d", block)
        bar(52, "enemy", max(s.enemy_hp, 0), full.enemy_hp, "#d9534f", "  vulnerable" if s.vulnerable else "")
        write(784, 22, f"turn {s.turn}", "e")
        for i, card in enumerate(hand):
            cost, damage, gained = CARDS[card]
            effect = f"{damage} damage" if damage else f"{gained} block"
            button(i, 16 + 126 * i, 116, colors[card] if i in options else "#343a46", card, f"cost {cost}", effect)
        if hand:
            write(784, 52, f"{energy} energy", "e")
            button(end_turn, 664, 120, "#343a46", "End turn")
        for row, line in enumerate(lines[-3:]):
            board.create_text(16, 204 + 19 * row, anchor="w", text=line.strip(), fill="#d8dee9", font=("Courier", 10))

    @handler
    def drawn(effect: Show, k):
        """The screen: the same lines the terminal prints, at the bottom of the window."""
        lines.append(effect.text)
        return (yield Resume(k, None))

    @handler
    def clicked(effect: Choose, k):
        """Draw the fight and wait for a click; "End turn" answers None, like an empty line at the terminal."""
        nonlocal last
        last = effect.state
        draw(effect.state, effect.hand, effect.energy, affordable(effect))
        root.wait_variable(answer)
        if answer.get() == closed:
            raise SystemExit("the window was closed")
        return (yield Resume(k, None if answer.get() == end_turn else answer.get()))

    def end(result) -> None:
        """Draw the end of the fight and keep the window until it is closed."""
        outcome, turn = result
        lost = {"hp": 0} if outcome == "lose" else {"enemy_hp": 0, "vulnerable": 0}
        draw(replace(last, **lost))
        write(400, 130, f"You {outcome} on turn {turn}", "center", 24)
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.mainloop()

    return drawn, clicked, end


def in_window(seed: int) -> None:
    """The fight of `seed` in a window: drawn answers Show, clicked answers Choose."""
    drawn, clicked, end = window()
    result = run(quiet_stats(drawn(dice(seed)(clicked(battle())))))
    print("->", result, flush=True)
    end(result)


def tour() -> None:
    """Layers 1 to 6 in order. Fixed seeds: the output is the same on every run."""
    print("1. a scripted player on seed 7, on screen")
    moves = [0, 0, 0, None] * 20
    print("  ->", run(quiet_stats(printed(dice(7)(scripted(moves)(battle()))))))

    print("2. a random bot on seed 3")
    print("  ->", run(quiet_stats(silent(dice(3)(random_bot(battle()))))))

    print("3. record that fight, then replay it with no dice and no player")
    tape: list = []
    first = run(quiet_stats(silent(dice(3)(random_bot(record(tape)(battle()))))))
    again = run(quiet_stats(silent(replay(tape)(battle()))))
    print("  ->", first, again, "same:", first == again, "tape:", len(tape), "answers")

    print("4. a Monte Carlo bot on seed 3")
    print("  ->", run(quiet_stats(silent(dice(3)(monte_carlo(PLAYOUTS)(battle()))))))

    print("5. 100 fights, one task each: random bot vs Monte Carlo bot")
    print("  -> random bot wins", run(scheduled(quiet_stats(win_rate(random_bot, 100)))))
    print("  -> monte carlo wins", run(scheduled(quiet_stats(win_rate(monte_carlo(PLAYOUTS), 100)))))

    print("6. damage by card over 100 random fights")
    totals: dict = {}
    run(scheduled(damage_stats(totals)(win_rate(random_bot, 100))))
    print("  ->", totals)


PLAYOUTS = 3  # per candidate move; the 100 Monte Carlo fights take well under a minute

if __name__ == "__main__":
    command, seeds = sys.argv[1:2], [int(seed) for seed in sys.argv[2:3]]
    if command == ["play"]:
        at_terminal(*seeds) if seeds else play()
    elif command == ["window"]:
        in_window(*seeds or [random.SystemRandom().randrange(10**6)])
    else:
        tour()
