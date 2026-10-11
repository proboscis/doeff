"""
Handlers for doeff-traverse effects.

sequential(): default handler — runs Traverse sequentially, Reduce/Zip/Inspect directly.
normalize_to_none(): Fail handler — resumes with None at fail site.
progress_bars(): ItemOutcome handler — one progress bar per Traverse.
"""

import types
from collections.abc import Sized
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from doeff_vm import WithBoundaries

from doeff import do, handler_effect_types
from doeff.program import GetHandlers, Pass, Resume, ResumeThrow, WithHandlerType
from doeff.program import handler as _program_handler
from doeff_traverse.collection import Collection, HistoryEntry, ItemResult
from doeff_traverse.effects import (
    Fail,
    Inspect,
    ItemOutcome,
    Reduce,
    Skip,
    SortBy,
    Take,
    Traverse,
    Zip,
)

# The effects every collection handler below answers. The VM skips the handler
# for any other effect without entering Python (doeff_vm._effect_types) — the
# same as the handler's own trailing Pass. ItemOutcome is answered (with None)
# only when no handler between the Traverse and this one took the report.
CollectionEffect = Skip | Traverse | Reduce | Zip | Inspect | SortBy | Take | ItemOutcome

# Sentinel for Skip — traverse handler checks identity
_SKIPPED = object()


def _joined_history(history_a: list, history_b: list) -> list:
    """The history of a Zip item: a's entries, then b's entries that a does not already hold.

    A Traverse over a collection carries each item's history entries forward as the same
    objects, so when b was built from a (ex07's Zip(signals, prices)) or both from one
    collection, b repeats steps a holds. An entry is one step that ran once, so a shared
    entry is kept once (card ki-7be8e95f45eb). Entries are compared by identity, not
    equality: two independent Traverses with one label give equal entries for two steps.
    """
    held = {id(entry) for entry in history_a}
    return history_a + [entry for entry in history_b if id(entry) not in held]


# Where _names_item_outcome keeps its answer on a plain-function handler: every Traverse
# asks again about the same handlers, and reading the annotation costs ~2.6 µs a handler
# (the VM keeps its own reading on the function the same way — doeff_vm._effect_types).
_NAMES_ITEM_OUTCOME_ATTR = "__doeff_traverse_names_item_outcome__"


def _names_item_outcome(raw_handler: object) -> bool:
    """Whether a handler names ItemOutcome (or a subclass) in its effect parameter's
    annotation — read the way the VM reads which effects reach it. One admitting every
    effect (None: no annotation) does not name it."""
    plain = type(raw_handler) is types.FunctionType
    if plain:
        known = raw_handler.__dict__.get(_NAMES_ITEM_OUTCOME_ATTR)
        if known is not None:
            return known
    admitted = handler_effect_types(raw_handler)
    names = admitted is not None and any(
        isinstance(cls, type) and issubclass(cls, ItemOutcome) for cls in admitted
    )
    if plain:
        setattr(raw_handler, _NAMES_ITEM_OUTCOME_ATTR, names)
    return names


def _report_boundaries(
    inner_handlers: list[object],
) -> list[tuple[Literal["handler"], object]] | None:
    """Where a Traverse handler performs ItemOutcome: the handlers between the Traverse
    and itself (``inner_handlers``, innermost first) as WithBoundaries entries — or None
    when none of them names ItemOutcome, so the Traverse reports nothing (ItemOutcome)."""
    for named in inner_handlers:  # a loop, not any(<generator>): asked once per Traverse
        if _names_item_outcome(named):
            return [("handler", h) for h in inner_handlers]
    return None


