"""Tests for type-safe format DSL."""

from dataclasses import FrozenInstanceError

import pytest
from beartype.roar import BeartypeCallHintParamViolation

from doeff_omni_converter import F, ImageFormat


class TestImageFormat:
    """Tests for ImageFormat dataclass."""

    def test_basic_creation(self):
        """Test creating an ImageFormat with all fields."""
        fmt = ImageFormat(
            backend="torch",
            dtype="float32",
            arrangement="CHW",
            colorspace="RGB",
            value_range=(0.0, 1.0),
        )
        assert fmt.backend == "torch"
        assert fmt.dtype == "float32"
        assert fmt.arrangement == "CHW"
        assert fmt.colorspace == "RGB"
        assert fmt.value_range == (0.0, 1.0)

    def test_partial_creation(self):
        """Test creating ImageFormat with only required fields."""
        fmt = ImageFormat(backend="path")
        assert fmt.backend == "path"
        assert fmt.dtype is None
        assert fmt.arrangement is None

    def test_str_representation(self):
        """Test string representation."""
        fmt = ImageFormat("torch", "float32", "CHW", "RGB", (0.0, 1.0))
        assert str(fmt) == "torch,float32,CHW,RGB,0.0_1.0"

        simple_fmt = ImageFormat("path")
        assert str(simple_fmt) == "path"

    def test_hash_and_equality(self):
        """Test hashing and equality."""
        fmt1 = ImageFormat("torch", "float32", "CHW", "RGB", (0.0, 1.0))
        fmt2 = ImageFormat("torch", "float32", "CHW", "RGB", (0.0, 1.0))
        fmt3 = ImageFormat("numpy", "float32", "HWC", "RGB", (0.0, 255.0))

        assert fmt1 == fmt2
        assert fmt1 != fmt3
        assert hash(fmt1) == hash(fmt2)

        # Can be used in sets/dicts
        fmt_set = {fmt1, fmt2, fmt3}
        assert len(fmt_set) == 2

    def test_frozen(self):
        """Test that ImageFormat is immutable."""
        fmt = ImageFormat("torch")
        with pytest.raises(FrozenInstanceError):
            fmt.backend = "numpy"


class TestFormatFactory:
    """Tests for F format factory."""

    def test_torch_defaults(self):
        """Test F.torch() default values."""
        fmt = F.torch()
        assert fmt.backend == "torch"
        assert fmt.dtype == "float32"
        assert fmt.arrangement == "CHW"
        assert fmt.colorspace == "RGB"
        assert fmt.value_range == (0.0, 1.0)

    def test_torch_custom(self):
        """Test F.torch() with custom values."""
        fmt = F.torch("float16", "BCHW", "BGR", (0.0, 255.0))
        assert fmt.dtype == "float16"
        assert fmt.arrangement == "BCHW"
        assert fmt.colorspace == "BGR"
        assert fmt.value_range == (0.0, 255.0)

    def test_numpy_defaults(self):
        """Test F.numpy() default values."""
        fmt = F.numpy()
        assert fmt.backend == "numpy"
        assert fmt.dtype == "float32"
        assert fmt.arrangement == "HWC"
        assert fmt.value_range == (0.0, 255.0)

    def test_jax_defaults(self):
        """Test F.jax() default values."""
        fmt = F.jax()
        assert fmt.backend == "jax"
        assert fmt.arrangement == "BHWC"

    def test_pil(self):
        """Test F.pil()."""
        fmt = F.pil("RGBA")
        assert fmt.backend == "pil"
        assert fmt.colorspace == "RGBA"

    def test_singleton_formats(self):
        """Test singleton format shortcuts."""
        assert F.path.backend == "path"
        assert F.base64.backend == "base64"
        assert F.url.backend == "url"
        assert F.bytes_.backend == "bytes"


class TestBeartypeValidation:
    """Tests for beartype validation of ImageFormat."""

    def test_invalid_backend(self):
        """Test that invalid backend is rejected."""
        with pytest.raises(BeartypeCallHintParamViolation):
            ImageFormat(backend="invalid")

    def test_invalid_dtype(self):
        """Test that invalid dtype is rejected."""
        with pytest.raises(BeartypeCallHintParamViolation):
            ImageFormat(backend="torch", dtype="invalid")

    def test_invalid_arrangement(self):
        """Test that invalid arrangement is rejected."""
        with pytest.raises(BeartypeCallHintParamViolation):
            ImageFormat(backend="torch", arrangement="invalid")
