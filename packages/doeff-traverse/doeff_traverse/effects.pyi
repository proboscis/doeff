"""effects.py の公開面の型(型検査のための宣言・agora-redesign #2321)。

for/do は `(_doeff_traverse_Traverse (fn [x] ((_doeff_do (fn [] 本体)))) items)` に展開される。Traverse を件の型 `_Item`
と答えの型 `_Answer` の総称にすると、pyright は items(`Iterable[_Item]`)から件の引数 x の型を推し、本体の答え
(`Program[_Answer, …]`)から for/do の答え `Collection[_Answer]` を推す — `(<- got (for/do …))` の
`got.valid_values` が `list[_Answer]` に読める。実行時の答えは effects.py の宣言どおり(handlers.py の handler が
Resume に渡す Collection)で、ここは型の宣言だけ。
"""

from collections.abc import Callable, Iterable
from typing import Any, ClassVar, Generic, Never, TypeVar

from _typeshed import SupportsRichComparison
from doeff_vm import EffectBase

from doeff import Program
from doeff_traverse.collection import Collection, ItemResult

_Item = TypeVar("_Item")
_Answer = TypeVar("_Answer")
_Acc = TypeVar("_Acc")
_Left = TypeVar("_Left")
_Right = TypeVar("_Right")
_V = TypeVar("_V")

class Fail(EffectBase[Any]):
    """件の失敗の知らせ。答えは handler が選ぶ代わりの値(normalize_to_none は None)なので Any(effects.py と同じ)。"""

    cause: object
    context: dict[str, object]
    def __init__(self, cause: object, **context: object) -> None: ...

class Traverse(EffectBase[Collection[_Answer]], Generic[_Item, _Answer]):
    """items の各件に f を当てる(順・並列は handler が決める)。答えは f の答えを有効な件の値に持つ Collection。"""

    # f が作る Program を出した所の handler の下で走らせる宣言(閉じの検が f の本体を出した所で読む・agora-redesign #2973)。
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
    """有効な件を f で畳む。答えは init の型。"""

    # Traverse と同じく、f が作る Program を出した所の handler の下で走らせる宣言(agora-redesign #2973)。
    __doeff_runs_carried__: ClassVar[frozenset[str]]
    f: Callable[[_Acc, _Item], Program[_Acc, Any]]
    init: _Acc
    collection: Iterable[_Item]
    def __init__(
        self, f: Callable[[_Acc, _Item], Program[_Acc, Any]], init: _Acc, collection: Iterable[_Item]
    ) -> None: ...

class Zip(EffectBase[Collection[tuple[_Left, _Right]]], Generic[_Left, _Right]):
    """2 つの列を件の位置で組む(どちらかで失敗した件は失敗)。有効な件の値は組 (a の値, b の値)。"""

    a: Iterable[_Left]
    b: Iterable[_Right]
    def __init__(self, a: Iterable[_Left], b: Iterable[_Right]) -> None: ...

class Inspect(EffectBase[list[ItemResult]]):
    """列の件ごとの結果と履歴を取り出す。"""

    collection: Iterable[object]
    def __init__(self, collection: Iterable[object]) -> None: ...

class Skip(EffectBase[Never]):
    """for/do の When が偽の件を落とす(handler は続きへ戻らない)。"""

    def __init__(self) -> None: ...

class SortBy(EffectBase[Collection[_V]], Generic[_V]):
    """有効な件を key の順に並べ替える。"""

    key: Callable[[_V], SupportsRichComparison]
    collection: Iterable[_V]
    reverse: bool
    def __init__(
        self, key: Callable[[_V], SupportsRichComparison], collection: Iterable[_V], reverse: bool = False
    ) -> None: ...

class Take(EffectBase[Collection[_V]], Generic[_V]):
    """有効な件の先頭 n 件を取る(失敗した件はそのまま運ぶ)。"""

    n: int
    collection: Iterable[_V]
    def __init__(self, n: int, collection: Iterable[_V]) -> None: ...
