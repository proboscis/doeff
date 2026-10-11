"""実験 2: S1 の「Traverse を答える handler を 1 つ足す」— for/do 全体の同時の件を N に抑える handler。

doeff_traverse.handlers.parallel の Traverse の枝を写し、違いは 1 点だけ: 枠(semaphore)を Traverse ごとに作らず、
handler 1 つにつき 1 つ作って全ての Traverse で分ける(外の API の上限 N を for/do 全体で守るため)。
P1(Collection で答える)・P2(件の program を、内の handler と自分を戻した下で走らせる)は parallel と同じに守る。
"""

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


def globally_limited(limit: int):
    """parallel の Traverse の枝と同じ。枠だけを handler の閉包に 1 つ持つ。"""
    shared: dict[str, object] = {}

    @do
    def handler(effect: Skip | Traverse, k):
        if isinstance(effect, Skip):
            return _SKIPPED
        if isinstance(effect, Traverse):
            inner_hs = yield get_inner_handlers(k)
            if isinstance(effect.items, Collection):
                all_items = effect.items.all_items
            else:
                all_items = [ItemResult(index=i, value=v) for i, v in enumerate(effect.items)]
            carry_forward = [item for item in all_items if item.failed]
            active_items = [item for item in all_items if not item.failed]
            if not active_items:
                return (yield Resume(k, Collection(carry_forward)))
            if "sem" not in shared:  # ← parallel との違い: 枠は handler に 1 つ
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
                    prog = program_handler(handler)(prog)
                    result = yield try_handler(Try(prog))
                    yield ReleaseSemaphore(sem)
                    return (item, result)

                tasks.append((yield Spawn(run_item())))
            task_results = yield Gather(*tasks)
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

    return program_handler(handler)


CASES = [
    ("single From, 6 items", 2, "(for/do (<- a (From (range 6))) (<- r (probe a)) r)"),
    ("single From over 6 pairs", 2, "(for/do (<- p (From (lfor a [1 2 3] b [10 20] #(a b)))) (<- r (probe #* p)) r)"),
    ("nested From 3x2", 2, "(for/do (<- a (From [1 2 3])) (<- b (From [10 20])) (<- r (probe a b)) r)"),
    ("nested From 3x2", 3, "(for/do (<- a (From [1 2 3])) (<- b (From [10 20])) (<- r (probe a b)) r)"),
    ("nested From 3x2", 4, "(for/do (<- a (From [1 2 3])) (<- b (From [10 20])) (<- r (probe a b)) r)"),
]

for name, limit, code in CASES:
    reset()
    try:
        collection = run(scheduled(globally_limited(limit)(evaluate(code))))
        print(f"limit={limit} | {name}: valid_values={collection.valid_values} max_active_leaf={STATE['max']}")
    except SchedulerDeadlockError as error:
        print(f"limit={limit} | {name}: SchedulerDeadlockError: {str(error)[:160]} (leaf calls started={STATE['calls']})")
    except Exception as error:  # noqa: BLE001 — 実験の観測: 例外の型と文をそのまま出す
        print(f"limit={limit} | {name}: {type(error).__name__}: {str(error)[:200]} (leaf calls started={STATE['calls']})")
