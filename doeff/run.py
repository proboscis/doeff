"""
run(doexpr) — execute a DoExpr program to completion.
"""

import sys
from typing import TYPE_CHECKING, Any, Never, TypeVar

from doeff_vm import PyVM
from doeff_vm._not_a_program import uncalled_do_function_call

if TYPE_CHECKING:
    from doeff import Program

_T = TypeVar("_T")


def run(doexpr: "Program[_T, Any]", *extra_args: Never, **extra_kwargs: Never) -> _T:
    """Run a DoExpr program to completion and return the result.

    On error, enriches the exception with __doeff_traceback__ (from Python's
    __traceback__) and prints the doeff traceback to stderr.

    Given a @do function itself (``run(main)``), raises a TypeError that says
    to call it first (``run(main())``). The check runs only after the VM
    rejected the value, so a program pays nothing for it.

    Given more than the program (``run(prog, env={...})``, the old ``handlers=``),
    raises a TypeError that says to install handlers by calling them on the program,
    before the program starts. ``Never`` keeps the extra arguments a type error too.
    """
    if extra_args or extra_kwargs:
        raise TypeError(_extra_arguments_message(len(extra_args), tuple(extra_kwargs)))
    vm = PyVM()
    try:
        return vm.run(doexpr)
    except Exception as e:
        call = uncalled_do_function_call(doexpr)
        if call is not None:
            raise TypeError(
                "run() got a @do function itself, not a program. "
                f"Call the function to build the program, then run that: run({call})"
            ) from None
        _enrich_and_print(e)
        raise


def _extra_arguments_message(positional_count: int, keywords: tuple[str, ...]) -> str:
    """The TypeError line for ``run`` given arguments besides the program."""
    positional = (
        (f"{positional_count} more positional argument{'s' if positional_count > 1 else ''}",)
        if positional_count
        else ()
    )
    extras = ", ".join((*positional, *(f"{key}=" for key in keywords)))
    return (
        f"run() takes only the program, not {extras}. "
        "Install each handler by calling it on the program: run(reader(env={...})(prog))"
    )


def _enrich_and_print(exception):
    """Enrich exception with doeff traceback and print to stderr."""
    try:
        if hasattr(exception, "__doeff_traceback__"):
            # VM already set __doeff_traceback__ (e.g. handler chain),
            # but pure Python frames from __traceback__ may be missing.
            _merge_python_frames(exception)
        else:
            _enrich_exception_traceback(exception)
        from doeff.traceback import format_default
        tb = format_default(exception)
        if tb is not None:
            sys.stderr.write(f"{tb}\n")
    except Exception:
        pass  # don't mask the original error


def _merge_python_frames(exc):  # noqa: PLR0912 - baseline cleanup keeps existing control flow unchanged
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
    for fs in tb_mod.StackSummary.extract(tb_mod.walk_tb(tb), lookup_lines=False):
        fn = fs.filename
        if any(p in fn for p in ("/doeff_vm/", "/doeff/do.py", "/doeff/run.py",
                                  "/doeff_core_effects/")):
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
    for fs in tb_mod.StackSummary.extract(tb_mod.walk_tb(tb), lookup_lines=False):
        fn = fs.filename
        # Skip doeff VM/framework internals
        if any(p in fn for p in ("/doeff_vm/", "/doeff/do.py", "/doeff/run.py",
                                  "/doeff_core_effects/")):
            continue
        frames.append([fs.name, fs.filename, fs.lineno])
    if frames:
        exc.__doeff_traceback__ = frames
