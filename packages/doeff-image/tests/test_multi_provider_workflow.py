# ruff: noqa: E402
"""Integration tests for unified multi-provider image workflows."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

REPO_PACKAGES = Path(__file__).resolve().parents[2]
for package_name in ("doeff-image", "doeff-seedream", "doeff-gemini"):
    package_src = REPO_PACKAGES / package_name / "src"
    if str(package_src) not in sys.path:
        sys.path.insert(0, str(package_src))

if "pydantic" not in sys.modules:
    try:
        import pydantic as _pydantic  # type: ignore # noqa: F401
    except ModuleNotFoundError:
        import types
        from typing import Any

        pydantic_stub = types.ModuleType("pydantic")

        class BaseModel:  # type: ignore[no-redef]
            """Minimal pydantic stub for handler import-time typing."""

            def model_dump(self) -> dict[str, Any]:
                return {}

            @classmethod
            def model_validate(cls, value: Any) -> Any:
                if isinstance(value, cls):
                    return value
                if isinstance(value, dict):
                    return cls()
                raise TypeError("Expected mapping payload")

        class ValidationError(Exception):  # type: ignore[no-redef]
            """Stub ValidationError."""

        pydantic_stub.BaseModel = BaseModel  # type: ignore[attr-defined]
        pydantic_stub.ValidationError = ValidationError  # type: ignore[attr-defined]
        sys.modules["pydantic"] = pydantic_stub

from doeff import Delegate, EffectGenerator, Resume, WithHandler, default_handlers, do, run
from doeff_image.effects import ImageEdit, ImageGenerate
from doeff_image.types import ImageResult

import doeff_gemini.handlers.production as gemini_production
import doeff_seedream.handlers.production as seedream_production


def _fallback_handler(value: str):
    def _handler(effect, k):
        if isinstance(effect, (ImageGenerate, ImageEdit)):
            return (yield Resume(k, value))
        yield Delegate()

    return _handler


def test_seedream_handler_delegates_unsupported_model() -> None:
    @do
    def flow() -> EffectGenerator[str]:
        return (
            yield ImageGenerate(
                prompt="ignored",
                model="gemini-3-pro-image",
            )
        )

    result = run(
        WithHandler(
            seedream_production.seedream_image_handler,
            WithHandler(_fallback_handler("delegated"), flow()),
        ),
        handlers=default_handlers(),
    )
    assert result.is_ok()
    assert result.value == "delegated"


def test_gemini_handler_delegates_unsupported_model() -> None:
    @do
    def flow() -> EffectGenerator[str]:
        return (
            yield ImageEdit(
                prompt="ignored",
                model="seedream-4",
            )
        )

    result = run(
        WithHandler(
            gemini_production.gemini_image_handler,
            WithHandler(_fallback_handler("delegated"), flow()),
        ),
        handlers=default_handlers(),
    )
    assert result.is_ok()
    assert result.value == "delegated"


def test_multi_provider_workflow_with_stacked_handlers(monkeypatch) -> None:
    @do
    def fake_seedream_generate(effect: ImageGenerate) -> EffectGenerator[ImageResult]:
        base_image = Image.new("RGB", (24, 24), color=(255, 0, 0))
        return ImageResult(
            images=[base_image],
            model=effect.model,
            prompt=effect.prompt,
            raw_response={"provider": "seedream"},
        )

    @do
    def fake_gemini_edit(effect: ImageEdit) -> EffectGenerator[ImageResult]:
        edited_image = effect.images[0].copy()
        edited_image.putpixel((0, 0), (0, 255, 0))
        return ImageResult(
            images=[edited_image],
            model=effect.model,
            prompt=effect.prompt,
            raw_response={"provider": "gemini"},
        )

    monkeypatch.setattr(seedream_production, "_image_generate_impl", fake_seedream_generate)
    monkeypatch.setattr(gemini_production, "_image_edit_impl", fake_gemini_edit)

    @do
    def flow() -> EffectGenerator[tuple[ImageResult, ImageResult]]:
        base = yield ImageGenerate(
            prompt="A scenic mountain",
            model="seedream-4",
            size=(1024, 1024),
        )
        edited = yield ImageEdit(
            prompt="Add a torii gate",
            model="gemini-3-pro-image",
            images=[base.images[0]],
            strength=0.7,
        )
        return base, edited

    result = run(
        WithHandler(
            seedream_production.seedream_image_handler,
            WithHandler(gemini_production.gemini_image_handler, flow()),
        ),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    base, edited = result.value
    assert base.model == "seedream-4"
    assert edited.model == "gemini-3-pro-image"
    assert base.images[0].getpixel((0, 0)) == (255, 0, 0)
    assert edited.images[0].getpixel((0, 0)) == (0, 255, 0)
