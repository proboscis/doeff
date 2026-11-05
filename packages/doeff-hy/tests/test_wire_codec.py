"""Tests for the home-grown decoder (doeff_hy/_wire_codec.py — the reading side).

The contract is the design record docs/design/defwire-decoder/design.md (per-type decision in section 2, common rules in section 3, arm names in appendix B). What it checks:

1. Accepted and refused forms per vocabulary type — the same input is also passed through the pydantic decoder (pydantic_parse_json of doeff_hy._wire_parity — the same settings as wire.hy before the switch), and the accepted values (the value and the types of field values)
   and the list of refusal (location, reason) must match. This includes the 4 refusals of the prototype (table 3 of artifact 2026-10-27-defwire-base-measurements.html).
2. Rule 4 of the accept-anything arm (where the answer that differs from pydantic is taken as correct) — checked in isolation.
3. JSON reading (section 3 "JSON reading"), and that answers decoded from a JSON value, a frozen value, or OpaqueJson equal the answer decoded from a string.
4. Types outside the vocabulary, type cycles, and unresolvable forward references are refused with TypeError when the tree is built.
5. The type tree is built only once, at first use, and remembered on the type (one tree even for simultaneous first uses).
6. pydantic is absent from the import closure of this module, and decoding works without loading pydantic.
"""


import dataclasses
import importlib
import json
import math
import subprocess
import sys
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import doeff_hy  # noqa: F401  # enable the Hy import hook
import pytest
from doeff_hy._frozen import freeze_json
from doeff_hy._json_value import OpaqueJson
from doeff_hy._wire_codec import _WireNode, codec_of
from doeff_hy._wire_malformed import Malformed, MalformedField
from doeff_hy._wire_parity import pydantic_parse_json

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

types = importlib.import_module("wire_codec_types")


def _typed(value: object) -> object:
    """Turn a value into a (type, content) tree — compared so as to tell apart 1 and 1.0, tuple and list, and str and StrEnum, which == does not distinguish."""
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


def _problems(answer: Malformed) -> list[tuple[str, str]]:
    return [(field.field, field.reason) for field in answer.fields]


def _kinds(answer: Malformed) -> list[tuple[str, str]]:
    return [(field.field, field.reason.rsplit(" [", 1)[1].rstrip("]")) for field in answer.fields]


def _both(wire_type: type, text: str) -> tuple[object, object]:
    return codec_of(wire_type).decode_json(text), pydantic_parse_json(wire_type, text)


# --- 1. Accepted and refused forms per vocabulary type (compared against pydantic) ------------------------------------------------------------

ACCEPTED: list[tuple[str, str, dict[str, object]]] = [
    ("Vocab", '{"s":"a"}', {"s": "a"}),
    ("Vocab", '{"i":1}', {"i": 1}),
    ("Vocab", '{"f":1}', {"f": 1.0}),
    ("Vocab", '{"f":1.5}', {"f": 1.5}),
    ("Vocab", '{"f":1e400}', {"f": math.inf}),
    ("Vocab", '{"f":1' + "0" * 400 + "}", {"f": math.inf}),
    ("Vocab", '{"b":true}', {"b": True}),
    ("Vocab", '{"oi":null}', {"oi": None}),
    ("Vocab", '{"oi":3}', {"oi": 3}),
    ("Vocab", '{"color":"blue"}', {"color": types.Color.BLUE}),
    ("Vocab", '{"lit":"y"}', {"lit": "y"}),
    ("Vocab", '{"namedLit":"plain"}', {"named_lit": "plain"}),
    ("Vocab", '{"ti":[1,2]}', {"ti": (1, 2)}),
    ("Vocab", '{"tf":[1,"a"]}', {"tf": (1, "a")}),
    ("Vocab", '{"ls":["a"]}', {"ls": ["a"]}),
    ("Vocab", '{"fs":["a","a","b"]}', {"fs": frozenset({"a", "b"})}),
    ("Vocab", '{"di":{"k":1}}', {"di": {"k": 1}}),
    ("Vocab", '{"bare":{"k":[1,{"x":null}]}}', {"bare": {"k": [1, {"x": None}]}}),
    ("Vocab", '{"anyValue":[1,"a"]}', {"any_value": [1, "a"]}),
    ("Vocab", '{"opaque":{"a":[1,null]}}', {"opaque": OpaqueJson.of({"a": [1, None]})}),
    ("Vocab", '{"note":{"noteText":"n"}}', {"note": types.Note(note_text="n")}),
    ("Vocab", '{"u":1}', {"u": 1}),
    ("Vocab", '{"u":"a"}', {"u": "a"}),
    ("Vocab", '{"fi":1}', {"fi": 1}),
    ("Vocab", '{"fi":1.0}', {"fi": 1.0}),
    ("Vocab", '{"cs":"red"}', {"cs": "red"}),
    ("Vocab", '{"cs":"x"}', {"cs": "x"}),
    ("Vocab", '{"arr":[1]}', {"arr": [1]}),
    ("Vocab", '{"arr":["a"]}', {"arr": ("a",)}),
    ("Vocab", '{"s":"a","s":"b"}', {"s": "b"}),
    ("Required", '{"laneId":"L","landedAt":null}', {"lane_id": "L", "landed_at": None, "n": 0}),
    ("Lenient", '{"lane_id":"L","laneId":"x","extra":1}', {"lane_id": "L"}),
    (
        "Outer",
        '{"inner":{"nVal":1},"byKey":{"a":{"nVal":2}}}',
        {"by_key": {"a": types.Inner(n_val=2)}},
    ),
    ("Checked", '{"n":1}', {"n": 1}),
    ("Arms", '{"ab":{"x":1}}', {"ab": types.ArmA(x=1)}),
    ("Arms", '{"ab":{"x":1,"y":2}}', {"ab": types.ArmB(x=1, y=2)}),
    ("Arms", '{"da":{"x":1}}', {"da": {"x": 1}}),
    ("WithAlias", '{"v":1}', {"v": 1}),
    ("WithAlias", '{"v":1.5}', {"v": 1.5}),
    ("WithAlias", '{"v":true}', {"v": True}),
    ("WithAlias", '{"v":null}', {"v": None}),
    (
        "LeaseAnswer",
        '{"leaseId":"a","state":"held","holders":[{"holderId":"h","expiresAt":1}]}',
        {"lease_id": "a"},
    ),
]

