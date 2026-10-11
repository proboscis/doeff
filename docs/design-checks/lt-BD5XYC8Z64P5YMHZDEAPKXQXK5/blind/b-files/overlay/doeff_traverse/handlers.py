"""
Handlers for doeff-traverse effects.

sequential(): default handler — runs Traverse sequentially, Reduce/Zip/Inspect directly.
normalize_to_none(): Fail handler — resumes with None at fail site.
"""

from doeff import do
from doeff.program import GetHandlers, Pass, Resume, ResumeThrow
from doeff.program import handler as _program_handler
from doeff_traverse.collection import Collection, HistoryEntry, ItemResult
from doeff_traverse.effects import Fail, Inspect, Reduce, Skip, SortBy, Take, Traverse, Zip

# The effects every collection handler below answers. The VM skips the handler
# for any other effect without entering Python (doeff_vm._effect_types) — the
# same as the handler's own trailing Pass.
CollectionEffect = Skip | Traverse | Reduce | Zip | Inspect | SortBy | Take

# Sentinel for Skip — traverse handler checks identity
_SKIPPED = object()


class _Progress:
    """for/do 1 つの進捗。答えの件(From を重ねた for/do では組)で数える。"""

    def __init__(self, label, total):
        self.label = label
        self.total = total
        self.done = 0

    def step(self, on_progress):
        self.done += 1
        if on_progress is not None:
            on_progress(self.label, self.done, self.total)


