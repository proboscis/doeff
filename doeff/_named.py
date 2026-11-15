"""Functions made at run time get their names when they are made, not by writing the names after.

On the free-threaded build of CPython 3.14, writing ``__name__``, ``__qualname__``, ``__doc__`` or
``__module__`` of a function stalls the writer every few hundred writes until every other thread
reaches a point where it can be paused. A thread inside a long C call (a sort, a big JSON encode or
decode, a page read back from swap) is paused only when that call ends, so the writer waits for it.
doeff writes these names on the thread that runs the scheduler each time it makes an installer or a
``@do`` wrapper at run time (``on_raise``, ``absent_as``, a handler built per call), and that thread
then serves no task while it waits: on a turn host with 90 threads, a quarter to a half of the
scheduler thread's time went there, and one host event took about 10 seconds. Writes into the function's ``__dict__``, ``__wrapped__``,
``__annotate__`` and ``__type_params__`` do not stall, and neither does making a function with
``types.FunctionType`` under a name.

``named`` remakes a function under the names given: the code object is copied once with the names
(``co_name``, ``co_qualname`` and, for the documentation, the first constant) and kept, so the same
names cost one copy per process, and the module is the ``__name__`` of the globals the function is
made with. ``wrapped_as`` is ``functools.update_wrapper`` made the same way.

A function remade here is written with ``TEMPLATE_DOC`` as its docstring: the documentation it gets
takes the place of that constant. A function without it can get names but no documentation (under
``python -OO``, where docstrings are dropped, the documentation is ``None`` as for every function).
"""

import builtins
import dis
import functools
import inspect
import sys
from collections.abc import Callable
from dataclasses import dataclass
from types import CodeType, FunctionType
from typing import Any, Final, TypeVar

TEMPLATE_DOC: Final = "(documentation given at run time by doeff._named)"

_F = TypeVar("_F", bound=Callable[..., object])

# The flag that says the first constant is the docstring (CPython 3.14 and later; before it, a
# first constant that is a str is the docstring).
_HAS_DOCSTRING: Final[int] = getattr(inspect, "CO_HAS_DOCSTRING", 0)
# What ``functools.update_wrapper`` copies besides the names (``__annotate__`` and
# ``__type_params__`` on 3.14, ``__annotations__`` before it): these writes do not stall.
_OTHER_ASSIGNMENTS: Final = tuple(
    attr
    for attr in functools.WRAPPER_ASSIGNMENTS
    if attr not in ("__module__", "__name__", "__qualname__", "__doc__")
)


@dataclass(frozen=True)
class _KeptCode:
    """A copy of a code object under names: ``template`` is kept so its id is read only while it lives."""

    template: CodeType
    made: CodeType


# Copies kept, keyed by (id of the template code, name, qualname, documentation).
_CODES: dict[tuple[int, str, str, object], _KeptCode] = {}
_CODES_LIMIT: Final = 4096


@dataclass(frozen=True)
class _KeptReads:
    """The global reads of a code object: ``code`` is kept so its id is read only while it lives."""

    code: CodeType
    names: frozenset[str]


_GLOBAL_READS: dict[int, _KeptReads] = {}
# Globals that only name a module, for a function that reads no global (``named`` with ``module``).
_MODULE_GLOBALS: dict[str, dict[str, object]] = {}
_KEEP: Any = object()


def _named_code(code: CodeType, name: str, qualname: str, doc: object) -> CodeType:
    """The copy of ``code`` under the names and documentation, made once per process and kept."""
    key = (id(code), name, qualname, doc)
    kept = _CODES.get(key)
    if kept is not None and kept.template is code:
        return kept.made
    changes: dict[str, Any] = {"co_name": name, "co_qualname": qualname}
    if doc is not _KEEP:
        consts = code.co_consts
        if consts and consts[0] == TEMPLATE_DOC:
            if isinstance(doc, str):
                changes["co_consts"] = (doc, *consts[1:])
            elif _HAS_DOCSTRING:
                changes["co_flags"] = code.co_flags & ~_HAS_DOCSTRING
            else:
                changes["co_consts"] = (None, *consts[1:])
        elif sys.flags.optimize < 2:
            raise ValueError(
                f"named: {code.co_qualname} has no docstring TEMPLATE_DOC to give a documentation in"
            )
    made = code.replace(**changes)
    if len(_CODES) >= _CODES_LIMIT:
        _CODES.clear()
    _CODES[key] = _KeptCode(code, made)
    return made


