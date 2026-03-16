from __future__ import annotations

from dataclasses import dataclass

import pytest

from doeff import (
    Ask,
    Delegate,
    Effect,
    EffectBase,
    Program,
    Try,
    WithHandler,
    async_run,
    default_async_handlers,
    default_handlers,
    do,
    run,
)


def test_run_does_not_print_doeff_trace_to_stderr_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    @do
    def failing() -> Program[int]:
        _ = yield Ask("nonexistent_key")
        return 42

    result = run(failing(), handlers=default_handlers())
    assert result.is_err()

    captured = capsys.readouterr()
    assert captured.err == ""


def test_run_prints_trace_when_flag_is_true(capsys: pytest.CaptureFixture[str]) -> None:
    @do
    def failing() -> Program[int]:
        _ = yield Ask("nonexistent_key")
        return 42

    result = run(failing(), handlers=default_handlers(), print_doeff_trace=True)
    assert result.is_err()

    captured = capsys.readouterr()
    assert "doeff Traceback" in captured.err
    assert "nonexistent_key" in captured.err


@pytest.mark.asyncio
async def test_async_run_does_not_print_doeff_trace_to_stderr_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    @do
    def failing() -> Program[int]:
        _ = yield Ask("nonexistent_key")
        return 42

    result = await async_run(failing(), handlers=default_async_handlers())
    assert result.is_err()

    captured = capsys.readouterr()
    assert captured.err == ""


@pytest.mark.asyncio
async def test_async_run_prints_trace_when_flag_is_true(
    capsys: pytest.CaptureFixture[str],
) -> None:
    @do
    def failing() -> Program[int]:
        _ = yield Ask("nonexistent_key")
        return 42

    result = await async_run(
        failing(),
        handlers=default_async_handlers(),
        print_doeff_trace=True,
    )
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


def test_run_warns_on_caught_early_termination(capsys: pytest.CaptureFixture[str]) -> None:
    @dataclass(frozen=True, kw_only=True)
    class Boom(EffectBase):
        pass

    @do
    def bad_handler(effect: Effect, _k: object):
        if isinstance(effect, Boom):
            return "bad-return"
        yield Delegate()

    @do
    def inner() -> Program[None]:
        yield Boom()

    @do
    def body():
        return (yield Try(WithHandler(bad_handler, inner())))

    result = run(body(), handlers=default_handlers())
    assert result.is_ok()
    assert result.early_terminated is True

    captured = capsys.readouterr()
    assert "Program terminated early before the root program completed." in captured.err
    assert "doeff Traceback" in captured.err
    assert "bad_handler" in captured.err


def test_run_can_suppress_caught_early_termination_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    @dataclass(frozen=True, kw_only=True)
    class Boom(EffectBase):
        pass

    @do
    def bad_handler(effect: Effect, _k: object):
        if isinstance(effect, Boom):
            return "bad-return"
        yield Delegate()

    @do
    def inner() -> Program[None]:
        yield Boom()

    @do
    def body():
        return (yield Try(WithHandler(bad_handler, inner())))

    result = run(
        body(),
        handlers=default_handlers(),
        warn_early_termination=False,
    )
    assert result.is_ok()
    assert result.early_terminated is True

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
    assert captured.err == ""


def test_doeff_trace_renders_active_chain_when_explicitly_enabled(
    capsys: pytest.CaptureFixture[str],
) -> None:
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

    result = run(
        WithHandler(exploding_handler, body()),
        handlers=default_handlers(),
        print_doeff_trace=True,
    )
    assert result.is_err()

    captured = capsys.readouterr()
    assert "exploding_handler" in captured.err
    assert "handler exploded" in captured.err
    assert "exploding_handler ✗" in captured.err
    assert "·" in captured.err
    assert "Boom" in captured.err
