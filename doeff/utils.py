"""
Utility functions for the doeff library.
"""

import os
import sys
from typing import Optional, TypeVar

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

        # Collect stack frames for deeper context if DEBUG is enabled
        stack_data = []
        if DEBUG_EFFECTS:
            current_frame = frame.f_back
            depth = 0
            max_depth = 10  # Limit stack depth to avoid too much data

            while current_frame and depth < max_depth:
                frame_data = {
                    "filename": current_frame.f_code.co_filename,
                    "line": current_frame.f_lineno,
                    "function": current_frame.f_code.co_name,
                    "frame": current_frame  # Store the frame object itself
                }
                # Try to get code for this frame too
                try:
                    import linecache
                    frame_data["code"] = linecache.getline(
                        current_frame.f_code.co_filename,
                        current_frame.f_lineno
                    ).strip()
                except:
                    pass

                stack_data.append(frame_data)
                current_frame = current_frame.f_back
                depth += 1

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
