"""収集記録のJSON境界が実値の型を確かめる反例(#1468)。"""

import pytest
from doeff_hy.pytest_items import (
    Dynamic,
    FunctionItem,
    LiteralValue,
    MalformedRecord,
    Mark,
    ModuleMarks,
    OpaqueValue,
    Parametrize,
    Record,
    SkipIf,
    decode_records,
    encode_records,
)


@pytest.mark.parametrize(
    "text",
    [
        '{"function": "test_x", "args": [], "decorators": [{"parametrize": "x", "values": [{"literal": []}]}]}',
        '{"function": "test_x", "args": [], "decorators": [{"parametrize": "x", "values": [{"literal": [], "opaque": true}]}]}',
        '{"function": "test_x", "pytestmark": []}',
        '{"function": 3, "args": [], "decorators": []}',
        '{"function": "test_x", "args": [3], "decorators": []}',
        '{"function": "test_x", "args": "xy", "decorators": []}',
        '{"function": "test_x", "args": [], "decorators": [{"mark": 3}]}',
        '{"function": "test_x", "args": [], "decorators": [{"parametrize": 3, "values": []}]}',
        '{"function": "test_x", "args": [], "decorators": [{"parametrize": "x", "values": [], "ids": ["extra"]}]}',
        '{"function": "test_x", "args": [], "decorators": [{"parametrize": "x", "values": [{"literal": 1}], "ids": [3]}]}',
        '{"pytestmark": "slow"}',
        '{"pytestmark": [3]}',
        '{"dynamic": 3, "reason": "unknown"}',
        '{"dynamic": "test_x", "reason": 3}',
        "[]",
        "null",
        "3",
    ],
)
def test_malformed_field_types_are_rejected(text: str) -> None:
    with pytest.raises(MalformedRecord):
        decode_records([text])


@pytest.mark.parametrize("value", [b'{"pytestmark": []}', 3, None])
def test_record_text_must_be_a_string(value: object) -> None:
    with pytest.raises(MalformedRecord):
        decode_records([value])


def test_record_values_and_ids_round_trip_without_coercion() -> None:
    records: list[Record] = [
        FunctionItem(
            "test_x", ("value",),
            (Parametrize("value", (LiteralValue(1), LiteralValue(True), LiteralValue(None), OpaqueValue()),
                         ("one", None, "none", "opaque")), Mark("slow"), SkipIf()),
        ),
        ModuleMarks(("slow",)),
        Dynamic("test_dynamic", "unknown"),
    ]
    assert decode_records(encode_records(records)) == records
