"""Effect classification functions for the CESK machine."""

from __future__ import annotations

from doeff._types_internal import EffectBase
from doeff.cesk.frames import InterceptFrame, Kontinuation


def is_control_flow_effect(effect: EffectBase) -> bool:
    """Check if effect is a control flow effect that pushes frames."""
    from doeff.effects import (
        GatherEffect,
        InterceptEffect,
        LocalEffect,
        ResultFinallyEffect,
        ResultSafeEffect,
        WriterListenEffect,
    )

    return isinstance(
        effect,
        (
            ResultFinallyEffect,
            ResultSafeEffect,
            LocalEffect,
            InterceptEffect,
            WriterListenEffect,
            GatherEffect,
        ),
    )


def is_pure_effect(effect: EffectBase) -> bool:
    """Check if effect can be handled synchronously without I/O."""
    from doeff.effects import (
        AskEffect,
        MemoGetEffect,
        MemoPutEffect,
        StateGetEffect,
        StateModifyEffect,
        StatePutEffect,
        WriterTellEffect,
    )
    from doeff.effects.cache import (
        CacheDeleteEffect,
        CacheExistsEffect,
        CacheGetEffect,
        CachePutEffect,
    )
    from doeff.effects.pure import PureEffect

    return isinstance(
        effect,
        (
            StateGetEffect,
            StatePutEffect,
            StateModifyEffect,
            AskEffect,
            WriterTellEffect,
            MemoGetEffect,
            MemoPutEffect,
            PureEffect,
            CacheGetEffect,
            CachePutEffect,
            CacheDeleteEffect,
            CacheExistsEffect,
        ),
    )


def is_effectful(effect: EffectBase) -> bool:
    """Check if effect may perform I/O (async boundary)."""
    from doeff.effects import (
        FutureAwaitEffect,
        IOPerformEffect,
        IOPrintEffect,
        SpawnEffect,
        TaskJoinEffect,
        ThreadEffect,
    )

    return isinstance(
        effect,
        (
            IOPerformEffect,
            IOPrintEffect,
            FutureAwaitEffect,
            ThreadEffect,
            SpawnEffect,
            TaskJoinEffect,
        ),
    )


def has_intercept_frame(K: Kontinuation) -> bool:
    """Check if continuation stack contains an InterceptFrame."""
    return any(isinstance(f, InterceptFrame) for f in K)


def find_intercept_frame_index(K: Kontinuation) -> int:
    """Find index of first InterceptFrame in continuation stack."""
    for i, f in enumerate(K):
        if isinstance(f, InterceptFrame):
            return i
    raise ValueError("No InterceptFrame found")


__all__ = [
    "is_control_flow_effect",
    "is_pure_effect",
    "is_effectful",
    "has_intercept_frame",
    "find_intercept_frame_index",
]
