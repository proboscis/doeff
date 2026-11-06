"""A tool that checks the in-house decoder of defwire (wire_codec.py) against the pydantic decoder — read only inside tests.

Decision: the defwire decoder is kept in-house inside doeff, and pydantic is kept only as the
counterpart to compare against in tests. This module is the only home of that counterpart, and it reads pydantic. The job path (entry, foundation, body) does not
import this module (it does not read doeff_hy._wire or wire_codec — a test of the import closure checks this). pydantic is not a dependency of the doeff-hy body;
it comes from the extra doeff-hy[parity], which a project with parity tests puts in its test dependency group. If a module on the job path
imports pydantic, doeff-job-path-imports-no-pydantic in .semgrep.yaml refuses it; this module is placed outside that scope.

The contract is section 7 of the design record ``docs/design/defwire-decoder/design.md``, "What the parity test compares":

- For each type, build a valid input from the type tree, then inputs broken per field (a value of a different JSON kind, null, a missing field, an unknown field, the Python field name, a spelling outside
  the definition, array length +/-1, nesting up to 2 levels) and breakages of the string itself (a string that is not JSON, a different top-level kind).
- Pass the same input through the in-house decoder and the pydantic decoder, and check that accept/reject agree, that accepted values are equal down to the value and field value types, and that the sequences of (location, kind)
  of rejections are equal. For msg, only the spellings of the kinds missing, enum, value_error, and assertion_error are compared (the msg of other kinds is determined by the kind).
- Dump the accepted value with both writers, and check that dump is equal down to the value and its types, and dump-json is equal down to the bytes.
- Check that the JSON Schema is equal down to key order. The only differences are the defaults in the table of section 3b (nested defwire, OpaqueJson), and those fields are counted as differences.
- Types with a union field that mixes an accept-anything arm (OpaqueJson, object) with a non-None arm are left out of the comparison of accepted values and counted (rule 4 of section 3 —
  where the answer that differs from pydantic is taken as correct). They stay in the accept/reject comparison.

Usage (takes a list of types as an argument so it can be used for agora-controllers types too):

    types, failures = wire_types_in(["doeff_cluster"])   # read every module under the package and collect defwire types
    report = compare_types(types)                         # per-type comparison
    assert report.mismatches == []                        # each mismatch (type, input, what differs)

The pydantic decoder is built from the ``__pydantic_config__`` of the current defwire type (accepts and writes only the wire names of :names; :unknown; strict). If the type
does not have one, this module sets the same config (from the names and unknown of the wire shape ``__doeff_wire__``).
"""

import dataclasses
import importlib
import inspect
import json
import math
import re
import secrets
import sys
import warnings
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, Final, cast

from pydantic import ConfigDict, TypeAdapter, ValidationError
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema

from doeff_hy._frozen import thaw_json
from doeff_hy._json_value import OpaqueJson
from doeff_hy._wire_codec import (
    Codec,
    _Anything,
    _Array,
    _BareDict,
    _Bool,
    _Dict,
    _Enum,
    _FixedTuple,
    _Float,
    _Int,
    _Literal,
    _Node,
    _NoneOnly,
    _Opaque,
    _Str,
    _Union,
    _WireNode,
    codec_of,
)
from doeff_hy._wire_malformed import Malformed, MalformedField

# --- The pydantic decoder (same config and calls as parse-json, dump, dump-json, and json-schema of the current wire.hy) ----------------------------------

#: Spelling of how unknown fields are handled (the value of defwire's :unknown) -> pydantic's extra.
_EXTRA: Final = {"reject": "forbid", "ignore": "ignore"}


def pydantic_config(names: Mapping[str, str], unknown: str) -> ConfigDict:
    """pydantic config of a defwire type: field names accept and write only the copy in names, unknown fields follow unknown, and type checks are strict."""
    return ConfigDict(
        alias_generator=names.__getitem__,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        extra=cast(Any, _EXTRA[unknown]),
        strict=True,
        defer_build=True,
    )


def _configured(wire_type: type) -> None:
    """If the type (and nested defwire types) has no pydantic config, set it from the wire shape."""
    if "__pydantic_config__" in wire_type.__dict__:
        return
    shape = wire_type.__dict__["__doeff_wire__"]
    wire_type.__pydantic_config__ = pydantic_config(shape.names, shape.unknown)  # type: ignore[attr-defined]


