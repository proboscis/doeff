"""A workaround that replaces the `inspect.stack` called by Hy's require family with a light version that holds only frames.

Upstream Hy 1.3.1 uses only `inspect.stack()[n][0]` (the caller's frame) inside `hy.compiler` and `hy.macros`,
but `inspect.stack` reads the file name, line, and source context of every frame, so even when loading from bytecode
each require-family call is slow (5.4 seconds to import one agora-controllers simulation test file).
Only when called from Hy, it hands over a light version that returns a list of tuples with the frame as the first element. Calls from anything other than Hy
go to the original function as is.

This module imports no other part of doeff (so that it can be loaded alone even on top of PyPI Hy).
"""
import inspect
import sys

# Modules that call inspect.stack in Hy 1.3.1. All use only the `[n][0]` form (the responsibility table of #1293).
_HY_CALLER_MODULES = frozenset({"hy.compiler", "hy.macros"})

_original_stack = inspect.stack


def _light_stack(context=1):
    caller = sys._getframe(1)
    if caller.f_globals.get("__name__") not in _HY_CALLER_MODULES:
        return _original_stack(context)
    frames = []
    frame = caller
    while frame is not None:
        frames.append((frame,))
        frame = frame.f_back
    return frames


def install():
    """Install the replacement. If already installed, do nothing (do not wrap twice)."""
    if inspect.stack is not _light_stack:
        inspect.stack = _light_stack


def uninstall():
    """Remove the replacement and restore the original `inspect.stack`."""
    if inspect.stack is _light_stack:
        inspect.stack = _original_stack
