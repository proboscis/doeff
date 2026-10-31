"""defwire の全型を自前の解き手と pydantic の解き手で照らす検(設計の記録の節 7)。

確かめること:

1. doeff の package(doeff-hy・doeff-cluster・doeff-core-effects・doeff-codex)で定義した defwire の型を全部組み、型ごとに作った
   入力(正しい形・欄の種類の違い・欄の欠け・知らない欄・Python の欄の名・定義の外の綴り・配列の長さの ±1・入れ子の 2 階層まで・文字列
   そのものの崩し)を両方の解き手に通して、受ける・断る・出来た値・書いた JSON・JSON Schema が一致する事(不一致 0 — 弱める側も 0)。
2. 検の型(wire_codec_types.hy — 語彙の型を 1 つずつ持つ型・語彙の外の型)でも同じく一致し、組めないのは語彙の外の型だけである事。
3. 照らしが見つけた差の直し: 配列と object の入れ子の深さの上限(pydantic と同じ 200 階層)を、文字列からも値からも守る事。

agora-controllers の型は agora-controllers の検が同じ道具(doeff_hy._wire_parity)で照らす。
"""

import importlib
import json
import sys
from pathlib import Path

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
from doeff_hy import _wire_parity as wire_parity
from doeff_hy._json_value import OpaqueJson
from doeff_hy._wire_codec import codec_of
from doeff_hy._wire_malformed import Malformed

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

#: defwire の型を定義している doeff の package(packages/*/src の grep で数えた所 — 増えたらここに足す)。
DOEFF_PACKAGES = ("doeff_hy", "doeff_cluster", "doeff_core_effects", "doeff_codex")

#: 検の型のうち、語彙の外として木を組む時に断る型(設計の記録の節 2「語彙の外」)。
OUT_OF_VOCABULARY = {
    "wire_codec_types.HasAnnotated",
    "wire_codec_types.HasBareList",
    "wire_codec_types.HasDatetime",
    "wire_codec_types.HasIntKeys",
    "wire_codec_types.HasIntLiteral",
    "wire_codec_types.HasPlainRecord",
    "wire_codec_types.LoopA",
    "wire_codec_types.LoopB",
    "wire_codec_types.SelfRef",
    "wire_codec_types.Unresolved",
}


def test_every_doeff_defwire_type_reads_and_writes_as_pydantic_does() -> None:
    types, failures = wire_parity.wire_types_in(DOEFF_PACKAGES)
    assert failures == {}, failures
    assert len(types) >= 100, len(types)  # 2026-10-27 に 101 型 — 集めが空振りしていない事
    report = wire_parity.compare_types(types)
    assert report.mismatches == (), wire_parity.summary(report)
    assert report.unbuildable == {}, report.unbuildable
    assert report.catch_all_types == (), report.catch_all_types
    assert report.schema_default_differences == (), report.schema_default_differences
    assert report.accepted > 0, wire_parity.summary(report)
    assert report.inputs > report.accepted, wire_parity.summary(report)


def test_the_vocabulary_types_read_and_write_as_pydantic_does() -> None:
    types, failures = wire_parity.wire_types_in(["wire_codec_types"])
    assert failures == {}, failures
    report = wire_parity.compare_types(types)
    assert report.mismatches == (), wire_parity.summary(report)
    assert set(report.unbuildable) == OUT_OF_VOCABULARY, report.unbuildable
    # 何でも受ける腕の混ざった union を持つ型は受けた値の比べから外す(規則 4 は test_wire_codec.py の単体の検が確かめる)。
    assert report.catch_all_types == ("wire_codec_types.Mixed",), report.catch_all_types
    # JSON Schema で違うのは節 3b の表の既定値(入れ子の defwire・OpaqueJson)だけ。
    assert all(
        line.split(":")[0].rsplit("/", 1)[1] in {"opaque", "note", "oa", "leaf", "leaves", "spec"}
        for line in report.schema_default_differences
    ), report.schema_default_differences


def test_a_refusal_that_differs_is_reported_with_its_side() -> None:
    # 道具そのものの検: pydantic が受けて自前が断る形(または逆)を不一致として返し、弱める側かを印す。
    types = importlib.import_module("wire_codec_types")
    verdict = wire_parity.compare_input(types.Note, "same", '{"noteText":"a"}', values=True)
    assert verdict.accepted, verdict
    assert verdict.mismatches == (), verdict
    refused = wire_parity.compare_input(types.Note, "refused", '{"noteText":1}', values=True)
    assert not refused.accepted, refused
    assert refused.mismatches == (), refused


def _nested(depth: int) -> str:
    return "[" * depth + "]" * depth


def test_nesting_deeper_than_pydantic_reads_is_invalid_json_from_text_and_from_values() -> None:
    # pydantic(jiter)は配列と object を 200 階層まで読み、201 階層目を json_invalid で断る。json.loads は 1000 階層ほどまで読むので、
    # 上限を置かないと自前の解き手が受ける物が広がる(照らしの test が見つけた差)。
    anything = codec_of(object)
    assert not isinstance(anything.decode_json(_nested(200)), Malformed)
    for text in (_nested(201), "[" * 3000 + "]" * 3000, '{"a":' * 201 + "1" + "}" * 201):
        refused = anything.decode_json(text)
        assert isinstance(refused, Malformed), text[:20]
        assert [(field.field, field.reason.rsplit("[", 1)[1]) for field in refused.fields] == [("", "json_invalid]")]
    assert not isinstance(anything.decode(json.loads(_nested(200))), Malformed)
    refused_value = anything.decode(json.loads(_nested(201)))
    assert isinstance(refused_value, Malformed), refused_value
    # OpaqueJson の中も同じ数え方(値の中の OpaqueJson は外の入れ子の深さを引き継ぐ)。
    inner = OpaqueJson.from_text(_nested(200))
    assert isinstance(anything.decode([inner]), Malformed)
    assert not isinstance(anything.decode([OpaqueJson.from_text(_nested(199))]), Malformed)
