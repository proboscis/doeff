"""Tests for how OpaqueJson is built.

``OpaqueJson.of`` wraps a string it has just written without re-parsing it in ``__post_init__``. This checks that the fast path does not change the accepted values, the rejected values, or the spelling
compared with ``cls(json.dumps(...))`` (the re-parsing path), that strings coming from outside are still parsed and verified as before,
and that ``of`` never calls ``json.loads``.
"""

import importlib
import json
import sys
from collections import Counter
from pathlib import Path

import doeff_hy  # noqa: F401  # enable the Hy import hook
import pytest
from doeff_hy import _json_value as json_value
from doeff_hy._frozen import freeze_json
from doeff_hy._json_value import OpaqueJson
from doeff_hy._wire import dump, dump_json

from doeff import run

# The defwire types with OpaqueJson fields (ToolCall, ToolBatch, Note, ToolArgs) come from the Hy check fixtures (read the same way as in test_defwire.py).
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
_wire = importlib.import_module("defwire_deftests")

# Accepted values: must produce the same spelling and equality as the re-parsing path (cls(json.dumps(...))).
_ACCEPTED = [
    {"path": "/a", "n": [1, {"x": None}]},
    [1, 2.5, True, False, None, "s"],
    "文字列",
    0,
    -3,
    1.5,
    True,
    None,
    {},
    [],
    {1: "int の鍵は文字列になる", None: "null"},
    {True: "bool の鍵も文字列になる", 2.5: "float の鍵も"},
    ("tuple", "は配列"),
    {"b": 1, "a": 2},  # field order is preserved
    "\ud800",  # json.dumps and json.loads both accept an unpaired surrogate
    float("inf"),
    {"深い": [[[{"x": [1]}]]]},
]

# Rejected values: whatever json.dumps rejects, of also rejects (same exception type).
# Test names are pinned by id: the repr of object() contains an address, so names would differ per xdist worker and break collection.
_REJECTED = [
    pytest.param({1, 2}, TypeError, id="set"),
    pytest.param(object(), TypeError, id="object"),
    pytest.param({("tuple", "鍵"): 1}, TypeError, id="tuple-key"),
    pytest.param(b"bytes", TypeError, id="bytes"),
]


def _slow_of(value: object) -> OpaqueJson:
    """The previous of (the path that re-parsed the written string in __post_init__) -- the reference to compare against."""
    return OpaqueJson(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=json_value._thawed)
    )


@pytest.mark.parametrize("value", _ACCEPTED, ids=repr)
def test_of_accepts_and_spells_the_same_as_the_validating_construction(value: object) -> None:
    """The spelling and equality of accepted values match the re-parsing path."""
    fast = OpaqueJson.of(value)
    assert type(fast) is OpaqueJson
    assert fast.text == _slow_of(value).text
    assert fast == _slow_of(value)
    assert hash(fast) == hash(_slow_of(value))
    assert json.loads(fast.text) == json.loads(_slow_of(value).text)


def test_of_spells_frozen_json_and_nan_as_before() -> None:
    """Frozen JSON (FrozenMap / tuple) and NaN also have the same spelling as the re-parsing path."""
    frozen = freeze_json({"a": [1, 2], "b": {"c": None}})
    assert OpaqueJson.of(frozen).text == _slow_of(frozen).text == '{"a":[1,2],"b":{"c":null}}'
    assert OpaqueJson.of(float("nan")).text == _slow_of(float("nan")).text == "NaN"
    assert OpaqueJson.of(frozen) == OpaqueJson.from_text('{ "a" : [1, 2], "b": {"c": null} }')


@pytest.mark.parametrize(("value", "error"), _REJECTED)
def test_of_rejects_what_json_dumps_rejects(value: object, error: type[Exception]) -> None:
    """Unwritable values are rejected with the same exception type as the re-parsing path."""
    with pytest.raises(error):
        _slow_of(value)
    with pytest.raises(error):
        OpaqueJson.of(value)


def test_text_from_outside_is_still_parsed_and_rejected() -> None:
    """Strings coming from outside (cls(text), from_text) are still parsed and verified as before; unreadable ones raise ValueError."""
    with pytest.raises(json.JSONDecodeError):
        OpaqueJson("{bad")
    with pytest.raises(json.JSONDecodeError):
        OpaqueJson.from_text("{bad")
    assert OpaqueJson('{"a":1}').text == '{"a":1}'


def test_of_does_not_parse_the_text_it_just_wrote(monkeypatch: pytest.MonkeyPatch) -> None:
    """of never calls json.loads; from_text calls it once; cls(text) calls it once (turns red if the fast path is bypassed)."""
    calls: Counter[str] = Counter()
    real_loads = json.loads

    def counting_loads(text: object, *args: object, **kwargs: object) -> object:
        calls["loads"] += 1
        return real_loads(text, *args, **kwargs)

    monkeypatch.setattr(json_value.json, "loads", counting_loads)
    OpaqueJson.of({"a": [1, 2, {"b": None}]})
    assert calls["loads"] == 0
    OpaqueJson.from_text('{ "a" : 1 }')
    assert calls["loads"] == 1
    OpaqueJson('{"a":1}')
    assert calls["loads"] == 2


# --- dump_json embeds the OpaqueJson string into the output as-is without re-parsing (removal of the double work: string -> json.loads -> write again) ---------


def _tool_call(input_: OpaqueJson, output: OpaqueJson | None = None) -> object:
    return _wire.ToolCall(tool_name="Read", input=input_, output=output)


