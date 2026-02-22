from __future__ import annotations

import pytest

import doeff.program as program_module


def test_safe_get_type_hints_warns_on_resolution_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_type_hint_error(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise NameError("missing symbol")

    def sample(value: int) -> int:
        return value

    monkeypatch.setattr(program_module, "get_type_hints", _raise_type_hint_error)

    with pytest.warns(UserWarning, match="Failed to resolve type hints for"):
        hints = program_module._safe_get_type_hints(sample)

    assert hints == {}
