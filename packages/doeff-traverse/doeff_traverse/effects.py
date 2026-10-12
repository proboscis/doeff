"""
Effects for doeff-traverse.

Fail: low-level failure notification. Handler can Resume(k, value) to inject
      a substitute value at the yield site, or Pass to let it raise.

Traverse: applicative functor — apply f to each element of items.
          Handler decides execution strategy (sequential, parallel, etc).

Reduce: aggregate a collection. Handler extracts valid items and applies f.

Zip: item-indexed join of two collections. Handler manages failure union.

Inspect: extract values + per-item history from an opaque collection.

ItemOutcome: the Traverse handler's report that one item got its outcome
             (ok, failed, skipped, carried) — what a progress handler counts.

Answer types: each effect declares what its handler answers with (EffectBase[T]),
so ``coll = yield from Traverse(f, items)`` is typed and a Hy ``(<- coll (for/do ...))``
reads ``coll.failed_items`` / ``coll.valid_values`` under the type checker
(agora-redesign #2047). The handlers in handlers.py resume with exactly these values.
The constructors are annotated too, so pyright does not infer each call from its arguments
(measured: with doeff's pyrightconfig, doeff-hy's static expansion of a for/do,
``_doeff_perform(Traverse(step, items, label="n"))`` with a nested ``step``, answered Any
while ``__init__`` was unannotated, and Collection once annotated).
"""

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Literal, Never, TypeVar

from doeff_vm import EffectBase

from doeff_traverse.collection import Collection, ItemResult

if TYPE_CHECKING:
    from _typeshed import SupportsRichComparison

_Acc = TypeVar("_Acc")


class Fail(EffectBase[Any]):
    """Failure effect: report a failure at a yield site.

    Handler can Resume(k, substitute_value) to continue,
    or Pass to let it propagate as an exception.
    The substitute is any value the handler picks (normalize_to_none answers None),
    so the answer type is Any.

    Args:
        cause: the exception or error object
        **context: additional context (e.g., item index, stage name)
    """

    def __init__(self, cause: object, **context: object) -> None:
        super().__init__()
        self.cause = cause
        self.context = context

    def __repr__(self):
        ctx = f", {self.context}" if self.context else ""
        return f"Fail({self.cause!r}{ctx})"


class Traverse(EffectBase[Collection]):
    """Applicative traverse: apply f to each element of items.

    f must be a callable that returns a DoExpr (e.g., a @do function).
    items is an iterable (list, Collection, or any iterable).

    Handler decides: sequential, parallel, error strategy per item.
    Returns an opaque Collection.

    Args:
        f: callable, item -> DoExpr
        items: iterable of items

    The handlers run each Program ``f`` builds where the Traverse was performed (they put
    the inner handlers and themselves back around it), so ``f`` is declared in
    ``__doeff_runs_carried__``: a closure check reads ``f``'s body at the performing site
    (agora-redesign #2973).

    Nesting: the Program ``f`` builds may itself perform Traverse to the same handler — a
    for/do with several From binds does this for every outer item, as does a for/do bound
    inside another one's body. A handler therefore runs each item program under itself and
    does not hold a resource shared across Traverses (one semaphore for the whole handler,
    a batch seat) while an item program runs: the outer item would keep it while waiting
    for its inner items and the scheduler deadlocks. A bound on a leaf resource (an API
    call) belongs to the handler of that leaf effect. Splicing the inner answers is not
    the handler's job either: the for/do expansion applies Collection.flatten, so a
    handler answers every Traverse alike and does not infer whether it is an inner From
    (design check lt-BD5XYC8Z64P5YMHZDEAPKXQXK5).
    """

    __doeff_runs_carried__: ClassVar[frozenset[str]] = frozenset({"f"})

    def __init__(self, f: Callable[..., object], items: Iterable[object], label: str | None = None) -> None:
        super().__init__()
        self.f = f
        self.items = items
        self.label = label

    def __repr__(self):
        lbl = f", label={self.label!r}" if self.label else ""
        return f"Traverse({self.f!r}, ...{lbl})"


