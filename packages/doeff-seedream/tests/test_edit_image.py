# ruff: noqa: E402
from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

IMAGE_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "doeff-image" / "src"
if str(IMAGE_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(IMAGE_PACKAGE_ROOT))

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from doeff_seedream import SeedreamClient, edit_image__seedream4, get_seedream_client

from doeff import (
    AskEffect,
    Get,
    Pass,
    Resume,
    Try,
    WithHandler,
    async_run,
    default_handlers,
    do,
)
from doeff.effects.base import Effect


class RecordingSeedreamClient(SeedreamClient):
    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__(api_key="test-key")
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def a_generate_images(self, payload, *, timeout=None, headers=None):  # type: ignore[override]
        self.calls.append({"payload": payload, "timeout": timeout, "headers": headers})
        return self.response


class FailingSeedreamClient(SeedreamClient):
    def __init__(self, error: Exception) -> None:
        super().__init__(api_key="test-key")
        self.error = error
        self.calls = 0

    async def a_generate_images(self, payload, *, timeout=None, headers=None):  # type: ignore[override]
        del payload, timeout, headers
        self.calls += 1
        raise self.error


def _build_mock_seedream_handler(overrides: dict[str, Any]):
    @do
    def _handler(effect: Effect, k: Any):
        if isinstance(effect, AskEffect) and effect.key in overrides:
            return (yield Resume(k, overrides[effect.key]))
        yield Pass()

    return _handler


async def _run_with_handler(program, overrides: dict[str, Any]):
    return await async_run(
        WithHandler(_build_mock_seedream_handler(overrides), program),
        handlers=default_handlers(),
    )


@do
def _cost_tracking_program():
    result = yield edit_image__seedream4(
        prompt="A test prompt",
        model="dummy-model",
        images=[Image.new("RGB", (16, 16), color="red")],
        generation_config_overrides={"response_format": "b64_json"},
        max_retries=1,
    )
    total_cost_result = yield Try(Get("seedream_total_cost_usd"))
    model_cost_result = yield Try(Get("seedream_cost_dummy-model"))
    calls_result = yield Try(Get("seedream_api_calls"))
    return {
        "result": result,
        "seedream_total_cost_usd": total_cost_result.value if total_cost_result.is_ok() else None,
        "seedream_cost_dummy_model": model_cost_result.value if model_cost_result.is_ok() else None,
        "seedream_api_calls": calls_result.value if calls_result.is_ok() else None,
    }


@pytest.mark.asyncio
async def test_edit_image_seedream4_decodes_payload_and_tracks_cost_with_handler():
    encoded = base64.b64encode(b"dummy-image-bytes").decode("ascii")
    client = RecordingSeedreamClient(
        {
            "model": "dummy-model",
            "data": [{"b64_json": encoded, "size": "64x64"}],
            "usage": {"generated_images": 1},
        }
    )
    run_result = await _run_with_handler(
        _cost_tracking_program(),
        {
            "seedream_client": client,
            "seedream_cost_per_image_usd": 0.05,
        },
    )

    assert run_result.is_ok()
    value = run_result.value["result"]
    assert value.image_bytes == b"dummy-image-bytes"
    assert value.images[0].size == "64x64"
    assert run_result.value["seedream_total_cost_usd"] == pytest.approx(0.05)
    assert run_result.value["seedream_cost_dummy_model"] == pytest.approx(0.05)
    calls = run_result.value["seedream_api_calls"]
    assert calls
    assert calls[-1]["total_cost"] == pytest.approx(0.05)
    assert client.calls
    assert client.calls[0]["payload"]["prompt"] == "A test prompt"
    assert any("estimated cost" in str(entry) for entry in run_result.log)


@pytest.mark.asyncio
async def test_edit_image_seedream4_surfaces_api_error_with_handler():
    client = FailingSeedreamClient(RuntimeError("seedream api failure"))

    @do
    def program():
        return (
            yield edit_image__seedream4(
                prompt="A failing prompt",
                model="dummy-model",
                max_retries=1,
            )
        )

    run_result = await _run_with_handler(program(), {"seedream_client": client})
    assert run_result.is_err()
    assert isinstance(run_result.error, RuntimeError)
    assert "seedream api failure" in str(run_result.error)
    assert client.calls == 1
    assert any("failed" in str(entry) for entry in run_result.log)


@pytest.mark.asyncio
async def test_edit_image_seedream4_invalid_base64_payload_returns_error_with_handler():
    client = RecordingSeedreamClient(
        {
            "model": "dummy-model",
            "data": [{"b64_json": "not-base64!!!", "size": "64x64"}],
            "usage": {"generated_images": 1},
        }
    )

    @do
    def program():
        return (
            yield edit_image__seedream4(
                prompt="invalid image data",
                model="dummy-model",
                max_retries=1,
            )
        )

    run_result = await _run_with_handler(program(), {"seedream_client": client})
    assert run_result.is_err()
    assert isinstance(run_result.error, ValueError)
    assert "Failed to decode Seedream base64 image payload" in str(run_result.error)


@pytest.mark.asyncio
async def test_edit_image_seedream4_missing_data_field_returns_error_with_handler():
    client = RecordingSeedreamClient({"model": "dummy-model", "usage": {"generated_images": 1}})

    @do
    def program():
        return (
            yield edit_image__seedream4(
                prompt="missing fields",
                model="dummy-model",
                max_retries=1,
            )
        )

    run_result = await _run_with_handler(program(), {"seedream_client": client})
    assert run_result.is_err()
    assert isinstance(run_result.error, ValueError)
    assert "Seedream response did not include image data" in str(run_result.error)


@pytest.mark.asyncio
async def test_get_seedream_client_initializes_and_caches_via_with_handler():
    @do
    def program():
        first = yield get_seedream_client()
        second = yield get_seedream_client()
        cached_result = yield Try(Get("seedream_client"))
        return {
            "first": first,
            "second": second,
            "cached": cached_result.value if cached_result.is_ok() else None,
        }

    run_result = await _run_with_handler(
        program(),
        {
            "seedream_api_key": "fake-seedream-key",
            "seedream_base_url": "https://seedream.test/v3",
            "seedream_default_headers": {"X-Test": "seedream"},
        },
    )

    assert run_result.is_ok()
    first = run_result.value["first"]
    second = run_result.value["second"]
    assert isinstance(first, SeedreamClient)
    assert first is second
    assert run_result.value["cached"] is first
    assert first.api_key == "fake-seedream-key"
    assert first.base_url == "https://seedream.test/v3"
    assert dict(first.default_headers or {}) == {"X-Test": "seedream"}


@pytest.mark.asyncio
async def test_get_seedream_client_prefers_injected_client_with_handler():
    injected_client = SeedreamClient(api_key="injected-key", base_url="https://injected.test/v3")

    @do
    def program():
        resolved = yield get_seedream_client()
        cached_result = yield Try(Get("seedream_client"))
        return {
            "resolved": resolved,
            "cached": cached_result.value if cached_result.is_ok() else None,
        }

    run_result = await _run_with_handler(program(), {"seedream_client": injected_client})

    assert run_result.is_ok()
    assert run_result.value["resolved"] is injected_client
    # A pre-injected client returns early, so get_seedream_client does not write state.
    assert run_result.value["cached"] is None
