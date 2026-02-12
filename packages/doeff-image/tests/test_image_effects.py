# ruff: noqa: E402
"""Tests for unified image effects and result types."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from doeff_image import ImageEdit, ImageGenerate, ImageResult


def test_public_exports() -> None:
    from doeff_image.effects import ImageEdit as ImportedEdit
    from doeff_image.effects import ImageGenerate as ImportedGenerate
    from doeff_image.types import ImageResult as ImportedResult

    assert ImportedGenerate is ImageGenerate
    assert ImportedEdit is ImageEdit
    assert ImportedResult is ImageResult


def test_result_save_round_trip(tmp_path: Path) -> None:
    image = Image.new("RGB", (8, 8), "green")
    result = ImageResult(images=[image], model="seedream-4", prompt="green square")
    output_path = tmp_path / "image.png"
    result.save(str(output_path))
    assert output_path.exists()


def test_effect_defaults() -> None:
    generated = ImageGenerate(prompt="A calm beach", model="seedream-4")
    edited = ImageEdit(prompt="Add lighthouse", model="gemini-3-pro-image")
    assert generated.num_images == 1
    assert edited.images == []
    assert edited.strength == 0.8
