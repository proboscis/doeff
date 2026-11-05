"""Tests for the hand-written writer and JSON Schema (doeff_hy/_wire_codec.py, the writing side).

The contract is the design record docs/design/defwire-decoder/design.md (the "write" column of the table in section 2, and section 3b). What is checked:

1. Values of each vocabulary type are written to the same JSON as the current pydantic dump / dump-json (dump matches values, value types and field order; dump-json matches bytes).
   This includes not writing fields equal to their default (nested too), writing None as null for fields without a default, how a union arm is chosen, values that do not match the field type, NaN and inf, and
   embedding the string of an OpaqueJson.
2. float spelling is the same as the current pydantic (random floats).
3. Decoding, writing and decoding again returns the original value (decoding the written string or the written JSON value keeps both the value and the field value types).
4. The JSON Schema matches the current pydantic json-schema down to key order, except for the known differences (defaults of nested defwire and OpaqueJson -- section 3b).
5. Unwritable values (lone surrogates, non-JSON values) are rejected, and the places that differ from pydantic (defwire values in object fields, OpaqueJson, FrozenMap).
6. The import closure of the writer and the JSON Schema contains no pydantic, so they can write without loading pydantic.
"""

import dataclasses
import importlib
import json
import math
import random
import struct
import subprocess
import sys
import textwrap
import warnings
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import doeff_hy  # noqa: F401  # enable the Hy import hook
import pytest
from doeff_hy._frozen import freeze_json
from doeff_hy._json_value import OpaqueJson
from doeff_hy._wire import WireValue
from doeff_hy._wire_codec import codec_of
from doeff_hy._wire_malformed import Malformed
from doeff_hy._wire_parity import pydantic_dump, pydantic_dump_json, pydantic_json_schema
from pydantic import TypeAdapter

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

types = importlib.import_module("wire_codec_types")


def _shape(value: object) -> object:
    """JSON value to a (type, content) tree -- tells 1 from 1.0 and True from 1, compares NaN as equal, and compares object key order too."""
    if isinstance(value, dict):
        return (dict, tuple((key, _shape(item)) for key, item in value.items()))
    if isinstance(value, list):
        return (list, tuple(_shape(item) for item in value))
    if isinstance(value, float):
        return (float, repr(value))
    return (type(value), value)


def _typed(value: object) -> object:
    """Value to a (type, content) tree -- tells apart 1 vs 1.0, tuple vs list, str vs StrEnum that == does not (sets are order-independent)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return (
            type(value),
            tuple((f.name, _typed(getattr(value, f.name))) for f in dataclasses.fields(value)),
        )
    if isinstance(value, list | tuple):
        return (type(value), tuple(_typed(item) for item in value))
    if isinstance(value, dict):
        return (type(value), tuple(sorted((key, _typed(item)) for key, item in value.items())))
    if isinstance(value, frozenset):
        return (type(value), tuple(sorted(value)))
    return (type(value), value)


def _pydantic(value: WireValue) -> tuple[object, str]:
    """Answer of the pydantic writer (doeff_hy._wire_parity -- same as dump / dump-json of wire.hy before the switch). Warnings about writing values that do not match the field type are discarded."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return pydantic_dump(value), pydantic_dump_json(value)


# --- 1. Write the same JSON as the current pydantic -------------------------------------------------------------------------------------------

NAN = math.nan
INF = math.inf

