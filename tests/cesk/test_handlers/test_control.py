"""Unit tests for control flow effect handlers."""

from __future__ import annotations

import pytest
from doeff import do
from doeff._vendor import FrozenDict
from doeff.cesk.actions import RunProgram, Resume
from doeff.cesk.handlers import HandlerContext, HandlerResult
from doeff.cesk.handlers.control import (
    handle_gather,
    handle_intercept,
    handle_listen,
    handle_local,
    handle_safe,
)
from doeff.cesk.types import TaskId
from doeff.effects import (
    GatherEffect,
    InterceptEffect,
    LocalEffect,
    PureEffect,
    ResultSafeEffect,
    WriterListenEffect,
)


def make_ctx(
    env: dict | None = None,
    store: dict | None = None,
) -> HandlerContext:
    """Create a test HandlerContext."""
    return HandlerContext(
        task_id=TaskId(0),
        env=FrozenDict(env or {}),
        store=store or {},
        kontinuation=[],
    )


class TestHandleLocal:
    """Tests for handle_local."""

    def test_runs_program_with_merged_env(self) -> None:
        """Runs sub-program with merged environment."""

        @do
        def sub_prog() -> int:
            yield from PureEffect(42)
            return 42

        effect = LocalEffect(env_update={"y": 2}, sub_program=sub_prog())
        ctx = make_ctx(env={"x": 1})

        result = handle_local(effect, ctx)

        assert len(result.actions) == 1
        assert isinstance(result.actions[0], RunProgram)
        assert result.actions[0].env is not None
        assert result.actions[0].env["x"] == 1  # Original preserved
        assert result.actions[0].env["y"] == 2  # New added

    def test_new_env_overrides_existing(self) -> None:
        """New environment values override existing."""

        @do
        def sub_prog() -> int:
            yield from PureEffect(42)
            return 42

        effect = LocalEffect(env_update={"x": 100}, sub_program=sub_prog())
        ctx = make_ctx(env={"x": 1})

        result = handle_local(effect, ctx)

        assert result.actions[0].env["x"] == 100


class TestHandleIntercept:
    """Tests for handle_intercept."""

    def test_runs_sub_program(self) -> None:
        """Runs the intercepted program."""

        @do
        def sub_prog() -> int:
            yield from PureEffect(42)
            return 42

        transform = lambda e: e
        effect = InterceptEffect(program=sub_prog(), transforms=(transform,))
        ctx = make_ctx()

        result = handle_intercept(effect, ctx)

        assert len(result.actions) == 1
        assert isinstance(result.actions[0], RunProgram)


class TestHandleListen:
    """Tests for handle_listen."""

    def test_runs_sub_program(self) -> None:
        """Runs the listened program."""

        @do
        def sub_prog() -> int:
            yield from PureEffect(42)
            return 42

        effect = WriterListenEffect(sub_program=sub_prog())
        ctx = make_ctx()

        result = handle_listen(effect, ctx)

        assert len(result.actions) == 1
        assert isinstance(result.actions[0], RunProgram)


class TestHandleGather:
    """Tests for handle_gather."""

    def test_empty_gather_returns_empty_list(self) -> None:
        """Empty gather returns empty list immediately."""
        effect = GatherEffect(programs=())
        ctx = make_ctx()

        result = handle_gather(effect, ctx)

        assert len(result.actions) == 1
        assert isinstance(result.actions[0], Resume)
        assert result.actions[0].value == []

    def test_runs_first_program(self) -> None:
        """Runs the first program in gather."""

        @do
        def prog1() -> int:
            yield from PureEffect(1)
            return 1

        @do
        def prog2() -> int:
            yield from PureEffect(2)
            return 2

        effect = GatherEffect(programs=(prog1(), prog2()))
        ctx = make_ctx()

        result = handle_gather(effect, ctx)

        assert len(result.actions) == 1
        assert isinstance(result.actions[0], RunProgram)


class TestHandleSafe:
    """Tests for handle_safe."""

    def test_runs_sub_program(self) -> None:
        """Runs the safe program."""

        @do
        def sub_prog() -> int:
            yield from PureEffect(42)
            return 42

        effect = ResultSafeEffect(sub_program=sub_prog())
        ctx = make_ctx()

        result = handle_safe(effect, ctx)

        assert len(result.actions) == 1
        assert isinstance(result.actions[0], RunProgram)