REFUSED: list[tuple[str, str, list[tuple[str, str]]]] = [
    # The 4 refusals of the prototype (table 3) — a numeric leaseId, a state outside the definition, an unknown field zz, and a string in a nested float field.
    ("LeaseAnswer", '{"leaseId":1,"state":"held"}', [("leaseId", "string_type")]),
    ("LeaseAnswer", '{"leaseId":"a","state":"x"}', [("state", "enum")]),
    (
        "LeaseAnswer",
        '{"leaseId":"a","state":"held","zz":1}',
        [("zz", "unexpected_keyword_argument")],
    ),
    (
        "LeaseAnswer",
        '{"leaseId":"a","state":"held","holders":[{"holderId":"h","expiresAt":"1"}]}',
        [("holders.0.expiresAt", "float_type")],
    ),
    ("Vocab", '{"s":1}', [("s", "string_type")]),
    ("Vocab", '{"s":null}', [("s", "string_type")]),
    ("Vocab", '{"i":"1"}', [("i", "int_type")]),
    ("Vocab", '{"i":true}', [("i", "int_type")]),
    ("Vocab", '{"i":1.0}', [("i", "int_type")]),
    ("Vocab", '{"i":1e3}', [("i", "int_type")]),
    ("Vocab", '{"f":"1"}', [("f", "float_type")]),
    ("Vocab", '{"f":true}', [("f", "float_type")]),
    ("Vocab", '{"b":1}', [("b", "bool_type")]),
    ("Vocab", '{"b":"true"}', [("b", "bool_type")]),
    ("Vocab", '{"oi":"1"}', [("oi", "int_type")]),
    ("Vocab", '{"color":"RED"}', [("color", "enum")]),
    ("Vocab", '{"lit":"z"}', [("lit", "literal_error")]),
    ("Vocab", '{"namedLit":"x"}', [("namedLit", "literal_error")]),
    ("Vocab", '{"ti":{}}', [("ti", "tuple_type")]),
    ("Vocab", '{"ti":"12"}', [("ti", "tuple_type")]),
    ("Vocab", '{"ti":[1,"a",true]}', [("ti.1", "int_type"), ("ti.2", "int_type")]),
    ("Vocab", '{"tf":[1]}', [("tf.1", "missing")]),
    ("Vocab", '{"tf":["x"]}', [("tf.0", "int_type"), ("tf.1", "missing")]),
    ("Vocab", '{"tf":[1,"a",2]}', [("tf", "too_long")]),
    ("Vocab", '{"ls":"a"}', [("ls", "list_type")]),
    ("Vocab", '{"ls":[1]}', [("ls.0", "string_type")]),
    ("Vocab", '{"fs":"a"}', [("fs", "frozen_set_type")]),
    ("Vocab", '{"di":[]}', [("di", "dict_type")]),
    ("Vocab", '{"di":{"k":"v"}}', [("di.k", "int_type")]),
    ("Vocab", '{"bare":[1]}', [("bare", "dict_type")]),
    ("Vocab", '{"note":[]}', [("note", "dataclass_type")]),
    ("Vocab", '{"note":{"noteText":1}}', [("note.noteText", "string_type")]),
    ("Vocab", '{"u":1.5}', [("u.int", "int_type"), ("u.str", "string_type")]),
    ("Vocab", '{"u":null}', [("u.int", "int_type"), ("u.str", "string_type")]),
    ("Vocab", '{"fi":"1"}', [("fi.float", "float_type"), ("fi.int", "int_type")]),
    ("Vocab", '{"cs":1}', [("cs.str-enum[Color]", "enum"), ("cs.str", "string_type")]),
    (
        "Vocab",
        '{"arr":[true]}',
        [("arr.list[int].0", "int_type"), ("arr.tuple[str, ...].0", "string_type")],
    ),
    ("Vocab", '{"zz":1}', [("zz", "unexpected_keyword_argument")]),
    (
        "Vocab",
        '{"zz":1,"u":1.5,"s":1,"oi":"x","yy":2}',
        [
            ("s", "string_type"),
            ("oi", "int_type"),
            ("u.int", "int_type"),
            ("u.str", "string_type"),
            ("zz", "unexpected_keyword_argument"),
            ("yy", "unexpected_keyword_argument"),
        ],
    ),
    ("Vocab", "[]", [("", "dataclass_type")]),
    ("Vocab", "null", [("", "dataclass_type")]),
    ("Vocab", '"s"', [("", "dataclass_type")]),
    ("Required", "{}", [("laneId", "missing"), ("landedAt", "missing")]),
    (
        "Required",
        '{"lane_id":"L","landedAt":1}',
        [("laneId", "missing"), ("lane_id", "unexpected_keyword_argument")],
    ),
    (
        "Outer",
        '{"inner":{"n_val":1}}',
        [("inner.nVal", "missing"), ("inner.n_val", "unexpected_keyword_argument")],
    ),
    ("Outer", '{"inner":{"nVal":1,"zz":0}}', [("inner.zz", "unexpected_keyword_argument")]),
    ("Outer", '{"inner":{"nVal":1},"byKey":{"a":{"nVal":"x"}}}', [("byKey.a.nVal", "int_type")]),
    ("Checked", '{"n":0}', [("", "value_error")]),
    ("Checked", '{"n":"0"}', [("n", "int_type")]),
    ("HoldsChecked", '{"k":{"n":0}}', [("k", "value_error")]),
    ("Asserting", '{"n":11}', [("", "assertion_error")]),
    ("Arms", '{"ab":{"x":"s"}}', [("ab.ArmA.x", "int_type"), ("ab.ArmB.x", "int_type")]),
    ("Arms", '{"ab":1}', [("ab.ArmA", "dataclass_type"), ("ab.ArmB", "dataclass_type")]),
    ("Arms", '{"da":{"y":"s"}}', [("da.dict[str,int].y", "int_type"), ("da.ArmA.x", "missing")]),
    (
        "WithAlias",
        '{"v":[1]}',
        [
            ("v.str", "string_type"),
            ("v.int", "int_type"),
            ("v.float", "float_type"),
            ("v.bool", "bool_type"),
        ],
    ),
    ("Vocab", "{bad", [("", "json_invalid")]),
]


