"""Helper functions for the CESK machine."""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any

from doeff.cesk.types import Store
from doeff.cesk.frames import InterceptFrame, Kontinuation

if TYPE_CHECKING:
    from doeff.program import ProgramBase
    from doeff.types import Effect
    from doeff.effects._program_types import ProgramLike


def apply_transforms(
    transforms: tuple[Callable[[Effect], Effect | ProgramBase | None], ...],
    effect: Effect,
) -> Effect | ProgramBase:
    for transform in transforms:
        result = transform(effect)
        if result is not None:
            return result
    return effect


def apply_intercept_chain(K: Kontinuation, effect: Effect) -> Effect | ProgramBase:
    """Apply intercept transforms from continuation frames to an effect.
    
    Once any transform returns a ProgramBase (not an Effect), we stop applying
    further transforms and return immediately - you're no longer transforming
    an effect, you're resuming execution with a new program.
    """
    from doeff._types_internal import EffectBase
    
    current: Effect | ProgramBase = effect
    for frame in K:
        if isinstance(frame, InterceptFrame):
            for transform in frame.transforms:
                result = transform(current)  # type: ignore[arg-type]
                if result is not None:
                    current = result
                    # If result is a ProgramBase but NOT an Effect, stop processing
                    # intercept frames entirely - we're resuming with a program
                    if not isinstance(result, EffectBase):
                        return current
                    break
    return current


def merge_store(parent_store: Store, child_store: Store, child_snapshot: Store | None = None) -> Store:
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


def to_generator(program: ProgramLike) -> Generator[Any, Any, Any]:
    from doeff.program import KleisliProgramCall, ProgramBase

    if isinstance(program, KleisliProgramCall):
        return program.to_generator()

    if isinstance(program, ProgramBase):
        to_gen = getattr(program, "to_generator", None)
        if callable(to_gen):
            return to_gen()

    raise TypeError(f"Cannot convert {type(program).__name__} to generator")


def shutdown_shared_executor(wait: bool = True) -> None:
    pass


__all__ = [
    "apply_transforms",
    "apply_intercept_chain",
    "merge_store",
    "_merge_thread_state",
    "to_generator",
    "shutdown_shared_executor",
]
