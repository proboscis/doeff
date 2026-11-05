"""Own decoder type tree for defwire: reader, writer, and JSON Schema.

Decodes JSON into defwire typed values strictly and checks them without reading pydantic, writes values back to JSON with the same tree, and emits JSON Schema. The contract is the design record
``docs/design/defwire-decoder/design.md`` (section 2: per-type decoding and writing; section 3: shared rules; section 3b: writing and JSON Schema; appendix B:
arm names). It accepts what the current pydantic strict mode accepts and rejects what it rejects (no string-to-number, no bool-to-number, an integer is accepted in a float field, an array
in a tuple field, defenum by value spelling, only the wire names of :names, :unknown reject / ignore, a failing :check is Malformed). There is exactly one difference:
the catch-all union arms (OpaqueJson, object) are taken only when no other arm accepts (section 3, rule 4). Writing produces the same JSON as the current pydantic dump and
dump-json (the differences are the three rows of the section 3b table, all places where pydantic wrote the Python field name or the fields inside OpaqueJson).

Usage:

    codec = codec_of(LandingRow)          # builds the type tree once on first use and remembers it on the type (not rebuilt afterwards)
    codec.decode_json(text)               # from a JSON string (bytes too); result = a LandingRow value or Malformed
    codec.decode(raw)                     # from a JSON value (frozen FrozenMap, tuple, and OpaqueJson also accepted)
    codec.encode(row)                     # to a JSON value (fields use wire names; fields equal to their default are omitted, as in the current dump)
    codec.encode_json(row)                # to a JSON string (OpaqueJson fields splice in the string they hold, as in the current dump-json)
    codec.schema()                        # JSON Schema (as in the current json-schema)

- The type tree is a tree of per-field-type checkers (``_Node``). A defwire type remembers a single ``_WireNode`` on the type, and nested fields reference the child checkers
  (they are not rebuilt per parent). Types outside the vocabulary, unresolvable forward references, and type cycles are rejected with TypeError at build time (never passed silently).
- wire.hy (parse, parse-json, dump, dump-json, json-schema) calls this tree (replacing pydantic's TypeAdapter after
  the parity test (doeff_hy/_wire_parity.py, tests/test_wire_parity.py) showed 0 mismatches against pydantic over all defwire types in doeff and agora-controllers).
- Table row types (plain defrecord) are built by ``row_codec_of`` with the same checkers (fields use Python names, unknown fields are discarded, writing emits all fields;
  used by typed.hy in doeff-records; section 6 of the design record).
- decode and encode_json read and write through a fast path first (the input is not copied again; a reader and writer with the fields fully unrolled are built once per type; defrecord
  types are created without going through the constructor). The answer is the same as the existing path, and anything the fast path does not accept falls back to the existing path (see the note in the section "checkers" and
  tests/test_wire_codec_fast.py).
- This module does not import pydantic (it reads only the standard modules plus doeff_hy._json_value, doeff_hy._wire_malformed, and doeff_hy._record_runtime;
  ``tests/test_wire_codec.py`` and ``tests/test_wire_codec_write.py`` check the import closure).
"""

import dataclasses
import enum
import inspect
import json
import json.encoder
import math
import operator
import re
import sys
import threading
import types
import typing
from collections import Counter
from collections.abc import Callable, Collection, Mapping
from typing import Any, Final, ForwardRef, Generic, TypeAlias, TypeGuard, TypeVar, cast, final

# Imported with a leading underscore: the only public names of this module are Codec, codec_of, and row_codec_of (Malformed comes from wire_malformed, OpaqueJson from json_value).
from doeff_hy._json_value import OpaqueJson as _OpaqueJson
from doeff_hy._record_runtime import RECORD_PARAMS as _RECORD_PARAMS
from doeff_hy._wire_malformed import Malformed as _Malformed
from doeff_hy._wire_malformed import MalformedField as _MalformedField

_T = TypeVar("_T")

#: One error: (path of keys, kind, msg). The path is wire names, array indexes, dict keys, and union arm names (empty for the whole value).
_Problem = tuple[tuple[str | int, ...], str, str]

#: A written JSON value and a JSON Schema value (the answers of a checker's encode and schema). _Raw is an OpaqueJson string while being written to a string;
#: _RefMark is a $ref target before building finishes. Neither remains in a Codec answer.
_Json: TypeAlias = (
    "dict[str, _Json] | list[_Json] | str | int | float | bool | None | _Raw | _RefMark"
)
#: One JSON Schema shape (object).
_Schema: TypeAlias = "dict[str, _Json]"


class _RefusedError(Exception):
    """A checker rejected the value (carries the list of errors; it never leaves the tree, and Codec maps it to Malformed)."""

    __slots__ = ("problems",)

    def __init__(self, problems: list[_Problem]) -> None:
        super().__init__()
        self.problems = problems


class _SlowPathError(Exception):
    """The fast path (_Node.fast) hit something it cannot read as a plain value (a non-frozen derived type, a lone surrogate, nesting that is too deep) --
    Codec re-reads with the existing path (copy with _plain, then decode)."""

    __slots__ = ()


def _refuse(kind: str, msg: str) -> _RefusedError:
    """One error when a checker rejects a value (the path is relative to the checker; the caller prepends the key)."""
    return _RefusedError([((), kind, msg)])


def _under(key: str | int, problems: list[_Problem]) -> list[_Problem]:
    """Prepend the key the parent saw (field wire name, index, key, arm name) to the child error paths."""
    return [((key, *loc), kind, msg) for loc, kind, msg in problems]


def _expected(values: tuple[str, ...]) -> str:
    # pydantic spelling: wrap each value in repr, join the last two with " or " and the earlier ones with ", " ('a', 'b' or 'c').
    """How to list the accepted values in the rejection msg of defenum and Literal (matches the pydantic spelling)."""
    quoted = [repr(value) for value in values]
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} or {quoted[-1]}"


# --- Checkers -------------------------------------------------------------------------------------------------------------------
# Every checker accepts the plain values produced by reading JSON with json.loads (dict, list, str, int, float, bool, None). Frozen values and OpaqueJson are
# first turned back into plain values by Codec.decode (_plain). decode returns a value or raises _RefusedError.
#
# Fast path (fast): Codec reads with fast first and does not copy the input with _plain. fast reads json.loads plain values and frozen
# FrozenMap (mapping) and tuple as they are. Anywhere else (str or int subclasses, OpaqueJson, set, ...) only that spot is copied with _plain and
# passed to decode (_Node.fast -- same judgment as the existing path). A lone surrogate or too-deep nesting raises _SlowPathError and Codec
# re-reads with the existing path. fast returns a value only when it read the whole input and judged every spot the same as the existing path. When it refuses (_RefusedError), Codec also
# re-reads with the existing path and collects the full error list as before (fast stops at the first error). room = how many more nesting levels are allowed (same as _plain).


class _Node:
    """Checker for one field type. label = the arm name inserted in a union error path (appendix B of the design record -- pydantic spelling)."""

    __slots__ = ("label",)
    #: The JSON kinds this accepts as a union arm (str, int, float, bool, array, object).
    kinds: frozenset[str] = frozenset()
    #: A catch-all arm (OpaqueJson, object) -- in a union it is chosen only when no other arm accepts (section 3, rule 4).
    catch_all: bool = False
    #: Order when union arms of the same kind are listed (smaller first -- pydantic's "exact match" arm comes before the "match after coercion" arm).
    rank: int = 0

    def __init__(self, label: str) -> None:
        self.label = label

    def decode(self, value: object) -> object:
        """Decode a plain JSON value into a value of this type and check it (a value it does not accept raises _RefusedError -- error paths are relative to this checker)."""
        raise NotImplementedError

    def fast(self, value: object, room: int) -> object:
        """Read on the fast path (see the note above). This is for spots not covered by a per-checker fast form -- only that spot is copied with _plain and passed to decode. A spot
        that cannot be copied (lone surrogate, too-deep nesting) raises _SlowPathError (so it is not mistaken for a union arm having rejected it)."""
        try:
            plain = _plain(value, room)
        except _RefusedError:
            raise _SlowPathError from None
        return self.decode(plain)

    def encode(self, value: object, raw: bool) -> _Json:
        """Write a value of this type as a plain JSON value (raw = while writing to a string -- an OpaqueJson string stays as _Raw). A value of the wrong type is
        written from its own type, as the current pydantic does (_inferred)."""
        return _inferred(value, raw)

    def text(self, value: object) -> str:
        """Write a value of this type straight to a JSON string (the fast path of writing): the answer is the same string as _json_text(encode(value, True)),
        but no tree of JSON values is built. This is for spots not covered by a per-checker fast form -- only that spot builds a tree and spells it out.
        A lone surrogate is not checked here (Codec.encode_json checks the finished string once, and rejects through the existing path if one exists)."""
        return _json_text(self.encode(value, True))

    def fits(self, value: object, strict: bool) -> bool:
        """As a union arm, is the value a value of this type (the arm is chosen when writing -- strict is the exact type, lax also types that can be rewritten)."""
        raise NotImplementedError

    def schema(self, defs: "_Definitions") -> _Schema:
        """JSON Schema of this type (defwire types and defenum are put in defs and pointed to with $ref)."""
        raise NotImplementedError

    def titled(self) -> bool:
        """Whether the field JSON Schema of this type gets a title derived from the field name (defwire and defenum, whose own type side carries a title, do not -- same as pydantic)."""
        return True


@final
class _Str(_Node):
    __slots__ = ()
    kinds = frozenset({"str"})

    def decode(self, value: object) -> object:
        if type(value) is str:
            return value
        raise _refuse("string_type", "Input should be a valid string")

    def fast(self, value: object, room: int) -> object:
        if type(value) is str and (value.isascii() or not _has_surrogate(value)):
            return value
        return _Node.fast(self, value, room)

    def encode(self, value: object, raw: bool) -> _Json:
        return value if type(value) is str else _inferred(value, raw)

    def text(self, value: object) -> str:
        return _ENCODE_STRING(value) if type(value) is str else _Node.text(self, value)

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, str)

    def schema(self, defs: "_Definitions") -> _Schema:
        return {"type": "string"}


@final
class _Int(_Node):
    __slots__ = ()
    kinds = frozenset({"int"})

    def decode(self, value: object) -> object:
        if type(value) is int:
            return value
        raise _refuse("int_type", "Input should be a valid integer")

    def fast(self, value: object, room: int) -> object:
        return value if type(value) is int else _Node.fast(self, value, room)

    def encode(self, value: object, raw: bool) -> _Json:
        if type(value) is int:
            return value
        if isinstance(value, int):  # bool and IntEnum are also written as integers (same as the current pydantic)
            return int(value)
        return _inferred(value, raw)

    def text(self, value: object) -> str:
        return int.__repr__(value) if type(value) is int else _Node.text(self, value)

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, int) and not (strict and isinstance(value, bool))

    def schema(self, defs: "_Definitions") -> _Schema:
        return {"type": "integer"}