_ADAPTERS: Final[dict[type, TypeAdapter[Any]]] = {}


def adapter_of(wire_type: type) -> TypeAdapter[Any]:
    """The pydantic decoder of a defwire type (built once per type and remembered)."""
    known = _ADAPTERS.get(wire_type)
    if known is None:
        for nested in _wire_types_under(codec_of(wire_type).node):
            _configured(nested)
        known = TypeAdapter(wire_type)
        _ADAPTERS[wire_type] = known
    return known


def _malformed_of(wire_type: type, error: ValidationError) -> Malformed:
    """Map a pydantic validation error to Malformed (the field location joins wire names and array positions with . — malformed-of of the current wire.hy)."""
    return Malformed(
        wire_type=wire_type.__name__,
        fields=tuple(
            MalformedField(
                field=".".join(str(part) for part in problem["loc"]),
                reason=f"{problem['msg']} [{problem['type']}]",
            )
            for problem in error.errors(include_url=False)
        ),
    )


def pydantic_parse_json(wire_type: type, text: str | bytes) -> object:
    """Same answer as parse-json of the current wire.hy (a value of the type or Malformed)."""
    try:
        return adapter_of(wire_type).validate_json(text)
    except ValidationError as error:
        return _malformed_of(wire_type, error)


def pydantic_parse(wire_type: type, raw: object) -> object:
    """Same answer as parse of the current wire.hy (rewrite the JSON value to a string and pass it to parse-json)."""
    text = (
        raw.text if isinstance(raw, OpaqueJson) else json.dumps(thaw_json(raw), ensure_ascii=False)
    )
    return pydantic_parse_json(wire_type, text)


def pydantic_dump(value: object) -> object:
    """Same answer as dump of the current wire.hy."""
    return adapter_of(type(value)).dump_python(
        value, mode="json", by_alias=True, exclude_defaults=True
    )


def pydantic_dump_json(value: object) -> str:
    """Same answer as dump-json of the current wire.hy (an OpaqueJson field embeds the string it holds as is — _Splice)."""
    splice = _Splice()
    written = adapter_of(type(value)).dump_json(
        value, by_alias=True, exclude_defaults=True, context=splice
    )
    return splice.fill(written)


class _WireDocSchema(GenerateJsonSchema):
    """pydantic's schema writer that takes a defwire type's description from its wire shape (WireShape.description).

    The defrecord runtime expansion keeps no docstring on the type (its __doc__ is the shared descriptor ``Name(*, a, b)``, which pydantic drops as a dataclass
    signature), so pydantic alone would write no description. The docstring pydantic would have read is the one the wire shape holds; this puts it where pydantic puts a
    class docstring (tidied with inspect.cleandoc; key order comes from pydantic's sort afterwards)."""

    def dataclass_schema(self, schema: core_schema.DataclassSchema) -> JsonSchemaValue:
        """pydantic's dataclass schema, plus the wire shape's description when the type is a defwire type that has one."""
        json_schema = super().dataclass_schema(schema)
        shape = schema["cls"].__dict__.get("__doeff_wire__")
        if shape is not None and shape.description and "description" not in json_schema:
            json_schema["description"] = inspect.cleandoc(shape.description)
        return json_schema


def pydantic_json_schema(wire_type: type) -> object:
    """Same answer as json-schema of the current wire.hy."""
    return adapter_of(wire_type).json_schema(by_alias=True, schema_generator=_WireDocSchema)


# Embedding marker (the same mechanism as OpaqueSplice in the current json_value.py — pydantic_core has no hook to put an already-written JSON string into the output, so
# the OpaqueJson writer returns a marker, and after the output bytes are produced the marker is replaced by the original string). The pydantic-side writer of OpaqueJson
# (_opaque_value in json_value.py) returns the marker when the context has admit.
_SPLICE_PREFIX: Final = secrets.token_hex(8)
_SPLICE_MARK: Final = re.compile(b'"' + _SPLICE_PREFIX.encode("ascii") + rb"([0-9]+)\"")
_NOT_STRICT_JSON: Final = ("NaN", "Infinity")


