"""
Type-safe format DSL for doeff-omni-converter.

This module provides structured, beartype-validated types for image formats
replacing string-based formats with IDE-friendly, type-safe alternatives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from beartype import beartype

# Type aliases for format components
Backend = Literal["numpy", "torch", "jax", "pil", "bytes", "path", "base64", "url"]
DType = Literal[
    "uint8", "int8", "int16", "int32", "int64", "float16", "float32", "float64", "bfloat16"
]
Arrangement = Literal["HW", "HWC", "CHW", "BHWC", "BCHW"]
ColorSpace = Literal["L", "RGB", "BGR", "RGBA", "LAB", "YCbCr"]

# Generic Format type alias for extensibility
Format = Any  # Can be ImageFormat or any hashable format identifier


@beartype
@dataclass(frozen=True)
class ImageFormat:
    """
    Type-safe image format specification.

    Benefits over string-based formats:
    - IDE autocomplete for all fields
    - Typos caught at call site by beartype, not deep in A* search
    - Explicit, readable format specifications

    Example:
        >>> fmt = ImageFormat("torch", "float32", "CHW", "RGB", (0.0, 1.0))
        >>> print(fmt)  # torch,float32,CHW,RGB,0.0_1.0
    """

    backend: Backend
    dtype: DType | None = None
    arrangement: Arrangement | None = None
    colorspace: ColorSpace | None = None
    value_range: tuple[float, float] | None = None

    def __str__(self) -> str:
        """Convert to string representation for compatibility."""
        parts = [self.backend]
        if self.dtype:
            parts.append(self.dtype)
        if self.arrangement:
            parts.append(self.arrangement)
        if self.colorspace:
            parts.append(self.colorspace)
        if self.value_range:
            parts.append(f"{self.value_range[0]}_{self.value_range[1]}")
        return ",".join(parts)

    def __repr__(self) -> str:
        return f"ImageFormat({self!s})"

    def __hash__(self) -> int:
        return hash((self.backend, self.dtype, self.arrangement, self.colorspace, self.value_range))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ImageFormat):
            return (
                self.backend == other.backend
                and self.dtype == other.dtype
                and self.arrangement == other.arrangement
                and self.colorspace == other.colorspace
                and self.value_range == other.value_range
            )
        return NotImplemented


class F:
    """
    Format factory shortcuts for common image formats.

    Provides convenient constructors with sensible defaults for each backend.

    Example:
        >>> tensor_fmt = F.torch()  # torch,float32,CHW,RGB,0.0_1.0
        >>> numpy_fmt = F.numpy()   # numpy,float32,HWC,RGB,0.0_255.0
        >>> F.path                  # path (no extra attributes)
    """

    @staticmethod
    def torch(
        dtype: DType = "float32",
        arr: Arrangement = "CHW",
        color: ColorSpace = "RGB",
        value_range: tuple[float, float] = (0.0, 1.0),
    ) -> ImageFormat:
        """Create a PyTorch tensor format."""
        return ImageFormat("torch", dtype, arr, color, value_range)

    @staticmethod
    def numpy(
        dtype: DType = "float32",
        arr: Arrangement = "HWC",
        color: ColorSpace = "RGB",
        value_range: tuple[float, float] = (0.0, 255.0),
    ) -> ImageFormat:
        """Create a NumPy array format."""
        return ImageFormat("numpy", dtype, arr, color, value_range)

    @staticmethod
    def jax(
        dtype: DType = "float32",
        arr: Arrangement = "BHWC",
        color: ColorSpace = "RGB",
        value_range: tuple[float, float] = (0.0, 1.0),
    ) -> ImageFormat:
        """Create a JAX array format."""
        return ImageFormat("jax", dtype, arr, color, value_range)

    @staticmethod
    def pil(
        color: ColorSpace = "RGB",
    ) -> ImageFormat:
        """Create a PIL Image format."""
        return ImageFormat("pil", colorspace=color)

    # Singleton formats for simple types
    path = ImageFormat("path")
    base64 = ImageFormat("base64")
    url = ImageFormat("url")
    bytes_ = ImageFormat("bytes")


__all__ = [
    "Arrangement",
    "Backend",
    "ColorSpace",
    "DType",
    "F",
    "Format",
    "ImageFormat",
]