@final
class _Float(_Node):
    __slots__ = ()
    kinds = frozenset({"int", "float"})
    rank = 1  # an integer is accepted first by the int arm (even in (| float int), 1 stays an int)

    def decode(self, value: object) -> object:
        kind = type(value)
        if kind is float:
            return value
        if kind is int:
            try:
                return float(cast(int, value))
            except OverflowError:  # an overflowing integer becomes inf, same as the current pydantic
                return math.inf if cast(int, value) > 0 else -math.inf
        raise _refuse("float_type", "Input should be a valid number")

    def fast(self, value: object, room: int) -> object:
        return value if type(value) is float else _Node.fast(self, value, room)

    def encode(self, value: object, raw: bool) -> _Json:
        # A typed float field keeps NaN and inf as float (dump) -- they become null when written to a string. Integers and bools are converted to float (same as now).
        if type(value) is float:
            return value
        if isinstance(value, float | int):
            try:
                return float(value)
            except (
                OverflowError
            ):  # an integer with too many digits for a float is written as an integer, same as the current pydantic dump
                return _inferred(value, raw)
        return _inferred(value, raw)

    def text(self, value: object) -> str:
        return _float_text(value) if type(value) is float else _Node.text(self, value)

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, float) or (not strict and isinstance(value, int))

    def schema(self, defs: "_Definitions") -> _Schema:
        return {"type": "number"}


@final
class _Bool(_Node):
    __slots__ = ()
    kinds = frozenset({"bool"})

    def decode(self, value: object) -> object:
        if type(value) is bool:
            return value
        raise _refuse("bool_type", "Input should be a valid boolean")

    def fast(self, value: object, room: int) -> object:
        return value if type(value) is bool else _Node.fast(self, value, room)

    def encode(self, value: object, raw: bool) -> _Json:
        return value if type(value) is bool else _inferred(value, raw)

    def text(self, value: object) -> str:
        if value is True:
            return "true"
        return "false" if value is False else _Node.text(self, value)

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, bool)

    def schema(self, defs: "_Definitions") -> _Schema:
        return {"type": "boolean"}


@final
class _NoneOnly(_Node):
    __slots__ = ()

    def decode(self, value: object) -> object:
        if value is None:
            return None
        raise _refuse("none_required", "Input should be None")

    def fast(self, value: object, room: int) -> object:
        return None if value is None else _Node.fast(self, value, room)

    def text(self, value: object) -> str:
        return "null" if value is None else _Node.text(self, value)

    def fits(self, value: object, strict: bool) -> bool:
        return value is None

    def schema(self, defs: "_Definitions") -> _Schema:
        return {"type": "null"}


@final
class _Enum(_Node):
    """defenum (StrEnum) -- accepts only the string spelling of a member value (not the member name)."""

    __slots__ = ("_fast_members", "_members", "_msg", "_spellings", "enum_type", "quoted")
    kinds = frozenset({"str"})
    rank = 2  # the str and Literal arms come first (pydantic picks the str arm for a string)

    def __init__(self, enum_type: type[enum.Enum]) -> None:
        super().__init__(f"str-enum[{enum_type.__name__}]")
        self.enum_type = enum_type
        self._members: dict[str, enum.Enum] = {
            cast(str, member.value): member for member in enum_type
        }
        self._spellings: dict[enum.Enum, str] = {
            member: spelling for spelling, member in self._members.items()
        }
        #: The JSON string spelling of each member (used by text and by the _WireNode writer).
        self.quoted: dict[enum.Enum, str] = {
            member: _ENCODE_STRING(spelling) for member, spelling in self._spellings.items()
        }
        self._msg = f"Input should be {_expected(tuple(self._members))}"
        # Spellings read on the fast path (a spelling containing a lone surrogate takes the existing path -- _plain rejects it with json_invalid).
        self._fast_members = {
            spelling: member
            for spelling, member in self._members.items()
            if _SURROGATE.search(spelling) is None
        }

    def decode(self, value: object) -> object:
        if type(value) is str:
            member = self._members.get(value)
            if member is not None:
                return member
        raise _refuse("enum", self._msg)

    def fast(self, value: object, room: int) -> object:
        if type(value) is str:
            member = self._fast_members.get(value)
            if member is not None:
                return member
        return _Node.fast(self, value, room)

    def encode(self, value: object, raw: bool) -> _Json:
        if isinstance(value, self.enum_type):
            return self._spellings[value]
        return _inferred(value, raw)

    def text(self, value: object) -> str:
        if isinstance(value, self.enum_type):
            return self.quoted[value]
        return _Node.text(self, value)

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, self.enum_type)

    def schema(self, defs: "_Definitions") -> _Schema:
        return defs.ref(self.enum_type, self.definition)

    def titled(self) -> bool:
        return False

    def definition(self, defs: "_Definitions") -> _Schema:
        """Shape put in $defs (list of value spellings, the type name, and the type doc as description -- tidied with inspect.cleandoc as the current pydantic does,
        and the default doc of Enum is not emitted. A difference the parity test found)."""
        doc = self.enum_type.__doc__
        description = inspect.cleandoc(doc) if doc else None
        shape: _Schema = {
            "enum": [*self._members],
            "title": self.enum_type.__name__,
            "type": "string",
        }
        if description is None or description == "An enumeration.":
            return shape
        return {"description": description, **shape}


@final
class _Literal(_Node):
    """Literal of strings -- any one of the listed strings."""

    __slots__ = ("_fast_values", "_msg", "_order", "_values")
    kinds = frozenset({"str"})

    def __init__(self, values: tuple[str, ...]) -> None:
        super().__init__(f"literal[{','.join(repr(value) for value in values)}]")
        self._order = values
        self._values = frozenset(values)
        self._fast_values = frozenset(value for value in values if _SURROGATE.search(value) is None)
        self._msg = f"Input should be {_expected(values)}"

    def decode(self, value: object) -> object:
        if type(value) is str and value in self._values:
            return value
        raise _refuse("literal_error", self._msg)

    def fast(self, value: object, room: int) -> object:
        if type(value) is str and value in self._fast_values:
            return value
        return _Node.fast(self, value, room)

    def encode(self, value: object, raw: bool) -> _Json:
        return value if type(value) is str else _inferred(value, raw)

    def text(self, value: object) -> str:
        return _ENCODE_STRING(value) if type(value) is str else _Node.text(self, value)

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, str) and value in self._values

    def schema(self, defs: "_Definitions") -> _Schema:
        if len(self._order) == 1:
            return {"const": self._order[0], "type": "string"}
        return {"enum": [*self._order], "type": "string"}


#: Outcome of one checker call: (True, value) or (False, list of errors). Used by the places that collect all errors (fields, elements, arms) to catch exceptions inside a comprehension.
_Outcome = tuple[typing.Literal[True], object] | tuple[typing.Literal[False], list[_Problem]]


def _attempt(node: _Node, value: object) -> _Outcome:
    """Turn one checker call into an outcome value (so the places that collect all errors can catch exceptions inside a comprehension)."""
    try:
        return (True, node.decode(value))
    except _RefusedError as invalid:
        return (False, invalid.problems)


#: Outcome when a value is missing where it should exist (a field without a default, a position of an exact-length array).
_MISSING: Final[_Outcome] = (False, [((), "missing", "Field required")])


def _problems_of(key: str | int, outcome: _Outcome) -> list[_Problem]:
    """Prepend a path key to the errors of one outcome (an accepted outcome has no errors) -- so the collecting place gathers them into one list in key order."""
    if outcome[0] is True:
        return []
    return _under(key, outcome[1])


def _settled(keyed: list[tuple[str | int, _Outcome]]) -> list[object]:
    """From the outcomes per key (index, key), return the list of values. If even one was rejected, prepend the key to the errors as the path and reject them all together."""
    problems = [problem for key, outcome in keyed for problem in _problems_of(key, outcome)]
    if problems:
        raise _RefusedError(problems)
    return [outcome[1] for _, outcome in keyed]


def _items(node: _Node, values: list[object]) -> list[object]:
    """Read array elements with the same checker (errors prepend the index to the path and all are collected)."""
    return _settled([(index, _attempt(node, item)) for index, item in enumerate(values)])


@final
class _Array(_Node):
    """Arrays of undetermined length: (get tuple #(T ...)), (get list T), (get frozenset T)."""

    __slots__ = ("_item", "_kind", "_make", "_own")
    kinds = frozenset({"array"})

    def __init__(
        self,
        label: str,
        item: _Node,
        kind: str,
        make: Callable[[list[object]], object],
        own: tuple[type, ...],
    ) -> None:
        super().__init__(label)
        self._item = item
        self._kind = kind
        self._make = make
        self._own = (
            own  # container accepted by strict when writing (exactly the declared container -- a frozenset field also accepts set)
        )

    def decode(self, value: object) -> object:
        if type(value) is list:
            return self._make(_items(self._item, cast(list[object], value)))
        raise _refuse(self._kind, "Input should be a valid array")

    @property
    def item(self) -> _Node:
        """The element checker (so the unrolled reader and writer of _WireNode can open up how to read and spell the elements)."""
        return self._item

    @property
    def make(self) -> Callable[[list[object]], object]:
        """Function that builds the field container (tuple, list, frozenset) from the list of read elements."""
        return self._make

    def fast(self, value: object, room: int) -> object:
        kind = type(value)
        if (kind is list or kind is tuple) and room:
            item = self._item.fast
            inner = room - 1
            if self._item is _STR:  # an element that is exactly an ASCII str is accepted without calling the checker (same answer as _Str.fast)
                items = [
                    element
                    if type(element) is str and _is_ascii(element)
                    else item(element, inner)
                    for element in cast(list[object], value)
                ]
            else:
                items = [item(element, inner) for element in cast(list[object], value)]
            return self._make(items)
        return _Node.fast(self, value, room)

    def encode(self, value: object, raw: bool) -> _Json:
        if _is_array(value):
            item = self._item
            return [item.encode(element, raw) for element in value]
        return _inferred(value, raw)

    def text(self, value: object) -> str:
        # An element that is exactly a str is spelled without calling the checker (every checker writes the same string -- see the note on _WireNode.text).
        # An element that is exactly a nested type that writes all fields calls that type's writer directly (same answer as _WireNode.text).
        if _is_array(value):
            node = self._item
            item = node.text
            encode_string = _ENCODE_STRING
            if isinstance(node, _WireNode) and node.whole:
                writer = node.writer()
                owner = node.wire_type
                texts = [writer(element) if type(element) is owner else item(element) for element in value]
            else:
                texts = [
                    encode_string(element) if type(element) is str else item(element)
                    for element in value
                ]
            return "[" + ",".join(texts) + "]"
        return _Node.text(self, value)

    def fits(self, value: object, strict: bool) -> bool:
        # strict is exactly the declared container; lax is any array container. Elements are checked with the same strict.
        item = self._item
        return (
            _is_array(value)
            and isinstance(value, self._own if strict else _ARRAYS)
            and all(item.fits(element, strict) for element in value)
        )

    def schema(self, defs: "_Definitions") -> _Schema:
        shape: _Schema = {"items": self._item.schema(defs), "type": "array"}
        return {**shape, "uniqueItems": True} if self._make is frozenset else shape


