"""Tests for parse-as and parse-json-as of doeff_hy._wire.

parse-as decodes non-defwire vocabulary types (containers -- dict[str, object], list[str], str | None, dict[str, OpaqueJson], tuple of defwire types) with
the same type tree and strictness as parse. It is the migration target for readers (agora-controllers) that decoded containers directly with pydantic's TypeAdapter. What is checked:

1. It accepts values of the container types, and also frozen values (FrozenMap, tuple) and OpaqueJson. An OpaqueJson field is wrapped without reading its contents.
2. As with parse, it does not turn strings into numbers, does not turn booleans into numbers, and does not put a non-object into a dict type -- anything outside the shape is Malformed, carrying the type name and location.
3. parse-json-as reads from a JSON string (bytes too), and a string that cannot be read as JSON is also Malformed.
"""

import importlib

import doeff_hy  # noqa: F401  # enable the Hy import hook
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