def test_dump_json_embeds_the_opaque_text_verbatim() -> None:
    """A string passed from outside (cls(text)) is not normalized to minimal serialization. If the output contains that string verbatim, it was not parsed and rewritten."""
    spaced = OpaqueJson('{ "path" : "/a",  "n" : [1, 2.50, 1e5] }')
    text = run(dump_json(_tool_call(spaced)))
    assert spaced.text in text
    assert json.loads(text) == {
        "toolName": "Read",
        "input": {"path": "/a", "n": [1, 2.5, 100000.0]},
    }


def test_dump_json_does_not_call_json_loads_for_opaque_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """However many OpaqueJson fields there are, dump_json never calls json.loads (the read-back is removed; before the fix it was once per field)."""
    batch = _wire.ToolBatch(
        rows=tuple(OpaqueJson.of({"i": i, "text": "日本語" * 5}) for i in range(50))
    )
    calls: Counter[str] = Counter()
    real_loads = json.loads

    def counting_loads(text: object, *args: object, **kwargs: object) -> object:
        calls["loads"] += 1
        return real_loads(text, *args, **kwargs)

    monkeypatch.setattr(json_value.json, "loads", counting_loads)
    text = run(dump_json(_tool_call(OpaqueJson.of({"a": [1, 2]}), OpaqueJson.of("ok"))))
    spelled = run(dump_json(batch))
    assert calls["loads"] == 0
    monkeypatch.undo()
    assert json.loads(text) == {"toolName": "Read", "input": {"a": [1, 2]}, "output": "ok"}
    assert [row["i"] for row in json.loads(spelled)["rows"]] == list(range(50))


def test_dump_json_means_the_same_json_as_dump() -> None:
    """The embedded output means the same JSON as dump (writing out to a JSON value) -- including Japanese, emoji, nesting, null, and empty nesting."""
    values = [
        {"path": "/a", "n": [1, {"x": None}], "文": '日本語 "引用" \\ \n 改行 😀'},
        [],
        {},
        "ok",
        0,
        None,
        {"deep": [[[{"x": [True, False, None]}]]]},
    ]
    for value in values:
        call = _tool_call(OpaqueJson.of(value), OpaqueJson.of(value))
        assert json.loads(run(dump_json(call))) == run(dump(call))


def test_dump_json_omits_an_opaque_field_at_its_default_and_keeps_a_null_value() -> None:
    """Fields with the default value (None) are not written; an OpaqueJson whose value is JSON null is written as null (same as before)."""
    only_input = json.loads(run(dump_json(_tool_call(OpaqueJson.of({"a": 1})))))
    assert only_input == {"toolName": "Read", "input": {"a": 1}}
    with_null = json.loads(run(dump_json(_tool_call(OpaqueJson.of({"a": 1}), OpaqueJson.of(None)))))
    assert with_null == {"toolName": "Read", "input": {"a": 1}, "output": None}


def test_dump_json_resolves_each_opaque_value_in_a_union_that_tries_the_opaque_arm_first() -> None:
    """For a union whose first arm is OpaqueJson, even a typed row first tries the OpaqueJson arm, fails, and then moves to the next arm. Order and content stay
    correct when OpaqueJson rows and typed rows are mixed (the writer embeds the opaque text itself; the splice marker of the pydantic path is gone)."""
    rows = (
        _wire.Note(note_text="a"),
        OpaqueJson.of({"i": 1}),
        _wire.ToolArgs(path="/p"),
        OpaqueJson.of([2, "二"]),
        _wire.Note(note_text="b"),
    )
    spelled = run(dump_json(_wire.ToolBatch(rows=rows)))
    assert json.loads(spelled) == {
        "rows": [{"noteText": "a"}, {"i": 1}, {"path": "/p"}, [2, "二"], {"noteText": "b"}]
    }


@pytest.mark.parametrize("spelling", ["NaN", "Infinity", "-Infinity"])
def test_dump_json_keeps_strict_json_when_an_opaque_text_has_a_non_json_number(
    spelling: str,
) -> None:
    """NaN / Infinity are not JSON numbers (only Python's json.dumps writes them). Embedding them as-is would break the reader's JSON.parse,
    so they are parsed and written through pydantic as before (becoming null), and the output is always strict JSON."""
    value = {"x": float(spelling.lower().replace("infinity", "inf")), "ok": 1}
    opaque = OpaqueJson.of(value)
    assert spelling in opaque.text
    text = run(dump_json(_tool_call(opaque)))

    def refuse(token: str) -> object:
        raise AssertionError(f"spelling that is not strict JSON appears in the output: {token}")

    assert json.loads(text, parse_constant=refuse)["input"] == {"x": None, "ok": 1}


def test_a_string_that_only_looks_like_the_marker_is_not_replaced() -> None:
    """Even if a string in the sent value resembles a splice marker (the shape the pydantic path used: a 16-digit hex prefix and a number), it is not mistaken for one and replaced."""
    lookalike = '"' + "0123456789abcdef" + '0"'
    note = _wire.Note(note_text=lookalike)
    text = run(dump_json(_wire.ToolBatch(rows=(note, OpaqueJson.of({"i": 1})))))
    assert json.loads(text) == {"rows": [{"noteText": lookalike}, {"i": 1}]}


def test_dump_json_fails_on_an_opaque_text_that_cannot_be_utf8() -> None:
    """A string containing an unpaired surrogate cannot be written as UTF-8. As with the parsing path (previously), it raises during the write-out before sending
    (it never gets as far as sending broken bytes to the reader)."""
    lone = OpaqueJson.of("\ud800")
    with pytest.raises(ValueError, match="lone surrogate"):
        run(dump_json(_tool_call(lone)))
