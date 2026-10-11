"""実験 4: 直す前から在る「書き手が for/do を本体の <- で重ねる」形でも、exp2 の handler は止まるか(新しさの切り分け)。"""

import contextlib
import io
import sys

sys.path.insert(0, "/tmp/blind-a")

from common import STATE, evaluate, reset  # noqa: E402
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    from exp2_global_limit import globally_limited  # noqa: E402
from doeff_core_effects.scheduler import SchedulerDeadlockError, scheduled  # noqa: E402

from doeff import run  # noqa: E402

CODES = {
    "one for/do, two From": "(for/do (<- a (From [1 2 3])) (<- b (From [10 20])) (<- r (probe a b)) r)",
    "for/do inside the body by <-": "(for/do (<- a (From [1 2 3])) (<- inner (for/do (<- b (From [10 20])) (<- r (probe a b)) r)) inner.valid_values)",
}
for name, code in CODES.items():
    reset()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            collection = run(scheduled(globally_limited(2)(evaluate(code))))
        print(f"limit=2 | {name}: valid_values={collection.valid_values}")
    except SchedulerDeadlockError:
        print(f"limit=2 | {name}: DEADLOCK (leaf calls started={STATE['calls']})")
