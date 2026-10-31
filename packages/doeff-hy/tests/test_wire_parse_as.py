"""doeff_hy._wire の parse-as・parse-json-as の検。

parse-as は defwire でない語彙の型(入れ物 — dict[str, object]・list[str]・str | None・dict[str, OpaqueJson]・defwire の型の tuple)を
parse と同じ型の木と厳しさで解く。pydantic の TypeAdapter で入れ物を直に解いていた読み手(agora-controllers)の移り先。確かめること:

1. 入れ物の型の値を受け、凍らせた値(FrozenMap・tuple)と OpaqueJson も受ける。OpaqueJson の欄は中を読まずに包む。
2. parse と同じく、文字列を数にしない・真偽を数にしない・object でない物を dict の型に入れない — 形の外は Malformed で、型の名と場所を持つ。
3. parse-json-as は JSON の文字列(bytes も)から読み、JSON として読めない文字列も Malformed。
"""

import importlib

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
import pytest
from doeff import run
from doeff_hy._frozen import freeze_json
from doeff_hy._json_value import OpaqueJson
from doeff_hy._wire import Malformed, parse_as, parse_json_as

types = importlib.import_module("wire_codec_types")


def test_parse_as_reads_container_shapes() -> None:
    assert run(parse_as(dict[str, object], {"a": [1, {"b": None}]})) == {"a": [1, {"b": None}]}
    assert run(parse_as(list[str], ["x", "y"])) == ["x", "y"]
    assert run(parse_as(tuple[str, ...], ["x", "y"])) == ("x", "y")
    assert run(parse_as(str | None, None)) is None
    assert run(parse_as(int, 3)) == 3
    assert run(parse_as(dict[str, int], freeze_json({"n": 1}))) == {"n": 1}
    wrapped = run(parse_as(dict[str, OpaqueJson], {"x": {"y": [1, 2]}}))
    assert wrapped == {"x": OpaqueJson.of({"y": [1, 2]})}
    assert run(parse_as(list[int], OpaqueJson.of([1, 2]))) == [1, 2]


def test_parse_as_reads_defwire_types_inside_containers() -> None:
    rows = run(parse_as(tuple[types.Holder, ...], [{"holderId": "h1", "expiresAt": 1}, {"holderId": "h2", "expiresAt": 2.5}]))
    assert rows == (types.Holder(holder_id="h1", expires_at=1), types.Holder(holder_id="h2", expires_at=2.5))


@pytest.mark.parametrize(
    ("shape", "raw"),
    [
        (int, "1"),
        (int, True),
        (str, 1),
        (dict[str, object], [1]),
        (list[str], ["x", 1]),
        (str | None, 0),
        (tuple[types.Holder, ...], [{"holderId": 1, "expiresAt": 1}]),
    ],
)
def test_parse_as_refuses_what_parse_refuses(shape: object, raw: object) -> None:
    refused = run(parse_as(shape, raw))
    assert isinstance(refused, Malformed), refused
    assert refused.fields, refused


def test_parse_as_names_the_shape_and_the_place() -> None:
    refused = run(parse_as(dict[str, int], {"n": "1"}))
    assert isinstance(refused, Malformed)
    assert [one.field for one in refused.fields] == ["n"]
    assert "dict" in refused.wire_type


def test_parse_json_as_reads_text_and_refuses_broken_json() -> None:
    assert run(parse_json_as(list[str], '["a","b"]')) == ["a", "b"]
    assert run(parse_json_as(dict[str, OpaqueJson], b'{"k":{"v":1}}')) == {"k": OpaqueJson.of({"v": 1})}
    assert isinstance(run(parse_json_as(list[str], "[1]")), Malformed)
    assert isinstance(run(parse_json_as(dict[str, object], "{not json")), Malformed)
