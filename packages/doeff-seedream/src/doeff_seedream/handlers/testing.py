"""Mock handlers for doeff-seedream domain effects."""

from __future__ import annotations

import base64
import copy
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin

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


@dataclass
class MockSeedreamHandler:
    """Deterministic in-memory mock for Seedream effects."""

    default_image_size: str = "1024x1024"
    generate_responses: Mapping[str, SeedreamImageEditResult] = field(default_factory=dict)
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

        return self._default_generate_result(effect)

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

    def _default_generate_result(self, effect: SeedreamGenerate) -> SeedreamImageEditResult:
        seed_text = f"{effect.model}:{effect.prompt}".encode("utf-8")
        digest = hashlib.sha256(seed_text).digest()
        image_bytes = b"mock-seedream-" + digest[:16]
        encoded = base64.b64encode(image_bytes).decode("ascii")

        size = self.default_image_size
        overrides = effect.generation_config_overrides
        if isinstance(overrides, Mapping):
            requested_size = overrides.get("size")
            if isinstance(requested_size, str) and requested_size:
                size = requested_size

        image = SeedreamImage(
            image_bytes=image_bytes,
            mime_type="image/jpeg",
            url=f"https://mock.seedream.local/{digest.hex()[:12]}.jpg",
            size=size,
        )
        return SeedreamImageEditResult(
            images=[image],
            prompt=effect.prompt,
            model=effect.model,
            raw_response={
                "model": effect.model,
                "data": [
                    {
                        "b64_json": encoded,
                        "url": image.url,
                        "size": size,
                    }
                ],
                "usage": {"generated_images": 1},
            },
        )


def mock_handlers(
    *,
    handler: MockSeedreamHandler | None = None,
    default_image_size: str = "1024x1024",
    generate_responses: Mapping[str, SeedreamImageEditResult] | None = None,
    structured_responses: Mapping[type[Any], Any] | None = None,
) -> dict[type[Any], ProtocolHandler]:
    """Build deterministic mock handlers for Seedream domain effects."""

    active_handler = handler or MockSeedreamHandler(
        default_image_size=default_image_size,
        generate_responses=generate_responses or {},
        structured_responses=structured_responses or {},
    )

    def handle_generate(effect: SeedreamGenerate, k):
        return (yield Resume(k, active_handler.handle_generate(effect)))

    def handle_structured(effect: SeedreamStructuredOutput, k):
        return (yield Resume(k, active_handler.handle_structured(effect)))

    return {
        SeedreamGenerate: handle_generate,
        SeedreamStructuredOutput: handle_structured,
    }


__all__ = [
    "MockSeedreamHandler",
    "ProtocolHandler",
    "mock_handlers",
]
