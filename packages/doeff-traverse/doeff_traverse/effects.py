"""
Effects for doeff-traverse.

Fail: low-level failure notification. Handler can Resume(k, value) to inject
      a substitute value at the yield site, or Pass to let it raise.

Traverse: applicative functor — apply f to each element of items.
          Handler decides execution strategy (sequential, parallel, etc).

Reduce: aggregate a collection. Handler extracts valid items and applies f.

Zip: item-indexed join of two collections. Handler manages failure union.

Inspect: extract values + per-item history from an opaque collection.

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
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Never, TypeVar

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
