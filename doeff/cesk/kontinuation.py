"""Kontinuation manipulation helpers for unified CESK machine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.types import Environment, Store
from doeff.cesk.frames import (
    ContinueWithError,
    ContinueWithValue,
    Frame,
    FrameResult,
    Kontinuation,
    PopFrame,
    PushProgram,
    ReturnFrame,
)

if TYPE_CHECKING:
    from doeff.cesk.state import TaskState
    from doeff.cesk_traceback import CapturedTraceback


def resume_kontinuation(
    value: Any,
    env: Environment,
    store: Store,
    k: Kontinuation,
) -> tuple[Any, Environment, Store, Kontinuation, CapturedTraceback | None]:
    current_value = value
    current_env = env
    captured_traceback: CapturedTraceback | None = None

    while k:
        frame = k[0]
        k_rest = k[1:]

        if isinstance(frame, ReturnFrame):
            from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator

            pre_captured = pre_capture_generator(
                frame.generator, is_resumed=True, program_call=frame.program_call
            )
            try:
                item = frame.generator.send(current_value)
                return item, frame.saved_env, store, k, None
            except StopIteration as e:
                current_value = e.value
                current_env = frame.saved_env
                k = k_rest
                continue
            except Exception as ex:
                captured_traceback = capture_traceback_safe(k_rest, ex, pre_captured=pre_captured)
                return _throw_kontinuation_inner(ex, frame.saved_env, store, k_rest, captured_traceback)

        result = frame.on_value(current_value, current_env)
        match result:
            case ContinueWithValue(v, e):
                current_value = v
                current_env = e
                k = k_rest
            case ContinueWithError(ex, e):
                return _throw_kontinuation_inner(ex, e, store, k_rest, None)
            case PushProgram(prog, e, new_frame):
                if new_frame:
                    return prog, e, store, [new_frame] + k_rest, None
                return prog, e, store, k_rest, None
            case PopFrame(v, e):
                current_value = v
                current_env = e
                k = k_rest
            case _:
                raise RuntimeError(f"Unknown frame result: {result}")

    return current_value, current_env, store, k, captured_traceback


def throw_kontinuation(
    ex: BaseException,
    env: Environment,
    store: Store,
    k: Kontinuation,
    captured_traceback: CapturedTraceback | None = None,
) -> tuple[Any, Environment, Store, Kontinuation, CapturedTraceback | None, bool]:
    result = _throw_kontinuation_inner(ex, env, store, k, captured_traceback)
    return (*result, True)


def _throw_kontinuation_inner(
    ex: BaseException,
    env: Environment,
    store: Store,
    k: Kontinuation,
    captured_traceback: CapturedTraceback | None,
) -> tuple[Any, Environment, Store, Kontinuation, CapturedTraceback | None]:
    current_ex = ex
    current_env = env

    while k:
        frame = k[0]
        k_rest = k[1:]

        if isinstance(frame, ReturnFrame):
            from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator

            pre_captured = pre_capture_generator(
                frame.generator, is_resumed=True, program_call=frame.program_call
            )
            try:
                item = frame.generator.throw(current_ex)
                return item, frame.saved_env, store, k, None
            except StopIteration as e:
                return e.value, frame.saved_env, store, k_rest, None
            except Exception as propagated:
                if propagated is current_ex:
                    current_env = frame.saved_env
                    k = k_rest
                    continue
                new_captured = capture_traceback_safe(k_rest, propagated, pre_captured=pre_captured)
                return _throw_kontinuation_inner(propagated, frame.saved_env, store, k_rest, new_captured)

        result = frame.on_error(current_ex, current_env)
        match result:
            case ContinueWithValue(v, e):
                return v, e, store, k_rest, None
            case ContinueWithError(new_ex, e):
                current_ex = new_ex
                current_env = e
                k = k_rest
            case PushProgram(prog, e, new_frame):
                if new_frame:
                    return prog, e, store, [new_frame] + k_rest, None
                return prog, e, store, k_rest, None
            case PopFrame(v, e):
                return v, e, store, k_rest, None
            case _:
                raise RuntimeError(f"Unknown frame result: {result}")

    return current_ex, current_env, store, k, captured_traceback


def push_frame(k: Kontinuation, frame: Frame) -> Kontinuation:
    return [frame] + k


def pop_frame(k: Kontinuation) -> tuple[Frame | None, Kontinuation]:
    if not k:
        return None, k
    return k[0], k[1:]


__all__ = [
    "resume_kontinuation",
    "throw_kontinuation",
    "push_frame",
    "pop_frame",
]
