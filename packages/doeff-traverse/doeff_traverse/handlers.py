"""
Handlers for doeff-traverse effects.

sequential(): reference handler — runs Traverse sequentially, Reduce/Zip/Inspect directly.
normalize_to_none(): Fail handler — resumes with None at fail site.
progress_bars(): ItemOutcome handler — one progress bar per Traverse.
"""

import types
from collections.abc import Sized
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, Protocol

from doeff import WithBoundaries, do, handler_effect_types
from doeff.handler_utils import reinstaller
from doeff.program import GetHandlers, Pass, Resume, ResumeThrow, WithHandlerType, _named_handler
from doeff.program import handler as _program_handler
from doeff_traverse.collection import Collection, HistoryEntry, ItemResult
from doeff_traverse.effects import (
    Fail,
    Inspect,
    ItemOutcome,
    ItemOutcomeNotPassedError,
    Reduce,
    Skip,
    SortBy,
    Take,
    Traverse,
    Zip,
)

if TYPE_CHECKING:
    from doeff_core_effects import Task

    from doeff import EffectGenerator

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


# What a Traverse handler answers its own ItemOutcome with: _report sees it come back only
# when every consumer passed the report on (ItemOutcome — a consumer neither answers it nor
# throws into it).
_PASSED_ON = object()


@do
def _report(handler, boundaries, report: ItemOutcome):
    """Perform ``report`` under the inner handlers (``boundaries``), then under ``handler`` —
    the Traverse handler itself, which answers _PASSED_ON — and fail the Traverse with
    ItemOutcomeNotPassedError when a consumer answered it or threw into it instead. A cancelled
    parallel task stays cancelled (TaskCancelledError is the scheduler's, not a consumer's)."""
    from doeff_core_effects import TaskCancelledError

    try:
        answer = yield WithHandlerType(handler, WithBoundaries(boundaries, report))
    except TaskCancelledError:
        raise
    except Exception as error:
        raise ItemOutcomeNotPassedError(report, error) from error
    if answer is not _PASSED_ON:
        raise ItemOutcomeNotPassedError(report, None)


def _cancelled(item: ItemResult, label: str | None) -> ItemResult:
    """The ItemResult reported for an item the Traverse stopped before it got an outcome: the
    item as it came in, failed, with a ``cancelled`` history entry."""
    return ItemResult(
        index=item.index,
        value=item.value,
        failed=True,
        history=item.history + [HistoryEntry(stage=label, event="cancelled")],
    )


@do
def _report_cancelled(
    handler, boundaries, traverse: object, label: str | None, total: int, items: list[ItemResult]
) -> "EffectGenerator[None]":
    """Report each of ``items`` — the items a stopping Traverse stopped — as cancelled, so a
    progress consumer counts them and reaches ``total``."""
    for item in items:
        yield _report(handler, boundaries, ItemOutcome(traverse, label, total, "cancelled", _cancelled(item, label)))


class _ItemTasks:
    """The item tasks of one run of a parallel Traverse: the Task of each item spawned so far,
    and which items have their outcome taken — what the Traverse reads when it stops
    (_stop_items). A position is the item's place among the items run."""

    def __init__(self, items: list[ItemResult]) -> None:
        self.items = items
        # The task of the item at each position, None until it is spawned.
        self._mut_tasks: list[Task[ItemResult] | None] = [None] * len(items)
        self._mut_taken: set[int] = set()

    def spawned(self, position: int, task: "Task[ItemResult]") -> None:
        """The task of the item at ``position`` is spawned."""
        self._mut_tasks[position] = task

    @property
    def tasks(self) -> "list[Task[ItemResult]]":
        """The tasks spawned so far, in spawn order."""
        return [task for task in self._mut_tasks if task is not None]

    def take(self, position: int) -> bool:
        """Take the outcome of the item at ``position``: True for the first taker — the item's
        task, which reports what the item ended with, or the stopping Traverse, which reports it
        cancelled — and False for the other, so no item is reported twice."""
        if position in self._mut_taken:
            return False
        self._mut_taken.add(position)
        return True

    def taken(self, position: int) -> bool:
        """Whether the item at ``position`` has its outcome taken: an item task that finds its
        item taken before it runs it was stopped before it started."""
        return position in self._mut_taken

    def take_the_rest(self) -> list[ItemResult]:
        """Take every item whose outcome is not taken yet (spawned or not) and answer them."""
        return [item for position, item in enumerate(self.items) if self.take(position)]


