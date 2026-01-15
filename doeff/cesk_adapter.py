"""
CESK Interpreter Adapter - provides ProgramInterpreter-compatible interface.

This module provides a compatibility layer for running tests written for
ProgramInterpreter against the new runtime-based interpreter.

Usage:
    # Replace:
    #   engine = ProgramInterpreter()
    #   result = await engine.run_async(program())
    # With:
    #   engine = CESKInterpreter()
    #   result = await engine.run_async(program())
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from doeff._vendor import Err, FrozenDict, Ok, Result
from doeff.cesk import Environment, Store
from doeff._types_internal import ExecutionContext

if TYPE_CHECKING:
    from doeff.program import Program

T = TypeVar("T")


@dataclass
class _CompatibilityContext:
    """Minimal ExecutionContext-like object for RunResult compatibility."""
    
    env: dict[Any, Any]
    state: dict[str, Any]
    log: list[Any]
    
    @property
    def graph(self) -> Any:
        from doeff._vendor import WGraph, WNode, WStep
        return WGraph(last=WStep(inputs=(), output=WNode("_root"), meta={}), steps=frozenset())
    
    @property
    def io_allowed(self) -> bool:
        return True
    
    @property
    def cache(self) -> dict[str, Any]:
        return {}
    
    @property
    def effect_observations(self) -> list[Any]:
        return []
    
    @property
    def program_call_stack(self) -> list[Any]:
        return []


@dataclass
class CESKRunResult(Generic[T]):
    """
    RunResult-compatible wrapper for CESK interpreter output.

    Provides the same interface as RunResult for test compatibility:
    - .is_ok / .is_err
    - .value
    - .log
    - .state
    - .result
    - .context (compatibility with RunResult)
    """

    _result: Result[T]
    _final_store: Store = field(default_factory=dict)
    _final_env: Environment = field(default_factory=FrozenDict)

    @property
    def result(self) -> Result[T]:
        return self._result

    @property
    def is_ok(self) -> bool:
        return isinstance(self._result, Ok)

    @property
    def is_err(self) -> bool:
        return isinstance(self._result, Err)

    @property
    def value(self) -> T:
        return self._result.unwrap()

    @property
    def log(self) -> list[Any]:
        return self._final_store.get("__log__", [])

    @property
    def state(self) -> dict[str, Any]:
        return {k: v for k, v in self._final_store.items() if not k.startswith("__")}

    @property
    def env(self) -> dict[Any, Any]:
        return dict(self._final_env)

    @property
    def error(self) -> Exception:
        err = self._result.err()
        if err is not None:
            return err
        raise ValueError("Cannot access error on successful result")
    
    @property
    def context(self) -> _CompatibilityContext:
        return _CompatibilityContext(
            env=self.env,
            state=self.state,
            log=self.log,
        )
    
    @property
    def formatted_error(self) -> str:
        if self.is_err:
            return str(self.error)
        return ""
    
    @property
    def graph(self) -> Any:
        from doeff._vendor import WGraph, WNode, WStep
        return WGraph(last=WStep(inputs=(), output=WNode("_root"), meta={}), steps=frozenset())
    
    def display(self, verbose: bool = False) -> str:
        if self.is_ok:
            return f"Ok({self.value!r})"
        parts = [f"Err({self.error!r})"]
        if verbose:
            parts.append(f"\nState: {self.state}")
            parts.append(f"\nLog: {self.log}")
        return "".join(parts)


class CESKInterpreter:
    """
    ProgramInterpreter-compatible wrapper for the runtime-based interpreter.

    Provides the same interface as ProgramInterpreter for test compatibility.
    """

    def __init__(
        self,
        env: dict[Any, Any] | FrozenDict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ):
        self._initial_env = env if env is not None else {}
        self._initial_state = state if state is not None else {}

    async def run_async(
        self,
        program: "Program",
        env: dict[Any, Any] | ExecutionContext | None = None,
        state: dict[str, Any] | None = None,
    ) -> CESKRunResult[T]:
        from doeff.runtimes import AsyncioRuntime
        
        # Handle ExecutionContext for backward compatibility
        if isinstance(env, ExecutionContext):
            actual_env = env.env
            actual_state = env.state
        else:
            actual_env = env or {}
            actual_state = state or {}
        
        final_env = {**self._initial_env, **actual_env}
        final_state = {**self._initial_state, **actual_state}

        if isinstance(final_env, FrozenDict):
            E = final_env
        else:
            E = FrozenDict(final_env)

        runtime = AsyncioRuntime()
        runtime_result = await runtime.run_safe(program, E, final_state)

        return CESKRunResult(
            _result=runtime_result.result,
            _final_store=runtime_result.final_store or {},
            _final_env=runtime_result.final_env or E,
        )

    def run(
        self,
        program: "Program",
        env: dict[Any, Any] | ExecutionContext | None = None,
        state: dict[str, Any] | None = None,
    ) -> CESKRunResult[T]:
        return asyncio.run(self.run_async(program, env, state))


async def cesk_run(
    program: "Program",
    env: dict[Any, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> CESKRunResult[T]:
    """
    Run a program using CESK and return a RunResult-compatible result.

    This is equivalent to:
        engine = CESKInterpreter()
        return await engine.run_async(program)
    """
    interpreter = CESKInterpreter(env=env, state=state)
    return await interpreter.run_async(program)


def cesk_run_sync(
    program: "Program",
    env: dict[Any, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> CESKRunResult[T]:
    """Run a program synchronously using CESK."""
    return asyncio.run(cesk_run(program, env, state))


__all__ = [
    "CESKRunResult",
    "CESKInterpreter",
    "cesk_run",
    "cesk_run_sync",
]