class Reduce(EffectBase[_Acc], Generic[_Acc]):
    """Fold a collection using f and init.

    f is a kleisli arrow: (acc, item) -> DoExpr[acc].
    Only valid (non-failed) items are folded.
    Answers the final accumulator, typed as init's type.

    Args:
        f: kleisli arrow, (acc, item) -> DoExpr[acc]
        init: initial accumulator value
        collection: a Collection (from Traverse) or plain iterable

    Like Traverse, the handlers run each Program ``f`` builds where the Reduce was
    performed, so ``f`` is declared in ``__doeff_runs_carried__`` (agora-redesign #2973).
    """

    __doeff_runs_carried__: ClassVar[frozenset[str]] = frozenset({"f"})

    def __init__(self, f: Callable[..., object], init: _Acc, collection: Iterable[object]) -> None:
        super().__init__()
        self.f = f
        self.init = init
        self.collection = collection

    def __repr__(self):
        return f"Reduce({self.f!r}, {self.init!r}, ...)"


class Zip(EffectBase[Collection]):
    """Item-indexed join of two collections.

    Items are matched by index. If an item failed in either collection,
    it is marked as failed in the result (failure union).

    A result item's history holds each step that ran for it once: a's history, then
    the steps of b's history a does not hold — a step shared because b was built from
    a, or both from one collection, is not repeated.

    Args:
        a: first Collection
        b: second Collection
    """

    def __init__(self, a: Iterable[object], b: Iterable[object]) -> None:
        super().__init__()
        self.a = a
        self.b = b

    def __repr__(self):
        return "Zip(..., ...)"


class Inspect(EffectBase[list[ItemResult]]):
    """Extract values and per-item history from an opaque Collection.

    Returns a list of ItemResult(index, value, history) for post-hoc analysis.

    Args:
        collection: a Collection
    """

    def __init__(self, collection: Iterable[object]) -> None:
        super().__init__()
        self.collection = collection

    def __repr__(self):
        return "Inspect(...)"


class Skip(EffectBase[Never]):
    """Internal: guard (mzero) for comprehension When clauses.

    Emitted by the for/do macro when a When predicate is falsy.
    Caught by the Traverse handler — marks the item as skipped.
    The handler never resumes the yield site, so the answer type is Never.
    Not intended for direct use.
    """

    def __repr__(self):
        return "Skip()"


# How one item of a Traverse ended. ok / failed / skipped are this Traverse's own run of
# the item (the history event it records); carried = the item came in failed or skipped
# from an earlier stage, so this Traverse kept it without running f.
ItemOutcomeKind = Literal["ok", "failed", "skipped", "carried", "cancelled"]