@final
class _FixedTuple(_Node):
    """Array of exact length: (get tuple #(T1 T2 ...))."""

    __slots__ = ("_items",)
    kinds = frozenset({"array"})

    def __init__(self, label: str, items: tuple[_Node, ...]) -> None:
        super().__init__(label)
        self._items = items

    def decode(self, value: object) -> object:
        if type(value) is not list:
            raise _refuse("tuple_type", "Input should be a valid array")
        values = cast(list[object], value)
        expected = len(self._items)
        if len(values) > expected:  # when too long, the elements are not judged (same as the current pydantic)
            raise _refuse(
                "too_long",
                f"Tuple should have at most {expected} items after validation, not {len(values)}",
            )
        # When too short, a missing is reported for each absent position (ordered with the errors of present positions, by position).
        return tuple(
            _settled(
                [
                    (index, _attempt(node, values[index]) if index < len(values) else _MISSING)
                    for index, node in enumerate(self._items)
                ]
            )
        )

    def fast(self, value: object, room: int) -> object:
        kind = type(value)
        values = cast(list[object], value)
        if (kind is list or kind is tuple) and room and len(values) == len(self._items):
            inner = room - 1
            return tuple(
                [node.fast(element, inner) for node, element in zip(self._items, values, strict=True)]
            )
        return _Node.fast(self, value, room)

    def encode(self, value: object, raw: bool) -> _Json:
        if _is_array(value) and isinstance(value, list | tuple) and len(value) == len(self._items):
            return [
                node.encode(element, raw) for node, element in zip(self._items, value, strict=True)
            ]
        return _inferred(value, raw)

    def text(self, value: object) -> str:
        if _is_array(value) and isinstance(value, list | tuple) and len(value) == len(self._items):
            texts = [node.text(element) for node, element in zip(self._items, value, strict=True)]
            return "[" + ",".join(texts) + "]"
        return _Node.text(self, value)

    def fits(self, value: object, strict: bool) -> bool:
        return (
            _is_array(value)
            and isinstance(value, tuple if strict else list | tuple)
            and len(value) == len(self._items)
            and all(
                node.fits(element, strict) for node, element in zip(self._items, value, strict=True)
            )
        )

    def schema(self, defs: "_Definitions") -> _Schema:
        count = len(self._items)
        shape: _Schema = {"maxItems": count, "minItems": count, "type": "array"}
        prefix: list[_Json] = [node.schema(defs) for node in self._items]
        return {**shape, "prefixItems": prefix} if count else shape


@final
class _Dict(_Node):
    """(get dict #(str T)) -- read the values of an object with T (the key is a JSON object key, so it is a string)."""

    __slots__ = ("_item",)
    kinds = frozenset({"object"})

    def __init__(self, label: str, item: _Node) -> None:
        super().__init__(label)
        self._item = item

    def decode(self, value: object) -> object:
        if type(value) is not dict:
            raise _refuse("dict_type", "Input should be an object")
        source = cast(dict[str, object], value)
        return dict(
            zip(
                source,
                _settled([(key, _attempt(self._item, item)) for key, item in source.items()]),
                strict=True,
            )
        )

    def fast(self, value: object, room: int) -> object:
        # A key must be exactly a str (derived keys and numeric keys take the existing path -- _plain_key fixes the spelling).
        if room and _is_mapping(value) and all(type(key) is str for key in value):
            item = self._item.fast
            inner = room - 1
            return {key: item(element, inner) for key, element in value.items()}
        return _Node.fast(self, value, room)

    def encode(self, value: object, raw: bool) -> _Json:
        if _is_mapping(value):
            item = self._item
            return {_json_key(key): item.encode(element, raw) for key, element in value.items()}
        return _inferred(value, raw)

    def text(self, value: object) -> str:
        # The key has the same spelling as encode (_json_key). Two keys with the same spelling (such as 1 and "1") are merged by the encode dict into one of the later values,
        # so in that case write via the path that builds a tree.
        if _is_mapping(value):
            item = self._item.text
            keys = [_json_key(key) for key in value]
            if len(set(keys)) == len(keys):
                texts = [
                    _ENCODE_STRING(key) + ":" + item(element)
                    for key, element in zip(keys, value.values(), strict=True)
                ]
                return "{" + ",".join(texts) + "}"
        return _Node.text(self, value)

    def fits(self, value: object, strict: bool) -> bool:
        item = self._item
        return (
            _is_mapping(value)
            and (not strict or isinstance(value, dict))
            and all(
                isinstance(key, str) and item.fits(element, strict)
                for key, element in value.items()
            )
        )

    def schema(self, defs: "_Definitions") -> _Schema:
        # True when the value shape does not matter (values of object and OpaqueJson) -- same as the current pydantic; a difference the parity test found.
        item = self._item.schema(defs)
        return {"additionalProperties": item if item else True, "type": "object"}


@final
class _BareDict(_Node):
    """Plain dict -- only checks that it is an object and keeps the value as the json.loads value without reading it (a census special case -- new fields should be typed)."""

    __slots__ = ()
    kinds = frozenset({"object"})

    def decode(self, value: object) -> object:
        if type(value) is dict:
            return value
        raise _refuse("dict_type", "Input should be an object")

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, dict if strict else Mapping)

    def schema(self, defs: "_Definitions") -> _Schema:
        return {"additionalProperties": True, "type": "object"}


@final
class _Anything(_Node):
    """object -- accepts any JSON as the json.loads value (no type check -- same as now)."""

    __slots__ = ()
    catch_all = True

    def decode(self, value: object) -> object:
        return value

    def fits(self, value: object, strict: bool) -> bool:
        return True

    def schema(self, defs: "_Definitions") -> _Schema:
        return {}


@final
class _Opaque(_Node):
    """OpaqueJson -- does not read inside any JSON and wraps it in the minimal serialized string."""

    __slots__ = ()
    catch_all = True

    def decode(self, value: object) -> object:
        return _OpaqueJson.of(cast(Any, value))

    def encode(self, value: object, raw: bool) -> _Json:
        if isinstance(value, _OpaqueJson):
            return _opaque_out(value, raw)
        return _inferred(value, raw)

    def fits(self, value: object, strict: bool) -> bool:
        return isinstance(value, _OpaqueJson)

    def schema(self, defs: "_Definitions") -> _Schema:
        return {}


@final
class _WireField:
    """One field of a defwire type: Python name, wire name, checker, default (dataclasses.MISSING if none -- plus the default factory)."""

    __slots__ = ("default", "factory", "node", "python", "required", "wire")

    def __init__(
        self,
        python: str,
        wire: str,
        node: _Node,
        default: object,
        factory: Callable[[], object] | None,
    ) -> None:
        self.python = python
        self.wire = wire
        self.node = node
        self.default = default
        self.factory = factory
        self.required = default is dataclasses.MISSING and factory is None

    def omits(self, value: object) -> bool:
        """Whether to omit this field when writing -- a field whose value == the default (the value the factory makes) is not written (the current pydantic exclude_defaults)."""
        if self.default is not dataclasses.MISSING:
            return bool(value == self.default)
        if self.factory is not None:
            return bool(value == self.factory())
        return False


