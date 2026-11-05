"""A test that compares every defwire type with the home-grown decoder and the pydantic decoder (section 7 of the design record).

What it checks:

1. Build every defwire type defined in doeff packages (doeff-hy, doeff-cluster, doeff-core-effects, doeff-codex), pass inputs made per type
   (a correct shape, a different field kind, a missing field, an unknown field, a Python field name, a spelling outside the definition, array length ±1, nesting down to 2 levels, corrupted string
   text itself) through both decoders, and check that accept / refuse, the produced value, the written JSON, and the JSON Schema match (0 mismatches — 0 on the weakening side too).
2. The test types (wire_codec_types.hy — types with one vocabulary type each, and types outside the vocabulary) also match, and only the types outside the vocabulary cannot be built.
3. Fixes for differences the comparison found: the nesting depth limit for arrays and objects (200 levels, same as pydantic) is enforced from both strings and values.

The agora-controllers types are compared by agora-controllers' tests with the same tool (doeff_hy._wire_parity).
"""

import importlib
import json
import sys
from pathlib import Path

import doeff_hy  # noqa: F401  # enable the Hy import hook
from doeff_hy import _wire_parity as wire_parity
from doeff_hy._json_value import OpaqueJson
from doeff_hy._wire_codec import codec_of
from doeff_hy._wire_malformed import Malformed

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

#: The doeff packages that define defwire types (counted by grep over packages/*/src — add here when they grow).
DOEFF_PACKAGES = ("doeff_hy", "doeff_cluster", "doeff_core_effects", "doeff_codex")

#: Among the test types, those refused when the tree is built because they are outside the vocabulary (section 2 "outside the vocabulary" of the design record).
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
    assert len(types) >= 100, len(types)  # 101 types on 2026-10-27 — the collection is not coming up empty
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
    # A type holding a union with an accept-anything arm is excluded from the accepted-value comparison (rule 4 is checked by the unit tests in test_wire_codec.py).
    assert report.catch_all_types == ("wire_codec_types.Mixed",), report.catch_all_types
    # The only difference in JSON Schema is the default values of the table in section 3b (nested defwire, OpaqueJson).
    assert all(
        line.split(":")[0].rsplit("/", 1)[1] in {"opaque", "note", "oa", "leaf", "leaves", "spec"}
        for line in report.schema_default_differences
    ), report.schema_default_differences


def test_a_refusal_that_differs_is_reported_with_its_side() -> None:
    # Test of the tool itself: return a form that pydantic accepts and the home-grown decoder refuses (or vice versa) as a mismatch, and mark whether it is on the weakening side.
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
    # pydantic (jiter) reads arrays and objects down to 200 levels and refuses the 201st level with json_invalid. json.loads reads down to about 1000 levels,
    # so without a limit, what the home-grown decoder accepts would widen (a difference the comparison test found).
    anything = codec_of(object)
    assert not isinstance(anything.decode_json(_nested(200)), Malformed)
    for text in (_nested(201), "[" * 3000 + "]" * 3000, '{"a":' * 201 + "1" + "}" * 201):
        refused = anything.decode_json(text)
        assert isinstance(refused, Malformed), text[:20]
        assert [(field.field, field.reason.rsplit("[", 1)[1]) for field in refused.fields] == [("", "json_invalid]")]
    assert not isinstance(anything.decode(json.loads(_nested(200))), Malformed)
    refused_value = anything.decode(json.loads(_nested(201)))
    assert isinstance(refused_value, Malformed), refused_value
    # OpaqueJson is counted the same way (an OpaqueJson inside a value inherits the nesting depth of the outside).
    inner = OpaqueJson.from_text(_nested(200))
    assert isinstance(anything.decode([inner]), Malformed)
    assert not isinstance(anything.decode([OpaqueJson.from_text(_nested(199))]), Malformed)
