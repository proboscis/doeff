"""SA-008 runtime contract regressions (converted from ad-hoc probes).

These tests codify correctness expectations for Rust VM run/store behavior.
"""

from __future__ import annotations

import asyncio

import pytest

from doeff import Await, Gather, Spawn, async_run, do
from doeff.effects.state import Get, Modify, Put
from doeff.rust_vm import default_async_handlers, default_handlers, run


def test_sa008_run_store_seed_and_put_get_roundtrip() -> None:
    @do
    def prog():
        _ = yield Put("x", 5)
        value = yield Get("x")
        return value

    result = run(prog(), handlers=default_handlers(), store={"x": 0})

    assert result.value == 5
    assert result.raw_store["x"] == 5


def test_sa008_modify_returns_old_value_and_updates_store() -> None:
    @do
    def prog():
        old = yield Modify("x", lambda v: (v or 0) + 1)
        new = yield Get("x")
        return (old, new)

    result = run(prog(), handlers=default_handlers(), store={"x": 3})

    assert result.value == (3, 4)
    assert result.raw_store["x"] == 4


def test_sa008_modify_missing_key_uses_none_and_initializes_store() -> None:
    @do
    def prog():
        old = yield Modify("x", lambda v: 42 if v is None else v + 1)
        new = yield Get("x")
        return (old, new)

    result = run(prog(), handlers=default_handlers())

    assert result.value == (None, 42)
    assert result.raw_store["x"] == 42


def test_sa008_put_overwrites_existing_value() -> None:
    @do
    def prog():
        yield Put("x", 1)
        yield Put("x", 2)
        value = yield Get("x")
        return value

    result = run(prog(), handlers=default_handlers())

    assert result.value == 2
    assert result.raw_store["x"] == 2


def test_sa008_put_returns_none() -> None:
    @do
    def prog():
        put_result = yield Put("key", "value")
        stored = yield Get("key")
        return (put_result, stored)

    result = run(prog(), handlers=default_handlers())

    assert result.value == (None, "value")
    assert result.raw_store["key"] == "value"


def test_sa008_modify_atomic_on_transform_error() -> None:
    @do
    def prog():
        yield Put("value", 100)

        def failing_transform(x: int) -> int:
            raise ValueError("transform failed")

        _ = yield Modify("value", failing_transform)
        return "unreachable"

    result = run(prog(), handlers=default_handlers())

    assert result.is_err()
    assert isinstance(result.error, ValueError)
    assert str(result.error) == "transform failed"
    assert result.raw_store["value"] == 100


def test_sa008_gather_uses_isolated_state_snapshots() -> None:
    @do
    def increment():
        current = yield Get("counter")
        yield Put("counter", current + 1)
        return current

    @do
    def prog():
        yield Put("counter", 0)
        t1 = yield Spawn(increment())
        t2 = yield Spawn(increment())
        t3 = yield Spawn(increment())
        results = yield Gather(t1, t2, t3)
        final = yield Get("counter")
        return (results, final)

    result = run(prog(), handlers=default_handlers())

    assert result.value == ([0, 0, 0], 0)
    assert result.raw_store["counter"] == 0


def test_sa008_sync_await_runs_in_default_handlers() -> None:
    @do
    def prog():
        _ = yield Await(asyncio.sleep(0.001))
        return "ok"

    result = run(prog(), handlers=default_handlers())

    assert result.value == "ok"


def test_sa008_sync_await_propagates_coroutine_error() -> None:
    async def boom() -> None:
        raise ValueError("await boom")

    @do
    def prog():
        _ = yield Await(boom())
        return "unreachable"

    result = run(prog(), handlers=default_handlers())

    assert result.is_err()
    assert isinstance(result.error, ValueError)
    assert str(result.error) == "await boom"


@pytest.mark.asyncio
async def test_sa008_gather_branch_state_changes_not_shared() -> None:
    @do
    def writer():
        yield Put("message", "written by branch 1")
        return "writer done"

    @do
    def reader():
        yield Await(asyncio.sleep(0.01))
        message = yield Get("message")
        return message

    @do
    def prog():
        yield Put("message", "initial")
        t1 = yield Spawn(writer())
        t2 = yield Spawn(reader())
        results = yield Gather(t1, t2)
        final = yield Get("message")
        return (results, final)

    result = await async_run(prog(), handlers=default_async_handlers())

    assert result.value == (["writer done", "initial"], "initial")