@final
class _WireNode(_Node):
    """Nested defwire type -- judges an object with the :names, :unknown, and :check of that type (also a plain defrecord of a table row -- _row_node).

    Fields are judged in declaration order and all errors are collected, then unknown fields are listed in input order (for :unknown :reject). :check runs only when all fields passed
    (the type constructor calls __post_init__) -- only ValueError and AssertionError are mapped to Malformed; other exceptions are raised as they are.
    """

    __slots__ = (
        "_bare",
        "_post_init",
        "_reader",
        "_reads",
        "_reject",
        "_texts",
        "_values_of",
        "_whole",
        "_wires",
        "_writer",
        "_writes",
        "fields",
        "wire_type",
    )
    kinds = frozenset({"object"})

    def __init__(self, wire_type: type, whole: bool = False) -> None:
        super().__init__(wire_type.__name__)
        self.wire_type = wire_type
        self.fields: tuple[_WireField, ...] = ()
        self._wires: frozenset[str] = frozenset()
        self._reject = False
        self._bare = False
        self._post_init = None
        #: Per field: (wire name, Python name, checker fast, whether it has no default, bare type, whether it accepts None, default, default factory).
        self._reads: tuple[
            tuple[
                str,
                str,
                Callable[[object, int], object],
                bool,
                type | None,
                bool,
                object,
                Callable[[], object] | None,
            ],
            ...,
        ] = ()
        self._writes: tuple[tuple[str, Callable[[object, bool], _Json]], ...] = ()
        #: Per field: (head of the written field `"wire":`, checker text).
        self._texts: tuple[tuple[str, Callable[[object], str]], ...] = ()
        self._values_of: Callable[[object], tuple[object, ...]] | None = None
        #: Reader and writer with the fields fully unrolled (built on first use -- _compile_reader, _compile_writer).
        self._reader: Callable[[Mapping[str, object], int], dict[str, object]] | None = None
        self._writer: Callable[[object], str] | None = None
        # whole = do not omit fields equal to their default when writing (the table-row image -- a row write is a per-field diff, so an omitted field would leave the previous value).
        self._whole = whole

    def fill(self, fields: tuple[_WireField, ...], reject: bool) -> None:
        """Install the field checkers after building finishes (a type cycle during building is watched by _BUILDING)."""
        self.fields = fields
        self._wires = frozenset(field.wire for field in fields)
        self._reject = reject
        # Line up, once, what the fast path and the writer fetch per field (so a row does not do one attribute lookup per field).
        # For a (| T None) field, the field loop accepts None and other values call the T checker directly (skipping one level of the union checker -- same answer).
        bodies = [_nullable_body(field.node) for field in fields]
        self._reads = tuple(
            (
                field.wire,
                field.python,
                (body or field.node).fast,
                field.required,
                _EXACT_OF.get(body or field.node),
                body is not None or field.node is _NONE,
                field.default,
                field.factory,
            )
            for field, body in zip(fields, bodies, strict=True)
        )
        # Whether the fast path may create the type without going through the constructor: a type whose constructor was written by the defrecord expansion (the seal_record mark RECORD_PARAMS) and all fields are
        # constructor arguments -- that constructor places the fields in declaration order (absent fields get the default or the factory value) and only calls the :check __post_init__.
        owned = vars(self.wire_type)
        self._bare = (
            owned.get("__dataclass_params__") is _RECORD_PARAMS
            and "__new__" not in owned
            and len(fields) == len(dataclasses.fields(self.wire_type))
        )
        self._post_init: Callable[[object], None] | None = owned.get("__post_init__")
        self._writes = tuple((field.wire, field.node.encode) for field in fields)
        self._texts = tuple(
            (_ENCODE_STRING(field.wire) + ":", (body or field.node).text)
            for field, body in zip(fields, bodies, strict=True)
        )
        self._values_of = (
            operator.attrgetter(*(field.python for field in fields)) if len(fields) > 1 else None
        )

    def decode(self, value: object) -> object:
        return self.decode_counted(value)[0]

    def decode_counted(self, value: object) -> tuple[object, int]:
        """The value, and the number of fields filled from the input (when two or more object-accepting arms of a union accept, the one with more is chosen -- pydantic smart union)."""
        if type(value) is not dict:
            raise _refuse("dataclass_type", "Input should be an object")
        source = cast(dict[str, object], value)
        # When a field is absent: if it has a default, do not pass it as an argument (the constructor fills the default); if not, it is missing.
        outcomes = [
            (field, _attempt(field.node, source[field.wire]) if field.wire in source else _MISSING)
            for field in self.fields
            if field.wire in source or field.required
        ]
        unknown: list[_Problem] = (
            [
                ((key,), "unexpected_keyword_argument", "Unexpected keyword argument")
                for key in source
                if key not in self._wires
            ]
            if self._reject and not self._wires.issuperset(source)
            else []
        )
        problems = [
            problem for field, outcome in outcomes for problem in _problems_of(field.wire, outcome)
        ] + unknown
        if problems:
            raise _RefusedError(problems)
        arguments = {field.python: outcome[1] for field, outcome in outcomes}
        return self._built(arguments), len(arguments)

    def _built(self, arguments: dict[str, object]) -> object:
        """Build the typed value from the field values (only ValueError and AssertionError from a failing :check are mapped to a rejection -- other exceptions are raised as they are)."""
        try:
            return self.wire_type(**arguments)
        except ValueError as error:
            raise _refuse("value_error", f"Value error, {error}") from None
        except AssertionError as error:
            raise _refuse("assertion_error", f"Assertion failed, {error}") from None

    def fast(self, value: object, room: int) -> object:
        # The common shape (a json.loads dict that has all fields) is read without going through fast_counted (same answer as fast_counted).
        if room and type(value) is dict and len(value) == len(self._reads) and self._wires.issuperset(value):
            arguments = (self._reader or self._compile_reader())(value, room - 1)
            return self._bare_built(arguments) if self._bare else self._built(arguments)
        return self.fast_counted(value, room)[0]

    def fast_counted(self, value: object, room: int) -> tuple[object, int]:
        """decode_counted for the fast path. Only when all mapping keys are field wire names of this type and every field without a default is present, read without copying
        (unknown fields, missing fields, and numeric keys take the existing path -- how errors and discarding are judged lives in that one place)."""
        if room and _is_mapping(value) and self._wires.issuperset(value):
            inner = room - 1
            if len(value) == len(self._reads):  # all fields are present (a table row writes all fields) -- read with the fully unrolled reader
                reader = self._reader or self._compile_reader()
                arguments = reader(value, inner)
                made = self._bare_built(arguments) if self._bare else self._built(arguments)
                return made, len(arguments)
            arguments = {}
            bare = self._bare
            defaulted = 0
            # A field value of exactly the bare type (exact -- ASCII string, int, float, bool) and the None of a field that accepts None are accepted without calling the checker
            # (same answer as the checker's fast -- so a row does not call one function per field).
            for wire, python, fast, required, exact, nullable, default, factory in self._reads:
                if wire in value:
                    item = value[wire]
                    kind = type(item)
                    if kind is exact and (kind is not str or _is_ascii(item)):
                        arguments[python] = item
                    elif item is None and nullable:
                        arguments[python] = None
                    else:
                        arguments[python] = fast(item, inner)
                elif required:
                    return self._fast_fallback(value, room)
                elif bare:  # a type created without the constructor places defaults in declaration order, like the constructor
                    arguments[python] = default if factory is None else factory()
                    defaulted += 1
            if bare:
                return self._bare_built(arguments), len(arguments) - defaulted
            return self._built(arguments), len(arguments)
        return self._fast_fallback(value, room)

    def _compile_reader(self) -> Callable[[Mapping[str, object], int], dict[str, object]]:
        """Build and remember, as one fully unrolled function of this type's fields, the function that reads a mapping with all fields present (once on first use -- two threads
        building at the same time yield the same function). The answer is the same as the field loop below (per field: a value of exactly the bare type, and None for a field that accepts None, pass as they are; others go to the
        checker's fast). On this Python (3.14t), about 0.7 µs per field of the field loop took half the cost of one row, so, like dataclass building __init__, the source is built and exec'd
        (field names are made literal with repr, and checkers and types are passed in the namespace)."""
        reads = self._reads
        bodies = [_nullable_body(field.node) or field.node for field in self.fields]
        arrays = {index: node for index, node in enumerate(bodies) if isinstance(node, _Array)}
        namespace: dict[str, object] = {
            **{f"f{index}": read[2] for index, read in enumerate(reads)},
            **{f"t{index}": read[4] for index, read in enumerate(reads)},
            **{f"i{index}": node.item.fast for index, node in arrays.items()},
            **{f"m{index}": node.make for index, node in arrays.items()},
        }
        body = [
            line
            for index, ((wire, _python, _fast, _required, exact, nullable, *_default), node) in enumerate(
                zip(reads, bodies, strict=True)
            )
            for line in _reader_lines(index, wire, node, exact, nullable)
        ]
        pairs = ", ".join(f"{read[1]!r}: v{index}" for index, read in enumerate(reads))
        source = "\n".join(("def read(value, inner):", *body, f"    return {{{pairs}}}"))
        reader = cast(
            Callable[[Mapping[str, object], int], dict[str, object]],
            _compiled(source, namespace, "read", self.wire_type),
        )
        self._reader = reader
        return reader

    @property
    def whole(self) -> bool:
        """Whether this type writes all fields (a table-row type -- fields equal to their default are also written)."""
        return self._whole

    def writer(self) -> Callable[[object], str]:
        """Fully unrolled function that writes a value of exactly this type to a JSON string (only for types that write all fields -- built on first use)."""
        return self._writer or self._compile_writer()

    def _compile_writer(self) -> Callable[[object], str]:
        """Build and remember, as one fully unrolled function, the function that writes a value of a type that writes all fields (_whole) to a JSON string (same reason and construction as _compile_reader).
        The answer is the same as the field loop of text (a value of exactly str and None are spelled as they are; others use the checker's text)."""
        texts = self._texts
        count = len(texts)
        names = [f"v{index}" for index in range(count)]
        written = [
            _writer_part(index, _nullable_body(field.node) or field.node)
            for index, field in enumerate(self.fields)
        ]
        namespace: dict[str, object] = {
            "encode_string": _ENCODE_STRING,
            "int_text": int.__repr__,
            "values_of": self._values_of,
            "name0": self.fields[0].python if count else None,
            **{
                f"h{index}": ("{" if index == 0 else ",") + head
                for index, (head, _text) in enumerate(texts)
            },
            **{f"w{index}": text for index, (_head, text) in enumerate(texts)},
            **{name: bound for _part, bindings in written for name, bound in bindings.items()},
        }
        parts = [
            part
            for index, (expression, _bindings) in enumerate(written)
            for part in (f"h{index}", expression)
        ]
        body = (
            ("    return '{}'",)
            if count == 0
            else (
                "    v0 = getattr(value, name0)" if count == 1 else f"    {', '.join(names)}, = values_of(value)",
                f"    return ''.join(({', '.join(parts)}, '}}'))",
            )
        )
        source = "\n".join(("def write(value):", *body))
        writer = cast(Callable[[object], str], _compiled(source, namespace, "write", self.wire_type))
        self._writer = writer
        return writer

    def _bare_built(self, arguments: dict[str, object]) -> object:
        """Create a value of a type that has the constructor written by the defrecord expansion (_bare) without going through the constructor -- the same value as the constructor (fields placed in declaration order,
        the :check __post_init__ called) without paying for object.__setattr__ per field (the same form pydantic built for a dataclass
        -- 18 µs → 6 µs for one TurnRow). arguments are all fields in declaration order."""
        made = object.__new__(self.wire_type)
        object.__setattr__(made, "__dict__", arguments)
        post_init = self._post_init
        if post_init is not None:
            try:
                post_init(made)
            except ValueError as error:
                raise _refuse("value_error", f"Value error, {error}") from None
            except AssertionError as error:
                raise _refuse("assertion_error", f"Assertion failed, {error}") from None
        return made

    def _fast_fallback(self, value: object, room: int) -> tuple[object, int]:
        """Read an object that does not fit the fast-path forms by copying only that spot with _plain and reading it with decode_counted (same as _Node.fast)."""
        try:
            plain = _plain(value, room)
        except _RefusedError:
            raise _SlowPathError from None
        return self.decode_counted(plain)

    def encode(self, value: object, raw: bool) -> _Json:
        # Fields are written in declaration order with wire names, and fields equal to their default are not written (a nested type's fields are likewise omitted by that type's writer).
        # A field without a default is written even when None (null).
        if not isinstance(value, self.wire_type):
            return _inferred(value, raw)
        values_of = self._values_of
        if values_of is None:  # zero or one field (attrgetter returns a value, not a tuple, for a single name)
            items: tuple[object, ...] = tuple(
                getattr(value, field.python) for field in self.fields
            )
        else:
            items = values_of(value)
        if self._whole:
            return {
                wire: encode(item, raw)
                for (wire, encode), item in zip(self._writes, items, strict=True)
            }
        return {
            wire: encode(item, raw)
            for (wire, encode), field, item in zip(self._writes, self.fields, items, strict=True)
            if not field.omits(item)
        }

    def text(self, value: object) -> str:
        # Spell the same fields as encode in the same order, with the field head (`"wire":`) and the checker's text. A value of exactly str and None are spelled without calling the checker --
        # every checker writes a value of exactly str as that string (a wrong-typed value is also written by _inferred as the string as it is) and writes None as null, so
        # the answer equals the checker's text (so a row does not call one function per field).
        if not isinstance(value, self.wire_type):
            return _Node.text(self, value)
        if self._whole:
            return self.writer()(value)
        values_of = self._values_of
        if values_of is None:
            items: tuple[object, ...] = tuple(
                getattr(value, field.python) for field in self.fields
            )
        else:
            items = values_of(value)
        encode_string = _ENCODE_STRING
        if self._whole:
            texts = [
                head
                + (
                    encode_string(item)
                    if type(item) is str
                    else "null"
                    if item is None
                    else text(item)
                )
                for (head, text), item in zip(self._texts, items, strict=True)
            ]
        else:
            texts = [
                head
                + (
                    encode_string(item)
                    if type(item) is str
                    else "null"
                    if item is None
                    else text(item)
                )
                for (head, text), field, item in zip(self._texts, self.fields, items, strict=True)
                if not field.omits(item)
            ]
        return "{" + ",".join(texts) + "}"

    def fits(self, value: object, strict: bool) -> bool:
        return type(value) is self.wire_type if strict else isinstance(value, self.wire_type)

    def schema(self, defs: "_Definitions") -> _Schema:
        return defs.ref(self.wire_type, self.definition)

    def titled(self) -> bool:
        return False

    def definition(self, defs: "_Definitions") -> _Schema:
        """Object shape of this type (fields use wire names, required are fields without a default, :unknown :reject gives additionalProperties false, title is
        the type name) -- the same shape as the current pydantic json_schema (section 3b)."""
        properties: _Schema = {field.wire: _field_schema(field, defs) for field in self.fields}
        required: list[_Json] = [field.wire for field in self.fields if field.required]
        shape: _Schema = {
            "properties": properties,
            "title": self.wire_type.__name__,
            "type": "object",
        }
        return {
            **shape,
            **({"required": required} if required else {}),
            **({"additionalProperties": False} if self._reject else {}),
        }


