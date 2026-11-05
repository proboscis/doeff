"""Types for the public surface of effects.py (declarations for type checking).

for/do expands to `(_doeff_traverse_Traverse (fn [x] ((_doeff_do (fn [] body)))) items)`. Making Traverse generic over the item type `_Item`
and the answer type `_Answer` lets pyright infer the type of the item argument x from items (`Iterable[_Item]`), and the for/do answer `Collection[_Answer]`
from the body's answer (`Program[_Answer, …]`) — so `got.valid_values` of `(<- got (for/do …))`
reads as `list[_Answer]`. The runtime answer is as declared in effects.py (the Collection the handler in handlers.py passes to
Resume); this file only declares types.
"""

from collections.abc import Callable, Iterable
from typing import Any, ClassVar, Generic, Literal, Never, TypeAlias, TypeVar

from _typeshed import SupportsRichComparison

from doeff import EffectBase, Program
from doeff_traverse._collection import Collection, ItemResult

_Item = TypeVar("_Item")
_Answer = TypeVar("_Answer")
_Acc = TypeVar("_Acc")
_Left = TypeVar("_Left")
_Right = TypeVar("_Right")
_V = TypeVar("_V")

class Fail(EffectBase[Any]):
    """Notice of an item failure. The answer is the replacement value the handler chooses (normalize_to_none gives None), so Any (same as effects.py)."""

    cause: object
    context: dict[str, object]
    def __init__(self, cause: object, **context: object) -> None: ...

class Traverse(EffectBase[Collection[_Answer]], Generic[_Item, _Answer]):
    """Applies f to each item of items (order and parallelism are decided by the handler). The answer is a Collection holding the answers of f as valid item values.

    The Program of f may emit Traverse to the same handler again (nested From, for/do received in the body). The handler runs the item Program under itself and
    does not hold resources spanning Traverse across item Programs. Flattening inner answers is done by expansion (Collection.flatten), and
    the handler does not infer whether it is an inner From (docstring in effects.py).
    """

    # Declaration that the Program built by f runs under the handler at the place it was emitted (the closure check reads it where f's body was emitted).
    __doeff_runs_carried__: ClassVar[frozenset[str]]
    f: Callable[[_Item], Program[_Answer, Any]]
    items: Iterable[_Item]
    label: str | None
    def __init__(
        self,
        f: Callable[[_Item], Program[_Answer, Any]],
        items: Iterable[_Item],
        label: str | None = None,
    ) -> None: ...

class Reduce(EffectBase[_Acc], Generic[_Acc, _Item]):
    """Folds valid items with f. The answer has the type of init."""

    # Same as Traverse, a declaration that the Program built by f runs under the handler at the place it was emitted.
    __doeff_runs_carried__: ClassVar[frozenset[str]]
    f: Callable[[_Acc, _Item], Program[_Acc, Any]]
    init: _Acc
    collection: Iterable[_Item]
    def __init__(
        self, f: Callable[[_Acc, _Item], Program[_Acc, Any]], init: _Acc, collection: Iterable[_Item]
    ) -> None: ...

class Zip(EffectBase[Collection[tuple[_Left, _Right]]], Generic[_Left, _Right]):
    """Pairs two sequences by item position (an item that failed in either fails). The valid item value is the pair (value of a, value of b).

    The item history keeps each step that ran once: after the history of a, add the steps of the history of b that a lacks (steps that b took from a,
    or that both took from one sequence, are not repeated).
    """

    a: Iterable[_Left]
    b: Iterable[_Right]
    def __init__(self, a: Iterable[_Left], b: Iterable[_Right]) -> None: ...

class Inspect(EffectBase[list[ItemResult]]):
    """Extracts the per-item results and history of a sequence."""

    collection: Iterable[object]
    def __init__(self, collection: Iterable[object]) -> None: ...

class Skip(EffectBase[Never]):
    """The When of for/do drops items whose condition is false (the handler does not return to the continuation)."""

    def __init__(self) -> None: ...

# Outcome of one item: ok, failed, skipped are outcomes of items this Traverse ran; carried is an item that failed (or was skipped) in an earlier stage and was carried over without running;
# cancelled is an item that got no outcome because Traverse stopped (parallel_fail_fast failure, task cancellation) (an item that was running and cancelled, or never started).
ItemOutcomeKind: TypeAlias = Literal["ok", "failed", "skipped", "carried", "cancelled"]

class ItemOutcome(EffectBase[None]):
    """The Traverse handler reports the outcome of one item (docstring in effects.py). Emitted under a handler between Traverse and the handler,
    and finally the handler itself answers. The receiver looks and passes it outward (does not answer or throw — breaking this raises ItemOutcomeNotPassedError).
    Emitted only when one of the intermediate handlers names ItemOutcome in an effect argument annotation. A Traverse that runs one or more items
    emits exactly total of them whether it finishes, stops by fail-fast, or is cancelled (stopped items are cancelled)."""

    traverse: object
    label: str | None
    total: int
    outcome: ItemOutcomeKind
    item: ItemResult
    def __init__(
        self, traverse: object, label: str | None, total: int, outcome: ItemOutcomeKind, item: ItemResult
    ) -> None: ...

class ItemOutcomeNotPassedError(RuntimeError):
    """The receiver of ItemOutcome answered or threw back the notice (did not pass it on) — raised where the Traverse handler emitted the notice.
    cause is what the receiver threw, or None when it answered."""

    report: ItemOutcome
    cause: BaseException | None
    def __init__(self, report: ItemOutcome, cause: BaseException | None) -> None: ...

class SortBy(EffectBase[Collection[_V]], Generic[_V]):
    """Sorts valid items in the order of key."""

    key: Callable[[_V], SupportsRichComparison]
    collection: Iterable[_V]
    reverse: bool
    def __init__(
        self, key: Callable[[_V], SupportsRichComparison], collection: Iterable[_V], reverse: bool = False
    ) -> None: ...

class Take(EffectBase[Collection[_V]], Generic[_V]):
    """Takes the first n valid items (failed items are carried through as is)."""

    n: int
    collection: Iterable[_V]
    def __init__(self, n: int, collection: Iterable[_V]) -> None: ...
