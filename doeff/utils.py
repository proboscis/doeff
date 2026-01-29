"""Utility helpers for the doeff library."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from typing import Any, TypeVar


class BoundedLog(list):
    """List-like buffer that keeps at most ``max_entries`` items."""

    __slots__ = ("_max_entries",)

    def __init__(
        self,
        iterable: Iterable[Any] | None = None,
        *,
        max_entries: int | None = None,
    ) -> None:
        super().__init__(iterable or [])
        self._max_entries: int | None = None
        self.set_max_entries(max_entries)

    @property
    def max_entries(self) -> int | None:
        """Return the maximum number of entries retained (``None`` means unbounded)."""

        return self._max_entries

    def set_max_entries(self, max_entries: int | None) -> None:
        """Update the retention limit and trim existing entries if required."""

        if max_entries is not None and max_entries < 0:
            raise ValueError("max_entries must be >= 0 or None")
        self._max_entries = max_entries
        self._trim()

    def append(self, item: Any) -> None:  # type: ignore[override]
        super().append(item)
        self._trim()

    def extend(self, iterable: Iterable[Any]) -> None:  # type: ignore[override]
        super().extend(iterable)
        self._trim()

    def __iadd__(self, iterable: Iterable[Any]):  # type: ignore[override]
        super().__iadd__(iterable)
        self._trim()
        return self

    def insert(self, index: int, item: Any) -> None:  # type: ignore[override]
        super().insert(index, item)
        self._trim()

    def copy(self) -> BoundedLog:  # type: ignore[override]
        """Return a shallow copy that preserves the retention limit."""

        return type(self)(self, max_entries=self._max_entries)

    def spawn_empty(self) -> BoundedLog:
        """Create an empty buffer with the same retention semantics."""

        return type(self)(max_entries=self._max_entries)

    def _trim(self) -> None:
        if self._max_entries is None:
            return
        overflow = len(self) - self._max_entries
        if overflow > 0:
            del self[:overflow]


def _is_site_package(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return "/site-packages/" in normalized


def _is_stdlib(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    if normalized.startswith("<"):
        return False
    return (
        "/lib/python" in normalized
        or "/frameworks/python.framework" in normalized
        or "/.local/share/uv/python" in normalized
    )


def _is_doeff_internal(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return "/doeff/" in normalized


def _is_user_frame(path: str) -> bool:
    if path.startswith("<"):
        return True
    return not (_is_site_package(path) or _is_stdlib(path) or _is_doeff_internal(path))

# Environment variable to control debug mode
DEBUG_EFFECTS = os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes")


def capture_creation_context(skip_frames: int = 2) -> EffectCreationContext | None:
    """
    Capture the current stack context for debugging effect creation.
    
    Args:
        skip_frames: Number of frames to skip (default 2 to skip this function and caller)
    
    Returns:
        EffectCreationContext with frame info (always captured, full stack if DEBUG enabled)
    """
    from doeff.types import EffectCreationContext

    try:
        # Always get the frame of the caller
        frame = sys._getframe(skip_frames)

        # Get basic info from the frame
        filename = frame.f_code.co_filename
        line = frame.f_lineno
        function = frame.f_code.co_name

        # Try to get the source code line
        code = None
        try:
            # Read the source line from the file if possible
            import linecache
            code = linecache.getline(filename, line).strip()
        except:
            pass

        # Collect stack frames so we can show where the effect originated.
        stack_data = []
        current_frame = frame.f_back
        depth = 0
        max_depth = 12 if DEBUG_EFFECTS else 8

        while current_frame and depth < max_depth:
            frame_filename = current_frame.f_code.co_filename
            frame_data = {
                "filename": frame_filename,
                "line": current_frame.f_lineno,
                "function": current_frame.f_code.co_name,
            }

            try:
                import linecache

                code_line = linecache.getline(frame_filename, current_frame.f_lineno)
                if code_line:
                    frame_data["code"] = code_line.strip()
            except Exception:  # pragma: no cover - best effort only
                pass

            if DEBUG_EFFECTS:
                frame_data["frame"] = current_frame

            stack_data.append(frame_data)
            depth += 1

            if not DEBUG_EFFECTS and _is_user_frame(frame_filename):
                break

            current_frame = current_frame.f_back

        return EffectCreationContext(
            filename=filename,
            line=line,
            function=function,
            code=code,
            stack_trace=stack_data,
            frame_info=frame  # Store the frame object
        )
    except:
        # If sys._getframe() is not available (e.g., in some Python implementations)
        return None


E = TypeVar("E", bound="EffectBase")


def create_effect_with_trace(effect: E, skip_frames: int = 3) -> E:
    """Attach creation context metadata to an effect instance."""

    from doeff.types import EffectBase

    if not isinstance(effect, EffectBase):  # Defensive
        raise TypeError(f"Expected EffectBase, got {type(effect)!r}")

    created_at = capture_creation_context(skip_frames=skip_frames)
    return effect.with_created_at(created_at)


__all__ = [
    "DEBUG_EFFECTS",
    "BoundedLog",
    "capture_creation_context",
    "create_effect_with_trace",
]
