"""Reproduce: WithHandler type filter bypassed in nested handler chains.

The WithHandler annotation-driven type filter (SPEC-WITHHANDLER-TYPE-FILTER)
works correctly in simple scenarios. But in mediagen's handler stack (built
inside a @do generator via _wrap_with_handler_bindings), the VM dispatches
CacheGetEffect to replace_audio_handler despite its annotation restricting
it to ReplaceAudioTrack only.

This test uses the actual mediagen stack to reproduce the bug. The topology:

    run(handlers=[default_handlers()])
      wrap_with_mediagen_stack()   →  builds ~28 WithHandler layers via Ask
        CacheHandler (outermost)
          ShellHandler
            ...domain handlers...
              ReplaceAudioHandler     ← typed: ReplaceAudioTrack only
                ...
                  TranscribeHandler
                    MemoHandlers (×9)  ← types=None, yield CacheGetEffect
                      WithHandler(transcribe_via_gemini_llm_handler, ...)
                        program()     ← yields Transcribe

    MemoFFmpegExtractAudioSegmentHandler intercepts FFmpegExtractAudioSegment
    and yields CacheGetEffect. CacheGetEffect should skip ReplaceAudioHandler
    (types=(ReplaceAudioTrack,)) but the VM dispatches it there anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import doeff_vm
from doeff import (
    Effect,
    EffectBase,
    WithHandler,
    default_handlers,
    do,
    run,
)


# ---------------------------------------------------------------------------
# Direct nesting baseline
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CacheGetFx(EffectBase):
    key: str


@dataclass(frozen=True)
class SegmentFx(EffectBase):
    src: str


@dataclass(frozen=True)
class ReplaceFx(EffectBase):
    video: str


@dataclass(frozen=True)
class TranscribeFx(EffectBase):
    asset: str


@do
def memo_handler(effect: Effect, k: Any):
    if not isinstance(effect, SegmentFx):
        yield doeff_vm.Pass()
        return
    cached = yield CacheGetFx(key="test_key")
    if cached is not None:
        return (yield doeff_vm.Resume(k, cached))
    result = yield doeff_vm.Delegate()
    return (yield doeff_vm.Resume(k, result))


@do
def replace_handler(effect: ReplaceFx, k: Any):
    assert isinstance(effect, ReplaceFx), (
        f"VM type filter bug: replace_handler received {type(effect).__name__} "
        f"but is annotated for ReplaceFx only"
    )
    return (yield doeff_vm.Resume(k, f"replaced:{effect.video}"))


@do
def cache_handler(effect: CacheGetFx, k: Any):
    return (yield doeff_vm.Resume(k, "cached_value"))


@do
def segment_handler(effect: SegmentFx, k: Any):
    return (yield doeff_vm.Resume(k, f"segment:{effect.src}"))


@do
def inner_handler(effect: TranscribeFx, k: Any):
    seg = yield SegmentFx(src="audio.wav")
    return (yield doeff_vm.Resume(k, f"transcribed:{seg}"))


@do
def _program():
    return (yield TranscribeFx(asset="test"))


def test_type_filter_direct_chain_with_inner_withhandler():
    """Baseline: type filter works with direct nesting + inner WithHandler."""
    handlers = [cache_handler, segment_handler, replace_handler, memo_handler]
    wrapped = WithHandler(inner_handler, _program())
    for h in reversed(handlers):
        wrapped = WithHandler(h, wrapped)

    result = run(wrapped, handlers=[*default_handlers()])
    assert result.is_ok(), f"Expected ok, got: {result.error}"
    assert result.value == "transcribed:cached_value"


# ---------------------------------------------------------------------------
# Mediagen integration test (requires mediagen to be installed)
# ---------------------------------------------------------------------------

def test_type_filter_mediagen_stack():
    """Regression: CacheGetEffect must not reach replace_audio_handler.

    Uses the actual mediagen stack to reproduce the bug where the VM's
    WithHandler type filter fails to skip typed handlers for cross-effect
    yields from memo rewriters.
    """
    try:
        import mediagen
        import mediagen.stack.compose as compose
    except ImportError:
        import pytest
        pytest.skip("mediagen not installed")

    from mediagen.domains.audio.effects import ReplaceAudioTrack

    # Patch replace_audio_handler to detect the bug instead of crashing
    seen_wrong_types: list[str] = []
    original_replace = mediagen.mediagen_env[compose.ReplaceAudioHandler]

    @do
    def guarded_replace(effect: Effect, k: Any):
        if not isinstance(effect, ReplaceAudioTrack):
            seen_wrong_types.append(type(effect).__name__)
            yield doeff_vm.Pass()
            return
        # Delegate to original
        result = yield doeff_vm.Delegate()
        return (yield doeff_vm.Resume(k, result))

    mediagen.mediagen_env[compose.ReplaceAudioHandler] = guarded_replace

    try:
        from mediagen.programs.transcribe import p_transcribe_with_openai
        result = mediagen.mediagen_interpreter(p_transcribe_with_openai)

        assert not seen_wrong_types, (
            f"VM type filter bug: replace_audio_handler received effects "
            f"it should not handle: {seen_wrong_types}"
        )
    finally:
        mediagen.mediagen_env[compose.ReplaceAudioHandler] = original_replace
