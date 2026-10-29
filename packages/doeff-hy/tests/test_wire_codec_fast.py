"""wire_codec の速い道(card ki-c43f34494b42)が、今までの道と同じ答えを返す事の検。

速い道 = Codec.decode が入力を _plain で写し直さずに読む道(_Node.fast・欄を並べ切った読み手・構築子を通さない作り方)と、
Codec.encode_json が書き出しの木を作らずに直に綴る道(_Node.text・欄を並べ切った書き手)。どちらも、受けない所は今までの道
(_plain で写してから decode・_json_text(encode(value, True)))へ戻る。ここでは同じ入力を今までの道にも通し、答え(値の型まで・
Malformed の誤りの列・bytes・投げる例外の型)が同じ事を確かめる。
"""

import dataclasses
import importlib
import json
import math
import sys
from pathlib import Path

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
import pytest
from doeff_hy._frozen import freeze_json
from doeff_hy._json_value import OpaqueJson
from doeff_hy._wire_codec import Codec, _json_text, _plain, _RefusedError, codec_of, row_codec_of

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

types = importlib.import_module("wire_codec_types")


def _typed(value: object) -> object:
    """値を (型, 中身) の木へ — == では見分けない 1 と 1.0・tuple と list・str と StrEnum・欄の並びを見分けて比べる。"""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return (type(value), tuple((name, _typed(item)) for name, item in vars(value).items()))
    if isinstance(value, dict):
        return (dict, tuple((key, _typed(item)) for key, item in value.items()))
    if isinstance(value, list | tuple | frozenset):
        items = sorted(value) if isinstance(value, frozenset) else value
        return (type(value), tuple(_typed(item) for item in items))
    if isinstance(value, float) and math.isnan(value):
        return (float, "nan")
    return (type(value), value)


def _old_decode(codec: Codec[object], value: object) -> object:
    """今までの道の decode(入力を _plain で写してから判じ手の decode — 速い道を足す前の Codec.decode)。"""
    try:
        return codec.node.decode(_plain(value))
    except _RefusedError as invalid:
        return codec._malformed(invalid.problems)


def _same_decode(codec: Codec[object], value: object) -> None:
    """速い道の decode と今までの道が、同じ値(型まで)か同じ Malformed か同じ型の例外を返す。"""
    try:
        old = _typed(_old_decode(codec, value))
    except Exception as error:
        with pytest.raises(type(error)):
            codec.decode(value)
        return
    assert _typed(codec.decode(value)) == old


def _same_text(codec: Codec[object], value: object) -> None:
    """速い道の encode_json と今までの道(_json_text(encode(value, True)))が、同じ文字列か同じ型の例外を返す。"""
    try:
        old = _json_text(codec.node.encode(value, True))
    except Exception as error:
        with pytest.raises(type(error)):
            codec.encode_json(value)
        return
    assert codec.encode_json(value) == old


# 素の dict の入力(json.loads の値)と、同じ値の凍らせた形(FrozenMap・tuple)の両方で照らす。
DECODE_CASES: list[tuple[str, object]] = [
    ("Vocab", {}),
    ("Vocab", {"s": "x", "i": 3, "f": 1, "b": True, "oi": None, "color": "blue", "lit": "y"}),
    ("Vocab", {"ti": [1, 2], "tf": [1, "a"], "ls": ["a"], "fs": ["a", "a"], "di": {"k": 1}}),
    ("Vocab", {"bare": {"x": [1, {"y": None}]}, "anyValue": [1, "a"], "opaque": {"a": [1.5]}}),
    ("Vocab", {"note": {"noteText": "n"}, "u": "s", "fi": 2, "cs": "red", "arr": ["a"]}),
    ("Vocab", {"u": 1.5}),
    ("Vocab", {"i": True}),
    ("Vocab", {"s": "\ud800"}),
    ("Vocab", {"s": "日本語"}),
    ("Vocab", {"color": "RED"}),
    ("Vocab", {"tf": [1]}),
    ("Vocab", {"unknownKey": 1}),
    ("Vocab", {"ti": [1, "a", 3]}),
    ("Arms", {"ab": {"x": 1}, "da": {"k": 2}}),
    ("Arms", {"ab": {"x": 1, "y": 2}, "da": {"x": 3}}),
    ("Arms", {"ab": {"y": 1}}),
    ("Mixed", {"so": "s", "oa": {"x": 1}, "io": "z"}),
    ("Mixed", {"so": {"a": 1}, "oa": [1], "io": 2}),
    ("Checked", {"n": 1}),
    ("Checked", {"n": 0}),
    ("HoldsChecked", {"k": {"n": 0}}),
    ("Asserting", {"n": 1}),
    ("Asserting", {"n": -1}),
    ("Required", {"laneId": "a", "landedAt": None}),
    ("Required", {"laneId": "a"}),
    ("Lenient", {"lane_id": "a", "other": [1, 2]}),
    ("Outer", {"inner": {"nVal": 1}, "byKey": {"a": {"nVal": 2}}}),
    ("Outer", {"inner": {"nVal": 1, "extra": 0}}),
    ("LeaseAnswer", {"leaseId": "l", "state": "held", "holders": [{"holderId": "h", "expiresAt": 1}]}),
    ("Defaults", {"maybeLeaf": None, "maybeColor": None, "color": "red"}),
    (
        "Defaults",
        {
            "maybeLeaf": {"leafName": "x"},
            "maybeColor": "blue",
            "color": "red",
            "leaves": [],
            "byName": {"a": {"leafName": "b", "n": 3}},
            "holes": [1, None],
            "litOrInt": "a",
            "noneFirst": "s",
            "ids": [3, 3],
            "notANumber": 1.5,
            "colorOrInt": "blue",
            "spec": None,
            "pair": [1],
            "table": {},
            "maybePair": [1, "x"],
        },
    ),
]


