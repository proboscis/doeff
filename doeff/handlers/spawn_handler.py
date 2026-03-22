"""Spawn handlers."""


import traceback
from typing import Any

import doeff_vm

from doeff.do import do
from doeff.effects.base import Effect


def _is_internal_trace_frame(frame: traceback.FrameSummary) -> bool:
    normalized = frame.filename.replace("\\", "/").lower()
    return normalized == "_effect_wrap" or "/doeff/" in normalized


def _exception_site_entry(error: Exception) -> object:
    frames = traceback.extract_tb(error.__traceback__)
    user_frame = next((frame for frame in reversed(frames) if not _is_internal_trace_frame(frame)), None)
    frame = user_frame or (frames[-1] if frames else None)
    if frame is None:
        return {
            "kind": "exception_site",
            "function_name": "[MISSING] <unknown>",
            "source_file": "[MISSING] <unknown>",
            "source_line": 0,
            "exception_type": type(error).__name__,
            "message": str(error),
        }
    return {
        "kind": "exception_site",
        "function_name": frame.name,
        "source_file": frame.filename,
        "source_line": frame.lineno,
        "exception_type": type(error).__name__,
        "message": str(error),
    }


def _ensure_error_execution_context(error: Exception, context: object) -> None:
    active_chain = list(getattr(context, "active_chain", ()) or ())
    if not any(
        isinstance(entry, dict)
        and entry.get("kind") == "exception_site"
        and entry.get("exception_type") == type(error).__name__
        and entry.get("message") == str(error)
        for entry in active_chain
    ):
        active_chain.append(_exception_site_entry(error))
    context.set_active_chain(tuple(active_chain))
    error.doeff_execution_context = context


def _spawn_intercept(effect: Effect, k: Any, handoff: Any):
    from doeff.effects.spawn import SpawnEffect, coerce_task_handle

    @do
    def _program():
        if isinstance(effect, SpawnEffect):
            raw = yield doeff_vm.Delegate()
            return (yield handoff(k, coerce_task_handle(raw)))
        yield doeff_vm.Pass()

    return _program()


def wrap_spawned_program_for_scheduler(program: Any, task_id: int):
    from doeff.effects import GetExecutionContext, TaskCompleted

    @do
    def _program():
        try:
            value = yield program
        except Exception as error:
            context = yield GetExecutionContext()
            _ensure_error_execution_context(error, context)
            _ = yield TaskCompleted(task_id=task_id, result=doeff_vm.Err(error))
            return None
        _ = yield TaskCompleted(task_id=task_id, result=doeff_vm.Ok(value))
        return None

    return _program()


@do
def spawn_intercept_handler(effect: Effect, k: Any):
    return (yield _spawn_intercept(effect, k, doeff_vm.Transfer))


@do
def sync_spawn_intercept_handler(effect: Effect, k: Any):
    # Sync Spawn stays on the scheduler hot path. Transfer preserves the same
    # task-handle result as Resume while avoiding scheduler-induced stack growth.
    return (yield _spawn_intercept(effect, k, doeff_vm.Transfer))


__all__ = [
    "spawn_intercept_handler",
    "sync_spawn_intercept_handler",
    "wrap_spawned_program_for_scheduler",
]
