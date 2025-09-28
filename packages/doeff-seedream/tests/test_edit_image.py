from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest
from PIL import Image

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from doeff import ExecutionContext, ProgramInterpreter, do

from doeff_seedream import SeedreamClient, edit_image__seedream4


class DummySeedreamClient(SeedreamClient):
    async def a_generate_images(self, payload, *, timeout=None, headers=None):  # type: ignore[override]
        del payload, timeout, headers
        encoded = base64.b64encode(b"dummy-image-bytes").decode("ascii")
        return {
            "model": "dummy",
            "data": [
                {
                    "b64_json": encoded,
                    "size": "64x64",
                }
            ],
            "usage": {"generated_images": 1},
        }


@do
def _program():
    result = yield edit_image__seedream4(
        prompt="A test prompt",
        model="dummy-model",
        images=[Image.new("RGB", (16, 16), color="red")],
        generation_config_overrides={"response_format": "b64_json"},
    )
    return result


@pytest.mark.asyncio
async def test_edit_image_seedream4_decodes_payload():
    engine = ProgramInterpreter()
    context = ExecutionContext(
        env={
            "seedream_client": DummySeedreamClient(api_key="test-key"),
            "seedream_cost_per_image_usd": 0.05,
        }
    )
    run_result = await engine.run(_program(), context=context)
    assert run_result.is_ok
    value = run_result.value
    assert value.image_bytes == b"dummy-image-bytes"
    assert value.images[0].size == "64x64"
    shared_state = run_result.shared_state
    assert shared_state["seedream_total_cost_usd"] == pytest.approx(0.05)
    assert shared_state["seedream_cost_dummy-model"] == pytest.approx(0.05)
    calls = shared_state["seedream_api_calls"]
    assert calls and calls[-1]["total_cost"] == pytest.approx(0.05)
    assert any("estimated cost" in str(entry) for entry in run_result.log)