class ItemOutcome(EffectBase[None]):
    """The Traverse handler's report that one item of a Traverse got its outcome.

    sequential, parallel and parallel_fail_fast perform it once per item of a Traverse that
    runs at least one item, as soon as the item's outcome is known (parallel: in the order
    items finish; carried items before the first run item are reported when it starts), so a
    progress handler counts every item — failed, skipped and carried ones too — without
    knowing how the handler runs items, and reaches ``total``. A Traverse with no item to run
    (no items, or only carried ones) reports nothing: sequential reads no inner handlers for
    it (agora-redesign #2958). parallel_fail_fast stops at the first failure: the failed
    item is reported, the items it cancels are not.

    Where it goes: the handler performs it under the handlers between the Traverse and the
    Traverse handler (the ones it puts back around every item program), then under itself —
    so a handler that counts outcomes is installed inside the Traverse handler, like a body
    handler: ``[... (progress-bars tqdm) (sequential)]``.

    What a consumer does with it: it looks at the report and passes it on (``Pass(effect,
    k)`` — ``(reperform effect)`` in a ``defhandler`` clause), so every consumer sees every
    report, in any order, and the Traverse handler itself answers it. A consumer neither
    answers it (that would hide it from the consumers further out) nor throws into it (how
    the Traverse runs is the Traverse handler's decision — parallel_fail_fast is the one
    that stops at a failure): the Traverse handler raises ItemOutcomeNotPassedError when the
    report comes back answered by someone else or thrown into (design check
    lt-H07C2RG4T2BM59EENGQWQHSJKH, blind A and B).

    When it is performed: only
    when one of those handlers names ItemOutcome (or a subclass) in the annotation of its
    effect parameter (``doeff.handler_effect_types`` — a ``defhandler`` clause
    ``(ItemOutcome [...] ...)`` names it): reporting adds half to all of an item's own cost
    (sequential, 2026-10-12), so a Traverse nobody counts does not pay for it — it pays one
    read of the inner handlers' annotations, kept on each handler function. A handler that
    admits every effect (no annotation, or ``EffectBase``) does not count as naming it — nor
    does ``Listen(prog, types=(ItemOutcome,))``, whose collector admits every effect.

    Args:
        traverse: identity of this run of a Traverse — the same object for every item it
            reports, a new one for every Traverse performed (nested ones too). Compare
            with ``is`` or use it as a dict key.
        label: the Traverse's label.
        total: how many items this Traverse has, carried ones included (sequential reads
            an iterable without a length to its end to know it, only when reporting).
        outcome: how the item ended (ItemOutcomeKind).
        item: the ItemResult the Traverse answers for this item (index, value or the
            exception, failed, history); for the item parallel_fail_fast stops at, the
            failed ItemResult it would have answered.
    """

    def __init__(
        self,
        traverse: object,
        label: str | None,
        total: int,
        outcome: ItemOutcomeKind,
        item: ItemResult,
    ) -> None:
        super().__init__()
        self.traverse = traverse
        self.label = label
        self.total = total
        self.outcome = outcome
        self.item = item

    def __repr__(self):
        lbl = f", label={self.label!r}" if self.label else ""
        return f"ItemOutcome({self.outcome!r}, index={self.item.index}, total={self.total}{lbl})"


class ItemOutcomeNotPassedError(RuntimeError):
    """A consumer of ItemOutcome answered the report or threw into it instead of passing it on.

    Raised by the Traverse handler where it performed the report (it fails the Traverse:
    the consumer is wrong). It is no item's failure: a Traverse around a nested one lets it
    through instead of keeping it in its Collection as a failed item. ``cause`` is what the
    consumer threw, or None when it answered.
    """

    def __init__(self, report: ItemOutcome, cause: BaseException | None) -> None:
        how = f"threw {type(cause).__name__}: {cause}" if cause is not None else "answered it"
        super().__init__(
            f"a handler took {report!r} and {how} — a consumer of ItemOutcome passes it on "
            "(Pass(effect, k) / (reperform effect)); only the Traverse handler answers it"
        )
        self.report = report
        self.cause = cause


class SortBy(EffectBase[Collection]):
    """Sort a Collection by a key function.

    key is a plain function: item_value -> comparable.
    reverse=True for descending order.

    Args:
        key: function, value -> comparable
        collection: a Collection or iterable
        reverse: sort descending (default False)
    """

    def __init__(self, key: "Callable[..., SupportsRichComparison]", collection: Iterable[object], reverse: bool = False) -> None:
        super().__init__()
        self.key = key
        self.collection = collection
        self.reverse = reverse

    def __repr__(self):
        return f"SortBy({self.key!r}, ..., reverse={self.reverse})"


class Take(EffectBase[Collection]):
    """Take the first n items from a Collection.

    Only valid (non-failed/non-skipped) items are counted.
    Failed items are carried forward unchanged.

    Args:
        n: number of items to take
        collection: a Collection or iterable
    """

    def __init__(self, n: int, collection: Iterable[object]) -> None:
        super().__init__()
        self.n = n
        self.collection = collection

    def __repr__(self):
        return f"Take({self.n}, ...)"