#: (id, function that builds the value). Values are built after loading the types module (to use the defenum and defwire types as arguments).
VALUES: list[tuple[str, Callable[[], WireValue]]] = [
    ("all-defaults", types.Vocab),
    ("str", lambda: types.Vocab(s="a")),
    ("str-escapes", lambda: types.Vocab(s='あ\u2028\x7f"\\\n\t\x00\x08\x0c\x1f')),
    ("int", lambda: types.Vocab(i=-3)),
    ("big-int", lambda: types.Vocab(i=10**30)),
    ("float", lambda: types.Vocab(f=1.5)),
    ("float-small", lambda: types.Vocab(f=1e-5)),
    ("float-tiny", lambda: types.Vocab(f=1e-7)),
    ("float-big", lambda: types.Vocab(f=1e16)),
    ("float-nan", lambda: types.Vocab(f=NAN)),
    ("float-inf", lambda: types.Vocab(f=-INF)),
    ("float-negative-zero", lambda: types.Vocab(f=-0.0)),
    ("bool", lambda: types.Vocab(b=True)),
    ("optional-int", lambda: types.Vocab(oi=3)),
    ("enum", lambda: types.Vocab(color=types.Color.BLUE)),
    ("literal", lambda: types.Vocab(lit="y")),
    ("tuple", lambda: types.Vocab(ti=(1, 2))),
    ("fixed-tuple", lambda: types.Vocab(tf=(1, "a"))),
    ("list", lambda: types.Vocab(ls=["a", "b"])),
    ("frozenset", lambda: types.Vocab(fs=frozenset({"a", "b", "c"}))),
    ("dict", lambda: types.Vocab(di={"k": 1, "j": 2})),
    ("bare-dict", lambda: types.Vocab(bare={"k": [1, {"x": None}], "n": NAN})),
    ("object", lambda: types.Vocab(any_value=(1, {"a": (2,)}, frozenset({3})))),
    ("object-nan", lambda: types.Vocab(any_value=[NAN, INF, 1e-5])),
    ("object-keys", lambda: types.Vocab(any_value={None: 1, 1.5: 2, True: 3, types.Color.RED: 4})),
    ("object-enum", lambda: types.Vocab(any_value=types.Color.BLUE)),
    ("opaque", lambda: types.Vocab(opaque=OpaqueJson.of({"a": [1, None], "b": 1e-5}))),
    ("opaque-spaced-text", lambda: types.Vocab(opaque=OpaqueJson('{"a": [1, 2]}'))),
    ("opaque-nan", lambda: types.Vocab(opaque=OpaqueJson.of({"a": NAN, "b": 1e-5}))),
    ("opaque-string-mentions-nan", lambda: types.Vocab(opaque=OpaqueJson.of("NaNa"))),
    ("opaque-default", lambda: types.Vocab(opaque=OpaqueJson.of(None))),
    ("nested", lambda: types.Vocab(note=types.Note(note_text="x"))),
    ("nested-default", lambda: types.Vocab(note=types.Note(note_text=""))),
    ("union-int", lambda: types.Vocab(u=1)),
    ("union-str", lambda: types.Vocab(u="a")),
    ("union-float-int-int", lambda: types.Vocab(fi=1)),
    ("union-float-int-float", lambda: types.Vocab(fi=2.0)),
    ("union-enum-str-enum", lambda: types.Vocab(cs=types.Color.RED)),
    ("union-enum-str-str", lambda: types.Vocab(cs="x")),
    ("union-arrays-list", lambda: types.Vocab(arr=[1])),
    ("union-arrays-tuple", lambda: types.Vocab(arr=("a",))),
    # Values that do not match the field type (entered via a constructor that does not check types) -- fix them or write from the value type, same as the current pydantic.
    ("misfit-float-int", lambda: types.Vocab(f=1)),
    ("misfit-float-bool", lambda: types.Vocab(f=True)),
    ("misfit-int-bool", lambda: types.Vocab(i=True)),
    ("misfit-int-float", lambda: types.Vocab(i=1.5)),
    ("misfit-str-enum", lambda: types.Vocab(s=types.Color.BLUE)),
    ("misfit-str-int", lambda: types.Vocab(s=5)),
    ("misfit-tuple-list", lambda: types.Vocab(ti=[1, 2])),
    ("misfit-list-tuple", lambda: types.Vocab(ls=("a",))),
    ("misfit-union-bool", lambda: types.Vocab(u=True)),
    ("misfit-union-float-int-bool", lambda: types.Vocab(fi=True)),
    ("misfit-dict-enum-key", lambda: types.Vocab(di={types.Color.RED: 1})),
    ("required-none", lambda: types.Required(lane_id="L", landed_at=None)),
    ("required-set", lambda: types.Required(lane_id="L", landed_at=4, n=2)),
    ("lenient", lambda: types.Lenient(lane_id="L")),
    (
        "outer",
        lambda: types.Outer(inner=types.Inner(n_val=1), by_key={"a": types.Inner(n_val=2)}),
    ),
    ("arms-a", lambda: types.Arms(ab=types.ArmA(x=1))),
    ("arms-b", lambda: types.Arms(ab=types.ArmB(x=1, y=2), da={"k": 3})),
    ("arms-dict-arm", lambda: types.Arms(da=types.ArmA(x=4))),
    ("mixed-str", lambda: types.Mixed(so="s", oa=types.ArmA(x=1), io=1)),
    (
        "mixed-opaque",
        lambda: types.Mixed(so=OpaqueJson.of([1]), oa=OpaqueJson.of({"y": 1}), io="a"),
    ),
    ("alias", lambda: types.WithAlias(v=1.5)),
    ("alias-bool", lambda: types.WithAlias(v=True)),
    (
        "lease",
        lambda: types.LeaseAnswer(
            lease_id="a",
            state=types.LeaseState.HELD,
            holders=(types.Holder(holder_id="h", expires_at=1.0),),
        ),
    ),
    (
        "defaults-set",
        lambda: types.Defaults(
            maybe_leaf=types.Leaf(leaf_name="z"),
            maybe_color=None,
            color=types.Color.RED,
            leaf=types.Leaf(leaf_name="a", n=0),
            leaves=(types.Leaf(leaf_name="b", n=2),),
            by_name={"p": types.Leaf(leaf_name="p", n=1)},
            holes=[1, None],
            ids=frozenset({5, 1, 300, 2}),
            not_a_number=1.0,
            color_or_int=types.Color.BLUE,
            lit_or_int="a",
            pair=(1, {"a": (2,)}),
            spec=OpaqueJson.of([INF]),
            maybe_pair=(1, "x"),
        ),
    ),
    (
        "defaults-default",
        lambda: types.Defaults(
            maybe_leaf=None, maybe_color=types.Color.BLUE, color=types.Color.BLUE
        ),
    ),
]


