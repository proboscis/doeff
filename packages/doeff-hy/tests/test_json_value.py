"""OpaqueJson の作り方の検(agora-redesign #2449)。

``OpaqueJson.of`` は書いた直後の文字列を ``__post_init__`` で解き直さずに包む。その速い道が、受ける値・断る値・綴りを
``cls(json.dumps(...))``(解き直す道)と変えないこと、外から渡る文字列は今までどおり解いて確かめること、
``of`` が ``json.loads`` を 1 度も呼ばないことを確かめる。
"""

import json
from collections import Counter

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
import pytest
from doeff_hy import json_value
from doeff_hy.frozen import freeze_json
from doeff_hy.json_value import OpaqueJson

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
_REJECTED = [
    ({1, 2}, TypeError),
    (object(), TypeError),
    ({("tuple", "鍵"): 1}, TypeError),
    (b"bytes", TypeError),
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


@pytest.mark.parametrize(("value", "error"), _REJECTED, ids=repr)
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