@pytest.mark.parametrize(("type_name", "text", "expected"), ACCEPTED)
def test_accepted_inputs_give_the_value_pydantic_gives(
    type_name: str, text: str, expected: dict[str, object]
) -> None:
    wire_type = getattr(types, type_name)
    ours, theirs = _both(wire_type, text)
    assert not isinstance(ours, Malformed), ours
    for name, value in expected.items():
        assert _typed(getattr(ours, name)) == _typed(value)
    assert _typed(ours) == _typed(theirs)


@pytest.mark.parametrize(("type_name", "text", "expected"), REFUSED)
def test_refused_inputs_give_the_places_and_reasons_pydantic_gives(
    type_name: str, text: str, expected: list[tuple[str, str]]
) -> None:
    wire_type = getattr(types, type_name)
    ours, theirs = _both(wire_type, text)
    assert isinstance(ours, Malformed), ours
    assert isinstance(theirs, Malformed), theirs
    assert ours.wire_type == theirs.wire_type == type_name
    assert _kinds(ours) == expected
    if expected == [("", "json_invalid")]:
        # The error text of JSON reading is a Python exception message (different from pydantic's — section 3). Compare only the location and kind.
        assert _kinds(theirs) == expected
    else:
        assert _problems(ours) == _problems(theirs)


