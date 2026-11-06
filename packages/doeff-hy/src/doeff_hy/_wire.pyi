"""Types for the public surface of wire.hy (declarations for type checking — at runtime wire.hy is read; child of #2279).

wire.hy is a Hy module, so pyright cannot read inside it, and every name in `from doeff_hy._wire import parse` becomes Unknown
(in agora's protocol files the strict red shows up in a form the author cannot fix — one of the sources of 317 → 416 in #2220).
The types are declared here.

- defk (parse, parse-json, parse-as, parse-json-as, dump, dump-json, json-schema) returns a Program when called. The x of `(<- x (parse T raw))`
  becomes `_doeff_perform(parse(T, raw))` (static_types.pyi) in the type-check expansion, and receives the Program's answer type = `T | Malformed`.
- parse / parse-json are generic functions taking a type as an argument: the answer is a value of the passed defwire type T, or Malformed. parse-as / parse-json-as also accept types that are not defwire
  vocabulary types (dict[str, object], str | None, etc.) — pass a plain type and the answer is that type; for a generic type or a union spelling it is Any.
- deff (wire-config, wire-shape) are ordinary functions called when the defwire expansion reads the module.
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
    """The wire shape of one defwire type (names = Python field name → wire name, unknown, wire_type, description = the defwire docstring, resolver codec)."""

    names: dict[str, str]
    unknown: str
    wire_type: type
    description: str | None
    def __init__(
        self, *, names: dict[str, str], unknown: str, wire_type: type, description: str | None = None
    ) -> None: ...
    @property
    def codec(self) -> Codec[Any]: ...

@runtime_checkable
class WireValue(Protocol):
    """A value of a type built with defwire (the type holds a WireShape in __doeff_wire__) — what dump accepts."""

    __doeff_wire__: ClassVar[WireShape]

def wire_config(names: dict[str, str], unknown: str) -> dict[str, object]: ...
def wire_shape(
    wire_type: type, names: dict[str, str], unknown: str, description: str | None = None
) -> WireShape: ...
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
