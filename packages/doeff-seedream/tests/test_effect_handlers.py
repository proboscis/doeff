# ruff: noqa: E402
"""Tests for Seedream domain effects and handlers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

IMAGE_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "doeff-image" / "src"
if str(IMAGE_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(IMAGE_PACKAGE_ROOT))

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from doeff_image.effects import ImageGenerate as UnifiedImageGenerate
from doeff_image.types import ImageResult
from doeff_seedream.effects import SeedreamGenerate, SeedreamStructuredOutput
from doeff_seedream.handlers import mock_handlers, production_handlers
from doeff_seedream.types import SeedreamImage, SeedreamImageEditResult

from doeff import EffectGenerator, do
from doeff.rust_vm import run_with_handler_map


class SummarySchema:
    keyword: str
    score: int

    def __init__(self, keyword: str, score: int):
        self.keyword = keyword
        self.score = score

    @classmethod
    def model_validate(cls, value: Any) -> SummarySchema:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise TypeError("SummarySchema expects mapping payload")
        return cls(
            keyword=str(value.get("keyword", "")),
            score=int(value.get("score", 0)),
        )


def _build_result(*, prompt: str, model: str, payload: bytes) -> SeedreamImageEditResult:
    image = SeedreamImage(
        image_bytes=payload,
        mime_type="image/jpeg",
        size="2K",
        url="https://seedream.mock/fixed.jpg",
    )
    return SeedreamImageEditResult(
        images=[image],
        prompt=prompt,
        model=model,
        raw_response={"model": model, "data": [{"size": "2K"}], "usage": {"generated_images": 1}},
    )


@do
def _domain_program() -> EffectGenerator[dict[str, Any]]:
    generated = yield SeedreamGenerate(
        prompt="Render a lighthouse at dawn",
        model="seedream-test-model",
        generation_config_overrides={"size": "2K"},
    )
    structured = yield SeedreamStructuredOutput(
        messages=[{"role": "user", "content": "Summarize image intent"}],
        response_format=SummarySchema,
        model="seedream-test-model",
    )
    return {
        "generated": generated,
        "structured": structured,
    }


def test_effect_exports() -> None:
    from doeff_seedream.effects import SeedreamGenerate as ImportedGenerate
    from doeff_seedream.effects import SeedreamStructuredOutput as ImportedStructured

    assert ImportedGenerate is SeedreamGenerate
    assert ImportedStructured is SeedreamStructuredOutput


def test_seedream_generate_is_deprecated_alias() -> None:
    with pytest.deprecated_call(match="SeedreamGenerate is deprecated"):
        effect = SeedreamGenerate(prompt="deprecated", model="seedream-4")
    assert effect.model == "seedream-4"


def test_handler_exports() -> None:
    from doeff_seedream.handlers import mock_handlers as imported_mock_handlers
    from doeff_seedream.handlers import production_handlers as imported_production_handlers

    assert imported_production_handlers is production_handlers
    assert imported_mock_handlers is mock_handlers


def test_mock_handlers_are_configurable_and_deterministic() -> None:
    configured_result = _build_result(
        prompt="Render a lighthouse at dawn",
        model="seedream-test-model",
        payload=b"configured-bytes",
    )
    handlers = mock_handlers(
        generate_responses={"seedream-test-model": configured_result},
        structured_responses={SummarySchema: {"keyword": "lighthouse", "score": 7}},
    )

    first = run_with_handler_map(_domain_program(), handlers)
    second = run_with_handler_map(_domain_program(), handlers)

    assert first.is_ok()
    assert second.is_ok()

    first_payload = first.value
    second_payload = second.value

    assert first_payload["generated"].image_bytes == b"configured-bytes"
    assert second_payload["generated"].image_bytes == b"configured-bytes"
    assert isinstance(first_payload["structured"], SummarySchema)
    assert first_payload["structured"].keyword == "lighthouse"
    assert first_payload["structured"].score == 7
    assert first_payload["generated"].raw_response == second_payload["generated"].raw_response


@do
def _swap_program() -> EffectGenerator[bytes | None]:
    generated = yield SeedreamGenerate(
        prompt="swap target",
        model="seedream-swap-model",
    )
    return generated.image_bytes


def test_handler_swapping_changes_behavior() -> None:
    mock_result = run_with_handler_map(
        _swap_program(),
        mock_handlers(default_image_size="1K"),
    )

    @do
    def production_generate(effect: SeedreamGenerate) -> EffectGenerator[SeedreamImageEditResult]:
        return _build_result(
            prompt=effect.prompt,
            model=effect.model,
            payload=b"production-bytes",
        )

    production_result = run_with_handler_map(
        _swap_program(),
        production_handlers(generate_impl=production_generate),
    )

    assert mock_result.is_ok()
    assert production_result.is_ok()
    assert mock_result.value != production_result.value
    assert production_result.value == b"production-bytes"


@do
def _unified_program() -> EffectGenerator[ImageResult]:
    return (
        yield UnifiedImageGenerate(
            prompt="Unified generation",
            model="seedream-test-model",
        )
    )


def test_mock_handlers_support_unified_effects() -> None:
    result = run_with_handler_map(_unified_program(), mock_handlers())
    assert result.is_ok()
    assert isinstance(result.value, ImageResult)
