from __future__ import annotations

from dataclasses import dataclass

import pytest

from doeff import (
    Ask,
    Delegate,
    Effect,
    EffectBase,
    Program,
    WithHandler,
    async_run,
    default_async_handlers,
    default_handlers,
    do,
    run,
)


def test_run_prints_doeff_trace_to_stderr_on_error(capsys: pytest.CaptureFixture[str]) -> None:
    @do
    def failing() -> Program[int]:
        _ = yield Ask("nonexistent_key")
        return 42

    result = run(failing(), handlers=default_handlers())
    assert result.is_err()

    captured = capsys.readouterr()
    assert "doeff Traceback" in captured.err
    assert "nonexistent_key" in captured.err


def test_run_suppresses_trace_when_flag_is_false(capsys: pytest.CaptureFixture[str]) -> None:
    @do
    def failing() -> Program[int]:
        _ = yield Ask("nonexistent_key")
        return 42

    result = run(failing(), handlers=default_handlers(), print_doeff_trace=False)
    assert result.is_err()

    captured = capsys.readouterr()
    assert "doeff Traceback" not in captured.err


@pytest.mark.asyncio
async def test_async_run_prints_doeff_trace_to_stderr_on_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    @do
    def failing() -> Program[int]:
        _ = yield Ask("nonexistent_key")
        return 42

    result = await async_run(failing(), handlers=default_async_handlers())
    assert result.is_err()

    captured = capsys.readouterr()
    assert "doeff Traceback" in captured.err
    assert "nonexistent_key" in captured.err


def test_run_no_stderr_on_success(capsys: pytest.CaptureFixture[str]) -> None:
    @do
    def ok() -> Program[int]:
        return 42

    result = run(ok(), handlers=default_handlers())
    assert result.is_ok()

    captured = capsys.readouterr()
    assert captured.err == ""


def test_doeff_trace_renders_active_chain(capsys: pytest.CaptureFixture[str]) -> None:
    @dataclass(frozen=True, kw_only=True)
    class Boom(EffectBase):
        pass

    @do
    def exploding_handler(effect: Effect, _k: object):
        if isinstance(effect, Boom):
            raise RuntimeError("handler exploded")
        yield Delegate()

    @do
    def body() -> Program[int]:
        yield Boom()
        return 1

    result = run(WithHandler(exploding_handler, body()), handlers=default_handlers())
    assert result.is_err()

    captured = capsys.readouterr()
    assert "exploding_handler" in captured.err
    assert "handler exploded" in captured.err
    assert "exploding_handler✗" in captured.err
    assert "·" in captured.err
    assert "Boom" in captured.err
