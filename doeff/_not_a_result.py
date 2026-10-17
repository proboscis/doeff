"""What was meant by an effect, a program or a Result used where its result was meant.

``count = Get("counter")`` holds the effect, not the value it gives; ``x = child()`` holds the
program, not what it returns; ``r = yield Try(child())`` holds Ok or Err, not the value in it.
Using one as a value fails with Python's TypeError, which names only the type
(``unsupported operand type(s) for +: 'Get' and 'int'``). ``run`` adds a note to such an error
that names the variable and the fix.

The note reads, frame by frame from where the error was raised, the expression that failed and
the values its names hold. It is built only for an error that reached ``run``, so a program
pays nothing for it.
"""

import ast
import linecache
import re
from types import FrameType, TracebackType

from doeff_vm import Call, EffectBase, Err, Ok

from doeff.traceback import _is_internal

# A repr longer than this is shown as the type and (...).
_SHOWN_REPR_LIMIT = 60


def not_a_result_note(error: BaseException) -> str | None:
    """The note for a TypeError raised by using an effect, a program or a Result as a value;
    None for any other error."""
    if not isinstance(error, TypeError):
        return None
    message = str(error)
    for entry in _entries_innermost_first(error.__traceback__):
        frame = entry.tb_frame
        if frame.f_code.co_name == "<module>" or _is_internal(frame.f_code.co_filename):
            continue
        names = _names_in_failing_expression(entry)
        held = [
            (name, value)
            for name, value in frame.f_locals.items()
            if (names is None or name in names) and _named_in(message, value)
        ]
        # Without the expression, a value is the cause only when no other one could be.
        if names is None and len(held) > 1:
            continue
        notes = [note for name, value in held if (note := _note_for(name, value)) is not None]
        if notes:
            return notes[0]
    return None


def _entries_innermost_first(traceback: TracebackType | None) -> tuple[TracebackType, ...]:
    """The frames an error passed through, from the one that raised it outwards — the cause is
    looked for where the error happened first."""
    entries: tuple[TracebackType, ...] = ()
    while traceback is not None:
        entries = (traceback, *entries)
        traceback = traceback.tb_next
    return entries


def _names_in_failing_expression(entry: TracebackType) -> set[str] | None:
    """The variable names in the expression the frame was running, so that the note names the
    variable the error is about, not another one of the same type; None when the source cannot
    be read."""
    source = _failing_source(entry.tb_frame, entry.tb_lasti)
    if source is None:
        return None
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except SyntaxError:
        # Hy source, or a part of a statement: the words that read as names, mangled as Hy
        # mangles them (my-count is my_count).
        return {word.replace("-", "_") for word in re.findall(r"[^\W\d][\w-]*", source)}
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _failing_source(frame: FrameType, lasti: int) -> str | None:
    """The source text of the instruction ``lasti`` of the frame (the span Python's traceback
    marks with ^), or None when it cannot be read."""
    positions = list(frame.f_code.co_positions())
    if not 0 <= lasti // 2 < len(positions):
        return None
    line, end_line, column, end_column = positions[lasti // 2]
    if line is None or end_line is None or column is None or end_column is None:
        return None
    lines = tuple(
        linecache.getline(frame.f_code.co_filename, number).encode()
        for number in range(line, end_line + 1)
    )
    if not all(lines):
        return None
    # The columns are byte offsets in the first and the last line.
    end = sum(len(text) for text in lines[:-1]) + end_column
    return b"".join(lines)[column:end].decode(errors="replace")


def _named_in(message: str, value: object) -> bool:
    """Whether the message names the type of the value — the error is about such a value."""
    name = type(value).__name__
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", message) is not None


def _note_for(name: str, value: object) -> str | None:
    """The note for the variable ``name`` holding ``value``, when the value is an effect, a
    program or a Result; None for any other value."""
    # doeff imports run, which imports this module: DoExpr is read when a note is built.
    from doeff import DoExpr

    if isinstance(value, EffectBase):
        shown = _shown(value)
        return (
            f"{name} is the effect {shown}, not its result. "
            f"Yield the effect to get its result: {name} = yield {shown}"
        )
    if isinstance(value, (Ok, Err)):
        return (
            f"{name} is {_shown(value)}, a Result of Try (Ok or Err), not the value in it. "
            f"Take the value out with match {name}: case Ok(value) / case Err(error), "
            "or yield the program without Try to get the value itself."
        )
    call = _call_of(value) if isinstance(value, Call) else None
    if call is not None:
        return (
            f"{name} is the program {call} builds, not its result. "
            f"Yield the program to run it and get its result: {name} = yield {call}"
        )
    if isinstance(value, DoExpr):
        return (
            f"{name} is a program ({type(value).__name__}), not its result. "
            f"Yield the program to run it and get its result: value = yield {name}"
        )
    return None


def _call_of(call: Call) -> str | None:
    """The call that built the program, as the note shows it in the fix — ``child()``, or
    ``child(...)`` when it was given arguments."""
    function_name = getattr(call.function.function, "__name__", None)
    if function_name is None:
        return None
    return f"{function_name}(...)" if call.args or call.kwargs else f"{function_name}()"


def _shown(value: object) -> str:
    """The value as the note writes it in the fix: its repr when that reads as code and is
    short, else the type and (...)."""
    shown = repr(value)
    if len(shown) > _SHOWN_REPR_LIMIT or shown.startswith("<"):
        return f"{type(value).__name__}(...)"
    return shown
