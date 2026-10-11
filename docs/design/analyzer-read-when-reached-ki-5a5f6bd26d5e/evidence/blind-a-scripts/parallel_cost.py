"""直し方ごとの並びの損を測る: K 個の thread が、それぞれ別の handler(別の package)の節を引く。
節の本体が呼ぶ common の module は filler で大きくして、初めて読む時の parse に時間をかける。
同じ handler を共有しないので、reader を分ける直し方なら並んで走り、process に 1 つの lock なら一列になる。
使い方: python parallel_cost.py <fixture の dir> <K> <filler>
"""

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixture  # noqa: E402

from doeff_effect_analyzer.handler_effects import analyze_handler, check_coverage  # noqa: E402

root = Path(sys.argv[1])
k = int(sys.argv[2])
filler = int(sys.argv[3])
names = [fixture.make(root, filler) for _ in range(k)]
handlers = [analyze_handler(f"{name}.handlers:two_clauses") for name in names]
effects = [fixture.effects(name) for name in names]
barrier = threading.Barrier(k + 1)
out: list[list[str]] = [[] for _ in range(k)]


def check(index: int) -> None:
    barrier.wait()
    out[index] = fixture.gaps(check_coverage([effects[index].Ping], [handlers[index]]))


threads = [threading.Thread(target=check, args=(i,)) for i in range(k)]
for thread in threads:
    thread.start()
barrier.wait()
start = time.perf_counter()
for thread in threads:
    thread.join()
elapsed = time.perf_counter() - start
print(f"cpus={os.cpu_count()} K={k} filler={filler} wall={elapsed:.3f}s right={sum(found == ['Far'] for found in out)}/{k}")