def sequential():  # noqa: PLR0915 - baseline cleanup keeps existing control flow unchanged
    """Sequential handler for Traverse/Reduce/Zip/Inspect.

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

    Each item's outcome is reported with ItemOutcome when a handler between the Traverse
    and this one names it (see ItemOutcome); an iterable without a length is then read
    to its end at the first item, to know the total.
    """
    from doeff_core_effects.handlers import try_handler

    from doeff.handler_utils import get_inner_handlers, inner_of
    @do
    def handler(effect: CollectionEffect, k):  # noqa: PLR0911, PLR0912, PLR0915 - baseline cleanup keeps existing control flow unchanged
        if isinstance(effect, Skip):
            return _SKIPPED

        if isinstance(effect, ItemOutcome):
            # No handler between the Traverse and this one took the report.
            return (yield Resume(k, None))

        if isinstance(effect, Traverse):
            # One installer per inner handler for the whole Traverse, not one per item —
            # read when the first item runs (a Traverse with no item to run reads none),
            # from GetHandlers itself rather than through get_inner_handlers' @do call
            # (agora-redesign #2958). The same read decides whether outcomes are reported,
            # so a Traverse with no item to run reports none either.
            inner_installs = None
            # Set only when outcomes are reported (a Traverse nobody counts computes none).
            report_under, this_run, total = None, None, 0
            results = []

            # Iterate: Collection (from previous traverse) or raw iterable/generator
            if isinstance(effect.items, Collection):
                items_iter = iter(effect.items.all_items)
            else:
                items_iter = (
                    ItemResult(index=i, value=v)
                    for i, v in enumerate(effect.items)
                )

            while (item := next(items_iter, None)) is not None:
                if item.failed:
                    # Carry forward already-failed items without re-processing
                    result, outcome = item, "carried"
                else:
                    if inner_installs is None:
                        inner_handlers = inner_of((yield GetHandlers(k)))
                        inner_installs = [_program_handler(h) for h in inner_handlers]
                        report_under = _report_boundaries(inner_handlers)
                        if report_under is not None:
                            # A report carries this run's identity and its item count; an
                            # iterable without a length is read to its end now to count it.
                            this_run = object()
                            match effect.items:
                                case Collection():
                                    total = len(effect.items.all_items)
                                case Sized():
                                    total = len(effect.items)
                                case _:
                                    rest = list(items_iter)
                                    total = len(results) + 1 + len(rest)
                                    items_iter = iter(rest)
                            # The items carried before this one, reported now that it is known.
                            for carried in results:
                                yield WithHandlerType(handler, WithBoundaries(
                                    report_under,
                                    ItemOutcome(this_run, effect.label, total, "carried", carried),
                                ))
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
                        result, outcome = ItemResult(
                            index=item.index,
                            value=error,
                            failed=True,
                            history=item.history + [HistoryEntry(
                                stage=effect.label,
                                event="failed",
                                detail=str(error),
                            )],
                        ), "failed"
                    else:
                        if value is _SKIPPED:
                            result, outcome = ItemResult(
                                index=item.index,
                                value=item.value,
                                failed=True,
                                history=item.history + [HistoryEntry(stage=effect.label, event="skipped")],
                            ), "skipped"
                        else:
                            result, outcome = ItemResult(
                                index=item.index,
                                value=value,
                                history=item.history + [HistoryEntry(stage=effect.label, event="ok")],
                            ), "ok"
                results.append(result)
                if report_under is not None:
                    # Under the inner handlers (where a progress handler sits), then under
                    # this handler, which answers None when none of them takes it.
                    yield WithHandlerType(handler, WithBoundaries(
                        report_under, ItemOutcome(this_run, effect.label, total, outcome, result),
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
                history = _joined_history(item_a.history, item_b.history)
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

    `concurrency` bounds the items of one Traverse. Each nested From (or for/do bound in
    an item) is its own Traverse with its own bound, so k nested From binds can run up to
    concurrency**k leaf items at once; bound a leaf resource in the handler of its effect.

    Each item's outcome is reported with ItemOutcome when a handler between the Traverse
    and this one names it (see ItemOutcome): carried items first, then each run item from
    its own task as it finishes.
    """
    from doeff_core_effects.effects import Try
    from doeff_core_effects.scheduler import (
        AcquireSemaphore,
        CreateSemaphore,
        Gather,
        ReleaseSemaphore,
        Spawn,
    )
    from doeff_vm import Ok

    from doeff.handler_utils import get_inner_handlers
    @do
    def handler(effect: CollectionEffect, k):  # noqa: PLR0911, PLR0912, PLR0915 - baseline cleanup keeps existing control flow unchanged
        if isinstance(effect, Skip):
            return _SKIPPED

        if isinstance(effect, ItemOutcome):
            # No handler between the Traverse and this one took the report.
            return (yield Resume(k, None))

        if isinstance(effect, Traverse):
            inner_hs = yield get_inner_handlers(k)
            report_under = _report_boundaries(inner_hs)
            this_run = object()

            # Collect items (must materialize for parallel dispatch)
            if isinstance(effect.items, Collection):
                all_items = effect.items.all_items
            else:
                all_items = [
                    ItemResult(index=i, value=v)
                    for i, v in enumerate(effect.items)
                ]
            total = len(all_items)

            # Separate failed (carry forward) from active
            carry_forward = [item for item in all_items if item.failed]
            active_items = [item for item in all_items if not item.failed]

            # A Traverse with no item to run reports none (as sequential, which reads no
            # inner handlers then).
            if not active_items:
                return (yield Resume(k, Collection(carry_forward)))

            if report_under is not None:
                for item in carry_forward:
                    yield WithHandlerType(handler, WithBoundaries(
                        report_under, ItemOutcome(this_run, effect.label, total, "carried", item),
                    ))

            # Create semaphore for concurrency limiting
            sem = yield CreateSemaphore(concurrency)

            # Spawn a task per active item; each answers the item's ItemResult
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
                    if not isinstance(result, Ok):
                        done, outcome = ItemResult(
                            index=item.index,
                            value=result.error,
                            failed=True,
                            history=item.history + [HistoryEntry(
                                stage=effect.label,
                                event="failed",
                                detail=str(result.error),
                            )],
                        ), "failed"
                    elif result.value is _SKIPPED:
                        done, outcome = ItemResult(
                            index=item.index,
                            value=item.value,
                            failed=True,
                            history=item.history + [HistoryEntry(stage=effect.label, event="skipped")],
                        ), "skipped"
                    else:
                        done, outcome = ItemResult(
                            index=item.index,
                            value=result.value,
                            history=item.history + [HistoryEntry(stage=effect.label, event="ok")],
                        ), "ok"
                    if report_under is not None:
                        yield WithHandlerType(handler, WithBoundaries(
                            report_under, ItemOutcome(this_run, effect.label, total, outcome, done),
                        ))
                    return done

                task = yield Spawn(run_item())
                tasks.append(task)

            # Gather all results; build Collection preserving original index order
            done_items = yield Gather(*tasks)
            results = sorted([*carry_forward, *done_items], key=lambda r: r.index)
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
                history = _joined_history(item_a.history, item_b.history)
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

    Each item's outcome is reported with ItemOutcome when a handler between the Traverse
    and this one names it (see ItemOutcome), from the item's task as it finishes — the
    item that fails too, before its exception reaches Gather. A Traverse that receives a
    failed item raises before running anything and reports nothing.
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

        if isinstance(effect, ItemOutcome):
            # No handler between the Traverse and this one took the report.
            return (yield Resume(k, None))

        if isinstance(effect, Traverse):
            inner_hs = yield get_inner_handlers(k)
            report_under = _report_boundaries(inner_hs)
            this_run = object()

            if isinstance(effect.items, Collection):
                all_items = effect.items.all_items
            else:
                all_items = [
                    ItemResult(index=i, value=v)
                    for i, v in enumerate(effect.items)
                ]
            total = len(all_items)

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

            # Spawn a task per active item; each answers the item's ItemResult
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
                    try:
                        value = yield prog
                    except Exception as error:
                        if report_under is not None:
                            failed = ItemResult(
                                index=item.index,
                                value=error,
                                failed=True,
                                history=item.history + [HistoryEntry(
                                    stage=effect.label,
                                    event="failed",
                                    detail=str(error),
                                )],
                            )
                            yield WithHandlerType(handler, WithBoundaries(
                                report_under, ItemOutcome(this_run, effect.label, total, "failed", failed),
                            ))
                        raise
                    yield ReleaseSemaphore(sem)
                    if value is _SKIPPED:
                        done, outcome = ItemResult(
                            index=item.index,
                            value=item.value,
                            failed=True,
                            history=item.history + [HistoryEntry(stage=effect.label, event="skipped")],
                        ), "skipped"
                    else:
                        done, outcome = ItemResult(
                            index=item.index,
                            value=value,
                            history=item.history + [HistoryEntry(stage=effect.label, event="ok")],
                        ), "ok"
                    if report_under is not None:
                        yield WithHandlerType(handler, WithBoundaries(
                            report_under, ItemOutcome(this_run, effect.label, total, outcome, done),
                        ))
                    return done

                task = yield Spawn(run_item())
                tasks.append(task)

            # Gather — if any task raised, the exception propagates here
            done_items = yield Gather(*tasks)
            results = sorted(done_items, key=lambda r: r.index)
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
                    history=_joined_history(item_a.history, item_b.history),
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


class ProgressBar(Protocol):
    """What progress_bars uses of a bar its make_bar builds (``tqdm.tqdm`` has both)."""

    def update(self, n: int = 1) -> object: ...
    def close(self) -> object: ...


class BarFactory(Protocol):
    """progress_bars' make_bar: builds a bar from a Traverse's item count and label
    (``"traverse"`` when it has none) — ``tqdm.tqdm`` fits."""

    def __call__(self, *, total: int, desc: str) -> ProgressBar: ...


@dataclass(frozen=True)
class _OpenBar:
    """A bar progress_bars opened for a Traverse run, and the items it has counted."""

    bar: ProgressBar
    counted: int


def progress_bars(make_bar: BarFactory):
    """ItemOutcome handler: one progress bar per Traverse, closed once all its items are counted.

    Counts every item the Traverse handler reports — ok, failed, skipped and carried — so a
    bar reaches ``total`` and closes even when items fail. ``make_bar(total=<int>,
    desc=<label or "traverse">)`` builds a bar when a Traverse reports its first item
    (``tqdm.tqdm`` fits); the handler calls its ``update(1)`` per item and ``close()`` at
    ``total``. Install it inside the Traverse handler, like a body handler:
    ``[... (progress-bars tqdm) (parallel 10)]`` (ItemOutcome says why). A Traverse that
    parallel_fail_fast stops keeps its bar open: the items it cancels report nothing.
    """
    # The bars of the Traverse runs (ItemOutcome.traverse) not yet at their total.
    open_bars: dict[object, _OpenBar] = {}

    @do
    def handler(effect: ItemOutcome, k):
        if isinstance(effect, ItemOutcome):
            opened = open_bars.get(effect.traverse) or _OpenBar(
                bar=make_bar(total=effect.total, desc=effect.label or "traverse"), counted=0,
            )
            opened.bar.update(1)
            if opened.counted + 1 >= effect.total:
                opened.bar.close()
                open_bars.pop(effect.traverse, None)
            else:
                open_bars[effect.traverse] = replace(opened, counted=opened.counted + 1)
            return (yield Resume(k, None))
        yield Pass(effect, k)

    return _program_handler(handler)
