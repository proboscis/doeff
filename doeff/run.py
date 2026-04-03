"""
run(doexpr) — execute a DoExpr program to completion.
"""

import sys

from doeff_vm import PyVM


def run(doexpr):
    """Run a DoExpr program to completion and return the result.

    On error, enriches the exception with __doeff_traceback__ (from Python's
    __traceback__) and prints the doeff traceback to stderr.
    """
    vm = PyVM()
    try:
        return vm.run(doexpr)
    except Exception as e:
        _enrich_and_print(e)
        raise


def _enrich_and_print(exception):
    """Enrich exception with doeff traceback and print to stderr."""
    try:
        if hasattr(exception, '__doeff_traceback__'):
            # VM already set __doeff_traceback__ (e.g. handler chain),
            # but pure Python frames from __traceback__ may be missing.
            _merge_python_frames(exception)
        else:
            _enrich_exception_traceback(exception)
        from doeff.traceback import format_default
        tb = format_default(exception)
        if tb is not None:
            print(tb, file=sys.stderr)
    except Exception:
        pass  # don't mask the original error


def _merge_python_frames(exc):
    """Merge pure Python __traceback__ frames into existing __doeff_traceback__.

    When the Rust VM sets __doeff_traceback__ (e.g. handler chain entries),
    pure Python call frames from __traceback__ are lost. Extract them and
    insert before the handler chain entry.
    """
    import traceback as tb_mod
    tb = exc.__traceback__
    if tb is None:
        return
    py_frames = []
    for fs in tb_mod.extract_tb(tb):
        fn = fs.filename
        if any(p in fn for p in ('/doeff_vm/', '/doeff/do.py', '/doeff/run.py',
                                  '/doeff_core_effects/')):
            continue
        py_frames.append([fs.name, fs.filename, fs.lineno])
    if not py_frames:
        return
    existing = exc.__doeff_traceback__
    # Check which frames are already present
    existing_names = set()
    for entry in existing:
        if isinstance(entry, (list, tuple)) and len(entry) >= 3:
            if entry[0] == "frame":
                existing_names.add(entry[1])
            elif isinstance(entry[2], (int, float)):
                existing_names.add(entry[0])
    new_frames = [f for f in py_frames if f[0] not in existing_names]
    if not new_frames:
        return
    # Insert before handler chain entries (at the end of frame entries)
    insert_idx = 0
    for i, entry in enumerate(existing):
        if isinstance(entry, (list, tuple)) and len(entry) >= 2 and entry[0] == "handler":
            insert_idx = i
            break
    else:
        insert_idx = len(existing)
    for j, frame in enumerate(new_frames):
        existing.insert(insert_idx + j, frame)


def _enrich_exception_traceback(exc):
    """Extract doeff-relevant frames from Python's __traceback__."""
    import traceback as tb_mod
    tb = exc.__traceback__
    if tb is None:
        return
    frames = []
    for fs in tb_mod.extract_tb(tb):
        fn = fs.filename
        name = fs.name
        # Skip doeff VM/framework internals
        if any(p in fn for p in ('/doeff_vm/', '/doeff/do.py', '/doeff/run.py',
                                  '/doeff_core_effects/')):
            continue
        frames.append([fs.name, fs.filename, fs.lineno])
    if frames:
        exc.__doeff_traceback__ = frames