@do
def _stop_items(
    handler, boundaries, traverse: object, label: str | None, total: int, tasks: _ItemTasks
) -> "EffectGenerator[None]":
    """Stop what a parallel Traverse leaves when Gather throws — an item's failure
    (parallel_fail_fast), a broken ItemOutcome contract, an item cancelled or letting
    TaskCancelledError out, or the Traverse's own task being cancelled — before the Traverse throws
    it on: take every item without an outcome, cancel the spawned tasks (a no-op for an ended
    one), wait for each to finish unwinding (Cancel only asks), then report the taken items
    cancelled. No item task outlives the Traverse, and every item gets one outcome. An item a
    task finishes after its item was taken here is not reported again (_ItemTasks.take).
    ``boundaries`` None reports nothing (no consumer, or one broke the ItemOutcome contract)."""
    from doeff_core_effects import Try
    from doeff_core_effects import try_handler
    from doeff_core_effects import Cancel, Wait

    stopped = tasks.take_the_rest()
    for task in tasks.tasks:
        yield Cancel(task)
    for task in tasks.tasks:
        yield try_handler(Try(Wait(task)))
    if boundaries is not None:
        yield _report_cancelled(handler, boundaries, traverse, label, total, stopped)


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

    TaskCancelledError out of an item is no item's failure: the Traverse's task was cancelled
    (or the item let one out), so the Traverse stops there — it reports that item and the
    ones it has not reached as cancelled and throws it on.
    """
    from doeff_core_effects import try_handler
    from doeff_core_effects import TaskCancelledError

    from doeff.handler_utils import get_inner_handlers, inner_of
    @do
    def handler(effect: CollectionEffect, k):  # noqa: PLR0911, PLR0912, PLR0915 - baseline cleanup keeps existing control flow unchanged
        if isinstance(effect, Skip):
            return _SKIPPED

        if isinstance(effect, ItemOutcome):
            # Every consumer between the Traverse and this one passed the report on (_report).
            return (yield Resume(k, _PASSED_ON))

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
                        inner_installs = [reinstaller(h) for h in inner_handlers]
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
                                yield _report(handler, report_under, ItemOutcome(this_run, effect.label, total, "carried", carried))
                    # Build fresh program for this item
                    prog = effect.f(item.value)
                    # Reinstall inner handlers + this handler for nested Traverse,
                    # and try_handler outermost (where the per-item Try used to put it)
                    for install in inner_installs:
                        prog = install(prog)
                    prog = try_handler(install_self(prog))

                    # Catch unhandled failures per item — but not a consumer breaking the
                    # ItemOutcome contract in a nested Traverse, nor a cancel: no item's failure.
                    try:
                        value = yield prog
                    except ItemOutcomeNotPassedError:
                        raise
                    except TaskCancelledError:
                        if report_under is not None:
                            yield _report_cancelled(handler, report_under, this_run, effect.label, total, [item, *items_iter])
                        raise
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
                    yield _report(handler, report_under, ItemOutcome(this_run, effect.label, total, outcome, result))

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
                    prog = reinstaller(h)(prog)
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

    An item's failure stays in the Collection, but TaskCancelledError out of an item is no
    item's failure. When Gather throws — the Traverse's task was cancelled, an item let
    TaskCancelledError out, or a consumer broke the ItemOutcome contract — the Traverse cancels
    the item tasks left, waits for them to finish unwinding, reports their items cancelled
    (not after a broken contract) and throws it on: no item task outlives the Traverse.
    """
    from doeff_core_effects import Try
    from doeff_core_effects import (
        AcquireSemaphore,
        CreateSemaphore,
        Gather,
        ReleaseSemaphore,
        Spawn,
        TaskCancelledError,
    )

    from doeff import Ok
    from doeff.handler_utils import get_inner_handlers
    @do
    def handler(effect: CollectionEffect, k):  # noqa: PLR0911, PLR0912, PLR0915 - baseline cleanup keeps existing control flow unchanged
        if isinstance(effect, Skip):
            return _SKIPPED

        if isinstance(effect, ItemOutcome):
            # Every consumer between the Traverse and this one passed the report on (_report).
            return (yield Resume(k, _PASSED_ON))

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
                    yield _report(handler, report_under, ItemOutcome(this_run, effect.label, total, "carried", item))

            # Create semaphore for concurrency limiting
            sem = yield CreateSemaphore(concurrency)

            # A task per active item; each answers the item's ItemResult
            tasks = _ItemTasks(active_items)

            @do
            def run_item(position: int, item: ItemResult) -> "EffectGenerator[ItemResult]":
                yield AcquireSemaphore(sem)
                if tasks.taken(position):
                    # The Traverse stopped before this item started (_stop_items).
                    raise TaskCancelledError
                prog = effect.f(item.value)
                for h in inner_hs:
                    prog = reinstaller(h)(prog)
                prog = _program_handler(handler)(prog)

                @do
                def attempt():
                    from doeff_core_effects import try_handler
                    value = yield try_handler(Try(prog))
                    return value

                result = yield attempt()
                yield ReleaseSemaphore(sem)
                if not isinstance(result, Ok) and isinstance(result.error, (ItemOutcomeNotPassedError, TaskCancelledError)):
                    # A consumer broke the ItemOutcome contract in a nested Traverse, or the item
                    # was cancelled: no item's failure — the Traverse stops.
                    raise result.error
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
                if tasks.take(position) and report_under is not None:
                    yield _report(handler, report_under, ItemOutcome(this_run, effect.label, total, outcome, done))
                return done

            # Spawn the tasks and gather them; build Collection preserving original index order.
            # When either throws (the Traverse's task can be cancelled while it waits for its turn
            # after a Spawn, too), the item tasks left are stopped first.
            try:
                for position, item in enumerate(active_items):
                    tasks.spawned(position, (yield Spawn(run_item(position, item))))
                done_items = yield Gather(*tasks.tasks)
            except Exception as error:
                stopped_under = None if isinstance(error, ItemOutcomeNotPassedError) else report_under
                yield _stop_items(handler, stopped_under, this_run, effect.label, total, tasks)
                raise
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
                    prog = reinstaller(h)(prog)
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
    If any spawned task raises, Gather propagates the exception immediately. Gather does not
    cancel the other tasks, so the Traverse does: it cancels the item tasks left — running
    ones receive TaskCancelledError, ones not started never run — waits for them to finish
    unwinding, and only then raises the failure. No item task outlives the Traverse, also when
    the caller catches the failure and goes on. An item that a task already queued to run
    finishes before the Traverse wakes still finishes (items that do not wait, concurrency > 1).
    The same holds when the Traverse's own task is cancelled, and TaskCancelledError out of an
    item is no item's failure: the Traverse stops and raises it.

    Use this instead of parallel() when silent failure collection is unacceptable.
    Swap in the interpreter: (parallel :concurrency 40) → (parallel-fail-fast :concurrency 40)

    Each item's outcome is reported with ItemOutcome when a handler between the Traverse
    and this one names it (see ItemOutcome), from the item's task as it finishes — the
    item that fails too, before its exception reaches Gather; then, when the Traverse stops,
    the items it stopped as ``cancelled`` — so a Traverse reports exactly ``total`` outcomes
    however it ends. A Traverse that receives a failed item raises before running anything
    and reports nothing.
    """
    from doeff_core_effects import (
        AcquireSemaphore,
        CreateSemaphore,
        Gather,
        ReleaseSemaphore,
        Spawn,
        TaskCancelledError,
    )

    from doeff.handler_utils import get_inner_handlers
    @do
    def handler(effect: CollectionEffect, k):  # noqa: PLR0911, PLR0912, PLR0915 - baseline cleanup keeps existing control flow unchanged
        if isinstance(effect, Skip):
            return _SKIPPED

        if isinstance(effect, ItemOutcome):
            # Every consumer between the Traverse and this one passed the report on (_report).
            return (yield Resume(k, _PASSED_ON))

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

            # A task per active item; each answers the item's ItemResult
            tasks = _ItemTasks(active_items)

            @do
            def run_item(position: int, item: ItemResult) -> "EffectGenerator[ItemResult]":
                yield AcquireSemaphore(sem)
                if tasks.taken(position):
                    # The Traverse stopped before this item started (_stop_items).
                    raise TaskCancelledError
                prog = effect.f(item.value)
                for h in inner_hs:
                    prog = reinstaller(h)(prog)
                prog = _program_handler(handler)(prog)
                # NO Try wrapper — exceptions propagate to Gather
                try:
                    value = yield prog
                except (ItemOutcomeNotPassedError, TaskCancelledError):
                    # A consumer broke the ItemOutcome contract in a nested Traverse, or the item
                    # was cancelled: no item's failure — the Traverse stops.
                    raise
                except Exception as error:
                    if tasks.take(position) and report_under is not None:
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
                        yield _report(handler, report_under, ItemOutcome(this_run, effect.label, total, "failed", failed))
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
                if tasks.take(position) and report_under is not None:
                    yield _report(handler, report_under, ItemOutcome(this_run, effect.label, total, outcome, done))
                return done

            # Spawn the tasks and gather them — if any task raised, the exception propagates here.
            # Gather does not cancel the other tasks, so when it (or a Spawn — the Traverse's task
            # can be cancelled while it waits for its turn after one) throws, the item tasks left
            # are stopped first.
            try:
                for position, item in enumerate(active_items):
                    tasks.spawned(position, (yield Spawn(run_item(position, item))))
                done_items = yield Gather(*tasks.tasks)
            except Exception as error:
                stopped_under = None if isinstance(error, ItemOutcomeNotPassedError) else report_under
                yield _stop_items(handler, stopped_under, this_run, effect.label, total, tasks)
                raise
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
                    prog = reinstaller(h)(prog)
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


fail_handler = _named_handler(_fail_handler, "fail_handler")


@do
def _normalize_to_none(effect: Fail, k):
    """Fail handler: resume with None at the fail site.

    The computation continues with None as the substitute value.
    """
    if isinstance(effect, Fail):
        return (yield Resume(k, None))
    yield Pass(effect, k)


normalize_to_none = _named_handler(_normalize_to_none, "normalize_to_none")


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

    Counts every item the Traverse handler reports — ok, failed, skipped, carried and
    cancelled — so a bar reaches ``total`` and closes however the Traverse ends: items failing,
    parallel_fail_fast stopping at a failure, or the Traverse being cancelled. ``make_bar(total=<int>,
    desc=<label or "traverse">)`` builds a bar when a Traverse reports its first item
    (``tqdm.tqdm`` fits); the handler calls its ``update(1)`` per item that got an outcome and
    ``close()`` at ``total``. A cancelled item (one the Traverse stopped) is counted without
    ``update``, so the bar of a stopped Traverse closes at n < total — as tqdm's own loop
    leaves its bar when the loop raises. Install it inside the Traverse handler, like a body
    handler: ``[... (progress-bars tqdm) (parallel 10)]`` (ItemOutcome says why). It passes
    each report on, as every consumer does, so other consumers (a second bar, a failure log)
    sit on either side of it.
    """
    # The bars of the Traverse runs (ItemOutcome.traverse) not yet at their total.
    open_bars: dict[object, _OpenBar] = {}

    @do
    def handler(effect: ItemOutcome, k):
        if isinstance(effect, ItemOutcome):
            opened = open_bars.get(effect.traverse) or _OpenBar(
                bar=make_bar(total=effect.total, desc=effect.label or "traverse"), counted=0,
            )
            if effect.outcome != "cancelled":
                opened.bar.update(1)
            if opened.counted + 1 >= effect.total:
                opened.bar.close()
                open_bars.pop(effect.traverse, None)
            else:
                open_bars[effect.traverse] = replace(opened, counted=opened.counted + 1)
        yield Pass(effect, k)

    return _program_handler(handler)
