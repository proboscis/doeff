"""
Handlers for doeff-traverse effects.

sequential(): default handler — runs Traverse sequentially, Reduce/Zip/Inspect directly.
normalize_to_none(): Fail handler — resumes with None at fail site.
"""

from doeff import do
from doeff.program import Resume, Pass, ResumeThrow

from doeff_traverse.effects import Fail, Traverse, Reduce, Zip, Inspect, Skip, SortBy, Take

# Sentinel for Skip — traverse handler checks identity
_SKIPPED = object()
from doeff_traverse.collection import Collection, ItemResult, HistoryEntry


def sequential():
    """Sequential handler for Traverse/Reduce/Zip/Inspect.

    Traverse: runs f(item) sequentially for each item.
              Uses effect.label for per-stage history tracking.
    Reduce: applies f to valid values.
    Zip: joins two collections by index (failure union).
    Inspect: returns ItemResult list.

    Unhandled Fail inside Traverse marks the item as failed.
    """
    from doeff.program import WithHandler as WH
    from doeff.handler_utils import get_inner_handlers
    from doeff_core_effects.effects import Try
    from doeff_vm import Ok, Err

    @do
    def handler(effect, k):
        if isinstance(effect, Skip):
            return _SKIPPED

        if isinstance(effect, Traverse):
            inner_hs = yield get_inner_handlers(k)
            results = []

            # Iterate: Collection (from previous traverse) or raw iterable/generator
            if isinstance(effect.items, Collection):
                items_iter = effect.items.all_items
            else:
                items_iter = (
                    ItemResult(index=i, value=v)
                    for i, v in enumerate(effect.items)
                )

            for item in items_iter:
                # Carry forward already-failed items without re-processing
                if item.failed:
                    results.append(item)
                    continue

                # Build fresh program for this item
                prog = effect.f(item.value)
                # Reinstall inner handlers + this handler for nested Traverse
                for h in inner_hs:
                    prog = WH(h, prog)
                prog = WH(handler, prog)

                # Wrap in Try to catch unhandled failures per item
                @do
                def attempt():
                    from doeff_core_effects.handlers import try_handler
                    value = yield WH(try_handler, Try(prog))
                    return value

                result = yield attempt()
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
                    prog = WH(h, prog)
                prog = WH(handler, prog)
                acc = yield prog
            return (yield Resume(k, acc))

        if isinstance(effect, Zip):
            a = Collection.from_iterable(effect.a)
            b = Collection.from_iterable(effect.b)
            results = []
            for item_a, item_b in zip(a.all_items, b.all_items):
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

    return handler


def parallel(concurrency=10):
    """Parallel handler for Traverse/Reduce/Zip/Inspect.

    Traverse: spawns up to `concurrency` tasks concurrently via Spawn/Gather.
    Reduce/Zip/Inspect: same as sequential (these are inherently sequential).

    Items from generators are materialized before spawning.
    """
    from doeff.program import WithHandler as WH
    from doeff.handler_utils import get_inner_handlers
    from doeff_core_effects.effects import Try
    from doeff_core_effects.scheduler import Spawn, Gather
    from doeff_core_effects.scheduler import (
        CreateSemaphore, AcquireSemaphore, ReleaseSemaphore,
    )
    from doeff_vm import Ok, Err

    @do
    def handler(effect, k):
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
                        prog = WH(h, prog)
                    prog = WH(handler, prog)

                    @do
                    def attempt():
                        from doeff_core_effects.handlers import try_handler
                        value = yield WH(try_handler, Try(prog))
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
                    prog = WH(h, prog)
                prog = WH(handler, prog)
                acc = yield prog
            return (yield Resume(k, acc))

        if isinstance(effect, Zip):
            a = Collection.from_iterable(effect.a)
            b = Collection.from_iterable(effect.b)
            results = []
            for item_a, item_b in zip(a.all_items, b.all_items):
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

    return handler


@do
def fail_handler(effect, k):
    """Default Fail handler: raises the cause as an exception.

    Converts unhandled Fail effects into Python exceptions.
    This is the fail-fast behavior.
    """
    if isinstance(effect, Fail):
        exc = effect.cause if isinstance(effect.cause, BaseException) else RuntimeError(str(effect.cause))
        return (yield ResumeThrow(k, exc))
    yield Pass(effect, k)


@do
def normalize_to_none(effect, k):
    """Fail handler: resume with None at the fail site.

    The computation continues with None as the substitute value.
    """
    if isinstance(effect, Fail):
        return (yield Resume(k, None))
    yield Pass(effect, k)
