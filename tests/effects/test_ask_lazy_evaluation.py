"""Deferred lazy Ask edge cases from legacy CESK-era suites.

Core lazy Ask behavior is now covered by active rust_vm tests in
`tests/core/test_runtime_regressions_manual.py`.

These remaining cases are intentionally deferred and tracked by ISSUE-SPEC-009.
"""

import pytest

from doeff import Ask, do

pytestmark = pytest.mark.skip(
    reason=(
        "Deferred lazy Ask edge cases are not in the active rust_vm matrix; "
        "tracked by ISSUE-SPEC-009 migration/drop plan."
    )
)


class TestDeferredLazyAskEdgeCases:
    @pytest.mark.asyncio
    async def test_hashable_keys_work(self, parameterized_interpreter) -> None:
        @do
        def make_prog(val):
            return val

        env = {
            "string_key": make_prog("string"),
            42: make_prog("int"),
            ("tuple", "key"): make_prog("tuple"),
        }

        @do
        def program():
            str_val = yield Ask("string_key")
            int_val = yield Ask(42)
            tuple_val = yield Ask(("tuple", "key"))
            return (str_val, int_val, tuple_val)

        result = await parameterized_interpreter.run_async(program(), env=env)
        assert result.is_ok
        assert result.value == ("string", "int", "tuple")

    @pytest.mark.asyncio
    async def test_direct_circular_ask_raises_error(self, parameterized_interpreter) -> None:
        @do
        def circular_program():
            return (yield Ask("self"))

        env = {"self": circular_program()}

        @do
        def program():
            return (yield Ask("self"))

        result = await parameterized_interpreter.run_async(program(), env=env)
        assert result.is_err()
        assert "circular" in str(result.error).lower()

    @pytest.mark.asyncio
    async def test_indirect_circular_ask_raises_error(self, parameterized_interpreter) -> None:
        @do
        def program_a():
            return (yield Ask("b"))

        @do
        def program_b():
            return (yield Ask("a"))

        env = {"a": program_a(), "b": program_b()}

        @do
        def program():
            return (yield Ask("a"))

        result = await parameterized_interpreter.run_async(program(), env=env)
        assert result.is_err()
        assert "circular" in str(result.error).lower()
