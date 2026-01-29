"""Helper functions for the CESK machine."""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any

from doeff.cesk.frames import InterceptBypassFrame, InterceptFrame, Kontinuation
from doeff.cesk.types import Store

if TYPE_CHECKING:
    from doeff.effects._program_types import ProgramLike
    from doeff.program import ProgramBase
    from doeff.types import Effect


def apply_transforms(
    transforms: tuple[Callable[[Effect], Effect | ProgramBase | None], ...],
    effect: Effect,
) -> Effect | ProgramBase:
    for transform in transforms:
        result = transform(effect)
        if result is not None:
            return result
    return effect


def apply_intercept_chain(
    K: Kontinuation, effect: Effect
) -> tuple[Effect | ProgramBase, InterceptFrame | None]:
    """Apply intercept transforms from continuation frames to an effect.
    
    Returns (result, returning_frame) where returning_frame is the InterceptFrame
    that returned a Program, or None if result is an Effect.
    """
    from doeff._types_internal import EffectBase

    bypass_map: dict[int, int] = {}
    for frame in K:
        if isinstance(frame, InterceptBypassFrame):
            bypass_map[id(frame.bypassed_frame)] = frame.bypassed_effect_id

    effect_id = id(effect)
    current: Effect | ProgramBase = effect
    for frame in K:
        if isinstance(frame, InterceptFrame):
            if id(frame) in bypass_map and effect_id == bypass_map[id(frame)]:
                continue
            for transform in frame.transforms:
                result = transform(current)  # type: ignore[arg-type]
                if result is not None:
                    current = result
                    if not isinstance(result, EffectBase):
                        return current, frame
                    break
    return current, None


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
    "_merge_thread_state",
    "apply_intercept_chain",
    "apply_transforms",
    "merge_store",
    "shutdown_shared_executor",
    "to_generator",
]