def _global_reads(code: CodeType) -> frozenset[str]:
    """The names ``code`` reads as globals that are not builtins (a function reading none can be
    made with globals that only name its module)."""
    kept = _GLOBAL_READS.get(id(code))
    if kept is not None and kept.code is code:
        return kept.names
    reads = frozenset(
        str(instruction.argval)
        for instruction in dis.get_instructions(code)
        if instruction.opname == "LOAD_GLOBAL"
    )
    names = frozenset(name for name in reads if not hasattr(builtins, name))
    if len(_GLOBAL_READS) >= _CODES_LIMIT:
        _GLOBAL_READS.clear()
    _GLOBAL_READS[id(code)] = _KeptReads(code, names)
    return names


def _module_globals(function: FunctionType, module: str) -> dict[str, Any]:
    """The globals that make a function's ``__module__`` be ``module``: its own when they already
    do, else globals that only name the module (refused for a function that reads globals)."""
    if function.__globals__.get("__name__") == module:
        return function.__globals__
    if _global_reads(function.__code__):
        raise ValueError(
            f"named: {function.__qualname__} reads globals, so it cannot be made in module {module!r}"
        )
    found = _MODULE_GLOBALS.get(module)
    if found is None:
        found = {"__name__": module, "__builtins__": builtins}
        _MODULE_GLOBALS[module] = found
    return found


def named(
    function: _F,
    name: str,
    qualname: str | None = None,
    *,
    doc: object = _KEEP,
    module: str | None = None,
) -> _F:
    """``function`` made again under ``name`` and ``qualname`` (``name`` when not given), with
    ``doc`` as its documentation when given (its docstring must be ``TEMPLATE_DOC``) and in
    ``module`` when given (a function that reads no global). Defaults, closure, ``__dict__`` and
    annotations are the function's."""
    if type(function) is not FunctionType:
        raise TypeError(f"named: a function is made again, got {type(function).__name__}")
    made = FunctionType(
        _named_code(function.__code__, name, name if qualname is None else qualname, doc),
        function.__globals__ if module is None else _module_globals(function, module),
        name,
        function.__defaults__,
        function.__closure__,
    )
    if function.__kwdefaults__:
        made.__kwdefaults__ = dict(function.__kwdefaults__)
    for attr in _OTHER_ASSIGNMENTS:
        try:
            value = getattr(function, attr)
        except AttributeError:
            continue
        setattr(made, attr, value)
    made.__dict__.update(function.__dict__)
    return made  # type: ignore[return-value]  # a FunctionType made from function's own parts


def wrapped_as(wrapper: _F, wrapped: Callable[..., object]) -> _F:
    """``functools.update_wrapper(wrapper, wrapped)`` without writing the names: ``wrapper`` (a
    function whose docstring is ``TEMPLATE_DOC`` and that reads no global) made again under the
    names, documentation and module of ``wrapped``, with its other assignments, its ``__dict__``
    and ``__wrapped__``."""
    name = getattr(wrapped, "__name__", wrapper.__name__)
    made = named(
        wrapper,
        name,
        getattr(wrapped, "__qualname__", name),
        doc=getattr(wrapped, "__doc__", None),
        module=getattr(wrapped, "__module__", None) or wrapper.__module__,
    )
    for attr in _OTHER_ASSIGNMENTS:
        try:
            value = getattr(wrapped, attr)
        except AttributeError:
            continue
        setattr(made, attr, value)
    made.__dict__.update(getattr(wrapped, "__dict__", {}))
    made.__wrapped__ = wrapped  # type: ignore[attr-defined]  # as functools.update_wrapper sets it
    return made
