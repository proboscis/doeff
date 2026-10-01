"""wire.hy の公開面の型(型検査のための宣言 — 実行時は wire.hy を読む・agora-redesign #2282・#2279 の子)。

wire.hy は Hy の module なので、pyright は中を読めず、`from doeff_hy.wire import parse` の名が全部 Unknown になる
(agora の protocol の file で strict の赤が書き手に直せない形で出る — #2220 で 317 → 416 の出どころの 1 つ)。
ここで型を宣言する。

- defk(parse・parse-json・dump・dump-json・json-schema・malformed-of)は呼ぶと Program を返す。`(<- x (parse T raw))` の
  x は型検査の展開で `_doeff_perform(parse(T, raw))`(static_types.pyi)になり、Program の答えの型 = `T | Malformed` を受ける。
- parse / parse-json は型を引数に取る総称の関数: 答えは渡した defwire の型 T の値か Malformed。
- deff(wire-config・wire-shape)は defwire の展開が module を読む時に呼ぶ普通の関数。
"""

from collections.abc import Mapping
from typing import Any, ClassVar, Protocol, TypeVar, runtime_checkable

from pydantic import ConfigDict, TypeAdapter, ValidationError

from doeff import Program
from doeff_hy.json_value import JsonValue, OpaqueJson

_W = TypeVar("_W")

UNKNOWN_FIELDS: dict[str, str]

class MalformedField:
    """形の合わない所 1 つ(field = 欄の場所・reason = なぜ)。"""

    field: str
    reason: str
    def __init__(self, *, field: str, reason: str) -> None: ...

class Malformed:
    """外から来た JSON が型の約束の形でない(wire-type = 解こうとした型の名・fields = 合わない所)。"""

    wire_type: str
    fields: tuple[MalformedField, ...]
    def __init__(self, *, wire_type: str, fields: tuple[MalformedField, ...]) -> None: ...

class WireShape:
    """defwire の型 1 つの wire の形(names = Python の欄の名 → wire の名・unknown・adapter)。"""

    names: dict[str, str]
    unknown: str
    adapter: TypeAdapter[Any]
    def __init__(self, *, names: dict[str, str], unknown: str, adapter: TypeAdapter[Any]) -> None: ...

@runtime_checkable
class WireValue(Protocol):
    """defwire で建てた型の値(型が __doeff_wire__ に WireShape を持つ)— dump の受ける物。"""

    __doeff_wire__: ClassVar[WireShape]

def wire_config(names: dict[str, str], unknown: str) -> ConfigDict: ...
def wire_shape(wire_type: type, names: dict[str, str], unknown: str) -> WireShape: ...
def malformed_of(wire_type: type, error: ValidationError) -> Program[Malformed, Any]: ...
def parse(
    wire_type: type[_W], raw: JsonValue | Mapping[str, object] | tuple[object, ...] | OpaqueJson
) -> Program[_W | Malformed, Any]: ...
def parse_json(wire_type: type[_W], text: str | bytes) -> Program[_W | Malformed, Any]: ...
def dump(value: WireValue) -> Program[JsonValue, Any]: ...
def dump_json(value: WireValue) -> Program[str, Any]: ...
def json_schema(wire_type: type) -> Program[JsonValue, Any]: ...
