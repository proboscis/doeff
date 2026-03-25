"""
doeff traceback rendering.

Reads __doeff_traceback__ from exceptions and renders the doeff
active call chain format per SPEC-TRACE-001.

Data comes from walking the live fiber chain (GetExecutionContext).
Each entry is either:
  ["frame", func_name, source_file, source_line]
  ["handler", handler_name, [handler_names_in_scope...]]
  [func_name, source_file, source_line]  (legacy 3-tuple)
  {"kind": "spawn_boundary", ...}
"""

import linecache
import os


def format_default(exception):
    """Render doeff traceback from __doeff_traceback__ attribute.

    Returns formatted string or None if no traceback data.
    """
    tb_data = getattr(exception, '__doeff_traceback__', None)
    if not tb_data:
        return None

    lines = ["\ndoeff Traceback (most recent call last):\n"]

    # Frames are innermost-first from VM; reverse for outermost-first display
    for entry in reversed(tb_data):
        rendered = _render_entry(entry)
        if rendered:
            lines.append(rendered)

    # Exception info
    exc_type = type(exception).__name__
    exc_msg = str(exception)
    lines.append(f"\n{exc_type}: {exc_msg}\n")

    return "".join(lines)


def _render_entry(entry):
    """Render a single traceback entry."""
    if isinstance(entry, dict):
        return _render_dict_entry(entry)
    if isinstance(entry, (list, tuple)):
        return _render_list_entry(entry)
    return None


def _render_list_entry(entry):
    """Render a list-format traceback entry."""
    if len(entry) < 2:
        return None

    kind = entry[0]

    if kind == "frame" and len(entry) >= 4:
        func_name, source_file, source_line = entry[1], entry[2], entry[3]
        return _render_frame(func_name, source_file, source_line)

    if kind == "handler" and len(entry) >= 3:
        handler_name = entry[1]
        handler_chain = entry[2] if len(entry) > 2 else []
        return _render_handler(handler_name, handler_chain)

    # Legacy 3-tuple: [func_name, source_file, source_line]
    if len(entry) >= 3 and isinstance(entry[0], str) and isinstance(entry[2], (int, float)):
        func_name, source_file, source_line = entry[0], entry[1], entry[2]
        return _render_frame(func_name, source_file, source_line)

    return None


def _render_frame(func_name, source_file, source_line):
    """Render a program frame entry."""
    short_file = _short_path(source_file)
    source_text = _get_source_line(source_file, int(source_line))
    result = f"\n  {func_name}()  {short_file}:{source_line}"
    if source_text:
        result += f"\n    {source_text}"
    return result


def _render_handler(handler_name, handler_chain):
    """Render a handler boundary entry."""
    if isinstance(handler_chain, (list, tuple)) and handler_chain:
        names = ", ".join(str(n) for n in handler_chain)
        return f"\n    handlers: {names}"
    return f"\n    [handler: {handler_name}]"


def _render_dict_entry(entry):
    """Render a dict-format traceback entry."""
    kind = entry.get("kind", "")
    if kind == "spawn_boundary":
        task_id = entry.get("task_id", "?")
        spawn_site = entry.get("spawn_site", "")
        return f"\n  ── in task {task_id} (spawned at {spawn_site}) ──"
    return None


def _get_source_line(filename, lineno):
    """Read a source line from a file for display."""
    if not filename or lineno <= 0:
        return None
    try:
        line = linecache.getline(filename, lineno)
        if line:
            return line.strip()
    except Exception:
        pass
    return None


def _short_path(path):
    """Shorten a file path for display."""
    if not path:
        return "<unknown>"
    try:
        cwd = os.getcwd()
        if path.startswith(cwd):
            rel = os.path.relpath(path, cwd)
            if not rel.startswith(".."):
                return rel
    except (ValueError, OSError):
        pass
    try:
        home = os.path.expanduser("~")
        if path.startswith(home):
            return "~" + path[len(home):]
    except (ValueError, OSError):
        pass
    return path
