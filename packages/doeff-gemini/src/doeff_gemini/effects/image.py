"""Gemini image effect aliases."""

from __future__ import annotations

from dataclasses import dataclass
from warnings import warn

from doeff_image.effects import ImageEdit


@dataclass(frozen=True, kw_only=True)
class GeminiImageEdit(ImageEdit):
    """Deprecated Gemini-specific alias for :class:`doeff_image.effects.ImageEdit`."""

    def __post_init__(self) -> None:
        warn(
            "GeminiImageEdit is deprecated; use doeff_image.effects.ImageEdit instead.",
            DeprecationWarning,
            stacklevel=2,
        )


__all__ = ["GeminiImageEdit"]
