"""Gemini image effect aliases."""


from dataclasses import dataclass
from warnings import warn

from doeff_image import ImageEdit


@dataclass(frozen=True, kw_only=True)
class GeminiImageEdit(ImageEdit):
    """Deprecated Gemini-specific alias for :class:`doeff_image.ImageEdit`."""

    def __post_init__(self) -> None:
        warn(
            "GeminiImageEdit is deprecated; use doeff_image.ImageEdit instead.",
            DeprecationWarning,
            stacklevel=2,
        )


__all__ = ["GeminiImageEdit"]