class _Splice:
    """A record, alive only during one pydantic_dump_json, that embeds the OpaqueJson string into the output as is."""

    __slots__ = ("_by_number",)

    def __init__(self) -> None:
        self._by_number: dict[int, bytes] = {}

    def admit(self, text: str) -> str | None:
        if any(spelling in text for spelling in _NOT_STRICT_JSON):
            return None
        number = len(self._by_number)
        self._by_number[number] = text.encode("utf-8")
        return f"{_SPLICE_PREFIX}{number}"

    def fill(self, written: bytes) -> str:
        by_number = self._by_number
        if not by_number:
            return written.decode("utf-8")
        return _SPLICE_MARK.sub(lambda mark: by_number[int(mark[1])], written).decode("utf-8")


# --- Collecting types -----------------------------------------------------------------------------------------------------------------


def _is_wire_type(candidate: object) -> bool:
    return (
        isinstance(candidate, type)
        and dataclasses.is_dataclass(candidate)
        and "__doeff_wire__" in candidate.__dict__
    )


def _module_names(package_name: str) -> list[str]:
    """List every module name under the package from the file layout (including namespace-package dirs without __init__ and Hy modules — pkgutil follows
    neither)."""
    package = importlib.import_module(package_name)
    roots = [Path(entry) for entry in getattr(package, "__path__", [])]
    return [package_name] + sorted(
        {
            ".".join((package_name, *path.relative_to(root).with_suffix("").parts)).removesuffix(
                ".__init__"
            )
            for root in roots
            for path in root.rglob("*")
            if path.suffix in (".py", ".hy")
            and all(part.isidentifier() for part in path.relative_to(root).with_suffix("").parts)
        }
    )


def _imported(name: str) -> object:
    """Import a module (returns a reason string if it cannot be imported — some modules lack optional dependencies, so return the reason and let the caller decide)."""
    try:
        return importlib.import_module(name)
    except Exception as error:
        return f"{type(error).__name__}: {error}"
    except SystemExit as error:  # a script module that exits when imported
        return f"SystemExit: {error}"


def wire_types_in(
    packages: Iterable[str], *, skip: Callable[[str], bool] = lambda _name: False
) -> tuple[list[type], dict[str, str]]:
    """Import every module under the package (module names for which skip is true are not imported) and collect the defwire types defined there.
    Answer = (list of types — in module-name and definition order, module names that could not be imported -> reason)."""
    names = sorted(
        {
            name
            for package_name in packages
            for name in _module_names(package_name)
            if not skip(name)
        }
    )
    modules = [(name, _imported(name)) for name in names]
    failures = {name: module for name, module in modules if isinstance(module, str)}
    found = [
        candidate
        for name, module in modules
        if not isinstance(module, str)
        for candidate in list(vars(module).values())
        if _is_wire_type(candidate) and candidate.__module__ == name
    ]
    return list(dict.fromkeys(found)), failures


# --- Building inputs -----------------------------------------------------------------------------------------------------------------

#: Values of a different JSON kind to put into any field (checks that both reject a value outside the field type for the same reason).
_WRONG_KINDS: Final[tuple[object, ...]] = (
    "x",
    7,
    2.5,
    False,
    None,
    [],
    {},
    [1],
    {"zz": 1},
    0,
    -1,
    "",
    1e400,
)
_UNKNOWN_KEY: Final = "zzUnknownField"
#: Depth to descend into nesting when breaking (top level counted as 0; nested objects up to 2 levels).
_DEPTH: Final = 2


def _nodes(node: _Node, seen: set[int] | None = None) -> Iterator[_Node]:
    """One discriminator for each node of the type tree (including itself; the same discriminator once — a nested type points at the same discriminator rather than being rebuilt per parent)."""
    seen = set() if seen is None else seen
    if id(node) in seen:
        return
    seen.add(id(node))
    yield node
    for child in _children(node):
        yield from _nodes(child, seen)


def _wire_types_under(node: _Node) -> list[type]:
    """The defwire types under the type tree (including itself, without duplicates) — to list the types that get a pydantic config."""
    return [current.wire_type for current in _nodes(node) if isinstance(current, _WireNode)]


def _children(node: _Node) -> tuple[_Node, ...]:
    """Children of a discriminator (field, element, and arm discriminators) — to walk the type tree."""
    match node:
        case _WireNode():
            return tuple(field.node for field in node.fields)
        case _Array() | _Dict():
            return (node._item,)
        case _FixedTuple():
            return node._items
        case _Union():
            return node._arms
        case _:
            return ()