@pytest.mark.parametrize(("name", "raw"), DECODE_CASES)
def test_fast_decode_answers_like_the_old_path(name: str, raw: object) -> None:
    codec = codec_of(getattr(types, name))
    _same_decode(codec, raw)
    _same_decode(codec, freeze_json(raw))


def test_fast_decode_answers_like_the_old_path_for_derived_and_deep_values() -> None:
    vocab = codec_of(types.Vocab)
    _same_decode(vocab, {"color": types.Color.BLUE, "s": types.Color.RED, "i": types.Color.RED})
    _same_decode(vocab, {"opaque": OpaqueJson.of([1, 2]), "anyValue": OpaqueJson.of({"a": 1})})
    _same_decode(vocab, {1: 2})
    _same_decode(vocab, {"bare": {"x": {1, 2}}})
    deep: object = 1
    for _ in range(205):
        deep = [deep]
    _same_decode(vocab, {"anyValue": deep})
    _same_decode(vocab, {"ti": deep})


@dataclasses.dataclass(frozen=True, kw_only=True)
class _Plain:
    """構築子を自分で書く(defrecord の展開でない)行の型 — 速い道は構築子を通して作る。"""

    n: int
    tags: tuple[str, ...] = ()


def test_bare_construction_equals_the_constructor_for_records() -> None:
    # defrecord の展開が書いた構築子の型は、速い道が構築子を通さずに作る — 欄の並び(vars)まで構築子と同じ。
    row = row_codec_of(types.PlainRecord)
    made = row.decode({"n": 3})
    assert _typed(made) == _typed(types.PlainRecord(n=3))
    plain = row_codec_of(_Plain)
    assert _typed(plain.decode({"n": 1})) == _typed(_Plain(n=1))
    assert _typed(plain.decode({"n": 1, "tags": ["a"]})) == _typed(_Plain(n=1, tags=("a",)))


ENCODE_VALUES: list[object] = [
    types.Vocab(),
    types.Vocab(s="日本語", f=1e-7, i=True, fi=1e16, oi=None, ti=(1, 2), fs=frozenset({"b", "a"})),
    types.Vocab(f=float("nan"), u="x", cs=types.Color.BLUE, arr=("a",), bare={"k": (1, 2.5e-5)}),
    types.Vocab(any_value={1: "a", None: 2}, di={"a": 1}, opaque=OpaqueJson.of({"x": [1, 2]})),
    types.Vocab(s="\ud800"),
    types.Vocab(any_value={1, 2}),
    types.Arms(ab=types.ArmB(x=1, y=2), da={"k": 1}),
    types.Mixed(so=OpaqueJson.of("s"), oa=types.ArmA(x=1), io=object()),
    types.Defaults(maybe_leaf=None, maybe_color=None, color=types.Color.RED),
    types.Defaults(maybe_leaf=types.Leaf(leaf_name="x"), maybe_color=types.Color.BLUE, color=types.Color.RED,
                   not_a_number=0.5, infinite=-1e300, pair=(1, "a"), maybe_pair=(1, "x")),
]


@pytest.mark.parametrize("value", ENCODE_VALUES)
def test_fast_text_writes_the_same_bytes_as_the_old_path(value: object) -> None:
    _same_text(codec_of(type(value)), value)


ROW_VALUES: list[object] = [
    types.PlainRecord(n=1),
    _Plain(n=2, tags=("a", "日本")),
    _Plain(n=True, tags=("a", 1)),  # type: ignore[arg-type]  # 型に合わない値も今までの道と同じに書く事の見本
]


@pytest.mark.parametrize("value", ROW_VALUES)
def test_fast_row_text_and_decode_round_trip_like_the_old_path(value: object) -> None:
    codec = row_codec_of(type(value))
    _same_text(codec, value)
    _same_decode(codec, json.loads(_json_text(codec.node.encode(value, True))))