def sequential(on_progress=None):  # noqa: PLR0915 - baseline cleanup keeps existing control flow unchanged
    """Sequential handler for Traverse/Reduce/Zip/Inspect.

    on_progress: ``on_progress(label, done, total)`` を答えの件が 1 つ終わるたびに呼ぶ(ex08 の tqdm の
    handler の代わり — Pass で f を包む handler は件の失敗・skip を数えられず、From を重ねると外の件ごとに
    bar が立っていた)。for/do 1 つにつき 1 本で、From を重ねた for/do は答えの件(組)の数で数える:
    内側の From の Traverse は外の件の program の中からこの handler に戻ってくる(件ごとに install_self を
    置き直す)ので、外の Traverse が走っている間に来た Traverse は外の進捗に足す — 外の件 1 つが内側の件 n 個に
    割れる(total に n - 1 を足す)。答えが Collection の件は flatten で内側の件に割れるので、その件自身は数えない。

    Traverse: runs f(item) sequentially for each item.
              Uses effect.label for per-stage history tracking.
    Reduce: applies f to valid values.
    Zip: joins two collections by index (failure union).
    Inspect: returns ItemResult list.

    Unhandled Fail inside Traverse marks the item as failed.

    An item's failure is caught by this handler's own ``try`` around the item program,
    not by a ``Try`` effect per item: the ``Try`` round (two try_handler installs, a
    GetHandlers, two ``@do`` functions and a Transfer per item) was ~29 µs of the ~46 µs
    an item cost (agora-redesign #2871). try_handler stays installed around each item,
    so a ``Try`` the item performs is still answered without an outer try_handler.
    """
    from doeff_core_effects.handlers import try_handler

    from doeff.handler_utils import get_inner_handlers, inner_of

    # 走っている for/do の進捗(内側ほど後ろ)。空でない間に来た Traverse は、外の From の件の中の内側の From。
    running = []

    @do
    def handler(effect: CollectionEffect, k):  # noqa: PLR0911, PLR0912, PLR0915 - baseline cleanup keeps existing control flow unchanged
        if isinstance(effect, Skip):
            return _SKIPPED

        if isinstance(effect, Traverse):
            # One installer per inner handler for the whole Traverse, not one per item —
            # read when the first item runs (a Traverse with no item to run reads none),
            # from GetHandlers itself rather than through get_inner_handlers' @do call
            # (agora-redesign #2958).
            inner_installs = None
            results = []

            # Iterate: Collection (from previous traverse) or raw iterable/generator
            # (進捗の total のため、件を先に並べる)
            if isinstance(effect.items, Collection):
                items_iter = effect.items.all_items
            else:
                items_iter = [
                    ItemResult(index=i, value=v)
                    for i, v in enumerate(effect.items)
                ]

            if running:
                # 内側の From: 外の件 1 つが、この Traverse の件の数に割れる(Collection.flatten)
                progress = running[-1]
                progress.total += len(items_iter) - 1
            else:
                progress = _Progress(effect.label, len(items_iter))
            running.append(progress)

            for item in items_iter:
                # Carry forward already-failed items without re-processing
                if item.failed:
                    results.append(item)
                    progress.step(on_progress)
                    continue

                if inner_installs is None:
                    inner_installs = [_program_handler(h) for h in inner_of((yield GetHandlers(k)))]
                # Build fresh program for this item
                prog = effect.f(item.value)
                # Reinstall inner handlers + this handler for nested Traverse,
                # and try_handler outermost (where the per-item Try used to put it)
                for install in inner_installs:
                    prog = install(prog)
                prog = try_handler(install_self(prog))

                # Catch unhandled failures per item
                try:
                    value = yield prog
                except Exception as error:
                    results.append(ItemResult(
                        index=item.index,
                        value=error,
                        failed=True,
                        history=item.history + [HistoryEntry(
                            stage=effect.label,
                            event="failed",
                            detail=str(error),
                        )],
                    ))
                    progress.step(on_progress)
                    continue
                if value is _SKIPPED:
                    results.append(ItemResult(
                        index=item.index,
                        value=item.value,
                        failed=True,
                        history=item.history + [HistoryEntry(stage=effect.label, event="skipped")],
                    ))
                    progress.step(on_progress)
                else:
                    results.append(ItemResult(
                        index=item.index,
                        value=value,
                        history=item.history + [HistoryEntry(stage=effect.label, event="ok")],
                    ))
                    # 答えが Collection の件(外の From の件)は flatten で内側の件に割れる — 内側の件で数え済み
                    if not isinstance(value, Collection):
                        progress.step(on_progress)

            running.pop()
            return (yield Resume(k, Collection(results)))

        if isinstance(effect, Reduce):
            inner_hs = yield get_inner_handlers(k)
            # Fold over valid items (skip failed)
            if isinstance(effect.collection, Collection):
                values_iter = (item.value for item in effect.collection.valid_items)
            else:
                values_iter = effect.collection
            acc = effect.init
            for value in values_iter:
                prog = effect.f(acc, value)
                for h in inner_hs:
                    prog = _program_handler(h)(prog)
                prog = _program_handler(handler)(prog)
                acc = yield prog
            return (yield Resume(k, acc))

        if isinstance(effect, Zip):
            a = Collection.from_iterable(effect.a)
            b = Collection.from_iterable(effect.b)
            results = []
            for item_a, item_b in zip(a.all_items, b.all_items, strict=False):
                failed = item_a.failed or item_b.failed
                value = (item_a.value if item_a.failed else item_b.value) if failed else (item_a.value, item_b.value)
                history = item_a.history + item_b.history
                if failed:
                    history.append(HistoryEntry(event="zip_failed"))
                results.append(ItemResult(
                    index=item_a.index,
                    value=value,
                    failed=failed,
                    history=history,
                ))
            return (yield Resume(k, Collection(results)))

        if isinstance(effect, Inspect):
            col = Collection.from_iterable(effect.collection)
            return (yield Resume(k, col.all_items))

        if isinstance(effect, SortBy):
            col = Collection.from_iterable(effect.collection)
            valid = list(col.valid_items)
            failed = list(col.failed_items)
            valid.sort(key=lambda item: effect.key(item.value), reverse=effect.reverse)
            # Build new items with fresh indices (don't mutate originals)
            results = [
                ItemResult(index=i, value=item.value, failed=item.failed, history=list(item.history))
                for i, item in enumerate(valid + failed)
            ]
            return (yield Resume(k, Collection(results)))

        if isinstance(effect, Take):
            col = Collection.from_iterable(effect.collection)
            taken = []
            count = 0
            for item in col.all_items:
                if item.failed:
                    taken.append(item)
                elif count < effect.n:
                    taken.append(item)
                    count += 1
            # Build new items with fresh indices
            results = [
                ItemResult(index=i, value=item.value, failed=item.failed, history=list(item.history))
                for i, item in enumerate(taken)
            ]
            return (yield Resume(k, Collection(results)))

        yield Pass(effect, k)

    # Built once: the Traverse branch reinstalls this handler around every item.
    install_self = _program_handler(handler)
    return install_self


