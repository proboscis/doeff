"""Test that Dep resolves Program values and caches the result."""

from collections.abc import Generator
from typing import Any

import pytest

from doeff import Dep, ExecutionContext, Log, Program, ProgramInterpreter, do


@pytest.mark.asyncio
async def test_dep_resolves_program_env_value():
    """Dep resolves Program-valued environment entries once and caches result."""
    engine = ProgramInterpreter()

    call_count = 0

    @do
    def config_provider() -> Generator[Program, Any, str]:
        nonlocal call_count
        call_count += 1
        yield Log(f"computing config (call #{call_count})")
        return "computed_value"

    @do
    def main_program() -> Generator[Program, Any, tuple[str, str, str]]:
        # Request the same Program-based env value three times
        first = yield Dep("config")
        second = yield Dep("config")
        third = yield Dep("config")
        return first, second, third

    context = ExecutionContext(env={"config": config_provider()})
    result = await engine.run_async(main_program(), context)

    assert result.is_ok
    first, second, third = result.value

    # All three should get the same cached value
    assert first == second == third == "computed_value"

    # The Program should only have been evaluated once
    assert call_count == 1

    # The env should now contain the resolved value, not the Program
    assert result.context.env["config"] == "computed_value"


@pytest.mark.asyncio
async def test_dep_and_ask_share_cache():
    """Dep and Ask share the same resolution cache."""
    from doeff import Ask

    engine = ProgramInterpreter()

    call_count = 0

    @do
    def expensive_config() -> Generator[Program, Any, int]:
        nonlocal call_count
        call_count += 1
        yield Log(f"expensive computation #{call_count}")
        return 42

    @do
    def mixed_program() -> Generator[Program, Any, tuple[int, int, int]]:
        # Use Dep first
        via_dep = yield Dep("value")
        # Then use Ask - should get cached result
        via_ask = yield Ask("value")
        # Then Dep again - still cached
        via_dep_again = yield Dep("value")
        return via_dep, via_ask, via_dep_again

    context = ExecutionContext(env={"value": expensive_config()})
    result = await engine.run_async(mixed_program(), context)

    assert result.is_ok
    via_dep, via_ask, via_dep_again = result.value

    # All should be the same cached value
    assert via_dep == via_ask == via_dep_again == 42

    # Should only compute once despite mixing Dep and Ask
    assert call_count == 1


@pytest.mark.asyncio
async def test_dep_detects_cyclic_dependencies():
    """Dep detects cyclic Program dependencies."""
    engine = ProgramInterpreter()

    @do
    def cyclic_a() -> Generator[Program, Any, str]:
        b_value = yield Dep("b")
        return f"a:{b_value}"

    @do
    def cyclic_b() -> Generator[Program, Any, str]:
        a_value = yield Dep("a")
        return f"b:{a_value}"

    @do
    def main_program() -> Generator[Program, Any, str]:
        result = yield Dep("a")
        return result

    context = ExecutionContext(env={
        "a": cyclic_a(),
        "b": cyclic_b(),
    })

    result = await engine.run_async(main_program(), context)

    assert result.is_err
    assert "Cyclic" in str(result.result.error) or "cyclic" in str(result.result.error).lower()


@pytest.mark.asyncio
async def test_dep_with_nested_program_values():
    """Dep can resolve Programs that themselves use Dep."""
    engine = ProgramInterpreter()

    @do
    def base_config() -> Generator[Program, Any, int]:
        yield Log("computing base")
        return 10

    @do
    def derived_config() -> Generator[Program, Any, int]:
        base = yield Dep("base")
        yield Log(f"computing derived from base={base}")
        return base * 2

    @do
    def main_program() -> Generator[Program, Any, int]:
        result = yield Dep("derived")
        return result

    context = ExecutionContext(env={
        "base": base_config(),
        "derived": derived_config(),
    })

    result = await engine.run_async(main_program(), context)

    assert result.is_ok
    assert result.value == 20

    # Both should be cached as resolved values
    assert result.context.env["base"] == 10
    assert result.context.env["derived"] == 20


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
