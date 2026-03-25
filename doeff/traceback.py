"""
doeff traceback rendering.

Reads __doeff_traceback__ from exceptions and renders the doeff
active call chain format per SPEC-TRACE-001.
"""

import linecache
import os

_INTERNAL_PATHS = ('/doeff_core_effects/', '/doeff/do.py', '/doeff/run.py', '/doeff_vm/')


def format_default(exception):
    """Render doeff traceback from __doeff_traceback__ attribute."""
    tb_data = getattr(exception, '__doeff_traceback__', None)
    if not tb_data:
        return None

    lines = ["\ndoeff Traceback (most recent call last):\n"]

    # Group entries: each user frame followed by its handler chain
    awaiting_handlers = False  # True after a user frame, waiting for handler entry

    for entry in tb_data:
        if isinstance(entry, dict):
            rendered = _render_dict_entry(entry)
            if rendered:
                lines.append(rendered)
            awaiting_handlers = False
            continue

        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue

        kind = entry[0]

        if kind == "frame" and len(entry) >= 4:
            func_name, source_file, source_line = entry[1], entry[2], int(entry[3])
            if _is_internal_frame(source_file):
                continue
            lines.append(_render_frame(func_name, source_file, source_line))
            awaiting_handlers = True  # expect handler chain after this frame

        elif kind == "handler" and len(entry) >= 3:
            if awaiting_handlers:
                handler_chain = entry[2] if len(entry) > 2 else []
                if isinstance(handler_chain, (list, tuple)) and handler_chain:
                    lines.append(_render_handler_chain(handler_chain))
                awaiting_handlers = False  # only show once per frame

        # Legacy 3-tuple
        elif len(entry) >= 3 and isinstance(entry[2], (int, float)):
            func_name, source_file, source_line = entry[0], entry[1], int(entry[2])
            if not _is_internal_frame(source_file):
                lines.append(_render_frame(func_name, source_file, source_line))
                awaiting_handlers = True

    exc_type = type(exception).__name__
    exc_msg = str(exception)
    lines.append(f"\n\n{exc_type}: {exc_msg}\n")

    return "".join(lines)


def _is_internal_frame(source_file):
    return any(p in source_file for p in _INTERNAL_PATHS)


def _render_frame(func_name, source_file, source_line):
    short_file = _short_path(source_file)
    source_text = _get_source_line(source_file, source_line)
    result = f"\n  {func_name}()  {short_file}:{source_line}"
    if source_text:
        result += f"\n    {source_text}"
    return result


def _clean_handler_names(handler_chain):
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
    names = _clean_handler_names(handler_chain)
    if not names:
        return ""
    lines = ["\n    handlers:"]
    for name in names:
        lines.append(f"\n      {name} ↗")
    return "".join(lines)


def _render_dict_entry(entry):
    kind = entry.get("kind", "")
    if kind == "spawn_boundary":
        task_id = entry.get("task_id", "?")
        spawn_site = entry.get("spawn_site", "")
        if spawn_site:
            return f"\n\n  ── in task {task_id} (spawned at {spawn_site}) ──"
        return f"\n\n  ── in task {task_id} ──"
    return None


def _get_source_line(filename, lineno):
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
