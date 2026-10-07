"""In-memory file watch: the effects of ``doeff_events.effects.files`` inside one process, for tests and emulated
environments (#3977).

``MemoryFiles`` holds the watches; nothing touches a disk. Whoever stands for a writer (a test, an emulated agent
environment) calls ``announce_file_change(files, path)`` when it writes a file, and every watch whose directory
holds the path sees it. A blocked wait is a scheduler promise (``CreatePromise`` / ``Wait``), like
``memory_notice_handler``, so a virtual clock and the scheduler's dead-end detection keep working.

A directory named in ``MemoryFiles(missing=...)`` cannot be watched (``WatchRefused``) — the stand-in for a
directory that does not exist.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, final

from doeff_core_effects.scheduler import CompletePromise, CreatePromise, Promise, Wait

from doeff import K, Pass, Resume, do
from doeff import handler as _program_handler
from doeff_events.effects.files import CloseFileWatch, FilesChanged, FileWatch, NextFileChanges, WatchFiles, WatchRefused

if TYPE_CHECKING:
    from doeff import EffectGenerator
    from doeff.program import ProgramHandler


@dataclass(frozen=True)
class _Wake:
    """A waiting task to wake: complete ``promise`` with ``changes`` (the handler does it — ``MemoryFiles`` has no
    effects)."""

    promise: Promise[object]
    changes: FilesChanged


@final
class _Watched:
    """What ``MemoryFiles`` keeps for one ``FileWatch``: paths changed but not yet taken, and the waiting task if
    any. Matched by the identity of its watch."""

    __slots__ = ("pending", "waiter", "watch")

    def __init__(self, watch: FileWatch) -> None:
        """Start watching for ``watch`` with nothing pending."""
        self.watch: Final = watch
        self.pending: tuple[str, ...] = ()
        self.waiter: Promise[object] | None = None


def _within(directory: str, path: str) -> bool:
    """Whether ``path`` is ``directory`` itself or below it."""
    return path == directory or path.startswith(directory.rstrip("/") + "/")


@final
class MemoryFiles:
    """The state of the in-memory file watch. Programs never see it; ``memory_file_watch_handler`` is its only
    user, and ``announce_file_change`` is the writer's control."""

    __slots__ = ("_missing", "_mut_watched")

    def __init__(self, missing: tuple[str, ...] = ()) -> None:
        """Start with no watch; the directories in ``missing`` cannot be watched."""
        self._missing: Final = missing
        self._mut_watched: tuple[_Watched, ...] = ()

    def watching(self) -> tuple[FileWatch, ...]:
        """The watches still open (a test reads it to see that a close forgot its watch)."""
        return tuple(watched.watch for watched in self._mut_watched)

    def watch(self, directory: str) -> FileWatch | WatchRefused:
        """Start a watch on ``directory``: changes announced from now on reach it."""
        if directory in self._missing:
            return WatchRefused(f"{directory} does not exist")
        watch = FileWatch(directory)
        self._mut_watched = (*self._mut_watched, _Watched(watch))
        return watch

    def watched(self, watch: FileWatch) -> _Watched | None:
        """The state kept for ``watch``, or ``None`` when it was closed."""
        return next((known for known in self._mut_watched if known.watch is watch), None)

    def changed(self, path: str) -> tuple[_Wake, ...]:
        """Give ``path`` to every watch whose directory holds it: to its waiting task, or to its pending paths
        (each path once)."""
        holding = tuple(watched for watched in self._mut_watched if _within(watched.watch.directory, path))
        wakes = tuple(_Wake(watched.waiter, FilesChanged((path,))) for watched in holding if watched.waiter is not None)
        for watched in holding:
            if watched.waiter is not None:
                watched.waiter = None
            elif path not in watched.pending:
                watched.pending = (*watched.pending, path)
        return wakes

    def close(self, watch: FileWatch) -> None:
        """Forget ``watch``; nothing is kept for it any more."""
        self._mut_watched = tuple(known for known in self._mut_watched if known.watch is not watch)


@do
def announce_file_change(files: MemoryFiles, path: str) -> "EffectGenerator[None]":
    """Writer's control: ``path`` (absolute) was written, created or removed — wake the watches that hold it."""
    for wake in files.changed(path):
        yield CompletePromise(wake.promise, wake.changes)


_CLOSED: Final = FilesChanged(())


def memory_file_watch_handler(files: MemoryFiles) -> "ProgramHandler":
    """Answer ``WatchFiles`` / ``NextFileChanges`` / ``CloseFileWatch`` from ``files``."""

    @do
    def handler(effect: WatchFiles | NextFileChanges | CloseFileWatch, k: K) -> "EffectGenerator[object]":
        """Answer one file-watch operation."""
        answer: object = None
        match effect:
            case WatchFiles(directory=directory):
                answer = files.watch(directory)
            case CloseFileWatch(watch=closing):
                files.close(closing)
            case NextFileChanges(watch=watch):
                watched = files.watched(watch)
                if watched is None:
                    answer = _CLOSED
                elif watched.pending:
                    answer, watched.pending = FilesChanged(watched.pending), ()
                else:
                    changes: Promise[object] = yield CreatePromise()
                    watched.waiter = changes
                    try:
                        answer = yield Wait(changes.future)
                    finally:
                        if watched.waiter is changes:
                            watched.waiter = None
            case _:
                yield Pass(effect, k)
                return None
        return (yield Resume(k, answer))

    return _program_handler(handler)
