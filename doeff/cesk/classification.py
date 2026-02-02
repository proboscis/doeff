"""Effect classification functions for the CESK machine.

Per SPEC-CESK-003: InterceptFrame has been removed. The intercept-related
functions now always return False/-1 for backwards compatibility.
"""

from __future__ import annotations

from doeff._types_internal import EffectBase
from doeff.cesk.frames import Kontinuation


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
        WaitEffect,
    )

    return isinstance(
        effect,
        (
            IOPerformEffect,
            FutureAwaitEffect,
            SpawnEffect,
            WaitEffect,
        ),
    )


def has_intercept_frame(K: Kontinuation) -> bool:
    """Check if K contains an InterceptFrame.

    DEPRECATED: InterceptFrame has been removed per SPEC-CESK-003.
    Always returns False for backwards compatibility.
    """
    return False


def find_intercept_frame_index(K: Kontinuation) -> int:
    """Find the index of InterceptFrame in K.

    DEPRECATED: InterceptFrame has been removed per SPEC-CESK-003.
    Always returns -1 for backwards compatibility.
    """
    return -1


__all__ = [
    "find_intercept_frame_index",
    "has_intercept_frame",
    "is_control_flow_effect",
    "is_effectful",
    "is_pure_effect",
]
