"""S5 を hook なしで撃つ: 毎回新しい package を作り、主 thread で handler を読み、2 つの thread を Barrier で同時に
走らせて別の effect で check_coverage を引く。正しい答えは両方とも gaps == ['Far']。
使い方: python race_stress.py <fixture の dir> <回数> <filler の関数の数>
"""

import collections
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixture  # noqa: E402

import doeff_effect_analyzer  # noqa: E402
from doeff_effect_analyzer.handler_effects import analyze_handler, check_coverage  # noqa: E402

root = Path(sys.argv[1])
rounds = int(sys.argv[2])
filler = int(sys.argv[3])
print("analyzer:", doeff_effect_analyzer.__file__, "GIL enabled:", sys._is_gil_enabled())

tally: collections.Counter[str] = collections.Counter()
for _ in range(rounds):
    name = fixture.make(root, filler)
    eff = fixture.effects(name)
    handler = analyze_handler(f"{name}.handlers:two_clauses")
    barrier = threading.Barrier(2)
    out: dict[str, list[str]] = {}

    def check(label: str, effect: type) -> None:
        barrier.wait()
        out[label] = fixture.gaps(check_coverage([effect], [handler]))

    threads = [
        threading.Thread(target=check, args=("Ping", eff.Ping)),
        threading.Thread(target=check, args=("Pong", eff.Pong)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wrong = [label for label, found in out.items() if found != ["Far"]]
    tally["wrong:" + ",".join(wrong) if wrong else "right"] += 1
print(dict(tally))
