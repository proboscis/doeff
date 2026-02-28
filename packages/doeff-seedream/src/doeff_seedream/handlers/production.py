"""Production handlers for doeff-seedream domain effects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doeff_image.effects import ImageEdit, ImageGenerate
from doeff_image.types import ImageResult

from doeff import Delegate, EffectGenerator, Resume, do
from doeff.effects.base import Effect
from doeff_seedream.effects import SeedreamGenerate, SeedreamStructuredOutput
from doeff_seedream.structured_llm import _edit_image__seedream4_impl
from doeff_seedream.types import SeedreamImageEditResult

ProtocolHandler = Callable[[Effect, Any], Any]

SEEDREAM_IMAGE_MODEL_PREFIXES = ("seedream-", "doubao-seedream-")


def _is_seedream_model(model: str) -> bool:
    return any(model.startswith(prefix) for prefix in SEEDREAM_IMAGE_MODEL_PREFIXES)


def _size_to_seedream_value(size: tuple[int, int] | None) -> str | None:
    if size is None:
        return None
    return f"{size[0]}x{size[1]}"


def _prompt_with_style(effect: ImageGenerate) -> str:
    prompt = effect.prompt
    if effect.style:
        prompt = f"{prompt}\nStyle: {effect.style}"
    if effect.negative_prompt:
        prompt = f"{prompt}\nNegative prompt: {effect.negative_prompt}"
    return prompt


def _build_generate_overrides(effect: ImageGenerate) -> dict[str, Any] | None:
    overrides = dict(effect.generation_config or {})
    requested_size = _size_to_seedream_value(effect.size)
    if requested_size:
        overrides.setdefault("size", requested_size)
    if effect.num_images > 1:
        overrides.setdefault("sequential_image_generation", True)
        overrides.setdefault("sequential_image_generation_options", {"count": effect.num_images})
    return overrides or None


def _build_edit_overrides(effect: ImageEdit) -> dict[str, Any] | None:
    overrides = dict(effect.generation_config or {})
    if effect.strength != 0.8:
        overrides.setdefault("guidance_scale", effect.strength)
    return overrides or None


def _seedream_result_to_unified(result: SeedreamImageEditResult) -> ImageResult:
    pil_images = [image.to_pil_image() for image in result.images if image.image_bytes is not None]
    if not pil_images:
        raise ValueError(
            "Seedream response did not include inline image bytes. "
            "Set generation_config['response_format'] = 'b64_json' for unified ImageResult."
        )
    return ImageResult(
        images=pil_images,
        model=result.model,
        prompt=result.prompt,
        raw_response=result.raw_response,
    )


@do
def _generate_impl(effect: SeedreamGenerate) -> EffectGenerator[SeedreamImageEditResult]:
    return (
        yield _edit_image__seedream4_impl(
            prompt=effect.prompt,
            model=effect.model,
            images=effect.images or None,
            generation_config_overrides=effect.generation_config_overrides
            or effect.generation_config,
            max_retries=effect.max_retries,
        )
    )


@do
def _image_generate_impl(effect: ImageGenerate) -> EffectGenerator[ImageResult]:
    seedream_result = yield _edit_image__seedream4_impl(
        prompt=_prompt_with_style(effect),
        model=effect.model,
        images=None,
        generation_config_overrides=_build_generate_overrides(effect),
        max_retries=3,
    )
    return _seedream_result_to_unified(seedream_result)


@do
def _image_edit_impl(effect: ImageEdit) -> EffectGenerator[ImageResult]:
    seedream_result = yield _edit_image__seedream4_impl(
        prompt=effect.prompt,
        model=effect.model,
        images=effect.images or None,
        generation_config_overrides=_build_edit_overrides(effect),
        max_retries=3,
    )
    return _seedream_result_to_unified(seedream_result)


@do
def _structured_impl(effect: SeedreamStructuredOutput) -> EffectGenerator[Any]:
    raise NotImplementedError(
        "SeedreamStructuredOutput has no default production implementation. "
        "Pass structured_impl=... to production_handlers() for custom behavior."
    )


@do
def seedream_image_handler(effect: Effect, k: Any):  # noqa: PLR0911
    """Protocol handler with model routing for unified image effects."""
    if isinstance(effect, SeedreamGenerate):
        if not _is_seedream_model(effect.model):
            yield Delegate()
            return
        value = yield _generate_impl(effect)
        return (yield Resume(k, value))

    if isinstance(effect, ImageGenerate):
        if not _is_seedream_model(effect.model):
            yield Delegate()
            return
        value = yield _image_generate_impl(effect)
        return (yield Resume(k, value))

    if isinstance(effect, ImageEdit):
        if not _is_seedream_model(effect.model):
            yield Delegate()
            return
        value = yield _image_edit_impl(effect)
        return (yield Resume(k, value))

    if isinstance(effect, SeedreamStructuredOutput):
        value = yield _structured_impl(effect)
        return (yield Resume(k, value))

    yield Delegate()


def production_handlers(
    *,
    generate_impl: Callable[[SeedreamGenerate], EffectGenerator[SeedreamImageEditResult]]
    | None = None,
    image_generate_impl: Callable[[ImageGenerate], EffectGenerator[ImageResult]] | None = None,
    image_edit_impl: Callable[[ImageEdit], EffectGenerator[ImageResult]] | None = None,
    structured_impl: Callable[[SeedreamStructuredOutput], EffectGenerator[Any]] | None = None,
) -> ProtocolHandler:
    """Build a protocol handler backed by Seedream production logic."""

    active_generate_impl = generate_impl or _generate_impl
    active_image_generate_impl = image_generate_impl or _image_generate_impl
    active_image_edit_impl = image_edit_impl or _image_edit_impl
    active_structured_impl = structured_impl or _structured_impl

    @do
    def handler(effect: Effect, k: Any):  # noqa: PLR0911
        if isinstance(effect, SeedreamGenerate):
            if not _is_seedream_model(effect.model):
                yield Delegate()
                return
            value = yield active_generate_impl(effect)
            return (yield Resume(k, value))
        if isinstance(effect, ImageGenerate):
            if not _is_seedream_model(effect.model):
                yield Delegate()
                return
            value = yield active_image_generate_impl(effect)
            return (yield Resume(k, value))
        if isinstance(effect, ImageEdit):
            if not _is_seedream_model(effect.model):
                yield Delegate()
                return
            value = yield active_image_edit_impl(effect)
            return (yield Resume(k, value))
        if isinstance(effect, SeedreamStructuredOutput):
            value = yield active_structured_impl(effect)
            return (yield Resume(k, value))
        yield Delegate()

    return handler


__all__ = [
    "SEEDREAM_IMAGE_MODEL_PREFIXES",
    "ProtocolHandler",
    "production_handlers",
    "seedream_image_handler",
]
