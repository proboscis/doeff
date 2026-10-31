"""自前の書き手と JSON Schema(doeff_hy/_wire_codec.py — 書く側)の検。

契約は設計の記録 docs/design/defwire-decoder/design.md(節 2 の表の「書き出し」の列・節 3b)。確かめること:

1. 語彙の型ごとの値を、今の pydantic の dump・dump-json と同じ JSON へ書く事(dump は値と値の型・欄の順まで、dump-json は bytes)。
   既定値と同じ欄を書かない事(入れ子も)・既定値の無い欄は None でも null・union の腕の選び方・欄の型に合わない値・NaN と inf・
   OpaqueJson の文字列の埋め込みを含む。
2. float の綴りが今の pydantic と同じ事(乱数の float)。
3. 解いて書いて解き直すと元の値に戻る事(書いた文字列を解いても・書いた JSON の値を解いても、値と欄の値の型が元のまま)。
4. JSON Schema が今の pydantic の json-schema と鍵の順まで同じ事と、違う所(入れ子の defwire と OpaqueJson の既定値 — 節 3b)。
5. 書けない値(孤立した surrogate・JSON でない値)を断る事と、pydantic と違う所(object の欄の defwire の値・OpaqueJson・FrozenMap)。
6. 書き手と JSON Schema の import の閉包に pydantic が無く、pydantic を読まずに書ける事。
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

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
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
    """JSON の値を (型, 中身) の木へ — 1 と 1.0・True と 1 を見分け、NaN を等しく比べ、object の鍵の順も比べる。"""
    if isinstance(value, dict):
        return (dict, tuple((key, _shape(item)) for key, item in value.items()))
    if isinstance(value, list):
        return (list, tuple(_shape(item) for item in value))
    if isinstance(value, float):
        return (float, repr(value))
    return (type(value), value)


def _typed(value: object) -> object:
    """値を (型, 中身) の木へ — == では見分けない 1 と 1.0・tuple と list・str と StrEnum を見分けて比べる(集合は順に依らない)。"""
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
    """pydantic の書き手(doeff_hy._wire_parity — 切り替える前の wire.hy の dump・dump-json と同じ)の答え。欄の型に合わない値の書き出しの警告は読み捨てる。"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return pydantic_dump(value), pydantic_dump_json(value)


# --- 1. 今の pydantic と同じ JSON を書く -------------------------------------------------------------------------------------------

NAN = math.nan
INF = math.inf

#: (id, 値を作る関数)。値は型の module を読んだ後に作る(defenum と defwire の型を引数に使うため)。
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
    # 欄の型に合わない値(作る時に型を確かめない構築子で入る)— 今の pydantic と同じく直すか値の型から書く。
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
    # 既定値の無い欄は None でも null・既定値と同じ欄は書かない(入れ子の型の欄も — leaf は既定値と等しい)。NaN は NaN と等しくないので書く。
    assert (
        codec.encode_json(value)
        == '{"maybeLeaf":null,"maybeColor":null,"color":"red","notANumber":null}'
    )
    nested = types.Defaults(
        maybe_leaf=types.Leaf(leaf_name="z", n=0), maybe_color=None, color=types.Color.RED
    )
    assert _object(codec.encode(nested))["maybeLeaf"] == {"leafName": "z"}


def test_opaque_json_text_is_embedded_without_reading_it_again() -> None:
    # 持っている文字列(区切りの空白も綴りも)をそのまま埋める — 読み直して書けば {"a":[1,2]} と 1e-05 は 0.00001 になる所。
    value = types.Vocab(opaque=OpaqueJson('{"a": [1, 2], "b": 1e-05}'))
    assert codec_of(types.Vocab).encode_json(value) == '{"opaque":{"a": [1, 2], "b": 1e-05}}'
    # JSON の値へ書く時は読み直す。NaN・Infinity を含む文字列は埋めずに読み直して null にする(今と同じ)。
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


# --- 2. float の綴り -----------------------------------------------------------------------------------------------------------


def _random_floats() -> list[float]:
    """乱数の 64 bit の並びの float(subnormal も)と、10 の -330 乗から 310 乗の各桁の数(-5 乗の桁と 16 乗の桁の境を含む)。"""
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


