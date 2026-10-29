"""Mock handlers for doeff-seedream domain effects."""


import base64
import copy
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin

from doeff_core_effects._effects import EffectBase as Effect
from doeff_image import ImageEdit, ImageGenerate, ImageResult
from PIL import Image as PILImage

from doeff import Pass, Resume, do
from doeff import handler as _program_handler
from doeff.program import ProgramHandler
from doeff_seedream._effects import SeedreamGenerate, SeedreamStructuredOutput
from doeff_seedream._types import SeedreamImage, SeedreamImageEditResult

ProtocolHandler = Callable[[Effect, Any], Any]


def _default_value_for_annotation(annotation: Any, field_name: str) -> Any:  # noqa: PLR0911
    if annotation in (str, Any):
        return f"mock-{field_name}"
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    if annotation is bool:
        return False

    origin = get_origin(annotation)
    if origin is not None:
        args = get_args(annotation)
        non_none_args = [arg for arg in args if arg is not type(None)]
        if len(non_none_args) == 1:
            return _default_value_for_annotation(non_none_args[0], field_name)
        if origin in (list, tuple, set, frozenset):
            return []
        if origin is dict:
            return {}

    return f"mock-{field_name}"


def _color_from_digest(seed_text: str) -> tuple[int, int, int]:  # noqa: DOEFF006 - PIL の色は (r, g, b) の tuple
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    return digest[0], digest[1], digest[2]


@dataclass(frozen=True)
class _MockSeedream:
    """What the Seedream mock handler answers with: the recorded answers by model or type, and the default image size."""

    default_image_size: str = "1024x1024"
    generate_responses: Mapping[str, SeedreamImageEditResult] = field(default_factory=dict)
    image_generate_responses: Mapping[str, ImageResult] = field(default_factory=dict)
    image_edit_responses: Mapping[str, ImageResult] = field(default_factory=dict)
    structured_responses: Mapping[type[Any], Any] = field(default_factory=dict)


def _generate_answer(mock: _MockSeedream, effect: SeedreamGenerate) -> SeedreamImageEditResult:
    configured = mock.generate_responses.get(effect.model)
    if configured is not None:
        return copy.deepcopy(configured)
    return _default_generate_result(mock, prompt=effect.prompt, model=effect.model)


def _image_generate_answer(mock: _MockSeedream, effect: ImageGenerate) -> ImageResult:
    configured = mock.image_generate_responses.get(effect.model)
    if configured is not None:
        return copy.deepcopy(configured)
    return _default_unified_result(prompt=effect.prompt, model=effect.model)


def _image_edit_answer(mock: _MockSeedream, effect: ImageEdit) -> ImageResult:
    configured = mock.image_edit_responses.get(effect.model)
    if configured is not None:
        return copy.deepcopy(configured)
    return _default_unified_result(prompt=effect.prompt, model=effect.model)


def _structured_answer(mock: _MockSeedream, effect: SeedreamStructuredOutput) -> Any:
    configured = mock.structured_responses.get(effect.response_format)
    if configured is None:
        configured = _build_default_structured_payload(effect.response_format)
    return _coerce_structured_response(effect.response_format, configured)


def _coerce_structured_response(response_format: type[Any], value: Any) -> Any:
    if isinstance(value, response_format):
        return value
    validator = getattr(response_format, "model_validate", None)
    if callable(validator):
        return validator(value)
    return value


def _build_default_structured_payload(response_format: type[Any]) -> dict[str, Any]:
    model_fields = getattr(response_format, "model_fields", None)
    if not isinstance(model_fields, dict):
        return {}

    payload: dict[str, Any] = {}
    for field_name, field_info in model_fields.items():
        annotation = getattr(field_info, "annotation", Any)
        payload[field_name] = _default_value_for_annotation(annotation, field_name)
    return payload


def _default_generate_result(mock: _MockSeedream, *, prompt: str, model: str) -> SeedreamImageEditResult:
    seed_text = f"{model}:{prompt}".encode()
    digest = hashlib.sha256(seed_text).digest()
    image_bytes = b"mock-seedream-" + digest[:16]
    encoded = base64.b64encode(image_bytes).decode("ascii")

    image = SeedreamImage(
        image_bytes=image_bytes,
        mime_type="image/jpeg",
        url=f"https://mock.seedream.local/{digest.hex()[:12]}.jpg",
        size=mock.default_image_size,
    )
    return SeedreamImageEditResult(
        images=[image],
        prompt=prompt,
        model=model,
        raw_response={
            "model": model,
            "data": [
                {
                    "b64_json": encoded,
                    "url": image.url,
                    "size": mock.default_image_size,
                }
            ],
            "usage": {"generated_images": 1},
        },
    )


def _default_unified_result(*, prompt: str, model: str) -> ImageResult:
    image = PILImage.new("RGB", (16, 16), _color_from_digest(f"{model}:{prompt}"))
    return ImageResult(
        images=[image],
        prompt=prompt,
        model=model,
        raw_response={"mock": True, "model": model},
    )


def mock_handlers(
    *,
    default_image_size: str = "1024x1024",
    generate_responses: Mapping[str, SeedreamImageEditResult] | None = None,
    image_generate_responses: Mapping[str, ImageResult] | None = None,
    image_edit_responses: Mapping[str, ImageResult] | None = None,
    structured_responses: Mapping[type[Any], Any] | None = None,
) -> ProgramHandler:
    """Build a deterministic mock protocol handler for Seedream domain effects."""

    mock = _MockSeedream(
        default_image_size=default_image_size,
        generate_responses=generate_responses or {},
        image_generate_responses=image_generate_responses or {},
        image_edit_responses=image_edit_responses or {},
        structured_responses=structured_responses or {},
    )

    @do
    def dispatch(effect: Effect, k: Any):
        if isinstance(effect, SeedreamGenerate):
            return (yield Resume(k, _generate_answer(mock, effect)))
        if isinstance(effect, ImageGenerate):
            return (yield Resume(k, _image_generate_answer(mock, effect)))
        if isinstance(effect, ImageEdit):
            return (yield Resume(k, _image_edit_answer(mock, effect)))
        if isinstance(effect, SeedreamStructuredOutput):
            return (yield Resume(k, _structured_answer(mock, effect)))
        return (yield Pass(effect, k))

    return _program_handler(dispatch)

