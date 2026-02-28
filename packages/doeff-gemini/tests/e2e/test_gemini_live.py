"""Gemini integration tests with WithHandler-based mocks and one live smoke test."""


import os
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from pydantic import BaseModel

IMAGE_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "doeff-image" / "src"
if str(IMAGE_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(IMAGE_PACKAGE_ROOT))

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from doeff_gemini import edit_image__gemini, structured_llm__gemini  # noqa: E402
from doeff_gemini.handlers import default_gemini_cost_handler  # noqa: E402

from doeff import (  # noqa: E402
    AskEffect,
    EffectGenerator,
    Pass,
    Resume,
    WithHandler,
    async_run,
    default_handlers,
    do,
)
from doeff.effects.base import Effect  # noqa: E402


class FunFact(BaseModel):
    topic: str
    fact: str


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (16, 16), color=color)
    with BytesIO() as buffer:
        image.save(buffer, format="PNG")
        return buffer.getvalue()


def _make_image_response(*, image_bytes: bytes, text: str) -> Any:
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(text=text),
                        SimpleNamespace(
                            inline_data=SimpleNamespace(data=image_bytes, mime_type="image/png")
                        ),
                    ]
                )
            )
        ],
        usage_metadata=None,
    )


def _make_structured_response(payload: FunFact) -> Any:
    return SimpleNamespace(
        parsed=[payload],
        text=payload.model_dump_json(),
        usage_metadata=None,
    )


def _make_mock_client(response: Any) -> tuple[Any, Any]:
    async_models = SimpleNamespace(generate_content=AsyncMock(return_value=response))
    async_client = SimpleNamespace(models=async_models)
    return SimpleNamespace(async_client=async_client), async_models


def _with_mock_gemini_handler(program: Any, *, mock_client: Any, asked_keys: list[str]) -> Any:
    @do
    def mock_handler(effect: Effect, k: Any):
        if isinstance(effect, AskEffect):
            asked_keys.append(effect.key)
            if effect.key == "gemini_client":
                return (yield Resume(k, mock_client))
            if effect.key == "gemini_api_key":
                return (yield Resume(k, "fake-gemini-key"))
        yield Pass()

    return WithHandler(mock_handler, program)


def _get_live_gemini_env_or_skip() -> dict[str, Any]:
    if os.getenv("DOEFF_GEMINI_RUN_E2E") != "1":
        pytest.skip("Set DOEFF_GEMINI_RUN_E2E=1 to run the live Gemini e2e smoke test")

    pytest.importorskip("google.genai")

    try:
        import google.auth  # type: ignore
        from google.auth.exceptions import DefaultCredentialsError  # type: ignore
    except ModuleNotFoundError:
        pytest.skip("google-auth not installed; skipping live Gemini test")

    try:
        credentials, project = google.auth.default()  # type: ignore[attr-defined]
    except DefaultCredentialsError:
        pytest.skip(
            "Application Default Credentials not available. "
            "Run 'gcloud auth application-default login'."
        )

    env: dict[str, Any] = {}
    if project:
        env["gemini_project"] = project
    if credentials:
        env["gemini_credentials"] = credentials
    return env


@pytest.mark.asyncio
async def test_edit_image__nanobanana_pro() -> None:
    """Run image edit pipeline with mocked Gemini response via WithHandler."""

    base_image = Image.new("RGB", (256, 256), color=(30, 30, 30))
    response = _make_image_response(
        image_bytes=_png_bytes((255, 240, 40)),
        text="Added a yellow banana icon",
    )
    mock_client, async_models = _make_mock_client(response)
    asked_keys: list[str] = []

    @do
    def flow() -> EffectGenerator[Any]:
        return (
            yield edit_image__gemini(
                prompt="Add a small yellow banana icon in the center of the image",
                model="gemini-3-pro-image-preview",
                images=[base_image],
                temperature=0.1,
                max_retries=1,
                aspect_ratio="16:9",
                image_size="2K",
            )
        )

    result = await async_run(
        _with_mock_gemini_handler(flow(), mock_client=mock_client, asked_keys=asked_keys),
        handlers=[default_gemini_cost_handler, *default_handlers()],
    )

    assert result.is_ok(), "\n".join(str(entry) for entry in result.log)
    payload = result.value
    assert payload.image_bytes
    assert payload.mime_type.startswith("image/")
    assert "gemini_client" in asked_keys
    async_models.generate_content.assert_called_once()
    assert async_models.generate_content.call_args.kwargs["model"] == "gemini-3-pro-image-preview"


