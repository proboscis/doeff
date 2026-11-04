"""The runs behind the numbers of the article-opening video: each stack of examples/effects_tour/spire.py once, and
the outcome of the random bot on each of the hundred seeds of Layer 5 (which squares of the grid are wins).

Usage (from the repository root): uv run python tools/docs-video/src/pages/article-opening/measure/stacks.py > tools/docs-video/src/pages/article-opening/measure/stacks.log
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[6] / "examples" / "effects_tour"))

from spire import (  # noqa: E402 — spire.py is found through the path above
    PLAYOUTS,
    battle,
    dice,
    monte_carlo,
    quiet_stats,
    random_bot,
    record,
    replay,
    run,
    scheduled,
    scripted,
    silent,
    win_rate,
)

moves = [0, 0, 0, None] * 20
print("scripted, three runs:", [run(quiet_stats(silent(dice(7)(scripted(moves)(battle()))))) for _ in range(3)])
print("random bot:", run(quiet_stats(silent(dice(3)(random_bot(battle()))))))
tape: list = []
first = run(quiet_stats(silent(dice(3)(random_bot(record(tape)(battle()))))))
again = run(quiet_stats(silent(replay(tape)(battle()))))
print("record and replay:", first, again, "same:", first == again, "tape:", len(tape))
print("monte carlo:", run(quiet_stats(silent(dice(3)(monte_carlo(PLAYOUTS)(battle()))))))
alone = [run(quiet_stats(silent(dice(seed)(random_bot(battle())))))[0] == "win" for seed in range(100)]
print("random bot wins by seed:", "".join("W" if won else "." for won in alone), sum(alone))
print("random bot win rate under scheduled:", run(scheduled(quiet_stats(win_rate(random_bot, 100)))))
print("monte carlo win rate under scheduled:", run(scheduled(quiet_stats(win_rate(monte_carlo(PLAYOUTS), 100)))))
