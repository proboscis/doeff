from __future__ import annotations

from doeff import Ask, MissingEnvKeyError, default_handlers, do, run


def test_default_handlers_missing_ask_key_exits_without_hanging() -> None:
    @do
    def failing():
        _ = yield Ask("missing_key")
        return 42

    result = run(failing(), handlers=default_handlers(), print_doeff_trace=False)

    assert result.is_err()
    assert isinstance(result.error, MissingEnvKeyError)