@final
class _Union(_Node):
    """(| T1 T2 ...) -- how arms are chosen is section 3 of the design record, "how union arms are chosen".

    1. null is accepted by the None arm.
    2. A string, number, or bool is accepted only by an arm whose JSON kind matches (arms of the same kind go in rank order -- an integer goes to the int arm before the float arm; a string goes to the
          str and Literal arms before the defenum arm). An array goes to the earliest declared arm among those that accept.
    3. Arms that accept an object are tried in declaration order; if a dict arm accepts, that is it. If two or more nested defwire arms accept, the arm with more fields filled from the input wins;
          on a tie, the earlier declared arm.
    4. A catch-all arm (OpaqueJson, object) accepts only when no other arm accepts.

    If there is exactly one non-None arm, return that arm's errors without adding the arm name to the path ((| T None) -- same as now). With two or more and no arm accepting,
    list the errors in declaration order per arm with the path <arm name>.
    """

    __slots__ = ("_arms", "_by_kind", "_fallback", "_nullable")

    def __init__(self, arms: tuple[_Node, ...], nullable: bool) -> None:
        super().__init__(f"union[{','.join(arm.label for arm in arms)}]")
        self._arms = arms
        self._nullable = nullable
        typed = [arm for arm in arms if not arm.catch_all]
        self._by_kind: dict[str, tuple[_Node, ...]] = {
            kind: tuple(
                sorted((arm for arm in typed if kind in arm.kinds), key=lambda arm: arm.rank)
            )
            for kind in ("str", "int", "float", "bool", "array", "object")
        }
        self._fallback = next((arm for arm in arms if arm.catch_all), None)
        if nullable:
            self.label = f"nullable[{arms[0].label if len(arms) == 1 else self.label}]"

    def decode(self, value: object) -> object:
        if value is None and self._nullable:
            return None
        if len(self._arms) == 1:
            return self._arms[0].decode(value)
        chosen = self._choose(value)
        if chosen is not _NO_ARM:
            return chosen
        if self._fallback is not None:
            return self._fallback.decode(value)
        raise _RefusedError(
            [
                problem
                for arm in self._arms
                for problem in _problems_of(arm.label, _attempt(arm, value))
            ]
        )

    def nullable_single(self) -> _Node | None:
        """The T of (| T None) (a nullable union with exactly one non-None arm -- so the field fast read can see the bare type of T). None for other unions."""
        return self._arms[0] if self._nullable and len(self._arms) == 1 else None

    def _choose(self, value: object) -> object:
        """Choose an arm by rules 1-3 and decode (if no arm accepts, _NO_ARM -- the caller handles rule 4 and the error list)."""
        kind = _KIND_OF.get(type(value))
        if kind is None:
            return _NO_ARM
        if kind != "object":
            for arm in self._by_kind[kind]:
                try:
                    return arm.decode(value)
                except _RefusedError:
                    continue
            return _NO_ARM
        best: tuple[object, int] | None = None
        for arm in self._by_kind["object"]:
            try:
                if isinstance(arm, _WireNode):
                    counted = arm.decode_counted(value)
                    if best is None or counted[1] > best[1]:
                        best = counted
                else:
                    return arm.decode(value)
            except _RefusedError:
                continue
        return _NO_ARM if best is None else best[0]

    def fast(self, value: object, room: int) -> object:
        # Arm choice is the same as decode (rules 1-4). If an arm rejects, go to the next arm (the content of the rejection is not read -- if all reject, Codec collects the
        # errors again through the existing path). If an arm's fast path raises _SlowPathError, pass it up as it is (since another arm might accept the copied value).
        if value is None and self._nullable:
            return None
        arms = self._arms
        if len(arms) == 1:
            return arms[0].fast(value, room)
        kind = _FAST_KIND_OF.get(type(value))
        if kind is None:
            return _Node.fast(self, value, room)
        chosen = self._fast_object(value, room) if kind == "object" else self._fast_scalar(kind, value, room)
        if chosen is not _NO_ARM:
            return chosen
        if self._fallback is not None:
            return self._fallback.fast(value, room)
        raise _refuse("union", "no arm accepted the value")

    def _fast_scalar(self, kind: str, value: object, room: int) -> object:
        """Rule 2 of the fast path (string, number, bool, array: try arms whose JSON kind matches in order -- if none accepts, _NO_ARM)."""
        for arm in self._by_kind[kind]:
            try:
                return arm.fast(value, room)
            except _RefusedError:
                continue
        return _NO_ARM

    def _fast_object(self, value: object, room: int) -> object:
        """Rule 3 of the fast path (arms that accept an object in declaration order -- a dict arm wins; for defwire arms, the one with more fields filled from the input. If none accepts,
        _NO_ARM).""" 
        best: tuple[object, int] | None = None
        for arm in self._by_kind["object"]:
            try:
                if not isinstance(arm, _WireNode):
                    return arm.fast(value, room)
                counted = arm.fast_counted(value, room)
            except _RefusedError:
                continue
            if best is None or counted[1] > best[1]:
                best = counted
        return _NO_ARM if best is None else best[0]

    def encode(self, value: object, raw: bool) -> _Json:
        # Choosing the arm to write is the same as the current pydantic: the exact-type arm in declaration order (strict); if none, the arm that can be rewritten in declaration order (lax -- a float arm also takes
        # integers and bools); if none fit, write from the value's type.
        if value is None and self._nullable:
            return None
        arms = self._arms
        if len(arms) == 1:
            return arms[0].encode(value, raw)
        for strict in (True, False):
            for arm in arms:
                if arm.fits(value, strict):
                    return arm.encode(value, raw)
        return _inferred(value, raw)

    def text(self, value: object) -> str:
        # Arm choice is the same as encode.
        if value is None and self._nullable:
            return "null"
        arms = self._arms
        if len(arms) == 1:
            return arms[0].text(value)
        for strict in (True, False):
            for arm in arms:
                if arm.fits(value, strict):
                    return arm.text(value)
        return _Node.text(self, value)

    def fits(self, value: object, strict: bool) -> bool:
        return (value is None and self._nullable) or any(
            arm.fits(value, strict) for arm in self._arms
        )

    def schema(self, defs: "_Definitions") -> _Schema:
        nulls: tuple[_Schema, ...] = ({"type": "null"},) if self._nullable else ()
        return _any_of((*(arm.schema(defs) for arm in self._arms), *nulls))

    def titled(self) -> bool:
        # (| T None) follows T (pydantic looks inside a nullable). A union with two or more arms gets a title.
        return not (self._nullable and len(self._arms) == 1 and not self._arms[0].titled())


def _reader_lines(
    index: int, wire: str, node: _Node, exact: type | None, nullable: bool
) -> tuple[str, ...]:
    """The line (one field -- checker node; for (| T None), T) of the function that _WireNode._compile_reader builds (fetch the value, check whether it is accepted as it is, and if not,
    call the checker f<index>). Values accepted as they are match the field loop: exactly the bare type t<index> (str only when ASCII), and the None of a field that
    accepts None. For a list array field, the element reading of _Array.fast is opened up here (element checker i<index>, container builder m<index>)."""
    item = f"v{index}"
    exact_test = (
        "False"
        if exact is None
        else f"type({item}) is t{index}" + (f" and {item}.isascii()" if exact is str else "")
    )
    test = f"{item} is None or {exact_test}" if nullable else exact_test
    head = (f"    {item} = value[{wire!r}]", f"    if {test}:", "        pass")
    if isinstance(node, _Array):
        each = (
            f"e if type(e) is str and e.isascii() else i{index}(e, inner - 1)"
            if node.item is _STR
            else f"i{index}(e, inner - 1)"
        )
        head = (
            *head,
            f"    elif type({item}) is list and inner:",
            f"        {item} = m{index}([{each} for e in {item}])",
        )
    return (*head, "    else:", f"        {item} = f{index}({item}, inner)")


def _compiled(
    source: str, namespace: dict[str, object], name: str, owner: type
) -> Callable[..., object]:
    """exec the built source and return the function name (the fully unrolled reader and writer of _WireNode -- the namespace holds only checkers and types)."""
    code = compile(source, f"<wire_codec {owner.__module__}.{owner.__qualname__}.{name}>", "exec")
    exec(code, namespace)
    return cast(Callable[..., object], namespace[name])


def _writer_part(index: int, node: _Node) -> tuple[str, dict[str, object]]:
    """The expression that spells one field (value v<index>, checker node -- for (| T None), T) of the function that _WireNode._compile_writer builds, and the names the expression reads. Every expression
    gives the same answer as the checker's text (w<index>): a value of exactly str is that string, None is null (every checker writes it the same -- see the note on _WireNode.text),
    an exact int in an int field, a member in a defenum field, and a value of exactly the nested type in a field of a nested type that writes all fields are spelled without calling the checker."""
    item = f"v{index}"
    rest = (
        f"encode_string({item}) if type({item}) is str else 'null' if {item} is None"
        f" else w{index}({item})"
    )
    if node is _INT:
        return f"(int_text({item}) if type({item}) is int else {rest})", {}
    if isinstance(node, _Enum):
        return (
            f"(q{index}[{item}] if type({item}) is e{index} else {rest})",
            {f"q{index}": node.quoted, f"e{index}": node.enum_type},
        )
    if isinstance(node, _WireNode) and node.whole:
        return (
            f"(n{index}({item}) if type({item}) is c{index} else {rest})",
            {f"n{index}": node.writer(), f"c{index}": node.wire_type},
        )
    if isinstance(node, _Array):  # tuple and list fields open up the element spelling of _Array.text here
        element = node.item
        array = f"type({item}) is tuple or type({item}) is list"
        if isinstance(element, _WireNode) and element.whole:
            each = f"n{index}(e) if type(e) is c{index} else i{index}(e)"
            bindings: dict[str, object] = {
                f"n{index}": element.writer(),
                f"c{index}": element.wire_type,
                f"i{index}": element.text,
            }
        else:
            each = f"encode_string(e) if type(e) is str else i{index}(e)"
            bindings = {f"i{index}": element.text}
        return (
            f"('[' + ','.join([{each} for e in {item}]) + ']' if {array} else {rest})",
            bindings,
        )
    return f"({rest})", {}


def _nullable_body(node: _Node) -> _Node | None:
    """The T checker of a (| T None) field (None for other checkers) -- so the fast path and writer of _WireNode accept None per field and call the T
    checker directly for other values (the union checker just calls the T checker after None, so the answer is the same)."""
    return node.nullable_single() if isinstance(node, _Union) else None


_NO_ARM: Final = object()
_KIND_OF: Final[dict[type, str]] = {
    str: "str",
    int: "int",
    float: "float",
    bool: "bool",
    list: "array",
    dict: "object",
}
#: JSON kinds the fast-path union reads (a frozen array tuple is also an array -- other types such as a frozen mapping are copied with _plain and decoded).
_FAST_KIND_OF: Final[dict[type, str]] = {**_KIND_OF, tuple: "array"}


# --- Build the type tree --------------------------------------------------------------------------------------------------------

_STR: Final = _Str("str")
_INT: Final = _Int("int")
_FLOAT: Final = _Float("float")
_BOOL: Final = _Bool("bool")
_NONE: Final = _NoneOnly("none")
_BARE_DICT: Final = _BareDict("dict[any,any]")
_ANYTHING: Final = _Anything("any")
_OPAQUE: Final = _Opaque("opaque-json")
#: isascii of a value confirmed to be exactly a str (so the field loop of the fast path reads it without calling the typing.cast function per field -- cast is a function call even at runtime,
#: and a row paid it once per str field). The caller checks type(item) is str first (_WireNode.fast_counted).
_is_ascii: Final = cast(Callable[[object], bool], str.isascii)
#: The bare checker that the fast path accepts without calling the checker when the value's type is exactly this type (_plain_accepts -- str only when ASCII).
_EXACT_OF: Final[dict[_Node, type]] = {_STR: str, _INT: int, _FLOAT: float, _BOOL: bool}
_SCALARS: Final[dict[object, _Node]] = {
    str: _STR,
    int: _INT,
    float: _FLOAT,
    bool: _BOOL,
    type(None): _NONE,
    None: _NONE,
    dict: _BARE_DICT,
    object: _ANYTHING,
    _OpaqueJson: _OPAQUE,
}

