"""Mock handlers for doeff-seedream domain effects."""

from __future__ import annotations

import base64
import copy
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin

from doeff_image.effects import ImageEdit, ImageGenerate
from doeff_image.types import ImageResult
from PIL import Image as PILImage

from doeff import Resume
from doeff_seedream.effects import SeedreamGenerate, SeedreamStructuredOutput
from doeff_seedream.types import SeedreamImage, SeedreamImageEditResult

ProtocolHandler = Callable[[Any, Any], Any]


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


def _color_from_digest(seed_text: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    return digest[0], digest[1], digest[2]


@dataclass
class MockSeedreamHandler:
    """Deterministic in-memory mock for Seedream effects."""

    default_image_size: str = "1024x1024"
    generate_responses: Mapping[str, SeedreamImageEditResult] = field(default_factory=dict)
    image_generate_responses: Mapping[str, ImageResult] = field(default_factory=dict)
    image_edit_responses: Mapping[str, ImageResult] = field(default_factory=dict)
    structured_responses: Mapping[type[Any], Any] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def handle_generate(self, effect: SeedreamGenerate) -> SeedreamImageEditResult:
        self.calls.append(
            {
                "effect": "SeedreamGenerate",
                "model": effect.model,
                "prompt": effect.prompt,
            }
        )

        configured = self.generate_responses.get(effect.model)
        if configured is not None:
            return copy.deepcopy(configured)

        return self._default_generate_result(prompt=effect.prompt, model=effect.model)

    def handle_image_generate(self, effect: ImageGenerate) -> ImageResult:
        self.calls.append(
            {
                "effect": "ImageGenerate",
                "model": effect.model,
                "prompt": effect.prompt,
            }
        )
        configured = self.image_generate_responses.get(effect.model)
        if configured is not None:
            return copy.deepcopy(configured)
        return self._default_unified_result(prompt=effect.prompt, model=effect.model)

    def handle_image_edit(self, effect: ImageEdit) -> ImageResult:
        self.calls.append(
            {
                "effect": "ImageEdit",
                "model": effect.model,
                "prompt": effect.prompt,
                "images": len(effect.images),
            }
        )
        configured = self.image_edit_responses.get(effect.model)
        if configured is not None:
            return copy.deepcopy(configured)
        return self._default_unified_result(prompt=effect.prompt, model=effect.model)

    def handle_structured(self, effect: SeedreamStructuredOutput) -> Any:
        self.calls.append(
            {
                "effect": "SeedreamStructuredOutput",
                "model": effect.model,
                "response_format": effect.response_format.__name__,
            }
        )

        configured = self.structured_responses.get(effect.response_format)
        if configured is None:
            configured = self._build_default_structured_payload(effect.response_format)
        return self._coerce_structured_response(effect.response_format, configured)

    def _coerce_structured_response(self, response_format: type[Any], value: Any) -> Any:
        if isinstance(value, response_format):
            return value
        validator = getattr(response_format, "model_validate", None)
        if callable(validator):
            return validator(value)
        return value

    def _build_default_structured_payload(self, response_format: type[Any]) -> dict[str, Any]:
        model_fields = getattr(response_format, "model_fields", None)
        if not isinstance(model_fields, dict):
            return {}

        payload: dict[str, Any] = {}
        for field_name, field_info in model_fields.items():
            annotation = getattr(field_info, "annotation", Any)
            payload[field_name] = _default_value_for_annotation(annotation, field_name)
        return payload

    def _default_generate_result(self, *, prompt: str, model: str) -> SeedreamImageEditResult:
        seed_text = f"{model}:{prompt}".encode()
        digest = hashlib.sha256(seed_text).digest()
        image_bytes = b"mock-seedream-" + digest[:16]
        encoded = base64.b64encode(image_bytes).decode("ascii")

        image = SeedreamImage(
            image_bytes=image_bytes,
            mime_type="image/jpeg",
            url=f"https://mock.seedream.local/{digest.hex()[:12]}.jpg",
            size=self.default_image_size,
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
                        "size": self.default_image_size,
                    }
                ],
                "usage": {"generated_images": 1},
            },
        )

    def _default_unified_result(self, *, prompt: str, model: str) -> ImageResult:
        image = PILImage.new("RGB", (16, 16), _color_from_digest(f"{model}:{prompt}"))
        return ImageResult(
            images=[image],
            prompt=prompt,
            model=model,
            raw_response={"mock": True, "model": model},
        )


def mock_handlers(
    *,
    handler: MockSeedreamHandler | None = None,
    default_image_size: str = "1024x1024",
    generate_responses: Mapping[str, SeedreamImageEditResult] | None = None,
    image_generate_responses: Mapping[str, ImageResult] | None = None,
    image_edit_responses: Mapping[str, ImageResult] | None = None,
    structured_responses: Mapping[type[Any], Any] | None = None,
) -> dict[type[Any], ProtocolHandler]:
    """Build deterministic mock handlers for Seedream domain effects."""

    active_handler = handler or MockSeedreamHandler(
        default_image_size=default_image_size,
        generate_responses=generate_responses or {},
        image_generate_responses=image_generate_responses or {},
        image_edit_responses=image_edit_responses or {},
        structured_responses=structured_responses or {},
    )

    def handle_generate(effect: SeedreamGenerate, k):
        return (yield Resume(k, active_handler.handle_generate(effect)))

    def handle_image_generate(effect: ImageGenerate, k):
        return (yield Resume(k, active_handler.handle_image_generate(effect)))

    def handle_image_edit(effect: ImageEdit, k):
        return (yield Resume(k, active_handler.handle_image_edit(effect)))

    def handle_structured(effect: SeedreamStructuredOutput, k):
        return (yield Resume(k, active_handler.handle_structured(effect)))

    return {
        SeedreamGenerate: handle_generate,
        ImageGenerate: handle_image_generate,
        ImageEdit: handle_image_edit,
        SeedreamStructuredOutput: handle_structured,
    }


__all__ = [
    "MockSeedreamHandler",
    "ProtocolHandler",
    "mock_handlers",
]
