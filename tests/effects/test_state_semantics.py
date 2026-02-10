"""Tests for State effects semantics.

These tests verify the behavior documented in SPEC-EFF-002-state.md.
"""

import pytest

from doeff import Get, Modify, Put, Safe, Spawn, do
from doeff.program import Program

pytestmark = pytest.mark.skip(
    reason=(
        "Legacy CESK-era state semantics are not in the active rust_vm matrix; "
        "tracked by ISSUE-SPEC-009 migration/drop plan."
    )
)


class TestPutSemantics:
    """Tests for Put effect behavior."""

    @pytest.mark.asyncio
    async def test_put_overwrites_existing(self, parameterized_interpreter) -> None:
        """Put overwrites any existing value for the key."""

        @do
        def program() -> Program[int]:
            yield Put("x", 1)
            yield Put("x", 2)
            value = yield Get("x")
            return value

        result = await parameterized_interpreter.run_async(program())

        assert result.is_ok
        assert result.value == 2

    @pytest.mark.asyncio
    async def test_put_returns_none(self, parameterized_interpreter) -> None:
        """Put returns None."""

        @do
        def program() -> Program[None]:
            put_result = yield Put("key", "value")
            return put_result

        result = await parameterized_interpreter.run_async(program())

        assert result.is_ok
        assert result.value is None


class TestModifySemantics:
    """Tests for Modify effect behavior."""

    @pytest.mark.asyncio
    async def test_modify_atomic_on_error(self, parameterized_interpreter) -> None:
        """Modify is atomic: if func raises, store is unchanged.

        See SPEC-EFF-002-state.md Composition Rules: Modify atomicity.
        """

        @do
        def program() -> Program[int]:
            yield Put("value", 100)

            def failing_transform(x: int) -> int:
                raise ValueError("transform failed")

            # Wrap in Safe to catch the error
            error_result = yield Safe(Modify("value", failing_transform))

            # Verify the store wasn't changed
            final_value = yield Get("value")
            return final_value

        result = await parameterized_interpreter.run_async(program())

        assert result.is_ok
        # Value should be unchanged because the transform raised
        assert result.value == 100


class TestGatherStateComposition:
    """Tests for Gather + State composition rules.

    Per SPEC-EFF-002-state.md: Spawned tasks get isolated store snapshots.
    """

    @pytest.mark.asyncio
    async def test_gather_isolated_store_semantics(self) -> None:
        """Each Gather branch has isolated store snapshot.

        See SPEC-EFF-002-state.md Composition Rules: Gather + Put.
        Spawned tasks receive a snapshot of the store at spawn time.
        """
        from doeff import async_run, default_handlers
        from doeff.effects import Gather

        @do
        def increment() -> Program[int]:
            current = yield Get("counter")
            yield Put("counter", current + 1)
            return current

        @do
        def program() -> Program[tuple[list[int], int]]:
            yield Put("counter", 0)
            t1 = yield Spawn(increment())
            t2 = yield Spawn(increment())
            t3 = yield Spawn(increment())
            results = yield Gather(t1, t2, t3)
            final = yield Get("counter")
            return (results, final)

        result = await async_run(program(), handlers=default_handlers())
        results, final = result.value

        # Each task sees isolated snapshot: counter=0
        assert results == [0, 0, 0]
        # Parent store unchanged (isolated from children)
        assert final == 0

    @pytest.mark.asyncio
    async def test_gather_state_isolated_across_branches(self) -> None:
        """State changes in one Gather branch are NOT visible to others (isolated)."""
        import asyncio

        from doeff import async_run, default_handlers
        from doeff.effects import Await, Gather

        @do
        def writer() -> Program[str]:
            yield Put("message", "written by branch 1")
            return "writer done"

        @do
        def reader() -> Program[str]:
            yield Await(asyncio.sleep(0.01))
            message = yield Get("message")
            return message

        @do
        def program() -> Program[tuple[list[str], str]]:
            yield Put("message", "initial")
            t1 = yield Spawn(writer())
            t2 = yield Spawn(reader())
            results = yield Gather(t1, t2)
            final = yield Get("message")
            return (results, final)

        result = await async_run(program(), handlers=default_handlers())
        results, final = result.value

        # Reader sees its own snapshot ("initial"), not writer's changes
        assert results == ["writer done", "initial"]
        # Parent store unchanged
        assert final == "initial"
