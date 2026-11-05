"""Collect the Python code objects that started running during an expansion (compile) — so that the macro record's digest covers, among the closure's functions, only the code of those that ran during that module's
expansion (:mod:`doeff_hy_bytecode_guard._macro_use` —
a change to binding_forms._Walker._assign, which defk expansion never calls, used to make every defk user go cold).

How it collects: sys.monitoring (Python 3.12+) PY_START (a function starts running) and PY_RESUME (a generator resumes — a generator
started before the collection was opened) are received only while a collection is open. A received code is added to every open collection, and the event for that code location is
turned off (DISABLE — no cost from the second time on for the same code). Each time a collection is opened, the disabled events are restored (restart_events), so code disabled during an earlier
collection is reported once more to the new collection. Threads are not separated — execution in other threads also goes into every open collection
(collecting more only makes the record more likely to be judged stale). Nested collections (a require inside an expansion compiles another Hy module) work the same way:
execution during an inner collection also goes into the outer collection.

In environments that cannot collect (Python 3.11 and earlier, or no free sys.monitoring tool id), :func:`start` returns :class:`Uncollectable`, and
the record covers all the code of the closure's functions (the previous behavior).
"""

import sys
import threading
from dataclasses import dataclass
from types import CodeType

#: Candidate sys.monitoring tool ids — 0, 1, 2 and 5 are ids named for debugger, coverage, profiler and optimizer, so only the unnamed
#: 3 and 4 are used.
_TOOL_CANDIDATES = (3, 4)
_TOOL_NAME = "doeff-hy-called-code"

_LOCK = threading.Lock()

#: Tuple of the set of open collections (a single field — opening and closing swap the whole tuple under a lock; the event callback reads it without taking the lock).
_OPEN: list[tuple[set[CodeType], ...]] = [()]


@dataclass(frozen=True)
class Uncollectable:
    """Collection is impossible in this process (a reason string — no sys.monitoring, or no free tool id)."""

    reason: str


#: Decision of the tool id to use (one "tool" key — decided when a collection is first opened, and not changed for the life of the process).
_TOOL: dict[str, "int | Uncollectable"] = {}


class CalledCode:
    """One open collection — :meth:`codes` returns the code collected so far, and :meth:`close` closes the collection (does nothing from the second call)."""

    def __init__(self, tool: int) -> None:
        self._tool = tool
        self._codes: set[CodeType] = set()

    def codes(self) -> frozenset[CodeType]:
        """The code collected so far (readable even before closing — code collected after reading is not included)."""
        return frozenset(self._codes)

    def reopen(self) -> None:
        """Reopen a closed collection (keeps the collected code and continues adding — does nothing while open)."""
        with _LOCK:
            if not any(codes is self._codes for codes in _OPEN[0]):
                self._open()

    def close(self) -> None:
        """Close the collection — when no open collection remains, stop receiving events."""
        with _LOCK:
            remaining = tuple(codes for codes in _OPEN[0] if codes is not self._codes)
            if len(remaining) == len(_OPEN[0]):
                return
            _OPEN[0] = remaining
            if not remaining:
                sys.monitoring.set_events(self._tool, 0)

    def _open(self) -> None:
        """Open a collection (call under the lock) — restores the events disabled so far, so code disabled during an earlier collection is reported again."""
        first = not _OPEN[0]
        _OPEN[0] = (*_OPEN[0], self._codes)
        if first:
            events = sys.monitoring.events
            sys.monitoring.set_events(self._tool, events.PY_START | events.PY_RESUME)
        sys.monitoring.restart_events()


def start() -> CalledCode | Uncollectable:
    """Open a collection (in an environment that cannot collect, :class:`Uncollectable` — the caller covers all the code of the closure's functions)."""
    with _LOCK:
        match _tool():
            case int() as tool:
                called = CalledCode(tool)
                called._open()
                return called
            case Uncollectable() as uncollectable:
                return uncollectable


def _started(code: CodeType, offset: int) -> object:
    """A sys.monitoring event — add the code to every open collection and disable the event for this location."""
    for codes in _OPEN[0]:
        codes.add(code)
    return sys.monitoring.DISABLE


def _tool() -> "int | Uncollectable":
    """The tool id to use (take a free id the first time and install the event callback — call under the lock)."""
    known = _TOOL.get("tool")
    if known is None:
        known = _TOOL.setdefault("tool", _take_tool())
    return known


def _take_tool() -> "int | Uncollectable":
    """Take a free tool id and install the event callback."""
    monitoring = getattr(sys, "monitoring", None)
    if monitoring is None:
        return Uncollectable(
            f"no sys.monitoring (Python {sys.version_info[0]}.{sys.version_info[1]})"
        )
    for candidate in _TOOL_CANDIDATES:
        if monitoring.get_tool(candidate) is not None:
            continue
        try:
            monitoring.use_tool_id(candidate, _TOOL_NAME)
        except ValueError:  # another tool took it first
            continue
        monitoring.register_callback(candidate, monitoring.events.PY_START, _started)
        monitoring.register_callback(candidate, monitoring.events.PY_RESUME, _started)
        return candidate
    taken = ", ".join(f"{index}={monitoring.get_tool(index)}" for index in _TOOL_CANDIDATES)
    return Uncollectable(f"no free sys.monitoring tool id ({taken})")
