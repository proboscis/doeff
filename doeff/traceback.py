"""
doeff traceback rendering.

Reads __doeff_traceback__ from exceptions and renders the doeff
active call chain format per SPEC-TRACE-001.
"""

import os


def format_default(exception):
    """Render doeff traceback from __doeff_traceback__ attribute.

    Returns formatted string or None if no traceback data.
    """
    tb_data = getattr(exception, '__doeff_traceback__', None)
    if not tb_data:
        return None

    lines = ["\ndoeff Traceback (most recent call last):\n"]

    # Frames are innermost-first from GetExecutionContext; reverse for display
    for frame in reversed(tb_data):
        if isinstance(frame, (list, tuple)) and len(frame) >= 3:
            func_name, source_file, source_line = frame[0], frame[1], frame[2]
            short_file = _short_path(source_file)
            lines.append(f"\n  {func_name}()  {short_file}:{source_line}")
        elif isinstance(frame, dict):
            kind = frame.get("kind", "program")
            if kind == "spawn_boundary":
                task_id = frame.get("task_id", "?")
                spawn_site = frame.get("spawn_site", "")
                lines.append(f"\n  ── in task {task_id} (spawned at {spawn_site}) ──")
            else:
                func_name = frame.get("function_name", "?")
                source_file = frame.get("source_file", "")
                source_line = frame.get("source_line", 0)
                short_file = _short_path(source_file)
                lines.append(f"\n  {func_name}()  {short_file}:{source_line}")

    # Exception info
    exc_type = type(exception).__name__
    exc_msg = str(exception)
    lines.append(f"\n\n{exc_type}: {exc_msg}\n")

    return "".join(lines)


def _short_path(path):
    """Shorten a file path for display."""
    if not path:
        return "<unknown>"
    # Try to make relative to cwd
    try:
        cwd = os.getcwd()
        if path.startswith(cwd):
            rel = os.path.relpath(path, cwd)
            if not rel.startswith(".."):
                return rel
    except (ValueError, OSError):
        pass
    # Try to shorten home directory
    try:
        home = os.path.expanduser("~")
        if path.startswith(home):
            return "~" + path[len(home):]
    except (ValueError, OSError):
        pass
    return path
