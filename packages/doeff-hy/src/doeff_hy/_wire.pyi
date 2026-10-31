"""wire.hy の公開面の型(型検査のための宣言 — 実行時は wire.hy を読む・#2279 の子)。

wire.hy は Hy の module なので、pyright は中を読めず、`from doeff_hy._wire import parse` の名が全部 Unknown になる
(agora の protocol の file で strict の赤が書き手に直せない形で出る — #2220 で 317 → 416 の出どころの 1 つ)。
ここで型を宣言する。

- defk(parse・parse-json・parse-as・parse-json-as・dump・dump-json・json-schema)は呼ぶと Program を返す。`(<- x (parse T raw))` の
  x は型検査の展開で `_doeff_perform(parse(T, raw))`(static_types.pyi)になり、Program の答えの型 = `T | Malformed` を受ける。
- parse / parse-json は型を引数に取る総称の関数: 答えは渡した defwire の型 T の値か Malformed。parse-as / parse-json-as は defwire でない
  語彙の型(dict[str, object]・str | None など)も受ける — 素の型を渡せば答えはその型、総称の型や union の綴りなら Any。
- deff(wire-config・wire-shape)は defwire の展開が module を読む時に呼ぶ普通の関数。
"""

from collections.abc import Mapping
from typing import Any, ClassVar, Protocol, TypeVar, overload, runtime_checkable

from doeff import Program
from doeff_hy._json_value import JsonValue, OpaqueJson
from doeff_hy._wire_codec import Codec
from doeff_hy._wire_malformed import Malformed as Malformed
from doeff_hy._wire_malformed import MalformedField as MalformedField

_W = TypeVar("_W")

UNKNOWN_FIELDS: dict[str, str]

class WireShape:
    """defwire の型 1 つの wire の形(names = Python の欄の名 → wire の名・unknown・wire_type・解き手 codec)。"""

    names: dict[str, str]
    unknown: str
    wire_type: type
    def __init__(self, *, names: dict[str, str], unknown: str, wire_type: type) -> None: ...
    @property
    def codec(self) -> Codec[Any]: ...

@runtime_checkable
class WireValue(Protocol):
    """defwire で建てた型の値(型が __doeff_wire__ に WireShape を持つ)— dump の受ける物。"""

    __doeff_wire__: ClassVar[WireShape]

def wire_config(names: dict[str, str], unknown: str) -> dict[str, object]: ...
def wire_shape(wire_type: type, names: dict[str, str], unknown: str) -> WireShape: ...
def parse(
    wire_type: type[_W], raw: JsonValue | Mapping[str, object] | tuple[object, ...] | OpaqueJson
) -> Program[_W | Malformed, Any]: ...
def parse_json(wire_type: type[_W], text: str | bytes) -> Program[_W | Malformed, Any]: ...
@overload
def parse_as(
    shape: type[_W], raw: JsonValue | Mapping[str, object] | tuple[object, ...] | OpaqueJson
) -> Program[_W | Malformed, Any]: ...
@overload
def parse_as(
    shape: object, raw: JsonValue | Mapping[str, object] | tuple[object, ...] | OpaqueJson
) -> Program[Any, Any]: ...
@overload
def parse_json_as(shape: type[_W], text: str | bytes) -> Program[_W | Malformed, Any]: ...
@overload
def parse_json_as(shape: object, text: str | bytes) -> Program[Any, Any]: ...
def dump(value: WireValue) -> Program[JsonValue, Any]: ...
def dump_json(value: WireValue) -> Program[str, Any]: ...
def json_schema(wire_type: type) -> Program[JsonValue, Any]: ...
