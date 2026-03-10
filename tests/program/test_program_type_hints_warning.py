from __future__ import annotations

import warnings

import doeff.program as program_module
import pytest


def test_safe_get_type_hints_returns_empty_dict_without_warning_on_resolution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_type_hint_error(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise NameError("missing symbol")

    def sample(value: int) -> int:
        return value

    monkeypatch.setattr(program_module, "get_type_hints", _raise_type_hint_error)

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        hints = program_module._safe_get_type_hints(sample)

    assert hints == {}
    assert recorded == []
