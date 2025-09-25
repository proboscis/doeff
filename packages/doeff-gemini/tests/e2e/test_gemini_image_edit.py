"""Live tests for Gemini image editing integrations.

These tests hit the real Gemini API and therefore require a valid
`GEMINI_API_KEY` environment variable (or ADC configuration). They are tagged
with ``pytest.mark.e2e`` so they can be excluded from fast test runs.
"""

from __future__ import annotations

from typing import Any

import pytest
from PIL import Image

from doeff import EffectGenerator, ExecutionContext, ProgramInterpreter, do

from doeff_gemini import edit_image__gemini


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_edit_image__gemini_live() -> None:
    """End-to-end exercise against the Gemini API for NanoBanana editing."""

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
            "Application Default Credentials not available. Run 'gcloud auth application-default login'."
        )

    env: dict[str, Any] = {}
    if project:
        env["gemini_project"] = project
    if credentials:
        env["gemini_credentials"] = credentials

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

    engine = ProgramInterpreter()
    context = ExecutionContext(env=env)
    result = await engine.run(flow(), context)

    if not result.is_ok:
        log_summary = "\n".join(str(entry) for entry in result.context.log)
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
