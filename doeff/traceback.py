"""
doeff traceback rendering.

Reads __doeff_traceback__ from exceptions and renders the doeff
active call chain format per SPEC-TRACE-001.

Data comes from walking the live fiber chain + Python __traceback__.
Each entry is either:
  ["frame", func_name, source_file, source_line]
  ["handler", handler_name, [handler_names_in_scope...]]
  {"kind": "spawn_boundary", ...}
"""

import linecache
import os


def format_default(exception):
    """Render doeff traceback from __doeff_traceback__ attribute."""
    tb_data = getattr(exception, '__doeff_traceback__', None)
    if not tb_data:
        return None

    lines = ["\ndoeff Traceback (most recent call last):\n"]

    last_handler_chain = None

    # Data is outermost-first (from Python extract_tb order)
    for entry in tb_data:
        if not isinstance(entry, (list, tuple)):
            if isinstance(entry, dict):
                rendered = _render_dict_entry(entry)
                if rendered:
                    lines.append(rendered)
            continue

        if len(entry) < 2:
            continue

        kind = entry[0]

        if kind == "frame" and len(entry) >= 4:
            func_name, source_file, source_line = entry[1], entry[2], int(entry[3])
            lines.append(_render_frame(func_name, source_file, source_line))
        elif kind == "handler" and len(entry) >= 3:
            handler_chain = entry[2] if len(entry) > 2 else []
            if isinstance(handler_chain, (list, tuple)) and handler_chain:
                rendered = _render_handler_chain(handler_chain)
                if rendered != last_handler_chain:
                    lines.append(rendered)
                    last_handler_chain = rendered
        elif len(entry) >= 3 and isinstance(entry[2], (int, float)):
            # Legacy [func_name, source_file, source_line]
            lines.append(_render_frame(entry[0], entry[1], int(entry[2])))

    # Exception info
    exc_type = type(exception).__name__
    exc_msg = str(exception)
    lines.append(f"\n\n{exc_type}: {exc_msg}\n")

    return "".join(lines)


def _render_frame(func_name, source_file, source_line):
    """Render a program frame."""
    short_file = _short_path(source_file)
    source_text = _get_source_line(source_file, source_line)
    result = f"\n  {func_name}()  {short_file}:{source_line}"
    if source_text:
        result += f"\n    {source_text}"
    return result


def _clean_handler_names(handler_chain):
    """Clean handler names, removing closure prefixes, deduplicating."""
    names = []
    for name in handler_chain:
        name = str(name)
        if ".<locals>." in name:
            name = name.split(".<locals>.")[0]
        names.append(name)
    deduped = []
    for n in names:
        if not deduped or deduped[-1] != n:
            deduped.append(n)
    return deduped


def _render_handler_chain(handler_chain):
    """Render handler chain with status markers per SPEC-TRACE-001.

    At error time, all handlers let the error propagate through (↗ passed).
    """
    names = _clean_handler_names(handler_chain)
    if not names:
        return ""
    lines = ["\n    handlers:"]
    for name in names:
        lines.append(f"\n      {name} ↗")
    return "".join(lines)


def _render_dict_entry(entry):
    """Render a dict-format traceback entry."""
    kind = entry.get("kind", "")
    if kind == "spawn_boundary":
        task_id = entry.get("task_id", "?")
        spawn_site = entry.get("spawn_site", "")
        return f"\n\n  ── in task {task_id} (spawned at {spawn_site}) ──"
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
