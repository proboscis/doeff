from __future__ import annotations

from doeff import Ask, MissingEnvKeyError, do
from tests._run_helpers import run_with_defaults


def test_default_handlers_missing_ask_key_exits_without_hanging() -> None:
    @do
    def failing():
        _ = yield Ask("missing_key")
        return 42

    result = run_with_defaults(failing())

    assert result.is_err()
    assert isinstance(result.error, MissingEnvKeyError)
