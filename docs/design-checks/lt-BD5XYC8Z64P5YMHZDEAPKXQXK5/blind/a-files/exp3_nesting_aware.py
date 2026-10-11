"""実験 3: exp2 の handler を直すには、handler が「Traverse が自分の件の program の中から来る(重ね)」を知る必要が在るか。

直し: 件の program の周りへ戻す handler を、その件が枠を持っている事を知る別の handler にし、件の中から来た Traverse
(= 重ねた From の内の Traverse)を受けた時は、内の件を待つ間だけ外の件の枠を返す。
"""

import contextlib
import io
import sys

sys.path.insert(0, "/tmp/blind-a")

from common import STATE, evaluate, reset  # noqa: E402
from doeff_core_effects.effects import Try  # noqa: E402
from doeff_core_effects.handlers import try_handler  # noqa: E402
from doeff_core_effects.scheduler import (  # noqa: E402
    AcquireSemaphore,
    CreateSemaphore,
    Gather,
    ReleaseSemaphore,
    SchedulerDeadlockError,
    Spawn,
    scheduled,
)
from doeff_traverse.collection import Collection, HistoryEntry, ItemResult  # noqa: E402
from doeff_traverse.effects import Skip, Traverse  # noqa: E402
from doeff_vm import Err, Ok  # noqa: E402

from doeff import do, run  # noqa: E402
from doeff.handler_utils import get_inner_handlers  # noqa: E402
from doeff.program import Pass, Resume  # noqa: E402
from doeff.program import handler as program_handler  # noqa: E402

_SKIPPED = object()


def nesting_aware(limit: int):
    shared: dict[str, object] = {}

    def make(inside_item: bool):
        @do
        def handler(effect: Skip | Traverse, k):
            if isinstance(effect, Skip):
                return _SKIPPED
            if isinstance(effect, Traverse):
                inner_hs = yield get_inner_handlers(k)
                all_items = (effect.items.all_items if isinstance(effect.items, Collection)
                             else [ItemResult(index=i, value=v) for i, v in enumerate(effect.items)])
                carry_forward = [item for item in all_items if item.failed]
                active_items = [item for item in all_items if not item.failed]
                if not active_items:
                    return (yield Resume(k, Collection(carry_forward)))
                if "sem" not in shared:
                    shared["sem"] = yield CreateSemaphore(limit)
                sem = shared["sem"]
                tasks = []
                for item in active_items:

                    @do
                    def run_item(item=item):
                        yield AcquireSemaphore(sem)
                        prog = effect.f(item.value)
                        for h in inner_hs:
                            prog = program_handler(h)(prog)
                        prog = program_handler(make(True))(prog)  # ← 件の中だと知る handler を戻す
                        result = yield try_handler(Try(prog))
                        yield ReleaseSemaphore(sem)
                        return (item, result)

                    tasks.append((yield Spawn(run_item())))
                if inside_item:  # ← 重ねの知識: 外の件の中の Traverse なら、内の件を待つ間は外の件の枠を返す
                    yield ReleaseSemaphore(sem)
                task_results = yield Gather(*tasks)
                if inside_item:
                    yield AcquireSemaphore(sem)
                results = list(carry_forward)
                for item, result in task_results:
                    if isinstance(result, Ok) and result.value is _SKIPPED:
                        results.append(ItemResult(index=item.index, value=item.value, failed=True,
                                                  history=item.history + [HistoryEntry(stage=effect.label, event="skipped")]))
                    elif isinstance(result, Ok):
                        results.append(ItemResult(index=item.index, value=result.value,
                                                  history=item.history + [HistoryEntry(stage=effect.label, event="ok")]))
                    elif isinstance(result, Err):
                        results.append(ItemResult(index=item.index, value=result.error, failed=True,
                                                  history=item.history + [HistoryEntry(stage=effect.label, event="failed",
                                                                                       detail=str(result.error))]))
                results.sort(key=lambda r: r.index)
                return (yield Resume(k, Collection(results)))
            yield Pass(effect, k)

        return handler

    return program_handler(make(False))


for m in (2, 3, 6):
    row = []
    for limit in (1, 2, 3, 4):
        reset()
        code = f"(for/do (<- a (From (range {m}))) (<- b (From [10 20])) (<- r (probe a b)) r)"
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                collection = run(scheduled(nesting_aware(limit)(evaluate(code))))
            ok = collection.valid_values == [a + b for a in range(m) for b in (10, 20)]
            row.append(f"N={limit}:{'ok' if ok else 'WRONG'}(leaf max {STATE['max']})")
        except SchedulerDeadlockError:
            row.append(f"N={limit}:DEADLOCK")
    print(f"outer m={m}: " + "  ".join(row))