@pytest.mark.parametrize("make", [pytest.param(make, id=name) for name, make in VALUES])
def test_values_are_written_as_pydantic_writes_them(make: Callable[[], WireValue]) -> None:
    value = make()
    codec = codec_of(type(value))
    theirs, theirs_text = _pydantic(value)
    assert _shape(codec.encode(value)) == _shape(theirs)
    assert codec.encode_json(value) == theirs_text


def test_fields_equal_to_their_default_are_not_written_and_fields_without_one_are() -> None:
    codec = codec_of(types.Defaults)
    value = types.Defaults(
        maybe_leaf=None, maybe_color=None, color=types.Color.RED, leaf=types.Leaf(leaf_name="a")
    )
    # Fields without a default are written as null even for None; fields equal to the default are not written (nested type fields too -- leaf equals default). NaN is not equal to NaN, so it is written.
    assert (
        codec.encode_json(value)
        == '{"maybeLeaf":null,"maybeColor":null,"color":"red","notANumber":null}'
    )
    nested = types.Defaults(
        maybe_leaf=types.Leaf(leaf_name="z", n=0), maybe_color=None, color=types.Color.RED
    )
    assert _object(codec.encode(nested))["maybeLeaf"] == {"leafName": "z"}


def test_opaque_json_text_is_embedded_without_reading_it_again() -> None:
    # Embed the held string as is (separator whitespace and spelling included) -- re-read and written, {"a":[1,2]} and 1e-05 would become 0.00001.
    value = types.Vocab(opaque=OpaqueJson('{"a": [1, 2], "b": 1e-05}'))
    assert codec_of(types.Vocab).encode_json(value) == '{"opaque":{"a": [1, 2], "b": 1e-05}}'
    # When writing to a JSON value it is re-read. A string containing NaN or Infinity is not embedded but re-read and becomes null (same as now).
    assert codec_of(types.Vocab).encode(value) == {"opaque": {"a": [1, 2], "b": 1e-05}}
    assert (
        codec_of(types.Vocab).encode_json(types.Vocab(opaque=OpaqueJson.of([NAN])))
        == '{"opaque":[null]}'
    )


