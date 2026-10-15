"""OpaqueJson の作り方の検(agora-redesign #2449)。

``OpaqueJson.of`` は書いた直後の文字列を ``__post_init__`` で解き直さずに包む。その速い道が、受ける値・断る値・綴りを
``cls(json.dumps(...))``(解き直す道)と変えないこと、外から渡る文字列は今までどおり解いて確かめること、
``of`` が ``json.loads`` を 1 度も呼ばないことを確かめる。
"""

import importlib
import json
import sys
from collections import Counter
from pathlib import Path

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
import pytest
from doeff_hy import json_value
from doeff_hy.frozen import freeze_json
from doeff_hy.json_value import OpaqueJson
from doeff_hy.wire import dump, dump_json

from doeff import run

# OpaqueJson の欄を持つ defwire の型(ToolCall・ToolBatch・Note・ToolArgs)は Hy の検の部品が持つ(test_defwire.py と同じ読み方)。
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
_wire = importlib.import_module("defwire_deftests")

# 受ける値: 解き直す道(cls(json.dumps(...)))と同じ綴り・同じ等しさになること。
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
    {"b": 1, "a": 2},  # 欄の順を保つ
    "\ud800",  # 対になっていない surrogate も json.dumps と json.loads は通す
    float("inf"),
    {"深い": [[[{"x": [1]}]]]},
]

# 断る値: json.dumps が断る物は of も断る(同じ例外の型)。
# 検の名は id で固定する — object() の repr は番地を含み、xdist の worker ごとに名が変わって集めが割れる。
_REJECTED = [
    pytest.param({1, 2}, TypeError, id="set"),
    pytest.param(object(), TypeError, id="object"),
    pytest.param({("tuple", "鍵"): 1}, TypeError, id="tuple-key"),
    pytest.param(b"bytes", TypeError, id="bytes"),
]


def _slow_of(value: object) -> OpaqueJson:
    """以前の of(書いた文字列を __post_init__ で解き直す道)— 比べる基準。"""
    return OpaqueJson(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=json_value._thawed)
    )


@pytest.mark.parametrize("value", _ACCEPTED, ids=repr)
def test_of_accepts_and_spells_the_same_as_the_validating_construction(value: object) -> None:
    """受ける値の綴りと等しさが、解き直す道と同じ。"""
    fast = OpaqueJson.of(value)
    assert type(fast) is OpaqueJson
    assert fast.text == _slow_of(value).text
    assert fast == _slow_of(value)
    assert hash(fast) == hash(_slow_of(value))
    assert json.loads(fast.text) == json.loads(_slow_of(value).text)


def test_of_spells_frozen_json_and_nan_as_before() -> None:
    """凍らせた JSON(FrozenMap / tuple)と NaN も解き直す道と同じ綴り。"""
    frozen = freeze_json({"a": [1, 2], "b": {"c": None}})
    assert OpaqueJson.of(frozen).text == _slow_of(frozen).text == '{"a":[1,2],"b":{"c":null}}'
    assert OpaqueJson.of(float("nan")).text == _slow_of(float("nan")).text == "NaN"
    assert OpaqueJson.of(frozen) == OpaqueJson.from_text('{ "a" : [1, 2], "b": {"c": null} }')


@pytest.mark.parametrize(("value", "error"), _REJECTED)
def test_of_rejects_what_json_dumps_rejects(value: object, error: type[Exception]) -> None:
    """書けない値は、解き直す道と同じ例外の型で断る。"""
    with pytest.raises(error):
        _slow_of(value)
    with pytest.raises(error):
        OpaqueJson.of(value)


def test_text_from_outside_is_still_parsed_and_rejected() -> None:
    """外から渡る文字列(cls(text)・from_text)は今までどおり解いて確かめ、読めなければ ValueError。"""
    with pytest.raises(json.JSONDecodeError):
        OpaqueJson("{bad")
    with pytest.raises(json.JSONDecodeError):
        OpaqueJson.from_text("{bad")
    assert OpaqueJson('{"a":1}').text == '{"a":1}'


