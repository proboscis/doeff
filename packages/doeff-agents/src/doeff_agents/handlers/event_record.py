"""The keeper of a session's event record (``LaunchEffect.event_record`` — agora card ki-31b41eac5071).

A caller of the headless adapter wrote its record of a turn (the agora answer ledger the PreToolUse hook reads)
only when its page reads (``EventsEffect``) caught up with the runtime, and those reads lagged seconds to minutes
behind the runtime's reader thread on a busy host. The keeper folds each item as the runtime reads it — on the
runtime's reader thread, through ``ClaudeStartTurn.tap`` — and on the caller's ``FollowUpEffect`` before the
input reaches the runtime, so the record does not wait for the page reads.

The keeper holds the state behind a lock (the reader thread and the handler's own clauses both fold) and replaces
the file with the text a fold answers: written to a sibling first and moved over the file, so a reader never sees
half a file. A write that fails removes the file (a reader then finds no record rather than a stale one). A fold
that raises drops the item and keeps the state — the reader thread must not stop.
"""

import os
import tempfile
import threading

from doeff_agents.effects.agent import EventRecord


class RecordKeeper:
    """Folds items into one session's event record and keeps its file current (see the module docstring)."""

    def __init__(self, record: EventRecord) -> None:
        self._lock = threading.Lock()
        self._path = record.path
        self._fold = record.fold
        self._state = record.state
        # The reason the last write failed (None = the last write, if any, went through).
        self.failure: str | None = None

    def take(self, item: object) -> None:
        """Fold one item (an ``AgentEvent`` read from the runtime or a ``record_note``) and write the text it answers."""
        with self._lock:
            try:
                state, text = self._fold(self._state, item)
            except Exception as error:  # noqa: BLE001 — a fold that raises drops the item, never the reader thread
                self.failure = f"fold: {type(error).__name__}: {error}"
                return
            self._state = state
            if text is not None:
                self._write(text)

    def _write(self, text: str) -> None:
        """Replace the record's file with ``text`` so a reader sees the whole new record or the old one, never half."""
        folder = os.path.dirname(self._path) or "."
        try:
            os.makedirs(folder, exist_ok=True)
            handle, temp = tempfile.mkstemp(dir=folder, prefix=os.path.basename(self._path) + ".", suffix=".tmp")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as out:
                    out.write(text)
                os.replace(temp, self._path)
            except OSError:
                if os.path.exists(temp):
                    os.remove(temp)
                raise
            self.failure = None
        except OSError as error:
            self.failure = f"write: {error}"
            try:
                os.remove(self._path)
            except OSError:
                pass