def test_vocabulary_types_are_written_at_the_top_level() -> None:
    by_key = {"a": OpaqueJson.of([1]), "b": OpaqueJson.of(None)}
    assert codec_of(dict[str, OpaqueJson]).encode_json(by_key) == '{"a":[1],"b":null}'
    assert codec_of(dict[str, OpaqueJson]).encode(by_key) == {"a": [1], "b": None}
    assert codec_of(list[int]).encode_json([1, 2]) == "[1,2]"
    assert codec_of(types.Color).encode_json(types.Color.RED) == '"red"'


# --- 2. float spelling -----------------------------------------------------------------------------------------------------------


def _random_floats() -> list[float]:
    """Random 64-bit-pattern floats (subnormals too), and the digits of numbers from 10^-330 to 10^310 (including the boundary between the 10^-5 digit and the 10^16 digit)."""
    chance = random.Random(20261027)
    patterns = [
        struct.unpack("<d", chance.getrandbits(64).to_bytes(8, "little"))[0] for _ in range(4000)
    ]
    decades = [
        float(f"{chance.choice('+-')}{chance.uniform(1, 10):.{chance.randint(0, 16)}f}e{power}")
        for power in range(-330, 309)
        for _ in range(4)
    ]
    exact = [1e-5, 1e-6, 1e-4, 9.999999999999999e-06, 1e15, 1e16, 1e17, 5e-324, 0.1, 100.0, 2.0**53]
    return [number for number in patterns + decades + exact if math.isfinite(number)]


def test_floats_are_spelled_as_pydantic_spells_them() -> None:
    codec = codec_of(float)
    adapter = TypeAdapter(float)
    differing = [
        (number, codec.encode_json(number), adapter.dump_json(number).decode())
        for number in _random_floats()
        if codec.encode_json(number) != adapter.dump_json(number).decode()
    ]
    assert not differing, differing[:10]


# --- 3. decode, write, decode again ---------------------------------------------------------------------------------------------------

#: Things that do not return to the original value after writing and decoding (ids in VALUES): NaN (NaN is not equal to NaN), inf written as null, -0.0 equal to the default 0.0
#: (not written, so it returns as 0.0), tuple and set in object fields (arrays in JSON), values that do not match the field type, union values accepted by an earlier arm when decoding
#: (the str arm before the defenum arm, the dict arm before the defwire arm, OpaqueJson in a union that has a catch-all arm -- table in section 1 of the design record).
NOT_ROUND_TRIPS = {
    "float-nan",
    "float-inf",
    "float-negative-zero",
    "union-enum-str-enum",
    "arms-dict-arm",
    "bare-dict",
    "object",
    "object-nan",
    "object-keys",
    "object-enum",
    "opaque-nan",
    "opaque-spaced-text",
    "mixed-opaque",
    "defaults-set",
    "defaults-default",
}


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(make, id=name)
        for name, make in VALUES
        if name not in NOT_ROUND_TRIPS and not name.startswith("misfit-")
    ],
)
def test_writing_then_reading_gives_the_value_back(make: Callable[[], WireValue]) -> None:
    value = make()
    codec = codec_of(type(value))
    for back in (codec.decode_json(codec.encode_json(value)), codec.decode(codec.encode(value))):
        assert back == value
        assert _typed(back) == _typed(
            value
        )  # field value types stay as they were (1 vs 1.0, tuple vs list, str vs StrEnum)