#: Lock that makes build-and-remember one step (free-threading Python -- two threads must not build the same type at the same time). The same thread
#: re-enters when building a nested type, hence RLock.
_LOCK: Final = threading.RLock()
#: defwire types being built (to find cycles -- rejects an indirect cycle A -> B -> A and a type that references itself). Touched only inside the lock.
_BUILDING: Final[set[type]] = set()
_CODEC_ATTRIBUTE: Final = "__doeff_codec__"
#: Row decoder remembered on a table-row type (plain defrecord) (row_codec_of -- kept under a different name from the defwire decoder).
_ROW_CODEC_ATTRIBUTE: Final = "__doeff_row_codec__"


def _is_wire_type(annotation: object) -> bool:
    """Whether this is a type built with defwire (a dataclass whose type holds the wire shape in __doeff_wire__)."""
    return (
        isinstance(annotation, type)
        and dataclasses.is_dataclass(annotation)
        and getattr(annotation, "__doeff_wire__", None) is not None
    )


def _resolved(annotation: object, namespace: dict[str, Any], where: str) -> object:
    """Resolve strings and annotations left as forward references in the type module's namespace (if unresolvable, reject showing the type and field)."""
    text = (
        annotation
        if isinstance(annotation, str)
        else annotation.__forward_arg__
        if isinstance(annotation, ForwardRef)
        else None
    )
    if text is None:
        return annotation
    try:
        return eval(
            text, namespace
        )  # spelling of the type annotation -- read the same way by dataclass and typing.get_type_hints
    except Exception as error:
        raise TypeError(
            f"{where}: cannot resolve type annotation {text!r} ({type(error).__name__}: {error})"
        ) from error


def _build(annotation: object, namespace: dict[str, Any], where: str, rows: bool = False) -> _Node:
    """Build the checker for one annotation (the only entry point for building the type tree). A type outside the vocabulary raises TypeError (showing which field of which type and which spelling).
    rows = build the type tree of a table row (build a plain defrecord with the row reading -- _row_node. Inside a defwire type, the wire reading applies regardless of rows)."""
    annotation = _resolved(annotation, namespace, where)
    named = _named_node(annotation, where)
    if named is not None:
        return named
    builder = _GENERIC_BUILDERS.get(typing.get_origin(annotation))
    if builder is not None:
        return builder(annotation, typing.get_args(annotation), namespace, where, rows)
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        if rows:
            return _row_node(annotation)
        raise TypeError(
            f"{where}: plain defrecord (a dataclass that is not defwire) {annotation.__name__} is outside the vocabulary -- make it a defwire"
        )
    raise TypeError(f"{where}: type outside the vocabulary {annotation!r}")


def _named_node(annotation: object, where: str) -> _Node | None:
    """Checker for a type written by name (a bare type such as str or int, a defenum, a defwire type). None if it is a generic type form (built by _GENERIC_BUILDERS)."""
    try:
        scalar = _SCALARS.get(annotation)
    except TypeError:  # an unhashable annotation is not a bare type
        scalar = None
    if scalar is not None:
        return scalar
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        if not all(isinstance(member.value, str) for member in annotation):
            raise TypeError(
                f"{where}: Enum {annotation.__name__} whose values are not strings is outside the vocabulary (use defenum)"
            )
        return _Enum(annotation)
    if _is_wire_type(annotation):
        return _wire_node(cast(type, annotation))
    return None


def _union(
    annotation: object, args: tuple[object, ...], namespace: dict[str, Any], where: str, rows: bool
) -> _Node:
    """(| T1 T2 ... None) -- mark the None arm as nullable and build the remaining arms."""
    nullable = any(arg is type(None) or arg is None for arg in args)
    arms = tuple(
        _build(arg, namespace, f"{where}|{index}", rows)
        for index, arg in enumerate(args)
        if not (arg is type(None) or arg is None)
    )
    if not arms:
        return _NONE
    return _Union(arms, nullable)


def _literal(
    annotation: object, args: tuple[object, ...], namespace: dict[str, Any], where: str, rows: bool
) -> _Node:
    """Only a Literal of strings enters the vocabulary (every Literal of the current defwire is a string)."""
    if not args or not all(type(arg) is str for arg in args):
        raise TypeError(f"{where}: Literal {annotation!r} that is not a string is outside the vocabulary")
    return _Literal(cast(tuple[str, ...], args))


def _tuple(
    annotation: object, args: tuple[object, ...], namespace: dict[str, Any], where: str, rows: bool
) -> _Node:
    """(get tuple #(T ...)) is an array of undetermined length; (get tuple #(T1 T2 ...)) is an array of exact length."""
    if len(args) == 2 and args[1] is Ellipsis:
        item = _build(args[0], namespace, f"{where}[]", rows)
        return _Array(f"tuple[{item.label}, ...]", item, "tuple_type", tuple, (tuple,))
    items = tuple(_build(arg, namespace, f"{where}[{index}]", rows) for index, arg in enumerate(args))
    return _FixedTuple(f"tuple[{', '.join(item.label for item in items)}]", items)


def _list(
    annotation: object, args: tuple[object, ...], namespace: dict[str, Any], where: str, rows: bool
) -> _Node:
    """(get list T) -- make an array into a list."""
    if len(args) != 1:
        raise TypeError(f"{where}: list without an element type {annotation!r} is outside the vocabulary")
    item = _build(args[0], namespace, f"{where}[]", rows)
    return _Array(f"list[{item.label}]", item, "list_type", list, (list,))


def _frozenset(
    annotation: object, args: tuple[object, ...], namespace: dict[str, Any], where: str, rows: bool
) -> _Node:
    """(get frozenset T) -- collapse an array into a set of distinct values (elements are only hashable str or int)."""
    item = _build(args[0], namespace, f"{where}[]", rows) if len(args) == 1 else None
    if item is None or (item is not _STR and item is not _INT):
        raise TypeError(f"{where}: frozenset elements must be only str or int: {annotation!r}")
    return _Array(f"frozenset[{item.label}]", item, "frozen_set_type", frozenset, (frozenset, set))


def _dict(
    annotation: object, args: tuple[object, ...], namespace: dict[str, Any], where: str, rows: bool
) -> _Node:
    """(get dict #(str T)) -- JSON object keys are strings, so only dicts with str keys enter the vocabulary."""
    if len(args) != 2 or _resolved(args[0], namespace, where) is not str:
        raise TypeError(f"{where}: dict whose key is not a string {annotation!r} is outside the vocabulary")
    item = _build(args[1], namespace, f"{where}[]", rows)
    return _Dict(f"dict[str,{item.label}]", item)


#: Generic type form (the answer of typing.get_origin) -> function that builds the checker. Forms not here (Annotated, datetime, etc.) are outside the vocabulary.
_GENERIC_BUILDERS: Final[
    dict[object, Callable[[object, tuple[object, ...], dict[str, Any], str, bool], _Node]]
] = {
    typing.Union: _union,
    types.UnionType: _union,
    typing.Literal: _literal,
    tuple: _tuple,
    list: _list,
    frozenset: _frozenset,
    dict: _dict,
}


def _wire_node(wire_type: type) -> _WireNode:
    """Checker of a defwire type (the one remembered on the type, or, if none, built exactly once inside the lock)."""
    known = wire_type.__dict__.get(_CODEC_ATTRIBUTE)
    if known is not None:
        return cast(_WireNode, cast(Codec[Any], known).node)
    with _LOCK:
        known = wire_type.__dict__.get(_CODEC_ATTRIBUTE)
        if known is not None:
            return cast(_WireNode, cast(Codec[Any], known).node)
        if wire_type in _BUILDING:
            raise TypeError(f"defwire {wire_type.__qualname__}: type cycle (a field that references itself) is outside the vocabulary")
        _BUILDING.add(wire_type)
        try:
            node = _WireNode(wire_type)
            shape = wire_type.__doeff_wire__
            names = cast(Mapping[str, str], shape.names)
            module = sys.modules.get(wire_type.__module__)
            namespace: dict[str, Any] = dict(vars(module)) if module is not None else {}
            fields = tuple(
                _WireField(
                    field.name,
                    names[field.name],
                    _build(
                        field.type, namespace, f"defwire {wire_type.__qualname__}  field {field.name}"
                    ),
                    field.default,
                    None if field.default_factory is dataclasses.MISSING else field.default_factory,
                )
                for field in dataclasses.fields(wire_type)
                if field.init
            )
            node.fill(fields, shape.unknown == "reject")
            setattr(wire_type, _CODEC_ATTRIBUTE, Codec(node, wire_type.__name__))
        finally:
            _BUILDING.discard(wire_type)
        return node


def _row_node(row_type: type) -> _WireNode:
    """Checker for the plain defrecord of a table row (a dataclass that is not defwire) -- field names are Python field names, unknown fields are discarded, and when writing
    fields equal to their default are also written (section 6 of the design record). Per-field judgment is the same strict as defwire. The one remembered on the type, or, if none, built exactly once inside the lock."""
    known = row_type.__dict__.get(_ROW_CODEC_ATTRIBUTE)
    if known is not None:
        return cast(_WireNode, cast(Codec[Any], known).node)
    with _LOCK:
        known = row_type.__dict__.get(_ROW_CODEC_ATTRIBUTE)
        if known is not None:
            return cast(_WireNode, cast(Codec[Any], known).node)
        if row_type in _BUILDING:
            raise TypeError(f"row type {row_type.__qualname__}: type cycle (a field that references itself) is outside the vocabulary")
        _BUILDING.add(row_type)
        try:
            node = _WireNode(row_type, whole=True)
            module = sys.modules.get(row_type.__module__)
            namespace: dict[str, Any] = dict(vars(module)) if module is not None else {}
            fields = tuple(
                _WireField(
                    field.name,
                    field.name,
                    _build(field.type, namespace, f"row type {row_type.__qualname__} field {field.name}", rows=True),
                    field.default,
                    None if field.default_factory is dataclasses.MISSING else field.default_factory,
                )
                for field in dataclasses.fields(row_type)
                if field.init
            )
            node.fill(fields, reject=False)
            setattr(row_type, _ROW_CODEC_ATTRIBUTE, Codec(node, row_type.__name__))
        finally:
            _BUILDING.discard(row_type)
        return node


# --- Reading JSON ---------------------------------------------------------------------------------------------------------------

#: Escape of a possibly lone surrogate (\ud800-\udfff). Only when present, walk the read value to look for surrogates that did not form a pair.
_SURROGATE_ESCAPE: Final = re.compile(r"\\u[dD][89a-fA-F][0-9a-fA-F]{2}")
_SURROGATE: Final = re.compile("[\ud800-\udfff]")


def _json_invalid(error: object) -> _RefusedError:
    """Error for input that cannot be read as JSON (the path is the whole value)."""
    return _refuse("json_invalid", f"Invalid JSON: {error}")


