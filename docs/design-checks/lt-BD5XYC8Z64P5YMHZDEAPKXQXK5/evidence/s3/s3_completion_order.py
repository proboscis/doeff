"""S3 の失敗例の対照: 件が終わる順を逆にしても、平らにした答えは件の番号の順か。

葉の件は b が小さいほど多く scheduler へ手を返す(後の件が先に終わる)。終わった順を記し、parallel・parallel_fail_fast の
答えが番号の順(外の順 → 内の順)である事と、終わった順がそれと違う事を出す。
"""

import sys
import types

import doeff_hy  # noqa: F401
import hy
import hy.macros
from doeff_core_effects.scheduler import Spawn, Wait, scheduled
from doeff_traverse.handlers import parallel, parallel_fail_fast, sequential

from doeff import do, run

FINISHED: list[tuple[int, int]] = []


@do
def _noop():
    return None


@do
def slow(a, b):
    """b が小さいほど長く手を返す葉の件。終わった順を FINISHED に記す。"""
    for _ in range(40 - b):
        task = yield Spawn(_noop())
        yield Wait(task)
    FINISHED.append((a, b))
    return a * 100 + b


def evaluate(code):
    mod = types.ModuleType("s3_probe")
    sys.modules["s3_probe"] = mod
    mod.__dict__["slow"] = slow
    hy.macros.require("doeff_hy.macros", mod, assignments=[["for/do", "for/do"], ["<-", "<-"]])
    result = None
    for form in hy.read_many("(import doeff [do :as _doeff-do])\n(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])\n" + code):
        result = hy.eval(form, mod.__dict__, module=mod)
    return result


CODE = "(for/do (<- a (From [1 2])) (<- b (From [10 20 30])) (<- r (slow a b)) r)"
for name, make in (("sequential", sequential), ("parallel-4", lambda: parallel(concurrency=4)), ("parallel_fail_fast-4", lambda: parallel_fail_fast(concurrency=4))):
    FINISHED.clear()
    values = run(scheduled(make()(evaluate(CODE)))).valid_values
    print(f"[{name}] valid_values={values} finished_order={FINISHED} index_order={values == [110, 120, 130, 210, 220, 230]} finished_differs={[a * 100 + b for a, b in FINISHED] != values}")
