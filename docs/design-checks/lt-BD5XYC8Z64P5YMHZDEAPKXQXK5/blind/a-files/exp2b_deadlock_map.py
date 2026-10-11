"""実験 2b: exp2 の handler(枠を handler に 1 つ)で、外の件の数 m・内の件の数 2・枠 N の組ごとの結末。"""

import contextlib
import io
import sys

sys.path.insert(0, "/tmp/blind-a")

from common import STATE, evaluate, reset  # noqa: E402
from exp2_global_limit import globally_limited  # noqa: E402  (import 時に exp2 の表も走る — 出力は捨てる)
from doeff_core_effects.scheduler import SchedulerDeadlockError, scheduled  # noqa: E402

from doeff import run  # noqa: E402

for m in (2, 3, 4, 6):
    row = []
    for limit in (1, 2, 3, 4, 6, 8):
        reset()
        code = f"(for/do (<- a (From (range {m}))) (<- b (From [10 20])) (<- r (probe a b)) r)"
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                collection = run(scheduled(globally_limited(limit)(evaluate(code))))
            ok = collection.valid_values == [a + b for a in range(m) for b in (10, 20)]
            row.append(f"N={limit}:{'ok' if ok else 'WRONG'}(leaf max {STATE['max']})")
        except SchedulerDeadlockError:
            row.append(f"N={limit}:DEADLOCK")
    print(f"outer m={m}: " + "  ".join(row))
