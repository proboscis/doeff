"""The runs behind the numbers of the article-monte-carlo video: the Monte Carlo bot of examples/effects_tour/spire.py
on seed 3 (Layer 4). For each of its Choose effects, the state it paused at, the wins of each candidate move over the
playouts and the move it resumed k with; then how many playouts the fight ran and how long a fight takes against the
random bot.

Usage (from the repository root): uv run python tools/docs-video/src/pages/article-monte-carlo/measure/monte.py > tools/docs-video/src/pages/article-monte-carlo/measure/monte.log
"""

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[6] / "examples" / "effects_tour"))

import spire  # noqa: E402 — spire.py is found through the path above
from spire import (  # noqa: E402
    PLAYOUTS,
    Choose,
    Resume,
    affordable,
    battle,
    dice,
    handler,
    hit,
    monte_carlo,
    quiet_stats,
    random_bot,
    run,
    silent,
)

# Every Choose that reaches monte_carlo, with the answer it resumed k with: this handler sits between battle and
# monte_carlo and passes the effect on.
seen: tuple = ()


@handler
def watch(effect: Choose, k):
    global seen
    answer = yield effect
    seen = (*seen, (effect, answer))
    return (yield Resume(k, answer))


# Every run(...) monte_carlo makes is one playout; the outer run of the fight is not.
runs = 0
inner_run = spire.run


def counted_run(program):
    global runs
    runs += 1
    return inner_run(program)


spire.run = counted_run
print("monte carlo on seed 3:", run(quiet_stats(silent(dice(3)(monte_carlo(PLAYOUTS)(watch(battle())))))))
spire.run = inner_run
print("playouts run in the fight:", runs, "Choose effects:", len(seen))


def playout_won(effect: Choose, i, n: int) -> bool:
    """One playout of monte_carlo's choose: move i (None ends the turn), then the rest of the fight on dice(n)."""
    s = effect.state if i is None else hit(effect.state, effect.hand[i])[0]
    return s.enemy_hp <= 0 or run(quiet_stats(silent(dice(n)(random_bot(battle(s))))))[0] == "win"


def candidate_wins(effect: Choose) -> list:
    """The wins of each candidate move, the same loop as monte_carlo's choose."""
    return [
        (i, "end turn" if i is None else effect.hand[i], sum(playout_won(effect, i, n) for n in range(PLAYOUTS)))
        for i in [*affordable(effect), None]
    ]


for number, (effect, answer) in enumerate(seen, 1):
    s = effect.state
    rows = candidate_wins(effect)
    best = max(rows, key=lambda row: row[2])  # the first of the most wins, as monte_carlo keeps the first
    print(
        f"Choose {number}: T{s.turn} hp {s.hp} block {s.block} enemy {s.enemy_hp} vulnerable {s.vulnerable} "
        f"energy {effect.energy} hand {list(effect.hand)}"
    )
    after = [s if i is None else hit(s, effect.hand[i])[0] for i, _, _ in rows]
    print("   enemy hp after the move:", [(card, a.enemy_hp) for (_, card, _), a in zip(rows, after)])
    print("   wins of", PLAYOUTS, ":", [(card, wins) for _, card, wins in rows], "-> resumed with", answer, "| same as the loop:", best[0] == answer)
print("playouts in the fight (candidate moves x PLAYOUTS):", sum(len(candidate_wins(effect)) for effect, _ in seen) * PLAYOUTS)


def median_seconds(make, times: int) -> float:
    def once() -> float:
        start = time.perf_counter()
        run(make())
        return time.perf_counter() - start

    return statistics.median(once() for _ in range(times))


print(f"one Monte Carlo fight on seed 3: {median_seconds(lambda: quiet_stats(silent(dice(3)(monte_carlo(PLAYOUTS)(battle())))), 21) * 1000:.0f} ms (median of 21)")
print(f"one random-bot fight on seed 3: {median_seconds(lambda: quiet_stats(silent(dice(3)(random_bot(battle())))), 201) * 1000:.2f} ms (median of 201)")
print("python", sys.version.split()[0])
