"""
Effects for doeff-traverse.

Fail: low-level failure notification. Handler can Resume(k, value) to inject
      a substitute value at the yield site, or Pass to let it raise.

Traverse: applicative functor — apply f to each element of items.
          Handler decides execution strategy (sequential, parallel, etc).

Reduce: aggregate a collection. Handler extracts valid items and applies f.

Zip: item-indexed join of two collections. Handler manages failure union.

Inspect: extract values + per-item history from an opaque collection.
"""

from doeff_vm import EffectBase


class Fail(EffectBase):
    """Failure effect: report a failure at a yield site.

    Handler can Resume(k, substitute_value) to continue,
    or Pass to let it propagate as an exception.

    Args:
        cause: the exception or error object
        **context: additional context (e.g., item index, stage name)
    """

    def __init__(self, cause, **context):
        super().__init__()
        self.cause = cause
        self.context = context

    def __repr__(self):
        ctx = f", {self.context}" if self.context else ""
        return f"Fail({self.cause!r}{ctx})"


class Traverse(EffectBase):
    """Applicative traverse: apply f to each element of items.

    f must be a callable that returns a DoExpr (e.g., a @do function).
    items is an iterable (list, Collection, or any iterable).

    Handler decides: sequential, parallel, error strategy per item.
    Returns an opaque Collection.

    Args:
        f: callable, item -> DoExpr
        items: iterable of items
    """

    def __init__(self, f, items, label=None):
        super().__init__()
        self.f = f
        self.items = items
        self.label = label

    def __repr__(self):
        lbl = f", label={self.label!r}" if self.label else ""
        return f"Traverse({self.f!r}, ...{lbl})"


class Reduce(EffectBase):
    """Fold a collection using f and init.

    f is a kleisli arrow: (acc, item) -> DoExpr[acc].
    Only valid (non-failed) items are folded.

    Args:
        f: kleisli arrow, (acc, item) -> DoExpr[acc]
        init: initial accumulator value
        collection: a Collection (from Traverse) or plain iterable
    """

    def __init__(self, f, init, collection):
        super().__init__()
        self.f = f
        self.init = init
        self.collection = collection

    def __repr__(self):
        return f"Reduce({self.f!r}, {self.init!r}, ...)"


class Zip(EffectBase):
    """Item-indexed join of two collections.

    Items are matched by index. If an item failed in either collection,
    it is marked as failed in the result (failure union).

    Args:
        a: first Collection
        b: second Collection
    """

    def __init__(self, a, b):
        super().__init__()
        self.a = a
        self.b = b

    def __repr__(self):
        return "Zip(..., ...)"


class Inspect(EffectBase):
    """Extract values and per-item history from an opaque Collection.

    Returns a list of ItemResult(index, value, history) for post-hoc analysis.

    Args:
        collection: a Collection
    """

    def __init__(self, collection):
        super().__init__()
        self.collection = collection

    def __repr__(self):
        return "Inspect(...)"


class Skip(EffectBase):
    """Internal: guard (mzero) for comprehension When clauses.

    Emitted by the for/do macro when a When predicate is falsy.
    Caught by the Traverse handler — marks the item as skipped.
    Not intended for direct use.
    """

    def __repr__(self):
        return "Skip()"


class SortBy(EffectBase):
    """Sort a Collection by a key function.

    key is a plain function: item_value -> comparable.
    reverse=True for descending order.

    Args:
        key: function, value -> comparable
        collection: a Collection or iterable
        reverse: sort descending (default False)
    """

    def __init__(self, key, collection, reverse=False):
        super().__init__()
        self.key = key
        self.collection = collection
        self.reverse = reverse

    def __repr__(self):
        return f"SortBy({self.key!r}, ..., reverse={self.reverse})"


class Take(EffectBase):
    """Take the first n items from a Collection.

    Only valid (non-failed/non-skipped) items are counted.
    Failed items are carried forward unchanged.

    Args:
        n: number of items to take
        collection: a Collection or iterable
    """

    def __init__(self, n, collection):
        super().__init__()
        self.n = n
        self.collection = collection

    def __repr__(self):
        return f"Take({self.n}, ...)"
