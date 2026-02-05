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
from doeff.cesk_v3.level3_core_effects.asyncio_bridge import (
    python_async_syntax_escape_handler,
    sync_await_handler,
)
from doeff.cesk_v3.level3_core_effects.reader import reader_handler
from doeff.cesk_v3.level3_core_effects.state import state_handler
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
    """Wrap program with handlers. handlers[0] = innermost, handlers[N-1] = outermost."""
    result: Program[T] | WithHandler[T] = program
    for handler in handlers:
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
        try:
            result = level2_step(state)
        except Exception as e:
            return RunResult(error=e, final_store=state.S)

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
        try:
            result = level2_step(state)
        except Exception as e:
            return RunResult(error=e, final_store=state.S)

        if isinstance(result, Done):
            return RunResult(value=result.value, final_store=state.S)

        if isinstance(result, Failed):
            return RunResult(error=result.error, final_store=state.S)

        if isinstance(result, PythonAsyncSyntaxEscape):
            try:
                state = await result.action()
            except BaseException as e:
                return RunResult(error=e, final_store=state.S)
            await asyncio.sleep(0)
            continue

        state = result
        await asyncio.sleep(0)


def _format_control(C: Any) -> str:
    from doeff.cesk_v3.level1_cesk.state import EffectYield, Error, Value

    if isinstance(C, ProgramControl):
        prog = C.program
        prog_type = type(prog).__name__
        return f"ProgramControl({prog_type})"
    if isinstance(C, Value):
        return f"Value({C.value!r})"
    if isinstance(C, EffectYield):
        return f"EffectYield({type(C.yielded).__name__})"
    if isinstance(C, Error):
        return f"Error({type(C.error).__name__}: {C.error})"
    return f"{type(C).__name__}"


def _format_k(K: list[Any]) -> str:
    from doeff.cesk_v3.level1_cesk.frames import ReturnFrame
    from doeff.cesk_v3.level2_algebraic_effects.frames import (
        DispatchingFrame,
        WithHandlerFrame,
    )

    parts = []
    for f in K:
        if isinstance(f, ReturnFrame):
            parts.append(f"RF#{f.frame_id}")
        elif isinstance(f, WithHandlerFrame):
            h_name = getattr(f.handler, "__name__", type(f.handler).__name__)
            parts.append(f"WHF#{f.frame_id}({h_name})")
        elif isinstance(f, DispatchingFrame):
            started = "+" if f.handler_started else "-"
            parts.append(f"DF#{f.frame_id}(idx={f.handler_idx},{started})")
        else:
            parts.append(type(f).__name__)
    return "[" + ", ".join(parts) + "]"


def debug_sync_run(
    program: Program[T],
    handlers: list[Handler] | None = None,
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
    max_steps: int = 1000,
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

    print(f"[DEBUG] Initial: C={_format_control(state.C)}, K={_format_k(state.K)}")

    step = 0
    while step < max_steps:
        step += 1
        result = level2_step(state)

        if isinstance(result, Done):
            print(f"[DEBUG] Step {step}: Done({result.value!r})")
            return RunResult(value=result.value, final_store=state.S)

        if isinstance(result, Failed):
            print(f"[DEBUG] Step {step}: Failed({result.error})")
            return RunResult(error=result.error, final_store=state.S)

        if isinstance(result, PythonAsyncSyntaxEscape):
            print(f"[DEBUG] Step {step}: PythonAsyncSyntaxEscape")
            raise TypeError(
                "sync_run received PythonAsyncSyntaxEscape. "
                "Use async_run or debug_async_run."
            )

        state = result
        print(f"[DEBUG] Step {step}: C={_format_control(state.C)}, K={_format_k(state.K)}")

    raise RuntimeError(f"debug_sync_run exceeded max_steps ({max_steps})")


async def debug_async_run(
    program: Program[T],
    handlers: list[Handler] | None = None,
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
    max_steps: int = 1000,
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

    print(f"[DEBUG] Initial: C={_format_control(state.C)}, K={_format_k(state.K)}")

    step = 0
    while step < max_steps:
        step += 1
        result = level2_step(state)

        if isinstance(result, Done):
            print(f"[DEBUG] Step {step}: Done({result.value!r})")
            return RunResult(value=result.value, final_store=state.S)

        if isinstance(result, Failed):
            print(f"[DEBUG] Step {step}: Failed({result.error})")
            return RunResult(error=result.error, final_store=state.S)

        if isinstance(result, PythonAsyncSyntaxEscape):
            print(f"[DEBUG] Step {step}: PythonAsyncSyntaxEscape - awaiting...")
            try:
                state = await result.action()
            except BaseException as e:
                print(f"[DEBUG] Step {step}: ...error during await: {e}")
                return RunResult(error=e, final_store=state.S)
            await asyncio.sleep(0)
            print(f"[DEBUG] Step {step}: ...resumed, C={_format_control(state.C)}")
            continue

        state = result
        print(f"[DEBUG] Step {step}: C={_format_control(state.C)}, K={_format_k(state.K)}")
        await asyncio.sleep(0)

    raise RuntimeError(f"debug_async_run exceeded max_steps ({max_steps})")


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


def sync_handlers_preset(
    initial_state: dict[str, Any] | None = None,
    env: dict[str, Any] | None = None,
) -> list[Handler]:
    """Create a list of handlers for sync_run.

    Provides:
    - State effects (Get, Put, Modify)
    - Reader effects (Ask)
    - Await effects via thread pool (blocks until complete)

    Args:
        initial_state: Initial state for state handler.
        env: Environment dict for reader handler.

    Returns:
        List of handlers suitable for sync_run.

    Example:
        from doeff.cesk_v3.run import sync_run, sync_handlers_preset
        from doeff.cesk_v3.level3_core_effects import Get, Put, Ask

        @do
        def program():
            yield Put("counter", 0)
            db_url = yield Ask("db_url")
            return (yield Get("counter"))

        result = sync_run(
            program(),
            handlers=sync_handlers_preset(env={"db_url": "localhost:5432"}),
        )
    """
    return [
        sync_await_handler,
        state_handler(initial_state),
        reader_handler(env),
    ]


def async_handlers_preset(
    initial_state: dict[str, Any] | None = None,
    env: dict[str, Any] | None = None,
) -> list[Handler]:
    """Create a list of handlers for async_run.

    Provides:
    - State effects (Get, Put, Modify)
    - Reader effects (Ask)
    - Await effects via Python async escape (non-blocking)

    Args:
        initial_state: Initial state for state handler.
        env: Environment dict for reader handler.

    Returns:
        List of handlers suitable for async_run.

    Example:
        import asyncio
        from doeff.cesk_v3.run import async_run, async_handlers_preset
        from doeff.cesk_v3.level3_core_effects import Get, Put
        from doeff.cesk_v3.level3_core_effects.asyncio_bridge import Await

        async def fetch_data():
            return "data"

        @do
        def program():
            data = yield Await(fetch_data())
            yield Put("result", data)
            return (yield Get("result"))

        result = await async_run(
            program(),
            handlers=async_handlers_preset(),
        )
    """
    return [
        python_async_syntax_escape_handler,
        state_handler(initial_state),
        reader_handler(env),
    ]
