from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, cast

from doeff.cesk_v3.level1_cesk.state import CESKState, Done, Failed, ProgramControl
from doeff.cesk_v3.level2_algebraic_effects.frames import Handler
from doeff.cesk_v3.level2_algebraic_effects.primitives import (
    PythonAsyncSyntaxEscape,
    WithHandler,
)
from doeff.cesk_v3.level2_algebraic_effects.step import level2_step
from doeff.program import Program

T = TypeVar("T")


@dataclass
class RunResult(Generic[T]):
    value: T | None = None
    error: BaseException | None = None
    final_store: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ok(self) -> bool:
        return self.error is None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    def unwrap(self) -> T:
        if self.error is not None:
            raise self.error
        return cast(T, self.value)


def _wrap_with_handlers(
    program: Program[T], handlers: list[Handler]
) -> Program[T] | WithHandler[T]:
    result: Program[T] | WithHandler[T] = program
    for handler in reversed(handlers):
        result = WithHandler(handler=handler, program=result)
    return result


def sync_run(
    program: Program[T],
    handlers: list[Handler] | None = None,
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> RunResult[T]:
    if handlers:
        wrapped = _wrap_with_handlers(program, handlers)
    else:
        wrapped = program

    state = CESKState(
        C=ProgramControl(wrapped),
        E=env if env else {},
        S=store if store else {},
        K=[],
    )

    while True:
        result = level2_step(state)

        if isinstance(result, Done):
            return RunResult(value=result.value, final_store=state.S)

        if isinstance(result, Failed):
            return RunResult(error=result.error, final_store=state.S)

        if isinstance(result, PythonAsyncSyntaxEscape):
            raise TypeError(
                "sync_run received PythonAsyncSyntaxEscape. "
                "Use async_run or replace python_async_syntax_escape_handler "
                "with sync_await_handler."
            )

        state = result


async def async_run(
    program: Program[T],
    handlers: list[Handler] | None = None,
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> RunResult[T]:
    if handlers:
        wrapped = _wrap_with_handlers(program, handlers)
    else:
        wrapped = program

    state = CESKState(
        C=ProgramControl(wrapped),
        E=env if env else {},
        S=store if store else {},
        K=[],
    )

    while True:
        result = level2_step(state)

        if isinstance(result, Done):
            return RunResult(value=result.value, final_store=state.S)

        if isinstance(result, Failed):
            return RunResult(error=result.error, final_store=state.S)

        if isinstance(result, PythonAsyncSyntaxEscape):
            state = await result.action()
            await asyncio.sleep(0)
            continue

        state = result
        await asyncio.sleep(0)


def run(program: Any) -> Any:
    if isinstance(program, WithHandler):
        from doeff.cesk_v3.level2_algebraic_effects.handlers import handle_with_handler

        state = handle_with_handler(
            program,
            CESKState(C=ProgramControl(None), E={}, S={}, K=[]),
        )
    else:
        state = CESKState(
            C=ProgramControl(program),
            E={},
            S={},
            K=[],
        )

    while True:
        result = level2_step(state)

        if isinstance(result, Done):
            return result.value
        if isinstance(result, Failed):
            raise result.error
        if isinstance(result, PythonAsyncSyntaxEscape):
            raise TypeError("run() received PythonAsyncSyntaxEscape. Use async_run().")

        state = result
