"""Production handlers for doeff-seedream domain effects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doeff import EffectGenerator, Resume, do

from doeff_seedream.effects import SeedreamGenerate, SeedreamStructuredOutput
from doeff_seedream.structured_llm import _edit_image__seedream4_impl
from doeff_seedream.types import SeedreamImageEditResult

ProtocolHandler = Callable[[Any, Any], Any]


@do
def _generate_impl(effect: SeedreamGenerate) -> EffectGenerator[SeedreamImageEditResult]:
    return (
        yield _edit_image__seedream4_impl(
            prompt=effect.prompt,
            model=effect.model,
            images=effect.images,
            generation_config_overrides=effect.generation_config_overrides,
            max_retries=effect.max_retries,
        )
    )


@do
def _structured_impl(effect: SeedreamStructuredOutput) -> EffectGenerator[Any]:
    raise NotImplementedError(
        "SeedreamStructuredOutput has no default production implementation. "
        "Pass structured_impl=... to production_handlers() for custom behavior."
    )


def production_handlers(
    *,
    generate_impl: Callable[[SeedreamGenerate], EffectGenerator[SeedreamImageEditResult]] | None = None,
    structured_impl: Callable[[SeedreamStructuredOutput], EffectGenerator[Any]] | None = None,
) -> dict[type[Any], ProtocolHandler]:
    """Build effect handlers backed by Seedream production logic."""

    active_generate_impl = generate_impl or _generate_impl
    active_structured_impl = structured_impl or _structured_impl

    def handle_generate(effect: SeedreamGenerate, k):
        value = yield active_generate_impl(effect)
        return (yield Resume(k, value))

    def handle_structured(effect: SeedreamStructuredOutput, k):
        value = yield active_structured_impl(effect)
        return (yield Resume(k, value))

    return {
        SeedreamGenerate: handle_generate,
        SeedreamStructuredOutput: handle_structured,
    }


__all__ = [
    "ProtocolHandler",
    "production_handlers",
]
