"""実験 1: 既存の parallel(concurrency=N) の下で、葉の件の program が同時に幾つ走るか。

同じ 16 組(a in 0..3, b in 0..3)を、(A) 組の list への From 1 つ、(B) From 2 つの重ね、で走らせる。
"""

import sys

sys.path.insert(0, "/tmp/blind-a")

from common import STATE, evaluate, reset  # noqa: E402
from doeff_core_effects.scheduler import scheduled  # noqa: E402
from doeff_traverse.handlers import parallel  # noqa: E402

from doeff import run  # noqa: E402

CASES = {
    "A flat From over 16 pairs": "(for/do (<- p (From (lfor a (range 4) b (range 4) #(a b)))) (<- r (probe #* p)) r)",
    "B nested From 4x4": "(for/do (<- a (From (range 4))) (<- b (From (range 4))) (<- r (probe a b)) r)",
    "C nested From 3x3x3": "(for/do (<- a (From (range 3))) (<- b (From (range 3))) (<- c (From (range 3))) (<- r (probe a b c)) r)",
}

for n in (2, 3):
    for name, code in CASES.items():
        reset()
        collection = run(scheduled(parallel(concurrency=n)(evaluate(code))))
        print(f"concurrency={n} | {name}: items={len(collection.valid_values)} calls={STATE['calls']} max_active_leaf={STATE['max']}")