def test_reasons_are_spelled_as_pydantic_spells_them() -> None:
    answer = codec_of(types.Vocab).decode_json('{"color":"x","lit":"z","tf":[1,"a",2],"note":[]}')
    assert isinstance(answer, Malformed)
    assert _problems(answer) == [
        ("color", "Input should be 'red' or 'blue' [enum]"),
        ("lit", "Input should be 'x' or 'y' [literal_error]"),
        ("tf", "Tuple should have at most 2 items after validation, not 3 [too_long]"),
        ("note", "Input should be an object [dataclass_type]"),
    ]
    checked = codec_of(types.Checked).decode_json('{"n":0}')
    assert checked == Malformed(
        wire_type="Checked",
        fields=(
            MalformedField(
                field="",
                reason="Value error, Checked: field n failed check (> n 0): n=0 [value_error]",
            ),
        ),
    )


# --- 2. The accept-anything arm (rule 4 of section 3) ------------------------------------------------------------


def test_a_catch_all_arm_takes_only_what_no_other_arm_takes() -> None:
    codec = codec_of(types.Mixed)

    def field(text: str, name: str) -> object:
        answer = codec.decode_json(text)
        assert not isinstance(answer, Malformed), answer
        return getattr(answer, name)

    # A string goes to the str arm (pydantic wraps it in OpaqueJson — what rule 4 changed); a value the str arm does not accept is OpaqueJson; null goes to the None arm.
    assert _typed(field('{"so":"s"}', "so")) == (str, "s")
    assert field('{"so":1}', "so") == OpaqueJson.of(1)
    assert field('{"so":{"a":1}}', "so") == OpaqueJson.of({"a": 1})
    assert field('{"so":null}', "so") is None
    # For object: if a nested defwire arm accepts it, that; otherwise OpaqueJson (independent of the order the arms were written).
    assert field('{"oa":{"x":1}}', "oa") == types.ArmA(x=1)
    assert field('{"oa":{"y":1}}', "oa") == OpaqueJson.of({"y": 1})
    assert field('{"oa":[1]}', "oa") == OpaqueJson.of([1])
    # The object arm is the same — a value accepted by a typed arm goes to that arm; a value not accepted stays as the json.loads value.
    assert _typed(field('{"io":1}', "io")) == (int, 1)
    assert field('{"io":"a"}', "io") == "a"
    assert field('{"io":1.5}', "io") == 1.5


# --- 3. JSON reading, and decoding from values ------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param('{"s":"\\ud800"}', id="lone-surrogate-escape"),
        pytest.param('{"s":"\\udc00x"}', id="lone-low-surrogate-escape"),
        pytest.param('﻿{"s":"a"}', id="bom"),
        pytest.param('{"s":"a"} x', id="trailing-garbage"),
        pytest.param('{"s":"a"}'.encode("utf-16"), id="utf-16-bytes"),
        pytest.param(b'{"s":"\xff"}', id="not-utf-8-bytes"),
        pytest.param("", id="empty"),
    ],
)
def test_text_that_is_not_json_is_json_invalid_like_pydantic(raw: str | bytes) -> None:
    ours = codec_of(types.Vocab).decode_json(raw)
    theirs = pydantic_parse_json(types.Vocab, raw)
    assert isinstance(ours, Malformed)
    assert isinstance(theirs, Malformed)
    assert _kinds(ours) == _kinds(theirs) == [("", "json_invalid")]


def test_a_paired_surrogate_escape_is_one_character() -> None:
    answer = codec_of(types.Vocab).decode_json('{"s":"\\ud83d\\ude00"}')
    assert not isinstance(answer, Malformed)
    assert answer.s == "\U0001f600"


def test_json_read_limits_are_json_invalid() -> None:
    # An integer over 4300 digits (a ValueError from json.loads — today's pydantic accepts it. Acceptance narrows, but the type check is not weakened), and
    # deep nesting on which json.loads fails with RecursionError.
    huge = codec_of(types.Vocab).decode_json('{"i":' + "1" * 5000 + "}")
    deep = codec_of(types.Vocab).decode_json('{"anyValue":' + "[" * 200_000 + "]" * 200_000 + "}")
    for answer in (huge, deep):
        assert isinstance(answer, Malformed)
        assert _kinds(answer) == [("", "json_invalid")]