@pytest.mark.asyncio
async def test_edit_image__gemini() -> None:
    """Run flash image edit pipeline with mocked Gemini response via WithHandler."""

    base_image = Image.new("RGB", (256, 256), color=(64, 128, 192))
    response = _make_image_response(
        image_bytes=_png_bytes((220, 20, 60)),
        text="Drew a red star",
    )
    mock_client, async_models = _make_mock_client(response)
    asked_keys: list[str] = []

    @do
    def flow() -> EffectGenerator[Any]:
        return (
            yield edit_image__gemini(
                prompt="Draw a small red star in the center of the image",
                model="gemini-2.5-flash-image-preview",
                images=[base_image],
                temperature=0.2,
                max_retries=1,
            )
        )

    result = await async_run(
        _with_mock_gemini_handler(flow(), mock_client=mock_client, asked_keys=asked_keys),
        handlers=[default_gemini_cost_handler, *default_handlers()],
    )

    assert result.is_ok(), "\n".join(str(entry) for entry in result.log)
    payload = result.value
    assert payload.image_bytes
    assert payload.mime_type.startswith("image/")
    assert "gemini_client" in asked_keys
    async_models.generate_content.assert_called_once()
    assert (
        async_models.generate_content.call_args.kwargs["model"] == "gemini-2.5-flash-image-preview"
    )


@pytest.mark.asyncio
async def test_structured_llm__gemini_with_pydantic() -> None:
    """Run structured Gemini pipeline with mocked response via WithHandler."""

    response = _make_structured_response(
        FunFact(topic="Hummingbirds", fact="They can fly backward.")
    )
    mock_client, async_models = _make_mock_client(response)
    asked_keys: list[str] = []

    @do
    def flow() -> EffectGenerator[FunFact]:
        return (
            yield structured_llm__gemini(
                text=(
                    "Return JSON describing a fun fact about hummingbirds with keys topic and fact"
                ),
                model="gemini-2.5-pro",
                response_format=FunFact,
                temperature=0.0,
                max_output_tokens=256,
            )
        )

    result = await async_run(
        _with_mock_gemini_handler(flow(), mock_client=mock_client, asked_keys=asked_keys),
        handlers=[default_gemini_cost_handler, *default_handlers()],
    )

    assert result.is_ok(), "\n".join(str(entry) for entry in result.log)
    payload = result.value
    assert isinstance(payload, FunFact)
    assert payload.topic == "Hummingbirds"
    assert payload.fact
    assert "gemini_client" in asked_keys

    async_models.generate_content.assert_called_once()
    assert async_models.generate_content.call_args.kwargs["model"] == "gemini-2.5-pro"
    config = async_models.generate_content.call_args.kwargs["config"]
    assert config.response_schema is FunFact


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_structured_llm__gemini_live_with_pydantic() -> None:
    """Single opt-in live smoke test against Gemini API."""

    env = _get_live_gemini_env_or_skip()

    @do
    def flow() -> EffectGenerator[FunFact]:
        return (
            yield structured_llm__gemini(
                text=(
                    "Return JSON describing a fun fact about hummingbirds with keys topic and fact"
                ),
                model="gemini-2.5-pro",
                response_format=FunFact,
                temperature=0.0,
                max_output_tokens=256,
            )
        )

    result = await async_run(
        flow(),
        handlers=[default_gemini_cost_handler, *default_handlers()],
        env=env,
    )

    if not result.is_ok():
        log_summary = "\n".join(str(entry) for entry in result.log)
        if "Internal error encountered" in log_summary:
            pytest.skip("Gemini API returned 500 Internal error during structured live test")
        if "SAFETY" in log_summary.upper():
            pytest.skip("Gemini request blocked by safety filters")
        raise AssertionError(log_summary)

    payload = result.value
    assert isinstance(payload, FunFact)
    assert payload.topic
    assert payload.fact