# --- 3. 解いて書いて解き直す ---------------------------------------------------------------------------------------------------

#: 書いて解き直すと元の値に戻らない物(VALUES の id): NaN(NaN は NaN と等しくない)・null を書く inf・既定値 0.0 と等しい -0.0
#: (書かないので 0.0 に戻る)・object の欄の tuple と set(JSON では配列)・欄の型に合わない値・解く時に先の腕が受ける union の値
#: (str の腕が defenum の腕より先・dict の腕が defwire の腕より先・何でも受ける腕の在る union の OpaqueJson — 設計の記録の節 1 の表)。
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
        )  # 欄の値の型も元のまま(1 と 1.0・tuple と list・str と StrEnum)


def test_defaults_round_trip_where_json_can_hold_the_value() -> None:
    # NaN と inf の欄(JSON では null・NaN は自分と等しくない)だけを有限の値にした Defaults も往復する。
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

#: pydantic と違う既定値(型の名 → 欄の wire の名 → 書き手の書く形)— pydantic は OpaqueJson を {"text": …}、入れ子の defwire を欄の
#: Python の名で全部書く(読み手はその JSON を断る — 節 3b)。
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
    """JSON Schema の中の object(型の検査に dict と示す — 形が違えば検の失敗)。"""
    assert isinstance(value, dict), value
    return cast(dict[str, object], value)


def _pydantic_schema(wire_type: type) -> dict[str, object]:
    """pydantic の json-schema(doeff_hy._wire_parity — 切り替える前の wire.hy と同じ)の答え。"""
    return _object(pydantic_json_schema(wire_type))


def _defaults_of(schema: Mapping[str, object], wires: list[str]) -> dict[str, object]:
    """JSON Schema の欄ごとの default。"""
    properties = _object(schema["properties"])
    return {wire: _object(properties[wire])["default"] for wire in wires}


def _with_defaults(schema: Mapping[str, object], defaults: dict[str, object]) -> dict[str, object]:
    """pydantic の JSON Schema の欄の default を、書き手の書く形に替えた写し(違うと分かっている欄だけ — 節 3b)。"""
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
    # 違いの訳: pydantic の既定値は読み手(pydantic 自身の parse)が断る形だった。書き手の形は読み手が受ける。
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


# --- 5. 書けない値と、pydantic と違う所 -----------------------------------------------------------------------------------------


def test_a_lone_surrogate_cannot_be_written_as_json_text() -> None:
    value = types.Vocab(s="\ud800")
    with pytest.raises(ValueError, match="surrogate"):
        codec_of(types.Vocab).encode_json(value)
    with pytest.raises(
        ValueError, match="surrogates not allowed"
    ):  # 今の pydantic も書かない(PydanticSerializationError は ValueError)
        pydantic_dump_json(value)
    assert codec_of(types.Vocab).encode(value) == {"s": "\ud800"}


def test_a_value_that_is_not_json_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        codec_of(types.Vocab).encode(types.Vocab(any_value=object()))
    with pytest.raises(TypeError):
        codec_of(types.Vocab).encode_json(types.Vocab(bare={"k": object()}))


def test_wire_values_and_opaque_json_in_object_fields_are_written_in_their_own_form() -> None:
    # pydantic は object の欄の defwire の値を欄の Python の名で、OpaqueJson を {"text": …} で書き、FrozenMap は書けなかった。
    codec = codec_of(types.Vocab)
    assert codec.encode(types.Vocab(any_value=types.Note(note_text="q"))) == {
        "anyValue": {"noteText": "q"}
    }
    assert codec.encode_json(types.Vocab(any_value=OpaqueJson.of([1]))) == '{"anyValue":[1]}'
    assert codec.encode_json(types.Vocab(bare=freeze_json({"a": [1]}))) == '{"bare":{"a":[1]}}'
    assert _pydantic(types.Vocab(any_value=types.Note(note_text="q")))[0] == {
        "anyValue": {"note_text": "q"}
    }


# --- 6. pydantic を読まない ----------------------------------------------------------------------------------------------------


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
