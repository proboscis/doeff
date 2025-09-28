"""
Utility functions for the doeff library.
"""

import os
import sys
from typing import Optional, TypeVar


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


def capture_creation_context(skip_frames: int = 2) -> Optional["EffectCreationContext"]:
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


E = TypeVar("E", bound="Effect")


def create_effect_with_trace(effect: E, skip_frames: int = 3) -> E:
    """Attach creation context metadata to an effect instance."""

    from doeff.types import Effect

    if not isinstance(effect, Effect):  # Defensive
        raise TypeError(f"Expected Effect, got {type(effect)!r}")

    created_at = capture_creation_context(skip_frames=skip_frames)
    return effect.with_created_at(created_at)


__all__ = [
    "DEBUG_EFFECTS",
    "capture_creation_context",
    "create_effect_with_trace",
]