def mixed_catch_all_unions(node: _Node) -> int:
    """Count of unions that mix an accept-anything arm with a non-None arm (under the type tree — to count where rule 4 takes the answer that differs from pydantic as correct)."""
    return sum(
        1
        for current in _nodes(node)
        if isinstance(current, _Union)
        and len(current._arms) > 1
        and any(arm.catch_all for arm in current._arms)
    )


#: Samples for discriminators with no children (discriminator type -> candidate JSON values to accept; earlier ones are used first).
_LEAF_SAMPLES: Final[dict[type, list[object]]] = {
    _Str: ["s", "id-000000000001", "a"],
    _Float: [1.5, 2],
    _Int: [1, 0, 2],
    _Bool: [True, False],
    _NoneOnly: [None],
    _BareDict: [{"k": 1}, {}],
    _Anything: [{"a": [1, "b"]}, "x", 1],
    _Opaque: [{"o": [1, "x", None]}, "x", 1],
}


def _union_samples(node: _Union, depth: int, full: bool) -> list[object]:
    """Union sample — one per arm, plus null if there is a None arm."""
    arms = [sample for arm in node._arms for sample in _samples(arm, depth, full)[:1]]
    return arms + ([None] if node._nullable else [])


#: Builders of samples for discriminators with children (discriminator type -> (discriminator, depth, whether to also write fields with defaults) -> candidates).
_SAMPLERS: Final[dict[type, Callable[[Any, int, bool], list[object]]]] = {
    _Enum: lambda node, _depth, _full: list(node._members)[:2],
    _Literal: lambda node, _depth, _full: list(node._order[:2]),
    _Array: lambda node, depth, full: [[_samples(node._item, depth + 1, full)[0]], []],
    _FixedTuple: lambda node, depth, full: [
        [_samples(item, depth + 1, full)[0] for item in node._items]
    ],
    _Dict: lambda node, depth, full: [{"k": _samples(node._item, depth + 1, full)[0]}, {}],
    _WireNode: lambda node, depth, full: [
        _object_sample(node, depth, full),
        _object_sample(node, depth, False),
    ],
    _Union: _union_samples,
}


def _samples(node: _Node, depth: int, full: bool) -> list[object]:
    """Candidate JSON values the type accepts (earlier ones are used first) — to build valid inputs and broken values. full = also write fields that have defaults."""
    leaf = _LEAF_SAMPLES.get(type(node))
    if leaf is not None:
        return leaf
    return _SAMPLERS[type(node)](node, depth, full)


def _object_sample(node: _WireNode, depth: int, full: bool) -> dict[str, object]:
    """A sample of a valid object of a defwire type (full = also write fields that have defaults)."""
    # In deep nesting (cycles are refused by the tree, but deep nesting exists), write only fields without defaults.
    write_all = full and depth < 4
    return {
        field.wire: _samples(field.node, depth + 1, write_all)[0]
        for field in node.fields
        if field.required or write_all
    }


def _accepted_base(codec: Codec[Any], wire_type: type) -> dict[str, object]:
    """The valid input to break — of the one with all fields written and the one with only the fields without defaults, the one pydantic accepted (the all-fields one if it rejects both).
    If it accepts neither, look for an accepted shape by varying the sample candidates per field (for types whose :check picks the value)."""
    node = cast(_WireNode, codec.node)
    candidates = [_object_sample(node, 0, True), _object_sample(node, 0, False)]
    for candidate in candidates:
        if not isinstance(pydantic_parse_json(wire_type, json.dumps(candidate)), Malformed):
            return candidate
    base = dict(candidates[0])
    for field in node.fields:
        if field.wire not in base:
            continue
        for alternative in _samples(field.node, 1, True)[1:]:
            trial = {**base, field.wire: alternative}
            if not isinstance(pydantic_parse_json(wire_type, json.dumps(trial)), Malformed):
                return trial
    return base