def _has_lone_surrogate(value: object) -> bool:
    """Whether the read value has a surrogate that did not form a pair (to reject strings the current pydantic rejects, in the same way)."""
    if type(value) is str:
        return _SURROGATE.search(cast(str, value)) is not None
    if type(value) is list:
        return any(_has_lone_surrogate(item) for item in cast(list[object], value))
    if type(value) is dict:
        return any(
            _has_lone_surrogate(key) or _has_lone_surrogate(item)
            for key, item in cast(dict[str, object], value).items()
        )
    return False


#: Upper limit of nesting depth of arrays and objects -- the current pydantic (jiter) reads up to 200 levels and rejects level 201 with json_invalid (recursion limit
#: exceeded) (a difference the parity test found -- json.loads reads about 1000 levels, so without a limit what is accepted would widen).
_DEPTH_LIMIT: Final = 200
_TOO_DEEP: Final = f"recursion limit exceeded (nesting deeper than {_DEPTH_LIMIT})"


def _deeper_than(value: object, room: int) -> bool:
    """Whether the nesting of arrays and objects in the read value does not fit in room more levels (exceeds if a container exists where room = 0)."""
    match value:
        case list():
            return room == 0 or any(
                _deeper_than(item, room - 1) for item in cast(list[object], value)
            )
        case dict():
            return room == 0 or any(
                _deeper_than(item, room - 1) for item in cast(dict[str, object], value).values()
            )
        case _:
            return False


def _read_json(text: str | bytes) -> object:
    """Read a JSON string into a plain value. Accepts only what the current pydantic accepts (section 3 of the design record, "reading JSON"): bytes only as UTF-8,
    a lone surrogate is rejected, a ValueError from json.loads (including an integer over 4300 digits), RecursionError, and arrays and objects nested beyond
    _DEPTH_LIMIT are json_invalid."""
    if isinstance(text, bytes | bytearray | memoryview):
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError as error:
            raise _json_invalid(error) from None
    elif not text.isascii():
        try:
            text.encode("utf-8")
        except UnicodeEncodeError as error:
            raise _json_invalid(error) from None
    try:
        value = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise _json_invalid(error) from None
    if text.count("[") + text.count("{") > _DEPTH_LIMIT and _deeper_than(value, _DEPTH_LIMIT):
        raise _json_invalid(_TOO_DEEP)
    if "\\u" in text and _SURROGATE_ESCAPE.search(text) is not None and _has_lone_surrogate(value):
        raise _json_invalid("lone surrogate in a string")
    return value


def _plain_key(key: object) -> str:
    # As json.dumps does, turn a key that is not a string into a string (bool, None, number).
    """As json.dumps does, turn an object key that is not a string into a string (bool, None, number)."""
    if type(key) is str:
        return cast(str, key)
    if isinstance(key, str):
        return str.__str__(key)
    if key is True or key is False or key is None or isinstance(key, int | float):
        return json.dumps(key)
    raise TypeError(f"not a JSON object key: {type(key).__name__}")


def _plain(value: object, room: int = _DEPTH_LIMIT) -> object:
    """Turn a JSON value (frozen FrozenMap, tuple, OpaqueJson, and str or int subclasses too) back into the plain value json.loads returns.

    The answer equals "the value written with json.dumps and read with json.loads" (made without going through a string). Something that is not a JSON value (set, arbitrary object) is
    TypeError (same as json.dumps in the current parse). Arrays and objects nested beyond _DEPTH_LIMIT are
    json_invalid, as when read from a string (room = how many more nesting levels are allowed).
    """
    kind = type(value)
    if kind is str:
        text = cast(str, value)
        if not text.isascii() and _SURROGATE.search(text) is not None:
            raise _json_invalid("lone surrogate in a string")
        return text
    if kind is int or kind is float or kind is bool or value is None:
        return value
    if kind is dict or isinstance(value, Mapping):
        if room == 0:
            raise _json_invalid(_TOO_DEEP)
        return {
            _plain_key(key): _plain(item, room - 1)
            for key, item in cast(Mapping[object, object], value).items()
        }
    if kind is list or kind is tuple or isinstance(value, list | tuple):
        if room == 0:
            raise _json_invalid(_TOO_DEEP)
        return [_plain(item, room - 1) for item in cast(list[object], value)]
    return _plain_derived(value, room)


def _plain_derived(value: object, room: int) -> object:
    """Turn a value that is not a plain JSON type (OpaqueJson and subclasses of str, int, float -- StrEnum, IntEnum, etc.) back into the value json.dumps writes."""
    if isinstance(value, _OpaqueJson):
        loaded = json.loads(value.text)
        if _deeper_than(loaded, room):
            raise _json_invalid(_TOO_DEEP)
        return loaded
    if isinstance(value, str):
        return _plain(str.__str__(value))
    if isinstance(value, int | float):  # bool is already handled by _plain
        return (int if isinstance(value, int) else float)(value)
    raise TypeError(f"not a JSON value: {type(value).__name__}")


# --- Writing --------------------------------------------------------------------------------------------------------------------
# A checker's encode writes a value as a plain JSON value (dict, list, str, int, float, bool, None). When writing to a string (raw), the OpaqueJson string is kept as
# _Raw and _json_text splices it into the output as it is (same meaning as the current OpaqueSplice -- the inside is not re-read).


@final
class _Raw:
    """The JSON string an OpaqueJson holds while being written to a string (spliced into the output without re-reading)."""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


#: Spellings that would no longer be JSON if spliced as they are (Python json.dumps writes NaN and inf this way). A string containing them is re-read and written (NaN becomes null -- same as now).
_NOT_STRICT_JSON: Final = ("NaN", "Infinity")
_ENCODE_STRING: Final = json.encoder.encode_basestring
#: Containers written as an array (when writing from the value's type, and when matching a union arm in lax mode).
_ARRAYS: Final = (list, tuple, set, frozenset)


def _is_array(value: object) -> TypeGuard[Collection[object]]:
    """Whether this is a container written as an array (list, tuple, set, frozenset -- not str or dict). Elements are only read, so it is narrowed as object."""
    return isinstance(value, _ARRAYS)


def _is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    """Whether this is a mapping written as an object (dict and frozen FrozenMap). Keys and values are only read, so it is narrowed as object."""
    return isinstance(value, Mapping)


def _opaque_out(value: _OpaqueJson, raw: bool) -> _Json:
    """Writing OpaqueJson: when writing to a string, splice in the string it holds as it is; when writing to a JSON value, or when it contains NaN or Infinity, re-read it and
    write from the value's type (same as the current pydantic dump and dump-json)."""
    text = value.text
    if raw and not any(spelling in text for spelling in _NOT_STRICT_JSON):
        return _Raw(text)
    return _inferred(json.loads(text), raw)


def _json_key(key: object) -> str:
    """Spelling of an object key written from the value's type (same as the current pydantic: subclasses of str and defenum use the value spelling, bool is true / false, None is None,
    numbers use the number spelling). Other keys are TypeError."""
    if isinstance(key, enum.Enum):
        return _json_key(key.value)
    if isinstance(key, str):
        return str.__str__(key)
    if key is None:
        return "None"
    if isinstance(key, int | float):  # bool is true / false; numbers use the same spelling as writing
        return _json_text(_inferred(key, False))
    raise TypeError(f"cannot be written as a JSON object key: {type(key).__name__}")


def _inferred(value: object, raw: bool) -> _Json:
    """Write a value outside the type tree (the contents of object and plain dict fields, and values that do not fit the field type) as a plain JSON value from the value's type. Same as the current pydantic's writing from the value's type
    such as: NaN and inf are null, tuple and set are arrays, defenum is the value spelling. A defwire value and OpaqueJson use that type's writing
    (pydantic wrote the Python field name and the OpaqueJson field text -- section 3b). A value that cannot be written as JSON is TypeError."""
    if value is None or type(value) is str or type(value) is int or type(value) is bool:
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if _is_mapping(value):
        return {_json_key(key): _inferred(item, raw) for key, item in value.items()}
    if _is_array(value):
        return [_inferred(item, raw) for item in value]
    return _inferred_named(value, raw)


def _inferred_named(value: object, raw: bool) -> _Json:
    """Continuation of _inferred -- write a value that is not a plain JSON type (OpaqueJson, a defwire value, defenum, subclasses of str or numbers)."""
    if isinstance(value, _OpaqueJson):
        return _opaque_out(value, raw)
    if _is_wire_type(type(value)):
        return codec_of(type(value)).node.encode(value, raw)
    if isinstance(value, enum.Enum):
        return _inferred(value.value, raw)
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, int):  # bool is already handled by _inferred
        return int(value)
    if isinstance(value, float):
        return _inferred(float(value), raw)
    raise TypeError(f"not a JSON value: {type(value).__name__}")


def _float_text(number: float) -> str:
    """JSON spelling of a float. Same as the current pydantic (serde_json and ryu): the shortest digits that round-trip (the same digits as Python repr), no leading 0 in the exponent
    (`1e-7`), digits up to 10 to the -5 are written as a decimal (`0.00001`), and NaN and inf are null (section 2 of the design record, "float spelling")."""
    if not math.isfinite(number):
        return "null"
    text = float.__repr__(number)
    if "e" not in text:
        return text
    mantissa, exponent = text.split("e")
    power = int(exponent)
    if power == -5:
        sign, digits = ("-", mantissa[1:]) if mantissa.startswith("-") else ("", mantissa)
        return f"{sign}0.0000{digits.replace('.', '')}"
    return f"{mantissa}e{'+' if power > 0 else '-'}{abs(power)}"


def _string_text(text: str) -> str:
    """JSON spelling of one string (same as the current pydantic: only `"` and `\\` and control characters are escaped; non-ASCII, U+2028, and DEL are left as they are).
    A lone surrogate cannot be UTF-8, so ValueError (the current pydantic dump-json also rejects without writing)."""
    if not text.isascii() and _SURROGATE.search(text) is not None:
        raise ValueError("a string containing a lone surrogate cannot be written as a JSON string (UTF-8)")
    return _ENCODE_STRING(text)


def _json_text(value: _Json) -> str:
    """Turn the written plain JSON value into a JSON string (no separator whitespace, no ensure_ascii -- the same bytes as the current dump-json)."""
    # Check types in order of frequency (string, object, array). Pass a list comprehension to join (faster than a generator -- this is called dozens of times per write).
    if isinstance(value, str):
        return _string_text(value)
    if isinstance(value, dict):
        return (
            "{"
            + ",".join([_string_text(key) + ":" + _json_text(item) for key, item in value.items()])
            + "}"
        )
    if isinstance(value, list):
        return "[" + ",".join([_json_text(item) for item in value]) + "]"
    return _scalar_text(value)


def _has_surrogate(text: str) -> bool:
    """Whether the string has a surrogate (to find a lone surrogate as something that cannot be written as a JSON string and is not accepted as a JSON value).
    Only surrogates cannot be UTF-8, so detect via an encoding failure, which is faster than searching with a regular expression (12 µs → 3 µs for 1 KB of non-ASCII)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def _scalar_text(value: _Json) -> str:
    """Spelling of a written value that is not a container (number, bool, null, and the OpaqueJson string -- check bool before int)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return int.__repr__(value)
    if isinstance(value, float):
        return _float_text(value)
    if isinstance(value, _Raw):
        return value.text
    if value is None:
        return "null"
    raise TypeError(f"not a written value: {type(value).__name__}")


