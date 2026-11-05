"""Types for the public surface of frozen.hy (declarations for type checking — at runtime frozen.hy is read).

frozen.hy is a Hy module, so pyright cannot read inside it, and `FrozenMap` and the freezing functions all become Unknown (definitions whose answer returns a FrozenMap and
reads of values holding a FrozenMap in a field get reportUnknown* that the writer cannot fix). The types are declared here.

- FrozenMap is an immutable mapping from string keys to values V. The implementation is a subtype of `Mapping[str, V]` (the key type is fixed to string,
  and there is one type argument, the value type V — in the form `(get FrozenMap TableDecl)`). A `FrozenMap` written without a type argument is FrozenMap[object]
  (a mapping regardless of its values — most annotations in the implementation take this form). Being an immutable mapping, it is covariant in the value type
  (FrozenMap[int] can be passed as FrozenMap[object] — the same as Mapping).
- freeze-json-text reads a JSON string and freezes it deeply. The answer is always FrozenJson (the Any answer of json.loads is fixed to typed
  JSON at the point of reading).
- freeze-json / thaw-json deeply freeze / restore a JSON value. When given a JSON value (JsonIn), the answer is typed JSON
  (freezing = FrozenJson, restoring = ThawedJson), so the types of the mapping contents are not lost. A non-JSON value is
  returned as is by the implementation, so the answer then is object (runtime behavior is unchanged — declaration only).
- frozen-json-object is a FrozenMap with the mapping deeply frozen. When given a JSON mapping, its contents are FrozenJson.
- frozen-map-of copies a mapping shallowly (the inner values stay as they are — it carries the value type of the mapping). A non-mapping value raises TypeError at runtime.
"""

from collections.abc import ItemsView, Iterable, Iterator, Mapping, Sequence, ValuesView
from typing import overload

from typing_extensions import TypeAlias, TypeVar

_V = TypeVar("_V", covariant=True, default=object)
_W = TypeVar("_W")

class FrozenMap(Mapping[str, _V]):
    """An immutable mapping from string keys to values (copied at construction, immutable afterwards)."""

    def __init__(self, source: Mapping[str, _V] | Iterable[tuple[str, _V]] | None = None) -> None: ...
    def __getitem__(self, key: str) -> _V: ...
    def __iter__(self) -> Iterator[str]: ...
    def __len__(self) -> int: ...
    def items(self) -> ItemsView[str, _V]: ...
    def values(self) -> ValuesView[_V]: ...
    def __hash__(self) -> int: ...
    def updated(self, changes: Mapping[str, _W]) -> FrozenMap[_V | _W]: ...

#: The JSON value accepted by freeze-json (mapping = string keys, sequence = list / tuple, leaf).
JsonIn: TypeAlias = Mapping[str, JsonIn] | Sequence[JsonIn] | str | int | float | bool | None
#: The answer of freeze-json: a JSON value deeply frozen, with mappings as FrozenMap and sequences as tuple.
FrozenJson: TypeAlias = FrozenMap[FrozenJson] | tuple[FrozenJson, ...] | str | int | float | bool | None
#: The answer of thaw-json: a JSON value of dict / list that json.dumps accepts.
ThawedJson: TypeAlias = dict[str, ThawedJson] | list[ThawedJson] | str | int | float | bool | None

@overload
def freeze_json(value: JsonIn) -> FrozenJson: ...
@overload
def freeze_json(value: object) -> object: ...
def freeze_json_text(text: str) -> FrozenJson: ...
@overload
def thaw_json(value: JsonIn) -> ThawedJson: ...
@overload
def thaw_json(value: object) -> object: ...
@overload
def frozen_json_object(value: Mapping[str, JsonIn], what: str) -> FrozenMap[FrozenJson]: ...
@overload
def frozen_json_object(value: object, what: str) -> FrozenMap[object]: ...
@overload
def frozen_map_of(value: Mapping[str, _W], what: str) -> FrozenMap[_W]: ...
@overload
def frozen_map_of(value: object, what: str) -> FrozenMap[object]: ...