def parallel(concurrency=10):  # noqa: PLR0915 - baseline cleanup keeps existing control flow unchanged
    """Parallel handler for Traverse/Reduce/Zip/Inspect.

    Traverse: spawns up to `concurrency` tasks concurrently via Spawn/Gather.
    Reduce/Zip/Inspect: same as sequential (these are inherently sequential).

    Items from generators are materialized before spawning.
    """
    from doeff_core_effects.effects import Try
    from doeff_core_effects.scheduler import (
        AcquireSemaphore,
        CreateSemaphore,
        Gather,
        ReleaseSemaphore,
        Spawn,
    )
    from doeff_vm import Err, Ok

    from doeff.handler_utils import get_inner_handlers
    @do
    def handler(effect: CollectionEffect, k):  # noqa: PLR0911, PLR0912, PLR0915 - baseline cleanup keeps existing control flow unchanged
        if isinstance(effect, Skip):
            return _SKIPPED

        if isinstance(effect, Traverse):
            inner_hs = yield get_inner_handlers(k)

            # Collect items (must materialize for parallel dispatch)
            if isinstance(effect.items, Collection):
                all_items = effect.items.all_items
            else:
                all_items = [
                    ItemResult(index=i, value=v)
                    for i, v in enumerate(effect.items)
                ]

            # Separate failed (carry forward) from active
            carry_forward = [item for item in all_items if item.failed]
            active_items = [item for item in all_items if not item.failed]

            if not active_items:
                return (yield Resume(k, Collection(carry_forward)))

            # Create semaphore for concurrency limiting
            sem = yield CreateSemaphore(concurrency)

            # Spawn a task per active item
            tasks = []
            for item in active_items:
                @do
                def run_item(item=item):
                    yield AcquireSemaphore(sem)
                    prog = effect.f(item.value)
                    for h in inner_hs:
                        prog = _program_handler(h)(prog)
                    prog = _program_handler(handler)(prog)

                    @do
                    def attempt():
                        from doeff_core_effects.handlers import try_handler
                        value = yield try_handler(Try(prog))
                        return value

                    result = yield attempt()
                    yield ReleaseSemaphore(sem)
                    return (item, result)

                task = yield Spawn(run_item())
                tasks.append(task)

            # Gather all results
            task_results = yield Gather(*tasks)

            # Build Collection preserving original index order
            results = list(carry_forward)
            for item, result in task_results:
                if isinstance(result, Ok):
                    if result.value is _SKIPPED:
                        results.append(ItemResult(
                            index=item.index,
                            value=item.value,
                            failed=True,
                            history=item.history + [HistoryEntry(stage=effect.label, event="skipped")],
                        ))
                    else:
                        results.append(ItemResult(
                            index=item.index,
                            value=result.value,
                            history=item.history + [HistoryEntry(stage=effect.label, event="ok")],
                        ))
                elif isinstance(result, Err):
                    results.append(ItemResult(
                        index=item.index,
                        value=result.error,
                        failed=True,
                        history=item.history + [HistoryEntry(
                            stage=effect.label,
                            event="failed",
                            detail=str(result.error),
                        )],
                    ))
            results.sort(key=lambda r: r.index)
            return (yield Resume(k, Collection(results)))

        if isinstance(effect, Reduce):
            inner_hs = yield get_inner_handlers(k)
            if isinstance(effect.collection, Collection):
                values_iter = (item.value for item in effect.collection.valid_items)
            else:
                values_iter = effect.collection
            acc = effect.init
            for value in values_iter:
                prog = effect.f(acc, value)
                for h in inner_hs:
                    prog = _program_handler(h)(prog)
                prog = _program_handler(handler)(prog)
                acc = yield prog
            return (yield Resume(k, acc))

        if isinstance(effect, Zip):
            a = Collection.from_iterable(effect.a)
            b = Collection.from_iterable(effect.b)
            results = []
            for item_a, item_b in zip(a.all_items, b.all_items, strict=False):
                failed = item_a.failed or item_b.failed
                value = (item_a.value if item_a.failed else item_b.value) if failed else (item_a.value, item_b.value)
                history = item_a.history + item_b.history
                if failed:
                    history.append(HistoryEntry(event="zip_failed"))
                results.append(ItemResult(
                    index=item_a.index,
                    value=value,
                    failed=failed,
                    history=history,
                ))
            return (yield Resume(k, Collection(results)))

        if isinstance(effect, Inspect):
            col = Collection.from_iterable(effect.collection)
            return (yield Resume(k, col.all_items))

        if isinstance(effect, SortBy):
            col = Collection.from_iterable(effect.collection)
            valid = list(col.valid_items)
            failed = list(col.failed_items)
            valid.sort(key=lambda item: effect.key(item.value), reverse=effect.reverse)
            results = [
                ItemResult(index=i, value=item.value, failed=item.failed, history=list(item.history))
                for i, item in enumerate(valid + failed)
            ]
            return (yield Resume(k, Collection(results)))

        if isinstance(effect, Take):
            col = Collection.from_iterable(effect.collection)
            taken = []
            count = 0
            for item in col.all_items:
                if item.failed:
                    taken.append(item)
                elif count < effect.n:
                    taken.append(item)
                    count += 1
            results = [
                ItemResult(index=i, value=item.value, failed=item.failed, history=list(item.history))
                for i, item in enumerate(taken)
            ]
            return (yield Resume(k, Collection(results)))

        yield Pass(effect, k)

    return _program_handler(handler)