def test_defaults_round_trip_where_json_can_hold_the_value() -> None:
    # A Defaults with only the NaN and inf fields (null in JSON; NaN is not equal to itself) replaced by finite values also round-trips.
    codec = codec_of(types.Defaults)
    value = types.Defaults(
        maybe_leaf=types.Leaf(leaf_name="z"),
        maybe_color=types.Color.RED,
        color=types.Color.BLUE,
        leaves=(),
        ids=frozenset({4, 5}),
        not_a_number=2.5,
        infinite=1.0,
        spec=OpaqueJson.of({"z": [True]}),
        pair=[1, "a"],
        table={"b": [1]},
        maybe_pair=(1, "a"),
    )
    for back in (codec.decode_json(codec.encode_json(value)), codec.decode(codec.encode(value))):
        assert back == value
        assert _typed(back) == _typed(value)


# --- 4. JSON Schema ------------------------------------------------------------------------------------------------------------

SAME_SCHEMA = [
    "Vocab",
    "Required",
    "Lenient",
    "Outer",
    "Arms",
    "Mixed",
    "WithAlias",
    "LeaseAnswer",
    "Checked",
    "Note",
    "BothNotes",
    "Leaf",
]

#: Defaults that differ from pydantic (type name -> field wire name -> form the writer writes) -- pydantic writes OpaqueJson as {"text": ...} and nested defwire with all fields under their
#: Python names (the reader rejects that JSON -- section 3b).
DIFFERENT_DEFAULTS: dict[str, dict[str, object]] = {
    "Vocab": {"opaque": None, "note": {"noteText": ""}},
    "Mixed": {"oa": None},
    "Defaults": {
        "leaf": {"leafName": "a"},
        "leaves": [{"leafName": "b", "n": 2}],
        "spec": {"k": [1, 2]},
    },
}


def _object(value: object) -> dict[str, object]:
    """An object in the JSON Schema (typed as dict -- a failure if the shape differs)."""
    assert isinstance(value, dict), value
    return cast(dict[str, object], value)


def _pydantic_schema(wire_type: type) -> dict[str, object]:
    """Answer of the pydantic json-schema (doeff_hy._wire_parity -- same as wire.hy before the switch)."""
    return _object(pydantic_json_schema(wire_type))


def _defaults_of(schema: Mapping[str, object], wires: list[str]) -> dict[str, object]:
    """default of each field of the JSON Schema."""
    properties = _object(schema["properties"])
    return {wire: _object(properties[wire])["default"] for wire in wires}


def _with_defaults(schema: Mapping[str, object], defaults: dict[str, object]) -> dict[str, object]:
    """Copy of the pydantic JSON Schema field defaults converted to the form the writer writes (only fields known to differ -- section 3b)."""
    properties = _object(schema["properties"])
    return {
        **schema,
        "properties": {
            wire: ({**_object(shape), "default": defaults[wire]} if wire in defaults else shape)
            for wire, shape in properties.items()
        },
    }


@pytest.mark.parametrize("type_name", [*SAME_SCHEMA, "Defaults"])
def test_schema_is_the_pydantic_schema_with_the_same_key_order(type_name: str) -> None:
    wire_type = getattr(types, type_name)
    ours = codec_of(wire_type).schema()
    theirs = _pydantic_schema(wire_type)
    known = DIFFERENT_DEFAULTS.get(type_name, {})
    assert _defaults_of(ours, list(known)) == known
    assert json.dumps(ours, ensure_ascii=False) == json.dumps(
        _with_defaults(theirs, known), ensure_ascii=False
    )