def test_of_does_not_parse_the_text_it_just_wrote(monkeypatch: pytest.MonkeyPatch) -> None:
    """of は json.loads を 1 度も呼ばない・from_text は 1 度だけ・cls(text) は 1 度(速い道を外すと赤)。"""
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


# --- dump_json は OpaqueJson の文字列を解き直さずそのまま出力へ埋める(二度手間 — 文字列 → json.loads → もう一度書く — の廃止)---------


def _tool_call(input_: OpaqueJson, output: OpaqueJson | None = None) -> object:
    return _wire.ToolCall(tool_name="Read", input=input_, output=output)


def test_dump_json_embeds_the_opaque_text_verbatim() -> None:
    """外から渡った文字列(cls(text))は最小の直列化へ整わない。出力にその文字列が丸ごと在れば、解いて書き直していない。"""
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
    """OpaqueJson の欄が何個在っても、dump_json は json.loads を 1 度も呼ばない(読み戻しの廃止 — 直す前は欄ごとに 1 回)。"""
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
    """埋めた出力の JSON としての意味は dump(JSON の値への書き出し)と同じ — 日本語・絵文字・入れ子・null・空の入れ子も。"""
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
    """既定値(None)の欄は書かない・値が JSON の null の OpaqueJson は null として書く(これまでと同じ)。"""
    only_input = json.loads(run(dump_json(_tool_call(OpaqueJson.of({"a": 1})))))
    assert only_input == {"toolName": "Read", "input": {"a": 1}}
    with_null = json.loads(run(dump_json(_tool_call(OpaqueJson.of({"a": 1}), OpaqueJson.of(None)))))
    assert with_null == {"toolName": "Read", "input": {"a": 1}, "output": None}


def test_dump_json_resolves_each_opaque_value_in_a_union_that_tries_the_opaque_arm_first() -> None:
    """OpaqueJson が最初の腕の和は、型を持つ行でもまず OpaqueJson の腕を試して落ちてから次の腕へ回る。試して捨てた腕が印を残しても、
    出力に印は残らず、OpaqueJson の行と型の行が混ざっても順と中身が合う。"""
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
    assert json_value._SPLICE_PREFIX not in spelled


@pytest.mark.parametrize("spelling", ["NaN", "Infinity", "-Infinity"])
def test_dump_json_keeps_strict_json_when_an_opaque_text_has_a_non_json_number(
    spelling: str,
) -> None:
    """NaN / Infinity は JSON の数ではない(Python の json.dumps だけが書く)。そのまま埋めると読み手の JSON.parse が壊れるので、
    これまでどおり解いて pydantic に書かせ(null になる)、出力は必ず厳密な JSON。"""
    value = {"x": float(spelling.lower().replace("infinity", "inf")), "ok": 1}
    opaque = OpaqueJson.of(value)
    assert spelling in opaque.text
    text = run(dump_json(_tool_call(opaque)))

    def refuse(token: str) -> object:
        raise AssertionError(f"厳密な JSON でない綴りが出力に在る: {token}")

    assert json.loads(text, parse_constant=refuse)["input"] == {"x": None, "ok": 1}


def test_a_string_that_only_looks_like_the_marker_is_not_replaced() -> None:
    """送る値の中の文字列が印に似ていても(印の頭は呼びの外へ出ない乱数)、取り違えて替えない。"""
    lookalike = '"' + json_value._SPLICE_PREFIX[:-2] + '0"'
    note = _wire.Note(note_text=lookalike)
    text = run(dump_json(_wire.ToolBatch(rows=(note, OpaqueJson.of({"i": 1})))))
    assert json.loads(text) == {"rows": [{"noteText": lookalike}, {"i": 1}]}


def test_dump_json_fails_on_an_opaque_text_that_cannot_be_utf8() -> None:
    """対になっていない surrogate を含む文字列は UTF-8 に書けない。解く道(これまで)と同じく、送り出す前の書き出しで例外になる
    (読み手へ壊れた bytes を送る所まで進まない)。"""
    lone = OpaqueJson.of("\ud800")
    with pytest.raises(Exception):  # noqa: B017, PT011  # 例外の型は pydantic の包み(解く道も埋める道も書き出しの中で落ちる)
        run(dump_json(_tool_call(lone)))
