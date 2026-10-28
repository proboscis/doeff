"""The keeper of a session's event record (``LaunchEffect.event_record`` — agora card ki-31b41eac5071).

A caller of the headless adapter wrote its record of a turn (the agora answer ledger the PreToolUse hook reads)
only when its page reads (``EventsEffect``) caught up with the runtime, and those reads lagged seconds to minutes
behind the runtime's reader thread on a busy host. The keeper folds each item as the runtime reads it — on the
runtime's reader thread, through ``ClaudeStartTurn.tap`` — and on the caller's ``FollowUpEffect`` before the
input reaches the runtime, so the record does not wait for the page reads.

The keeper holds the state behind a lock (the reader thread and the handler's own clauses both fold) and replaces
the file with the text a fold answers: written to a sibling first and moved over the file, so a reader never sees
half a file. A write that fails removes the file (a reader then finds no record rather than a stale one). A copy or
fold that raises drops the item and keeps the state — the reader thread must not stop.

Those drops are not silent (agora card ki-d59018b840f9): the keeper counts, per turn, the items the tap brought and
the copies, folds and writes that failed, and the adapter settles each turn once when it closes the turn
(``settle``). A turn whose pages carried text or tool calls while the tap brought nothing — a handler between the
adapter and the runtime rebuilt ``ClaudeStartTurn`` without its tap (agora card ki-3f17ef920d2b) — or a turn with
any failure is logged as one warning naming the session, the turn and the record's path.
"""

import contextlib
import logging
import os
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from doeff_agents.effects.agent import EventRecord, RecordStep

LOGGER = logging.getLogger("doeff_agents.event_record")


@dataclass(frozen=True)
class Failure:
    """The failures of one kind (``copy``, ``fold`` or ``write``) in a turn's tally: how many and the first reason."""

    kind: str
    count: int
    first: str


class RecordKeeper:
    """Folds items into one session's event record and keeps its file current (see the module docstring)."""

    def __init__(self, record: EventRecord, name: str = "") -> None:
        self._lock = threading.Lock()
        self._path = record.path
        self._fold = record.fold
        self._state = record.state
        self._name = name
        # The turn's tally since the last settle: items the tap brought, and the first reason and count of each failure.
        self._tapped = 0
        self._failures: tuple[Failure, ...] = ()

    def take(self, item: object) -> None:
        """Fold one item (an ``AgentEvent`` read from the runtime or a ``record_note``) and write the text it answers."""
        with self._lock:
            self._take(item)

    def take_tapped(self, copy: Callable[[], Iterable[object]]) -> None:
        """Fold the items the tap copied from one runtime line (``copy`` answers them) and count them as tapped."""
        with self._lock:
            try:
                items = list(copy())
            except Exception as error:  # a copy that raises drops the line, never the reader thread
                self._fail("copy", f"{type(error).__name__}: {error}")
                return
            self._tapped += len(items)
            for item in items:
                self._take(item)

    def settle(self, turn: str, paged: int) -> str | None:
        """Close the turn's tally: log one warning if an item was dropped or the tap brought nothing while the turn's
        pages carried ``paged`` texts or tool calls, and answer the warning (None = the turn's record was whole)."""
        with self._lock:
            failed = tuple(
                f"{each.kind} failed {each.count} time(s), first: {each.first}"
                for each in self._failures
            )
            untapped = paged > 0 and self._tapped == 0
            unseen = (
                (
                    f"the tap brought no item while the pages carried {paged} text(s) or tool call(s)",
                )
                if untapped
                else ()
            )
            problems = failed + unseen
            self._tapped = 0
            self._failures = ()
        if not problems:
            return None
        at = datetime.now(UTC).isoformat(timespec="seconds")
        message = f"{at} event record of session {self._name} {turn} ({self._path}): " + "; ".join(
            problems
        )
        LOGGER.warning("%s", message)
        return message

    def _take(self, item: object) -> None:
        """Fold one item under the held lock and write the text it answers."""
        try:
            step = self._fold(self._state, item)
            if not isinstance(step, RecordStep):
                raise TypeError(
                    f"EventRecord.fold answered {type(step).__name__}, not a RecordStep"
                )
        except Exception as error:  # a fold that raises drops the item, never the reader thread
            self._fail("fold", f"{type(error).__name__}: {error}")
            return
        self._state = step.state
        if step.text is not None:
            self._write(step.text)

    def _fail(self, kind: str, reason: str) -> None:
        """Count one failure of ``kind`` in the turn's tally, keeping the first reason."""
        if any(each.kind == kind for each in self._failures):
            self._failures = tuple(
                replace(each, count=each.count + 1) if each.kind == kind else each
                for each in self._failures
            )
        else:
            self._failures = (*self._failures, Failure(kind, 1, reason))

    def _write(self, text: str) -> None:
        """Replace the record's file with ``text`` so a reader sees the whole new record or the old one, never half."""
        folder = os.path.dirname(self._path) or "."
        try:
            os.makedirs(folder, exist_ok=True)
            handle, temp = tempfile.mkstemp(
                dir=folder, prefix=os.path.basename(self._path) + ".", suffix=".tmp"
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as out:
                    out.write(text)
                os.replace(temp, self._path)
            except OSError:
                if os.path.exists(temp):
                    os.remove(temp)
                raise
        except OSError as error:
            self._fail("write", str(error))
            with contextlib.suppress(OSError):
                os.remove(self._path)
