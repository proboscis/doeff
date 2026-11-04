"""The runs behind the article-hundred-fights video: win_rate and damage_stats of examples/effects_tour/spire.py,
unchanged, with a player that also writes down what its task does and a handler around win_rate that writes down what
reaches the scheduler's edge, so the order on screen is the order of a run. check_code.py compares the numbers of the
video with this output.

Usage (from the repository root): uv run python tools/docs-video/src/pages/article-hundred-fights/measure/hundred.py > tools/docs-video/src/pages/article-hundred-fights/measure/hundred.log
"""

import sys
import threading
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[6] / "examples" / "effects_tour"))

from doeff import Gather, Pass, Resume, handler  # noqa: E402

from spire import (  # noqa: E402 — spire.py is found through the path above
    PLAYOUTS,
    Choose,
    Played,
    Roll,
    Show,
    damage_stats,
    monte_carlo,
    quiet_stats,
    random_bot,
    run,
    scheduled,
    win_rate,
)

timeline: list[tuple[str, int, str]] = []  # ("spawn" | "effect" | "gather", the task, the effect's name)
threads: set[int] = set()
at_the_edge: Counter = Counter()
results: list = []
spawned = 0


def noted_random_bot(program):
    """random_bot, plus a handler inside it that writes down every effect of this task and passes it on."""
    global spawned
    seed = spawned
    spawned += 1
    timeline.append(("spawn", seed, ""))

    @handler
    def note(effect: Roll | Choose | Show | Played, k):
        timeline.append(("effect", seed, type(effect).__name__))
        threads.add(threading.get_ident())
        return (yield Pass(effect, k))

    return random_bot(note(program))


@handler
def edge(effect, k):
    """Just inside `scheduled`: sees everything no handler further in has taken, on its way to the scheduler."""
    at_the_edge[type(effect).__name__] += 1
    if isinstance(effect, Gather):
        timeline.append(("gather", -1, ""))
        answer = yield effect
        results.extend(answer)
        return (yield Resume(k, answer))
    return (yield Pass(effect, k))


rate = run(scheduled(edge(quiet_stats(win_rate(noted_random_bot, 100)))))
effects = [(seed, name) for kind, seed, name in timeline if kind == "effect"]
kinds = [kind for kind, _, _ in timeline]
print("random_rate:", rate)
print("random_wins:", sum(result == "win" for result, _ in results))
print("random_wins_by_seed:", "".join("W" if result == "win" else "." for result, _ in results))
print("results_first_three:", results[:3])
print("effects_in_tasks:", len(effects))
print("effects_by_name:", dict(Counter(name for _, name in effects)))
print("task_changes:", sum(a[0] != b[0] for a, b in zip(effects, effects[1:])))
print("threads:", len(threads))
print("reached_scheduler:", {name: at_the_edge[name] for name in ("Roll", "Choose", "Show", "Played", "Spawn", "Gather")})
print("tasks_in_spawn_order:", [seed for seed, _ in effects] == sorted(seed for seed, _ in effects))
print("spawns_before_first_effect:", kinds[: kinds.index("effect")].count("spawn"))
print("effects_after_gather:", kinds[kinds.index("gather") :].count("effect"))
print("played_by_task_first_six:", [sum(1 for seed, name in effects if seed == s and name == "Played") for s in range(6)])
print("played_total:", sum(1 for _, name in effects if name == "Played"))

print("monte_carlo_rate:", run(scheduled(quiet_stats(win_rate(monte_carlo(PLAYOUTS), 100)))))
print("playouts:", PLAYOUTS)

totals: dict = {}
run(scheduled(damage_stats(totals)(win_rate(random_bot, 100))))
print("damage_by_card:", totals)
