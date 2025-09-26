"""Tests for the Maybe monad utility types."""

import pytest

from doeff import Err, Maybe, NOTHING, Ok, Some


def test_maybe_from_optional_and_truthiness():
    some = Maybe.from_optional(42)
    nothing = Maybe.from_optional(None)

    assert isinstance(some, Some)
    assert some.unwrap() == 42

    assert nothing is NOTHING
    assert not nothing
    assert some


def test_maybe_map_and_flat_map():
    base = Maybe.from_optional(3)

    mapped = base.map(lambda x: x + 1)
    assert isinstance(mapped, Some)
    assert mapped.unwrap() == 4

    chained = base.flat_map(lambda x: Maybe.from_optional(x * 2))
    assert isinstance(chained, Some)
    assert chained.unwrap() == 6

    assert Maybe.from_optional(None).map(lambda x: x) is NOTHING
    assert Maybe.from_optional(None).flat_map(lambda x: Maybe.from_optional(x)) is NOTHING

    with pytest.raises(TypeError):
        base.flat_map(lambda x: x + 1)  # type: ignore[arg-type]


def test_maybe_ok_or_helpers():
    some = Maybe.from_optional("value")
    nothing = Maybe.from_optional(None)

    ok_result = some.ok_or(ValueError("missing"))
    assert isinstance(ok_result, Ok)
    assert ok_result.unwrap() == "value"

    err_instance = ValueError("missing")
    err_result = nothing.ok_or(err_instance)
    assert isinstance(err_result, Err)
    with pytest.raises(ValueError):
        err_result.unwrap()

    lazy_result = nothing.ok_or_else(lambda: RuntimeError("boom"))
    assert isinstance(lazy_result, Err)
    with pytest.raises(RuntimeError):
        lazy_result.unwrap()


def test_maybe_to_optional_conversion():
    some = Maybe.from_optional(10)
    nothing = Maybe.from_optional(None)

    assert some.to_optional() == 10
    assert nothing.to_optional() is None
