"""Helper functions for the CESK machine."""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any

from doeff.cesk.types import Store
from doeff.cesk.frames import InterceptFrame, Kontinuation

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff.types import Effect


def apply_transforms(
    transforms: tuple[Callable[[Effect], Effect | Program | None], ...],
    effect: Effect,
) -> Effect | Program:
    """Apply transform functions in order. First non-None result wins."""
    for transform in transforms:
        result = transform(effect)
        if result is not None:
            return result
    return effect


def apply_intercept_chain(K: Kontinuation, effect: Effect) -> Effect | Program:
    """Apply intercept transforms from ALL InterceptFrames in the continuation stack."""
    current = effect
    for frame in K:
        if isinstance(frame, InterceptFrame):
            for transform in frame.transforms:
                result = transform(current)
                if result is not None:
                    current = result
                    break
    return current


def merge_store(parent_store: Store, child_store: Store, child_snapshot: Store | None = None) -> Store:
    """Merge child store into parent after child completion."""
    merged = {**parent_store}

    for key, value in child_store.items():
        if key.startswith("__"):
            continue
        if key not in parent_store:
            merged[key] = value

    parent_log = merged.get("__log__", [])
    child_log = child_store.get("__log__", [])
    merged["__log__"] = parent_log + child_log

    parent_memo = merged.get("__memo__", {})
    child_memo = child_store.get("__memo__", {})
    merged["__memo__"] = {**parent_memo, **child_memo}

    return merged


def _merge_thread_state(parent_store: Store, child_store: Store) -> Store:
    """Merge thread state: child state replaces parent (except logs append)."""
    merged = {}

    for key, value in child_store.items():
        if not key.startswith("__"):
            merged[key] = value
    for key, value in parent_store.items():
        if not key.startswith("__") and key not in merged:
            merged[key] = value

    parent_log = parent_store.get("__log__", [])
    child_log = child_store.get("__log__", [])
    if child_log:
        merged["__log__"] = list(parent_log) + list(child_log)
    elif parent_log:
        merged["__log__"] = list(parent_log)

    parent_memo = parent_store.get("__memo__", {})
    child_memo = child_store.get("__memo__", {})
    if parent_memo or child_memo:
        merged["__memo__"] = {**parent_memo, **child_memo}

    if "__cache_storage__" in parent_store:
        merged["__cache_storage__"] = parent_store["__cache_storage__"]

    return merged


def _wrap_callable_as_program(func: Callable[[], Any]) -> Program:
    """Wrap a callable (thunk) in a program that calls it."""
    from doeff.do import do

    @do
    def call_thunk():
        result = func()
        from doeff.program import ProgramBase
        from doeff.types import EffectBase

        if isinstance(result, (ProgramBase, EffectBase)):
            return (yield result)
        return result

    return call_thunk()


def make_cleanup_then_return(cleanup: Program, value: Any) -> Program:
    """Create program that runs cleanup then returns value."""
    from doeff.do import do

    @do
    def cleanup_then_return_impl():
        yield cleanup
        return value

    return cleanup_then_return_impl()


def make_cleanup_then_raise(cleanup: Program, ex: BaseException) -> Program:
    """Create program that runs cleanup then re-raises exception."""
    from doeff.do import do

    @do
    def cleanup_then_raise_impl():
        yield cleanup
        raise ex.with_traceback(ex.__traceback__)

    return cleanup_then_raise_impl()


def to_generator(program: Program) -> Generator[Any, Any, Any]:
    """Convert a program to a generator."""
    from doeff.program import KleisliProgramCall, ProgramBase

    if isinstance(program, KleisliProgramCall):
        return program.to_generator()

    if isinstance(program, ProgramBase):
        to_gen = getattr(program, "to_generator", None)
        if callable(to_gen):
            return to_gen()

    raise TypeError(f"Cannot convert {type(program).__name__} to generator")


def shutdown_shared_executor(wait: bool = True) -> None:
    """Shutdown the shared executor. Call this on application exit."""
    import doeff.scheduled_handlers.concurrency as concurrency_module
    if concurrency_module._shared_executor is not None:
        with concurrency_module._shared_executor_lock:
            if concurrency_module._shared_executor is not None:
                concurrency_module._shared_executor.shutdown(wait=wait)
                concurrency_module._shared_executor = None


__all__ = [
    "apply_transforms",
    "apply_intercept_chain",
    "merge_store",
    "_merge_thread_state",
    "_wrap_callable_as_program",
    "make_cleanup_then_return",
    "make_cleanup_then_raise",
    "to_generator",
    "shutdown_shared_executor",
]
