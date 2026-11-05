"""The sole definition of the JSON value type.

``JsonValue`` is the type of a value as decoded from JSON (``dict`` / ``list`` / string / number / bool / ``None``), and is merely a plain
dict under another name. A value with structure is carried only after being decoded and checked into a type-declared value (doeff-hy's ``defwire`` / ``defrecord``)
(do not let plain dicts be used directly; decode and check as pydantic does). The only things allowed to touch this type are the generic decoder (parse / dump of ``doeff_hy._wire``) and the foundation modules that do the sending and receiving
themselves -- using it elsewhere makes doeff-linter's DOEFF120 an error.

Location: a Python module of doeff-hy (where the decoder lives). doeff-hy cannot import doeff-records (the dependency direction is reversed),
so the former source ``doeff_records._wire`` imports this. It is a Python module because the type checker (pyright) cannot read Hy
modules -- Python readers (the model ``.py`` of agora-controllers) also get the same type from this one place.

When carrying arbitrary JSON whose shape the caller decides (tool arguments and results, memo values of durable runs) without reading inside it, use ``OpaqueJson`` (below) rather than JsonValue
-- a named type with no interface for taking it apart, which DOEFF120 does not count.

Two views (same name, same definition point):
- At type-check time (``TYPE_CHECKING``) it is a recursive type alias -- ``isinstance(v, dict)`` narrows to ``dict[str, JsonValue]``.
- At run time it is a union that can be passed to ``isinstance`` -- the defk contract ``(: x JsonValue)`` is checked with ``isinstance`` at run time,
  so a type alias as a string (forward reference) cannot be passed. ``JsonObject`` is ``dict`` at run time.
"""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias, cast, final

if TYPE_CHECKING:
    from pydantic import GetCoreSchemaHandler
    from pydantic_core import CoreSchema
    from pydantic_core.core_schema import SerializationInfo

    JsonValue: TypeAlias = "dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None"
    JsonObject: TypeAlias = "dict[str, JsonValue]"
else:
    JsonValue = dict | list | str | int | float | bool | None
    JsonObject = dict


@final
@dataclass(frozen=True)
class OpaqueJson:
    """A named type for carrying an arbitrary JSON value whose shape the caller decides, without reading inside it.

    Where to use: values whose shape the carrying side does not know and must not know -- JSON whose shape differs per author, such as tool call arguments and results, memo values of durable runs, and specs.
    A value of fixed shape should be a ``defwire`` type (do not escape into this type).

    Unlike ``JsonValue``, this type has no interface for taking it apart. All it holds is the JSON string ``text`` (minimal serialization -- no separator whitespace,
    UTF-8 kept as is, object field order preserved), and equality is equality of that string. Only the following two read inside:

    - A reader that knows the shape decodes it into a ``defwire`` type with ``parse`` of ``doeff_hy._wire`` (``(parse T opaque)``).
    - A foundation module that does the sending and receiving itself (``:wire-modules`` of doeff-linter DOEFF120) reads it with ``json.loads(opaque.text)``.

    Build one with ``OpaqueJson.of(value)`` (from a JSON value) or ``OpaqueJson.from_text(text)`` (from a JSON string -- ValueError if unreadable).
    If written in a field of a ``defwire`` type, the decoder wraps arbitrary JSON into this type, and when writing returns it to the original JSON value (``dump`` of ``doeff_hy._wire`` treats it as
    a JSON value. ``dump-json`` embeds the string it holds into the output as is without decoding again -- the writer of ``doeff_hy._wire_codec``).

    ``__get_pydantic_core_schema__`` is the hook for when pydantic decodes or writes this type, and pydantic is imported only when pydantic calls it (this module does not
    import it). Users are the comparison tool ``doeff_hy._wire_parity`` and readers that decode directly with pydantic (to be moved to the wire API).
    """

    text: str

    def __post_init__(self) -> None:
        json.loads(self.text)

    @classmethod
    def of(cls, value: "JsonValue") -> "OpaqueJson":
        """Wrap a JSON value (dict / list / string / number / bool / None, and the frozen JSON FrozenMap / tuple too).

        The string is what ``json.dumps`` just wrote, so ``json.loads`` can always read it (values that cannot be written are refused by ``json.dumps`` with
        TypeError / ValueError -- the values accepted, the values refused, and the spelling are the same as through ``cls(text)``). Decoding that string
        again in ``__post_init__`` would only produce the same answer twice, so it is wrapped here without decoding (
        in example 124 of the custodian's contract, 4.2k calls -- 40% of the cost of ``of`` -- were ``json.loads`` re-decoding the string just written).
        A string coming from outside (where ``cls(text)`` is called directly) is decoded and checked by ``__post_init__`` as before.
        """
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_thawed)
        opaque = object.__new__(cls)
        object.__setattr__(opaque, "text", text)
        return opaque

    @classmethod
    def from_text(cls, text: str | bytes) -> "OpaqueJson":
        """Wrap a JSON string (normalized to minimal serialization). ValueError if it cannot be read as JSON."""
        return cls.of(json.loads(text))

    @property
    def encoded_size(self) -> int:
        """Number of UTF-8 bytes of the minimal serialization (the check of a size limit -- measured without reading inside)."""
        return len(self.text.encode("utf-8"))

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: "GetCoreSchemaHandler") -> "CoreSchema":
        from pydantic_core import core_schema

        return core_schema.no_info_plain_validator_function(
            _opaque_of,
            json_schema_input_schema=core_schema.any_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(_opaque_value, info_arg=True),
        )


def _thawed(value: object) -> object:
    # Turn frozen JSON (FrozenMap of doeff_hy._frozen) into a form json.dumps can write. json.dumps turns a tuple into an array as is.
    items = getattr(value, "items", None)
    if callable(items):
        return dict(cast("Any", items)())
    raise TypeError(f"not a JSON value: {type(value).__name__}")


def _opaque_of(value: object) -> OpaqueJson:
    return value if isinstance(value, OpaqueJson) else OpaqueJson.of(value)  # type: ignore[arg-type]  # only values the decoder read from JSON come here


def _opaque_value(value: OpaqueJson, info: "SerializationInfo") -> "JsonValue | str":
    # So that the pydantic writer can write OpaqueJson. When the comparison tool (pydantic_dump_json of doeff_hy._wire_parity) passes a record of embedding (one that has admit) in the
    # context, it returns the record's marker instead of the string it holds, and after the output is made the record embeds the string. A write without a record
    # returns the value. If a value that is not OpaqueJson comes, it fails with AttributeError at ``value.text`` -- this is also the "not this arm" when tried as an arm of a union.
    admit = getattr(info.context, "admit", None)
    if callable(admit):
        marker = admit(value.text)
        if isinstance(marker, str):
            return marker
    return json.loads(value.text)
