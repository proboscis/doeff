"""The file watch on the operating system's change notices: the effects of ``doeff_events.effects.files`` answered
by the ``watchfiles`` library (inotify on Linux, FSEvents on macOS — #3977).

``os_file_watch_handler()`` needs the optional dependency ``doeff-events[files]``. The module itself imports without
it; the library is imported when a watch is started.

- ``WatchFiles`` starts the library's notifier on the directory at once, so a change made after the answer is
  kept by the notifier until the next wait takes it (none is lost between two waits).
- ``NextFileChanges`` is one ``Await`` (it needs ``await_handler`` outside, inside ``scheduled``): the notifier's
  blocking wait runs in a worker thread and holds no scheduler task busy. The wait ends when the operating system
  reports a change under the directory; a quiet stretch of the notifier's own time limit only waits again (the
  program is never woken to look). Changes reported together within ``DEBOUNCE_MS`` come as one answer, sorted by
  path, each path once.
- ``CloseFileWatch`` sets the notifier's stop flag (a wait in progress ends) and closes it — at once when no wait is
  inside the notifier, otherwise when the last wait leaves it. The library's notifier must never be closed while a
  wait is inside it: its close takes the notifier mutably and its wait borrows it between steps, and on a Python
  without the GIL (3.14t — the library declares ``gil_used = false``) the two meet and its Rust code panics with
  ``PyBorrowError``, which ended a whole process (agora-redesign card ki-715d1af556eb, 2026-10-10 13:08Z).
"""

import asyncio
import os
import threading
from typing import TYPE_CHECKING, Final, Protocol, final

from doeff_core_effects.effects import Await

from doeff import K, Pass, Resume, do
from doeff import handler as _program_handler
from doeff_events.effects.files import (
    CloseFileWatch,
    FilesChanged,
    FileWatch,
    NextFileChanges,
    WatchFiles,
    WatchRefused,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from doeff import EffectGenerator
    from doeff.program import ProgramHandler

# Changes the operating system reports within this many ms come as one answer (short — the reader wants each
# append promptly), and the notifier checks its stop flag every STEP_MS.
DEBOUNCE_MS: Final = 50
STEP_MS: Final = 50
# The notifier's own limit for one blocking wait; when it passes with no change, the handler waits again.
NOTIFIER_TIMEOUT_MS: Final = 5_000
# Polling interval the library would use only if the operating system gave no notices (it does on Linux and macOS).
LIBRARY_POLL_DELAY_MS: Final = 300


class _NotifierLike(Protocol):
    """The part of the library's notifier (``watchfiles._rust_notify.RustNotify``) this handler uses."""

    def watch(
        self, debounce_ms: int, step_ms: int, timeout_ms: int, stop_event: threading.Event
    ) -> object:
        """Block until changes, a timeout, or the stop flag; answer the changes or a word."""
        ...

    def close(self) -> None:
        """Release the operating system's watch."""
        ...


@final
class _Notifier:
    """What the handler keeps for one watch: the library's notifier, the stop flag its wait checks, and who closes it.

    The close is made by whoever is last (module docstring): ``close`` closes the notifier at once when no wait is inside
    it, otherwise the last wait to leave closes it; once closing, no new wait enters. ``_lock`` guards the count and the
    two flags only — it is never held while the notifier waits.
    """

    __slots__ = ("_closed", "_closing", "_lock", "_waits", "notifier", "stop")

    def __init__(self, notifier: _NotifierLike) -> None:
        """Keep a started notifier; no wait is inside it yet."""
        self.notifier = notifier
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._waits = 0
        self._closing = False
        self._closed = False

    def wait_once(self) -> object:
        """One blocking wait of the notifier (runs in a worker thread): its answer, or ``"stop"`` without waiting once
        the watch is closing."""
        with self._lock:
            if self._closing:
                return "stop"
            self._waits += 1
        try:
            return self.notifier.watch(DEBOUNCE_MS, STEP_MS, NOTIFIER_TIMEOUT_MS, self.stop)
        finally:
            with self._lock:
                self._waits -= 1
                self._close_if_idle()

    def close(self) -> None:
        """End the waits (stop flag) and close the notifier now, or leave the close to the last wait inside it."""
        self.stop.set()
        with self._lock:
            self._closing = True
            self._close_if_idle()

    def _close_if_idle(self) -> None:
        """Close the notifier once, when closing and no wait is inside it (called with ``_lock`` held)."""
        if self._closing and self._waits == 0 and not self._closed:
            self._closed = True
            self.notifier.close()


def _changes_of(raw: object) -> FilesChanged | None:
    """The notifier's answer → the changed paths (sorted, each once), or ``None`` when it only timed out."""
    if raw == "timeout":
        return None
    if raw in ("stop", "signal"):
        return FilesChanged(())
    if not isinstance(raw, set):
        raise TypeError(f"unexpected answer from the file notifier: {raw!r}")
    return FilesChanged(tuple(sorted({str(path) for _change, path in raw})))


@final
class _NextChanges:
    """The awaitable handed to ``Await``: the notifier's blocking waits run in a worker thread, made only when it
    is awaited (a dropped effect leaves nothing behind)."""

    __slots__ = ("_kept",)

    def __init__(self, kept: _Notifier) -> None:
        """Name the notifier to wait on."""
        self._kept = kept

    async def _wait(self) -> FilesChanged:
        """Wait until the notifier reports changes (a timeout of the notifier only waits again)."""
        kept = self._kept
        while True:
            raw = await asyncio.to_thread(kept.wait_once)
            changes = _changes_of(raw)
            if changes is not None:
                return changes

    def __await__(self) -> "Generator[object, None, FilesChanged]":
        """Run the wait when awaited."""
        return self._wait().__await__()


def _start(directory: str) -> _Notifier | WatchRefused:
    """Start the library's notifier on ``directory`` (recursive), or refuse when it is not a directory."""
    if not os.path.isdir(directory):
        return WatchRefused(f"{directory} is not a directory")
    from watchfiles._rust_notify import (
        RustNotify,
    )  # imported here: the optional dependency doeff-events[files]

    return _Notifier(RustNotify([directory], False, False, LIBRARY_POLL_DELAY_MS, True, False))


def os_file_watch_handler() -> "ProgramHandler":
    """Answer ``WatchFiles`` / ``NextFileChanges`` / ``CloseFileWatch`` from the operating system's change notices."""
    watches: dict[FileWatch, _Notifier] = {}

    @do
    def handler(
        effect: WatchFiles | NextFileChanges | CloseFileWatch, k: K
    ) -> "EffectGenerator[object]":
        """Answer one file-watch operation."""
        answer: object = None
        match effect:
            case WatchFiles(directory=directory):
                started = _start(directory)
                if isinstance(started, WatchRefused):
                    answer = started
                else:
                    watch = FileWatch(directory)
                    watches[watch] = started
                    answer = watch
            case NextFileChanges(watch=watch):
                kept = watches.get(watch)
                answer = FilesChanged(()) if kept is None else (yield Await(_NextChanges(kept)))
            case CloseFileWatch(watch=closing):
                kept = watches.pop(closing, None)
                if kept is not None:
                    kept.close()
            case _:
                yield Pass(effect, k)
                return None
        return (yield Resume(k, answer))

    return _program_handler(handler)