# --- JSON Schema ---------------------------------------------------------------------------------------------------------------


@final
class _RefMark:
    """Mark of a $ref target (one per type). Replaced by a $defs name after building finishes -- so that if different types share a name, the module is added to the name."""

    __slots__ = ("target",)

    def __init__(self, target: type) -> None:
        self.target = target


@final
class _Definitions:
    """The $defs while building one JSON Schema. Builds and places each defwire type and defenum once, and fields point to them with $ref."""

    __slots__ = ("marks", "shapes")

    def __init__(self) -> None:
        self.marks: dict[type, _RefMark] = {}
        self.shapes: dict[type, _Schema] = {}

    def ref(self, target: type, build: Callable[["_Definitions"], _Schema]) -> _Schema:
        """Place the type in $defs (building it if not yet there) and return a $ref pointing to it."""
        mark = self.marks.get(target)
        if mark is None:
            mark = _RefMark(target)
            self.marks[target] = mark
            self.shapes[target] = build(self)
        return {"$ref": mark}

    def names(self) -> dict[type, str]:
        """Name in $defs: the type name. If different types share a name, they use the module and qualname spelling (same as the current pydantic -- `.` becomes `__`)."""
        counts = Counter(target.__name__ for target in self.shapes)
        return {
            target: target.__name__
            if counts[target.__name__] == 1
            else re.sub(
                r"[^a-zA-Z0-9.\-_]", "_", f"{target.__module__}.{target.__qualname__}"
            ).replace(".", "__")
            for target in self.shapes
        }


def _schema_default(value: object) -> _Json:
    """default of a JSON Schema field -- write the default from the value's type (NaN and inf of a typed float stay float -- same as the current pydantic).
    The defaults of a nested defwire and of OpaqueJson take the shape the writer writes (pydantic wrote the Python field name and the OpaqueJson field text -- section 3b)."""
    if type(value) is float:
        return value
    return _inferred(value, False)


def _field_schema(field: _WireField, defs: _Definitions) -> _Schema:
    """JSON Schema of one field: add to the type shape the default (not written for a factory field) and the title (from the wire name -- not added to defwire and defenum, whose type side carries a title, and
    to their (| T None)) (same as the current pydantic)."""
    default: _Schema = (
        {"default": _schema_default(field.default)}
        if field.default is not dataclasses.MISSING
        else {}
    )
    title: _Schema = (
        {"title": field.wire.title().replace("_", " ").strip()} if field.node.titled() else {}
    )
    return {**field.node.schema(defs), **default, **title}


def _json_hashable(value: _Json) -> object:
    """Turn a JSON Schema value into a hashable form so arms of the same shape can be collapsed into one (same as pydantic's _make_json_hashable)."""
    if isinstance(value, dict):
        return tuple(sorted((key, _json_hashable(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_json_hashable(item) for item in value)
    return value


def _arms_of(schema: _Schema) -> list[_Schema]:
    """Open up and list the shape of one union arm if it is an anyOf-only shape (same as pydantic's get_flattened_anyof)."""
    arms = schema.get("anyOf")
    if len(schema) == 1 and isinstance(arms, list):
        return [arm for arm in arms if isinstance(arm, dict)]
    return [schema]


def _any_of(members: tuple[_Schema, ...]) -> _Schema:
    """JSON Schema of a union (same as the current pydantic: anyOf-only arms are opened up, arms of the same shape are collapsed into one, and with one arm there is no anyOf wrapper)."""
    unique = list(
        {_json_hashable(arm): arm for member in members for arm in _arms_of(member)}.values()
    )
    if len(unique) == 1:
        return unique[0]
    arms: list[_Json] = [*unique]
    return {"anyOf": arms}


def _sorted_schema(value: _Json, parent: str | None, names: dict[type, str]) -> _Json:
    """Order the keys of the built JSON Schema the same as the current pydantic (the $defs names too are in key order) and replace $ref marks with names. The ordering is the same as pydantic's sort:
    directly under properties and default keep declaration order, others are in key character order, and list elements inherit the parent key (some writers compare contract files by bytes,
    so the order is matched too)."""
    if isinstance(value, dict):
        keys = list(value) if parent in ("properties", "default") else sorted(value)
        return {key: _sorted_schema(value[key], key, names) for key in keys}
    if isinstance(value, list):
        return [_sorted_schema(item, parent, names) for item in value]
    if isinstance(value, _RefMark):
        return f"#/$defs/{names[value.target]}"
    return value


def _schema_of(node: _Node) -> _Schema:
    """JSON Schema of the root of the type tree. The top-level defwire type and defenum put the shape as it is instead of a $ref, and nested types go in $defs."""
    defs = _Definitions()
    top = node.definition(defs) if isinstance(node, _WireNode | _Enum) else node.schema(defs)
    names = defs.names()
    nested: _Schema = {names[target]: shape for target, shape in defs.shapes.items()}
    whole = _sorted_schema({**top, "$defs": nested} if nested else top, None, names)
    if not isinstance(whole, dict):
        raise TypeError(
            "the JSON Schema root is not an object"
        )  # the root is a reordered object shape -- cannot happen
    return whole


# --- Public entry ---------------------------------------------------------------------------------------------------------------


@final
class Codec(Generic[_T]):
    """Decoder of one vocabulary type (the root of the type tree and the type name written to Malformed). Created only by ``codec_of``."""

    __slots__ = ("name", "node")

    def __init__(self, node: _Node, name: str) -> None:
        self.node = node
        self.name = name

    def decode_json(self, text: str | bytes) -> _T | _Malformed:
        """Decode a JSON string (bytes too) and check it. Result = a value of the type or Malformed (including a string that cannot be read as JSON -- the path is the empty string)."""
        try:
            return cast(_T, self._decoded(_read_json(text)))
        except _RefusedError as invalid:
            return self._malformed(invalid.problems)

    def decode(self, value: object) -> _T | _Malformed:
        """Decode a JSON value (frozen FrozenMap, tuple, and OpaqueJson carried without reading inside) and check it. The answer is the same as turning the same value into a JSON
        string and passing it to decode_json. Something that is not a JSON value is TypeError."""
        try:
            if isinstance(value, _OpaqueJson):
                return cast(_T, self._decoded(_read_json(value.text)))
            return cast(_T, self._decoded(value))
        except _RefusedError as invalid:
            return self._malformed(invalid.problems)

    def _decoded(self, value: object) -> object:
        """Read the value first on the fast path (_Node.fast -- no copying), and only if it is not accepted re-read with the existing path (copy with _plain and decode).
        When the fast path does not accept (it rejected, there was a spot unreadable as a plain value, or a type constructor raised), the existing path decides the answer in every case --
        collecting all errors and raising exceptions happen in that one place (the fast path stops at the first error, and its exception order may differ from the existing path)."""
        try:
            return self.node.fast(value, _DEPTH_LIMIT)
        except Exception:
            return self.node.decode(_plain(value))

    def encode(self, value: _T) -> object:
        """Write a value as a JSON value (same as the current dump: fields use wire names, tuple and frozenset are arrays, defenum is the value spelling, fields equal to their default are not written
        -- nested too; a field without a default is null even when None; NaN and inf in a typed float field stay float). A value that cannot be written as JSON is TypeError."""
        return self.node.encode(value, False)

    def encode_json(self, value: _T) -> str:
        """Write a value as a JSON string (the same bytes as the current dump-json: the same shape as encode, no separator whitespace, NaN and inf are null, and OpaqueJson fields splice in
        the string they hold without re-reading). A string containing a lone surrogate is ValueError; a value that cannot be written as JSON is TypeError."""
        try:
            text = self.node.text(value)
        except Exception:
            return _json_text(self.node.encode(value, True))
        if not text.isascii() and _has_surrogate(text):
            return _json_text(self.node.encode(value, True))  # lone surrogate -- the existing path rejects with ValueError
        return text

    def schema(self) -> Mapping[str, object]:
        """JSON Schema of this type (same shape and key order as the current json-schema -- differences are in section 3b of the design record). Built on every call (only the place that compares with a contract file
        uses it -- not remembered on the type)."""
        return _schema_of(self.node)

    def _malformed(self, problems: list[_Problem]) -> _Malformed:
        """Map the collected list of errors to Malformed (paths are joined with ., reason is `<msg> [<kind>]`)."""
        return _Malformed(
            wire_type=self.name,
            fields=tuple(
                _MalformedField(field=".".join(str(part) for part in loc), reason=f"{msg} [{kind}]")
                for loc, kind, msg in problems
            ),
        )


#: Codec for a vocabulary type that is not defwire (such as the top-level (get dict #(str OpaqueJson))). The Codec of a defwire type is remembered as an attribute of the type.
_ALIAS_CODECS: Final[dict[object, Codec[Any]]] = {}


@typing.overload
def codec_of(wire_type: type[_T]) -> Codec[_T]: ...
@typing.overload
def codec_of(wire_type: object) -> Codec[Any]: ...
def codec_of(wire_type: object) -> Codec[Any]:
    """Decoder of one vocabulary type. A defwire type builds the type tree once on first use and remembers it on the type (``__doeff_codec__``); it is not rebuilt afterwards.
    A vocabulary type that is not defwire (``str``, ``(get dict #(str OpaqueJson))``, etc. -- decoded as the top level) is also built and remembered by the same rule.
    A type outside the vocabulary, an unresolvable forward reference, or a type cycle is TypeError."""
    if _is_wire_type(wire_type):
        known = cast(type, wire_type).__dict__.get(_CODEC_ATTRIBUTE)
        if known is None:
            _wire_node(cast(type, wire_type))
            known = cast(type, wire_type).__dict__[_CODEC_ATTRIBUTE]
        return cast(Codec[Any], known)
    known_alias = _ALIAS_CODECS.get(wire_type)
    if known_alias is not None:
        return known_alias
    with _LOCK:
        known_alias = _ALIAS_CODECS.get(wire_type)
        if known_alias is None:
            node = _build(wire_type, {}, f"type {wire_type!r}")
            known_alias = Codec(node, node.label)
            _ALIAS_CODECS[wire_type] = known_alias
        return known_alias


def row_codec_of(row_type: type[_T]) -> Codec[_T]:
    """Decoder of one table-row type (used by typed.hy in doeff-records -- section 6 of the design record). A row type is a plain defrecord (a
    dataclass that is not defwire); field names are Python field names, unknown fields are discarded (an old reader does not reject a row from a new writer that added a field), and when writing,
    fields equal to their default are also written (a row write is a per-field diff, so it is an image carrying all fields). A nested plain defrecord is read the same way, and a nested
    defwire type uses that type's reading. Per-field judgment is the same strict as defwire (section 2). Built on first use and remembered on the type (``__doeff_row_codec__``).
    A defwire type, a type that is not a dataclass, or a field outside the vocabulary is TypeError."""
    if not (isinstance(row_type, type) and dataclasses.is_dataclass(row_type)) or _is_wire_type(row_type):
        raise TypeError(f"row type must be a plain defrecord (a dataclass that is not defwire): {row_type!r}")
    known = row_type.__dict__.get(_ROW_CODEC_ATTRIBUTE)
    if known is None:
        _row_node(row_type)
        known = row_type.__dict__[_ROW_CODEC_ATTRIBUTE]
    return cast(Codec[_T], known)