def _mutations(node: _Node, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """The sequence (name, value) of values made by breaking the valid value `value` (the JSON of type node) in one place at a time."""
    if isinstance(node, _WireNode) and isinstance(value, dict):
        source = cast(dict[str, object], value)
        yield "unknown-field", {**source, _UNKNOWN_KEY: 1}
        for field in node.fields:
            here = field.wire
            if here in source:
                rest = {key: item for key, item in source.items() if key != here}
                yield f"{here}:missing", rest
                if field.python != field.wire:
                    yield f"{here}:python-name", {**rest, field.python: source[here]}
                for wrong in _WRONG_KINDS:
                    yield f"{here}:={json.dumps(wrong)}", {**source, here: wrong}
                for name, inner in _field_mutations(field.node, source[here], depth):
                    yield f"{here}.{name}", {**source, here: inner}
            else:
                for sample in _samples(field.node, depth + 1, False)[:2]:
                    yield f"{here}:+", {**source, here: sample}
        return
    yield from _field_mutations(node, value, depth)


def _spelling_mutations(node: Any, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """Breakages for defenum and Literal: a spelling outside the definition, and the member name of the defenum (not the value spelling)."""
    yield "outside", "no-such-spelling"
    names = [member.name for member in node.enum_type] if isinstance(node, _Enum) else []
    yield from (("member-name", name) for name in names[:1])


def _array_mutations(node: Any, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """Breakages for arrays of unbounded length: make longer, make empty, a different element kind, and inside the first element."""
    if not isinstance(value, list):
        return
    sample = _samples(node._item, depth + 1, False)[0]
    yield "longer", [*value, sample, sample]
    yield "empty", []
    yield from ((f"item={json.dumps(wrong)}", [wrong]) for wrong in ("x", None, {"zz": 1}))
    if value and depth < _DEPTH:
        yield from (
            (f"0.{name}", [inner, *value[1:]])
            for name, inner in _mutations(node._item, value[0], depth + 1)
        )


def _fixed_tuple_mutations(node: Any, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """Breakages for fixed-length arrays: length +/-1, a different kind per position, and inside each position."""
    if not isinstance(value, list):
        return
    yield "shorter", value[:-1]
    yield "longer", [*value, value[0] if value else 1]
    for index, item in enumerate(node._items):
        yield from (
            (f"{index}={json.dumps(wrong)}", [*value[:index], wrong, *value[index + 1 :]])
            for wrong in ("x", 7, None)
        )
        if depth < _DEPTH:
            yield from (
                (f"{index}.{name}", [*value[:index], inner, *value[index + 1 :]])
                for name, inner in _mutations(item, value[index], depth + 1)
            )


def _dict_mutations(node: Any, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """Breakages for dicts with string keys: a different value kind, and inside the first value."""
    if not isinstance(value, dict):
        return
    yield from ((f"value={json.dumps(wrong)}", {**value, "w": wrong}) for wrong in ("x", None, [1]))
    if value and depth < _DEPTH:
        key = next(iter(value))
        yield from (
            (f"{key}.{name}", {**value, key: inner})
            for name, inner in _mutations(node._item, value[key], depth + 1)
        )


def _nested_mutations(node: Any, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """Breakages for nested defwire (up to 2 levels)."""
    if depth < _DEPTH:
        yield from _mutations(node, value, depth + 1)


def _union_mutations(node: Any, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """Breakages for unions: a value per arm, and inside the nested defwire arm."""
    for arm in node._arms:
        yield from (
            (f"arm[{arm.label}]{index}", sample)
            for index, sample in enumerate(_samples(arm, depth + 1, True)[:2])
        )
        if isinstance(arm, _WireNode) and depth < _DEPTH:
            yield from (
                (f"arm[{arm.label}].{name}", inner)
                for name, inner in _mutations(arm, _object_sample(arm, depth + 1, True), depth + 1)
            )


#: Builders of breakages per field type (discriminator type -> breakage). Types not listed here (plain types, accept-anything types) are broken only by a different field kind (_WRONG_KINDS).
_FIELD_MUTATIONS: Final[dict[type, Callable[[Any, object, int], Iterator[tuple[str, object]]]]] = {
    _Enum: _spelling_mutations,
    _Literal: _spelling_mutations,
    _Array: _array_mutations,
    _FixedTuple: _fixed_tuple_mutations,
    _Dict: _dict_mutations,
    _WireNode: _nested_mutations,
    _Union: _union_mutations,
}


def _field_mutations(node: _Node, value: object, depth: int) -> Iterator[tuple[str, object]]:
    """Breakages per field type (a spelling outside the definition, array length +/-1, a value per union arm, nesting up to 2 levels)."""
    mutate = _FIELD_MUTATIONS.get(type(node))
    if mutate is not None:
        yield from mutate(node, value, depth)


#: Breakages of the string itself (a string that is not JSON, a different top-level kind, differences in how strings are read — design record section 3, "Reading JSON").
_TEXT_CASES: Final[tuple[tuple[str, str | bytes], ...]] = (
    ("not-json", "{"),
    ("trailing", '{"a":1} x'),
    ("top-array", "[]"),
    ("top-null", "null"),
    ("top-string", '"x"'),
    ("top-number", "1"),
    ("empty", ""),
    ("lone-surrogate", '{"a":"\\ud800"}'),
    ("bom", "﻿{}"),
    ("not-utf8", b'{"a":"\xff"}'),
    ("deep", "[" * 3000 + "]" * 3000),
)


def inputs_for(wire_type: type) -> list[tuple[str, str | bytes]]:
    """Parity inputs for one type (name, JSON string — the same string once)."""
    codec = codec_of(wire_type)
    base = _accepted_base(codec, wire_type)
    full = _object_sample(cast(_WireNode, codec.node), 0, True)
    named = [("base", base), ("full", full), *_mutations(codec.node, base, 0)]
    by_text = {
        json.dumps(value): name for name, value in reversed(named)
    }  # the same string once, under the earlier name
    return [(name, text) for text, name in reversed(by_text.items())] + list(_TEXT_CASES)


# --- Comparing ---------------------------------------------------------------------------------------------------------------------


def typed(value: object) -> object:
    """Turn a value into a (type, content) tree — to compare while telling apart what == does not: 1 and 1.0, tuple and list, str and StrEnum."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return (
            type(value),
            tuple(
                (field.name, typed(getattr(value, field.name)))
                for field in dataclasses.fields(value)
            ),
        )
    if isinstance(value, list | tuple):
        return (type(value), tuple(typed(item) for item in value))
    if isinstance(value, dict):
        return (type(value), tuple((key, typed(item)) for key, item in value.items()))
    if isinstance(value, frozenset):
        return (type(value), tuple(sorted(value, key=repr)))
    if isinstance(value, float) and math.isnan(value):  # NaN is not equal to itself
        return (float, "nan")
    return (type(value), value)


#: Kinds whose msg is also compared (the msg of other kinds is a spelling determined by the kind). The msg of input that cannot be read as JSON differs between Python and pydantic (section 3).
_MSG_KINDS: Final = frozenset(
    {"missing", "enum", "value_error", "assertion_error", "literal_error", "too_long"}
)


def _problems(answer: Malformed) -> list[tuple[str, str]]:
    """Sequence of rejection (location, kind) (for kinds in _MSG_KINDS, the whole reason) — to compare the rejections of both decoders."""
    return [
        (field.field, field.reason if kind in _MSG_KINDS else kind)
        for field in answer.fields
        for kind in (field.reason.rpartition(" [")[2].rstrip("]"),)
    ]


@dataclasses.dataclass(frozen=True)
class Mismatch:
    """One mismatch: type name, input name, input, what differs (weaker = the in-house decoder accepted and pydantic rejected — the side that weakens the type's check)."""

    wire_type: str
    case: str
    text: str
    what: str
    ours: str
    theirs: str
    weaker: bool = False


@dataclasses.dataclass(frozen=True)
class InputVerdict:
    """Outcome of the comparison of one input: whether both accepted, and the list of mismatches."""

    accepted: bool
    mismatches: tuple[Mismatch, ...]


@dataclasses.dataclass(frozen=True)
class Report:
    """Result of the comparison: number of types, number of inputs, number of inputs both accepted (= number of writes compared), mismatches, counted differences (defaults of section 3b, types of rule 4),
    and types that could not be built (type name -> reason)."""

    types: int = 0
    inputs: int = 0
    accepted: int = 0
    mismatches: tuple[Mismatch, ...] = ()
    schema_default_differences: tuple[str, ...] = ()
    catch_all_types: tuple[str, ...] = ()
    unbuildable: Mapping[str, str] = dataclasses.field(default_factory=dict)


def _call(function: Callable[[], object]) -> tuple[bool, object]:
    """The answer of a call or the text of the exception — to compare the exceptions of both decoders as answers."""
    try:
        return True, function()
    except Exception as error:  # a comparison tool — compare the exceptions of both decoders as answers
        return False, f"{type(error).__name__}: {error}"


def _text(value: object) -> str:
    """Spelling to write in the mismatch record."""
    return value if isinstance(value, str) else repr(value)


def compare_input(wire_type: type, case: str, text: str | bytes, *, values: bool) -> InputVerdict:
    """Pass one input through both decoders and compare, then write the values both accepted with both writers and compare (values = also compare the accepted values)."""
    codec = codec_of(wire_type)
    shown = text if isinstance(text, str) else repr(text)
    shown = shown if len(shown) <= 400 else shown[:400] + "…"

    def miss(what: str, ours: object, theirs: object, weaker: bool = False) -> InputVerdict:
        found = Mismatch(
            wire_type.__qualname__,
            case,
            shown,
            what,
            _text(ours)[:600],
            _text(theirs)[:600],
            weaker,
        )
        return InputVerdict(accepted=False, mismatches=(found,))

    ok_ours, ours = _call(lambda: codec.decode_json(text))
    ok_theirs, theirs = _call(lambda: pydantic_parse_json(wire_type, text))
    match (ok_ours and ok_theirs, isinstance(ours, Malformed), isinstance(theirs, Malformed)):
        case (False, _, _):
            same = ok_ours == ok_theirs and ours == theirs
            return InputVerdict(False, ()) if same else miss("exception", ours, theirs)
        case (True, True, True):
            ours_problems = _problems(cast(Malformed, ours))
            theirs_problems = _problems(cast(Malformed, theirs))
            same = ours_problems == theirs_problems
            return (
                InputVerdict(False, ()) if same else miss("refusal", ours_problems, theirs_problems)
            )
        case (True, True, False) | (True, False, True):
            return miss("accept", ours, theirs, weaker=isinstance(theirs, Malformed))
        case _:
            if values and typed(ours) != typed(theirs):
                return miss("value", ours, theirs)
            return InputVerdict(
                True, tuple(_write_mismatches(ours, wire_type.__qualname__, case, shown))
            )


def _write_mismatches(value: object, name: str, case: str, shown: str) -> Iterator[Mismatch]:
    """Differences in the answers from writing one accepted value with both writers (dump down to the value and its types, dump-json down to the bytes)."""
    codec = codec_of(type(value))
    with warnings.catch_warnings():
        warnings.simplefilter(
            "ignore"
        )  # discard pydantic's serialization warnings for values that do not match the field type
        dumped = (_call(lambda: codec.encode(value)), _call(lambda: pydantic_dump(value)))
        texts = (_call(lambda: codec.encode_json(value)), _call(lambda: pydantic_dump_json(value)))
    for what, ((ok_ours, ours), (ok_theirs, theirs)) in (("dump", dumped), ("dump-json", texts)):
        same = ok_ours == ok_theirs and (
            typed(ours) == typed(theirs) if ok_ours else ours == theirs
        )
        if not same:
            yield Mismatch(name, case, shown, what, _text(ours)[:600], _text(theirs)[:600])


def _schema_differences(ours: object, theirs: object, where: str) -> tuple[bool, tuple[str, ...]]:
    """Compare JSON Schema down to key order. Answer = (whether they are equal apart from default, places where only the default value differs — counted as differences of section 3b)."""
    match ours, theirs:
        case dict(), dict():
            if list(ours) != list(theirs):
                return False, ()
            defaults = tuple(
                f"{where}: ours={json.dumps(ours[key])} theirs={json.dumps(theirs[key])}"
                for key in ours
                if key == "default" and typed(ours[key]) != typed(theirs[key])
            )
            inner = [
                _schema_differences(ours[key], theirs[key], f"{where}/{key}")
                for key in ours
                if not (key == "default" and typed(ours[key]) != typed(theirs[key]))
            ]
            return all(same for same, _ in inner), defaults + tuple(
                line for _, lines in inner for line in lines
            )
        case list(), list():
            if len(ours) != len(theirs):
                return False, ()
            inner = [
                _schema_differences(a, b, f"{where}/{index}")
                for index, (a, b) in enumerate(zip(ours, theirs, strict=True))
            ]
            return all(same for same, _ in inner), tuple(
                line for _, lines in inner for line in lines
            )
        case _:
            return typed(ours) == typed(theirs), ()


@dataclasses.dataclass(frozen=True)
class _TypeVerdict:
    """Outcome of the comparison of one type."""

    inputs: int
    accepted: int
    mismatches: tuple[Mismatch, ...]
    schema_default_differences: tuple[str, ...]
    catch_all: bool


def _compare_type(wire_type: type, codec: Codec[Any]) -> _TypeVerdict:
    """Pass the inputs of one type through both decoders, and also compare the written output and the JSON Schema."""
    catch_all = mixed_catch_all_unions(codec.node) > 0
    verdicts = [
        compare_input(wire_type, case, text, values=not catch_all)
        for case, text in inputs_for(wire_type)
    ]
    ok_ours, ours = _call(lambda: json.loads(json.dumps(codec.schema())))
    ok_theirs, theirs = _call(lambda: json.loads(json.dumps(pydantic_json_schema(wire_type))))
    same, defaults = (
        _schema_differences(ours, theirs, wire_type.__qualname__)
        if ok_ours and ok_theirs
        else (False, ())
    )
    schema_mismatch = (
        ()
        if same
        else (
            Mismatch(
                wire_type.__qualname__,
                "json-schema",
                "",
                "json-schema",
                _text(json.dumps(ours))[:600],
                _text(json.dumps(theirs))[:600],
            ),
        )
    )
    return _TypeVerdict(
        inputs=len(verdicts),
        accepted=sum(1 for verdict in verdicts if verdict.accepted),
        mismatches=tuple(found for verdict in verdicts for found in verdict.mismatches)
        + schema_mismatch,
        schema_default_differences=defaults,
        catch_all=catch_all,
    )


def _codec_or_refusal(wire_type: type) -> Codec[Any] | str:
    """Build the type tree (a type outside the vocabulary gives a TypeError text) — to count types that cannot be built."""
    try:
        return codec_of(wire_type)
    except TypeError as error:
        return str(error)


def compare_types(wire_types: Iterable[type]) -> Report:
    """Build inputs per type, pass them through both decoders, and also compare the written output and the JSON Schema."""
    built = [(wire_type, _codec_or_refusal(wire_type)) for wire_type in wire_types]
    unbuildable = {
        f"{wire_type.__module__}.{wire_type.__qualname__}": codec
        for wire_type, codec in built
        if isinstance(codec, str)
    }
    verdicts = [
        (wire_type, _compare_type(wire_type, codec))
        for wire_type, codec in built
        if not isinstance(codec, str)
    ]
    return Report(
        types=len(verdicts),
        inputs=sum(verdict.inputs for _, verdict in verdicts),
        accepted=sum(verdict.accepted for _, verdict in verdicts),
        mismatches=tuple(found for _, verdict in verdicts for found in verdict.mismatches),
        schema_default_differences=tuple(
            line for _, verdict in verdicts for line in verdict.schema_default_differences
        ),
        catch_all_types=tuple(
            f"{wire_type.__module__}.{wire_type.__qualname__}"
            for wire_type, verdict in verdicts
            if verdict.catch_all
        ),
        unbuildable=unbuildable,
    )


def summary(report: Report) -> str:
    """Summary of the result (used for test failure text and measurement records)."""
    head = (
        f"types={report.types} inputs={report.inputs} accepted={report.accepted} "
        f"mismatches={len(report.mismatches)} weaker={sum(1 for item in report.mismatches if item.weaker)} "
        f"schema-default-differences={len(report.schema_default_differences)} catch-all-types={len(report.catch_all_types)} "
        f"unbuildable={len(report.unbuildable)}"
    )
    mismatches = [
        f"- {item.wire_type} [{item.case}] {item.what}{' (weaker)' if item.weaker else ''}: input={item.text}\n"
        f"    ours={item.ours}\n    theirs={item.theirs}"
        for item in report.mismatches[:40]
    ]
    unbuildable = [
        f"- unbuildable {name}: {error}" for name, error in list(report.unbuildable.items())[:20]
    ]
    return "\n".join([head, *mismatches, *unbuildable])


def main(argv: list[str]) -> int:
    """Take package names as arguments, compare every defwire type, and print a summary (for viewing the whole outside tests)."""
    types, failures = wire_types_in(argv)
    report = compare_types(types)
    print(summary(report))
    for name, error in failures.items():
        print(f"- import failed {name}: {error}")
    for line in report.schema_default_differences[:20]:
        print(f"- schema default difference {line}")
    return 1 if report.mismatches else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