def parallel_fail_fast(concurrency=10):  # noqa: PLR0915 - baseline cleanup keeps existing control flow unchanged
    """Parallel handler that aborts on first failure.

    Same interface as parallel(), but does NOT wrap items in Try.
    If any spawned task raises, Gather propagates the exception immediately
    and remaining tasks are cancelled.

    Use this instead of parallel() when silent failure collection is unacceptable.
    Swap in the interpreter: (parallel :concurrency 40) → (parallel-fail-fast :concurrency 40)
    """
    from doeff_core_effects.scheduler import (
        AcquireSemaphore,
        CreateSemaphore,
        Gather,
        ReleaseSemaphore,
        Spawn,
    )

    from doeff.handler_utils import get_inner_handlers
    @do
    def handler(effect: CollectionEffect, k):  # noqa: PLR0911, PLR0912, PLR0915 - baseline cleanup keeps existing control flow unchanged
        if isinstance(effect, Skip):
            return _SKIPPED

        if isinstance(effect, Traverse):
            inner_hs = yield get_inner_handlers(k)

            if isinstance(effect.items, Collection):
                all_items = effect.items.all_items
            else:
                all_items = [
                    ItemResult(index=i, value=v)
                    for i, v in enumerate(effect.items)
                ]

            carry_forward = [item for item in all_items if item.failed]
            active_items = [item for item in all_items if not item.failed]

            # If there are already-failed items, fail fast immediately
            if carry_forward:
                first_fail = carry_forward[0]
                cause = first_fail.value if first_fail.value else "previous stage failed"
                raise RuntimeError(f"parallel_fail_fast: item {first_fail.index} already failed: {cause}")

            if not active_items:
                return (yield Resume(k, Collection([])))

            sem = yield CreateSemaphore(concurrency)

            tasks = []
            for item in active_items:
                @do
                def run_item(item=item):
                    yield AcquireSemaphore(sem)
                    prog = effect.f(item.value)
                    for h in inner_hs:
                        prog = _program_handler(h)(prog)
                    prog = _program_handler(handler)(prog)
                    # NO Try wrapper — exceptions propagate to Gather
                    result = yield prog
                    yield ReleaseSemaphore(sem)
                    return (item, result)

                task = yield Spawn(run_item())
                tasks.append(task)

            # Gather — if any task raised, the exception propagates here
            task_results = yield Gather(*tasks)

            results = []
            for item, value in task_results:
                if value is _SKIPPED:
                    results.append(ItemResult(
                        index=item.index,
                        value=item.value,
                        failed=True,
                        history=item.history + [HistoryEntry(stage=effect.label, event="skipped")],
                    ))
                else:
                    results.append(ItemResult(
                        index=item.index,
                        value=value,
                        history=item.history + [HistoryEntry(stage=effect.label, event="ok")],
                    ))
            results.sort(key=lambda r: r.index)
            return (yield Resume(k, Collection(results)))

        # Reduce/Zip/Inspect/SortBy/Take — delegate to sequential behavior
        if isinstance(effect, Reduce):
            inner_hs = yield get_inner_handlers(k)
            if isinstance(effect.collection, Collection):
                values_iter = (item.value for item in effect.collection.valid_items)
            else:
                values_iter = effect.collection
            acc = effect.init
            for value in values_iter:
                prog = effect.f(acc, value)
                for h in inner_hs:
                    prog = _program_handler(h)(prog)
                prog = _program_handler(handler)(prog)
                acc = yield prog
            return (yield Resume(k, acc))

        if isinstance(effect, Zip):
            a = Collection.from_iterable(effect.a)
            b = Collection.from_iterable(effect.b)
            results = []
            for item_a, item_b in zip(a.all_items, b.all_items, strict=False):
                failed = item_a.failed or item_b.failed
                if failed:
                    cause = item_a.value if item_a.failed else item_b.value
                    raise RuntimeError(f"parallel_fail_fast: zip item failed: {cause}")
                results.append(ItemResult(
                    index=item_a.index,
                    value=(item_a.value, item_b.value),
                    history=item_a.history + item_b.history,
                ))
            return (yield Resume(k, Collection(results)))

        if isinstance(effect, Inspect):
            col = Collection.from_iterable(effect.collection)
            return (yield Resume(k, col.all_items))

        if isinstance(effect, SortBy):
            col = Collection.from_iterable(effect.collection)
            valid = list(col.valid_items)
            failed = list(col.failed_items)
            if failed:
                raise RuntimeError(f"parallel_fail_fast: {len(failed)} items failed before sort")
            valid.sort(key=lambda item: effect.key(item.value), reverse=effect.reverse)
            results = [
                ItemResult(index=i, value=item.value, failed=item.failed, history=list(item.history))
                for i, item in enumerate(valid)
            ]
            return (yield Resume(k, Collection(results)))

        if isinstance(effect, Take):
            col = Collection.from_iterable(effect.collection)
            taken = []
            count = 0
            for item in col.all_items:
                if item.failed:
                    raise RuntimeError(f"parallel_fail_fast: item {item.index} failed before take")
                if count < effect.n:
                    taken.append(item)
                    count += 1
            results = [
                ItemResult(index=i, value=item.value, failed=item.failed, history=list(item.history))
                for i, item in enumerate(taken)
            ]
            return (yield Resume(k, Collection(results)))

        yield Pass(effect, k)

    return _program_handler(handler)


@do
def _fail_handler(effect: Fail, k):
    """Default Fail handler: raises the cause as an exception.

    Converts unhandled Fail effects into Python exceptions.
    This is the fail-fast behavior.
    """
    if isinstance(effect, Fail):
        exc = effect.cause if isinstance(effect.cause, BaseException) else RuntimeError(str(effect.cause))
        return (yield ResumeThrow(k, exc))
    yield Pass(effect, k)


fail_handler = _program_handler(_fail_handler)
fail_handler.__name__ = "fail_handler"
fail_handler.__qualname__ = "fail_handler"


@do
def _normalize_to_none(effect: Fail, k):
    """Fail handler: resume with None at the fail site.

    The computation continues with None as the substitute value.
    """
    if isinstance(effect, Fail):
        return (yield Resume(k, None))
    yield Pass(effect, k)


normalize_to_none = _program_handler(_normalize_to_none)
normalize_to_none.__name__ = "normalize_to_none"
normalize_to_none.__qualname__ = "normalize_to_none"
