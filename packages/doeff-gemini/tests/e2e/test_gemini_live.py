"""Live tests for Gemini integrations (image editing and structured text)."""

from __future__ import annotations

from typing import Any

import pytest
from PIL import Image
from pydantic import BaseModel
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from doeff import AsyncRuntime, EffectGenerator, do

from doeff_gemini import edit_image__gemini, structured_llm__gemini


def _get_gemini_env_or_skip() -> dict[str, Any]:
    """Return environment bindings for Gemini credentials or skip the test."""

    pytest.importorskip("google.genai")

    try:
        import google.auth  # type: ignore
        from google.auth.exceptions import DefaultCredentialsError  # type: ignore
    except ModuleNotFoundError:
        pytest.skip("google-auth not installed; skipping live Gemini tests")

    try:
        credentials, project = google.auth.default()  # type: ignore[attr-defined]
    except DefaultCredentialsError:
        pytest.skip(
            "Application Default Credentials not available. Run 'gcloud auth application-default login'."
        )

    env: dict[str, Any] = {}
    if project:
        env["gemini_project"] = project
    if credentials:
        env["gemini_credentials"] = credentials

    return env


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_edit_image__nanobanana_pro_live() -> None:
    """Exercise NanoBanana Pro (Gemini 3 Pro Image) image edit flow."""

    env = _get_gemini_env_or_skip()

    base_image = Image.new("RGB", (256, 256), color=(30, 30, 30))

    @do
    def flow() -> EffectGenerator[Any]:
        result = yield edit_image__gemini(
            prompt="Add a small yellow banana icon in the center of the image",
            model="gemini-3-pro-image-preview",
            images=[base_image],
            temperature=0.1,
            max_retries=1,
            aspect_ratio="16:9",
            image_size="2K",
        )
        return result

    runtime = AsyncRuntime()
    result = await runtime.run(flow(), env=env)

    if not result.is_ok():
        log_summary = "\n".join(str(entry) for entry in result.log)
        if "permission" in log_summary.lower():
            pytest.skip("NanoBanana Pro not enabled for current credentials")
        if "Reauthentication is needed" in log_summary:
            pytest.skip(
                "ADC credentials require reauthentication; run 'gcloud auth application-default login'."
            )
        if "Internal error encountered" in log_summary:
            pytest.skip("Gemini API returned 500 Internal error during NanoBanana test")
        raise AssertionError(log_summary)

    payload = result.value
    assert payload.image_bytes, "Expected edited image bytes"
    assert payload.mime_type.startswith("image/"), payload.mime_type


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_edit_image__gemini_live() -> None:
    """End-to-end exercise against the Gemini NanoBanana image editing flow."""

    env = _get_gemini_env_or_skip()

    base_image = Image.new("RGB", (256, 256), color=(64, 128, 192))

    @do
    def flow() -> EffectGenerator[Any]:
        result = yield edit_image__gemini(
            prompt="Draw a small red star in the center of the image",
            model="gemini-2.5-flash-image-preview",
            images=[base_image],
            temperature=0.2,
            max_retries=1,
        )
        return result

    runtime = AsyncRuntime()
    result = await runtime.run(flow(), env=env)

    if not result.is_ok():
        log_summary = "\n".join(str(entry) for entry in result.log)
        if "Reauthentication is needed" in log_summary:
            pytest.skip(
                "ADC credentials require reauthentication; run 'gcloud auth application-default login'."
            )
        if "Internal error encountered" in log_summary:
            pytest.skip("Gemini API returned 500 Internal error during image edit test")
        raise AssertionError(log_summary)

    payload = result.value
    assert payload.image_bytes, "Expected edited image bytes"
    assert payload.mime_type.startswith("image/"), payload.mime_type


class FunFact(BaseModel):
    topic: str
    fact: str


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_structured_llm__gemini_live_with_pydantic() -> None:
    """Exercise Gemini 2.5 Pro with a structured Pydantic response."""

    env = _get_gemini_env_or_skip()

    @do
    def flow() -> EffectGenerator[FunFact]:
        result = yield structured_llm__gemini(
            text=(
                "Return JSON describing a fun fact about hummingbirds with keys "
                "topic and fact"
            ),
            model="gemini-2.5-pro",
            response_format=FunFact,
            temperature=0.0,
            max_output_tokens=256,
        )
        return result

    runtime = AsyncRuntime()
    result = await runtime.run(flow(), env=env)

    if not result.is_ok():
        log_summary = "\n".join(str(entry) for entry in result.log)
        if "Internal error encountered" in log_summary:
            pytest.skip("Gemini API returned 500 Internal error during structured test")
        if "SAFETY" in log_summary.upper():
            pytest.skip("Gemini request blocked by safety filters")
        raise AssertionError(log_summary)

    payload = result.value
    assert isinstance(payload, FunFact)
    assert payload.topic
    assert payload.fact
