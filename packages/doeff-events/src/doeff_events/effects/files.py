"""Effects of a file watch: wake a program when files under a directory change, instead of reading them at an
interval (#3977).

- ``WatchFiles`` — start watching a directory (and what is below it); changes made from the answer on are seen.
- ``NextFileChanges`` — wait for the next changes on a watch; answers the paths that changed (created, written,
  removed), in the order they were first seen, each once.
- ``CloseFileWatch`` — stop watching and give up what the handler keeps for the watch.

The answerers are ``memory_file_watch_handler`` (one process — tests and emulated environments announce changes
by hand) and ``os_file_watch_handler`` (the operating system's change notices — inotify on Linux, FSEvents on
macOS — through the ``watchfiles`` library). A program never reads a file at an interval to see whether it grew:
it waits here, and reads the file when it is told the file changed.
"""

from dataclasses import dataclass
from typing import Final, final

from doeff import EffectBase


@dataclass(frozen=True)
class WatchRefused:
    """The answer of ``WatchFiles`` when the directory cannot be watched (it does not exist, or is not a
    directory): ``detail`` says why in the operating system's (or the handler's) words."""

    detail: str


@dataclass(frozen=True)
class FilesChanged:
    """The changes ``NextFileChanges`` answered: the absolute paths that changed since the last answer, in the
    order they were first seen, each once."""

    paths: tuple[str, ...]


@final
class FileWatch:
    """A watch on a directory, as ``WatchFiles`` answers it. Compared by identity; the handler that made it keeps
    what it needs to wait on it."""

    __slots__ = ("directory",)

    def __init__(self, directory: str) -> None:
        """Name the watched directory (an absolute path)."""
        self.directory: Final = directory

    def __repr__(self) -> str:
        """Show the directory in logs and test failures."""
        return f"FileWatch({self.directory})"


@dataclass(frozen=True)
class WatchFiles(EffectBase["FileWatch | WatchRefused"]):
    """Start watching ``directory`` (an absolute path) and what is below it. A change made after the answer is
    seen by ``NextFileChanges``; a change made before it is not."""

    directory: str

    def __post_init__(self) -> None:
        if not isinstance(self.directory, str) or not self.directory.startswith("/"):
            raise ValueError(f"WatchFiles.directory must be an absolute path, got {self.directory!r}")


@dataclass(frozen=True)
class NextFileChanges(EffectBase[FilesChanged]):
    """Wait for the next changes on ``watch``. The wait has no time limit: it ends when a file under the
    directory changes, or when the waiting task is cancelled. Changes made while nobody waits are kept for the
    next wait (none is lost between two waits)."""

    watch: FileWatch


@dataclass(frozen=True)
class CloseFileWatch(EffectBase[None]):
    """Stop watching and forget what the handler keeps for ``watch``. Never fails."""

    watch: FileWatch