def test_pydantic_wrote_nested_defaults_with_python_names() -> None:
    # Reason for the difference: pydantic defaults were a form that the reader (pydantic's own parse) rejects. The writer's form is accepted by the reader.
    theirs = _defaults_of(_pydantic_schema(types.Defaults), ["leaf", "spec"])
    assert theirs == {"leaf": {"leaf_name": "a", "n": 0}, "spec": {"text": '{"k":[1,2]}'}}
    assert codec_of(types.Leaf).decode({"leafName": "a"}) == types.Leaf(leaf_name="a")
    assert isinstance(codec_of(types.Leaf).decode({"leaf_name": "a", "n": 0}), Malformed)


def test_schema_names_types_of_the_same_name_by_module() -> None:
    ours = codec_of(types.BothNotes).schema()
    assert sorted(_object(ours["$defs"])) == [
        "wire_codec_types__Note",
        "wire_codec_types_other__Note",
    ]
    assert ours["properties"] == {
        "mine": {"$ref": "#/$defs/wire_codec_types__Note"},
        "theirs": {"$ref": "#/$defs/wire_codec_types_other__Note"},
    }


# --- 5. Unwritable values, and where this differs from pydantic -----------------------------------------------------------------------------------------


def test_a_lone_surrogate_cannot_be_written_as_json_text() -> None:
    value = types.Vocab(s="\ud800")
    with pytest.raises(ValueError, match="surrogate"):
        codec_of(types.Vocab).encode_json(value)
    with pytest.raises(
        ValueError, match="surrogates not allowed"
    ):  # the current pydantic does not write these either (PydanticSerializationError is a ValueError)
        pydantic_dump_json(value)
    assert codec_of(types.Vocab).encode(value) == {"s": "\ud800"}


def test_a_value_that_is_not_json_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        codec_of(types.Vocab).encode(types.Vocab(any_value=object()))
    with pytest.raises(TypeError):
        codec_of(types.Vocab).encode_json(types.Vocab(bare={"k": object()}))


def test_wire_values_and_opaque_json_in_object_fields_are_written_in_their_own_form() -> None:
    # pydantic writes a defwire value in an object field under the field's Python names and OpaqueJson as {"text": ...}, and could not write FrozenMap.
    codec = codec_of(types.Vocab)
    assert codec.encode(types.Vocab(any_value=types.Note(note_text="q"))) == {
        "anyValue": {"noteText": "q"}
    }
    assert codec.encode_json(types.Vocab(any_value=OpaqueJson.of([1]))) == '{"anyValue":[1]}'
    assert codec.encode_json(types.Vocab(bare=freeze_json({"a": [1]}))) == '{"bare":{"a":[1]}}'
    assert _pydantic(types.Vocab(any_value=types.Note(note_text="q")))[0] == {
        "anyValue": {"note_text": "q"}
    }


# --- 6. Do not load pydantic ----------------------------------------------------------------------------------------------------


def test_the_writer_and_schema_import_no_pydantic_and_work_without_it() -> None:
    script = textwrap.dedent(
        """
        import dataclasses, sys, types
        import doeff_hy
        from doeff_hy import wire_codec
        from doeff_hy._json_value import OpaqueJson

        @dataclasses.dataclass(frozen=True, kw_only=True)
        class Row:
            lane_id: str
            n: int = 0
            spec: OpaqueJson | None = None

        Row.__doeff_wire__ = types.SimpleNamespace(
            names={"lane_id": "laneId", "n": "n", "spec": "spec"}, unknown="reject"
        )
        codec = wire_codec.codec_of(Row)
        row = Row(lane_id="L", spec=OpaqueJson.of({"a": 1}))
        assert codec.encode(row) == {"laneId": "L", "spec": {"a": 1}}, codec.encode(row)
        assert codec.encode_json(row) == '{"laneId":"L","spec":{"a":1}}', codec.encode_json(row)
        assert codec.decode_json(codec.encode_json(row)) == row
        assert codec.schema()["required"] == ["laneId"], codec.schema()
        assert codec.schema()["additionalProperties"] is False
        loaded = sorted(name for name in sys.modules if name.split(".")[0] in ("pydantic", "pydantic_core"))
        assert not loaded, loaded
        print("ok")
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"