@pytest.mark.parametrize(
    ("type_name", "text"), [(name, text) for name, text, _ in ACCEPTED + REFUSED if text != "{bad"]
)
def test_values_frozen_values_and_opaque_json_decode_as_their_text_does(
    type_name: str, text: str
) -> None:
    codec = codec_of(getattr(types, type_name))
    from_text = codec.decode_json(text)
    value = json.loads(text)
    for answer in (
        codec.decode(value),
        codec.decode(freeze_json(value)),
        codec.decode(OpaqueJson.from_text(text)),
    ):
        assert _typed(answer) == _typed(from_text)


def test_value_subclasses_read_as_their_json() -> None:
    codec = codec_of(types.Vocab)
    answer = codec.decode(
        {"s": types.Color.RED, "f": 2, "ti": (1, 2), "note": freeze_json({"noteText": "n"})}
    )
    assert not isinstance(answer, Malformed), answer
    assert _typed(answer.s) == (str, "red")
    assert _typed(answer.f) == (float, 2.0)
    assert answer.ti == (1, 2)
    assert answer.note == types.Note(note_text="n")


def test_a_value_that_is_not_json_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        codec_of(types.Vocab).decode({"anyValue": {1, 2}})


# --- 4. Outside the vocabulary is refused when building ------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_name", "words"),
    [
        ("HasDatetime", "type outside the vocabulary"),
        ("HasAnnotated", "type outside the vocabulary"),
        ("HasPlainRecord", "plain defrecord"),
        ("HasIntLiteral", "Literal that is not a string"),
        ("HasIntKeys", "dict whose keys are not strings"),
        ("HasBareList", "type outside the vocabulary"),
        ("SelfRef", "type cycle"),
        ("LoopA", "type cycle"),
        ("Unresolved", "cannot resolve"),
    ],
)
def test_types_outside_the_vocabulary_are_refused_when_the_tree_is_built(
    type_name: str, words: str
) -> None:
    with pytest.raises(TypeError) as refused:
        codec_of(getattr(types, type_name))
    assert words in str(refused.value)
    assert "__doeff_codec__" not in vars(getattr(types, type_name))


def test_vocabulary_types_decode_at_the_top_level() -> None:
    by_key = codec_of(dict[str, OpaqueJson]).decode_json('{"a":[1],"b":null}')
    assert by_key == {"a": OpaqueJson.of([1]), "b": OpaqueJson.of(None)}
    refused = codec_of(list[int]).decode_json('[1,"x"]')
    assert isinstance(refused, Malformed)
    assert refused.wire_type == "list[int]"
    assert _kinds(refused) == [("1", "int_type")]
    assert codec_of(str).decode_json('"s"') == "s"


# --- 5. Building happens once, at first use ------------------------------------------------------------


def test_the_tree_is_built_at_first_use_and_kept_on_the_type() -> None:
    probe = types.Lenient
    first = codec_of(probe)
    assert vars(probe)["__doeff_codec__"] is first
    assert codec_of(probe) is first
    # The tree of a nested type is remembered on the child type and is not rebuilt per parent.
    outer = codec_of(types.Outer)
    assert isinstance(outer.node, _WireNode)
    inner_field = next(field for field in outer.node.fields if field.wire == "inner")
    assert inner_field.node is codec_of(types.Inner).node


def test_concurrent_first_uses_build_one_tree() -> None:
    probe = types.Note
    if "__doeff_codec__" in vars(probe):
        delattr(probe, "__doeff_codec__")
    barrier = threading.Barrier(8)

    def use(_: int) -> object:
        barrier.wait()
        return codec_of(probe)

    with ThreadPoolExecutor(max_workers=8) as pool:
        seen = list(pool.map(use, range(8)))
    assert len(seen) == 8
    assert all(codec is seen[0] for codec in seen)


# --- 6. pydantic is not loaded ------------------------------------------------------------


def test_the_decoder_imports_no_pydantic_and_decodes_without_it() -> None:
    script = textwrap.dedent(
        """
        import dataclasses, sys, types
        import doeff_hy
        from doeff_hy import wire_codec
        from doeff_hy._wire_malformed import Malformed

        @dataclasses.dataclass(frozen=True, kw_only=True)
        class Row:
            lane_id: str
            n: int = 0

        Row.__doeff_wire__ = types.SimpleNamespace(names={"lane_id": "laneId", "n": "n"}, unknown="reject")
        codec = wire_codec.codec_of(Row)
        assert codec.decode_json('{"laneId":"L","n":2}') == Row(lane_id="L", n=2)
        refused = codec.decode_json('{"laneId":1,"zz":0}')
        assert isinstance(refused, Malformed), refused
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
