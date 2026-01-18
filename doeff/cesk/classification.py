"""Effect classification functions for the CESK machine."""

from __future__ import annotations

from doeff._types_internal import EffectBase
from doeff.cesk.frames import InterceptFrame, Kontinuation


def is_control_flow_effect(effect: EffectBase) -> bool:
    from doeff.effects import (
        GatherEffect,
        InterceptEffect,
        LocalEffect,
        ResultSafeEffect,
        WriterListenEffect,
    )
    from doeff.effects.graph import GraphCaptureEffect

    return isinstance(
        effect,
        (
            ResultSafeEffect,
            LocalEffect,
            InterceptEffect,
            WriterListenEffect,
            GatherEffect,
            GraphCaptureEffect,
        ),
    )


def is_pure_effect(effect: EffectBase) -> bool:
    from doeff.effects import (
        AskEffect,
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
            PureEffect,
            CacheGetEffect,
            CachePutEffect,
            CacheDeleteEffect,
            CacheExistsEffect,
        ),
    )


def is_effectful(effect: EffectBase) -> bool:
    from doeff.effects import (
        FutureAwaitEffect,
        IOPerformEffect,
        SpawnEffect,
        TaskJoinEffect,
    )

    return isinstance(
        effect,
        (
            IOPerformEffect,
            FutureAwaitEffect,
            SpawnEffect,
            TaskJoinEffect,
        ),
    )


def has_intercept_frame(K: Kontinuation) -> bool:
    return any(isinstance(f, InterceptFrame) for f in K)


def find_intercept_frame_index(K: Kontinuation) -> int:
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
