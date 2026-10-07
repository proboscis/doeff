"""A file watch wakes a program when files under a directory change (#3977).

The program waits for the change instead of reading the file at an interval. Laws, for both answerers:

- a change made after ``WatchFiles`` answered is seen by the next ``NextFileChanges`` — also when it was made
  before the program started waiting (none is lost between two waits);
- the answer names each changed path once, in the order first seen;
- a change outside the watched directory is not seen;
- after ``CloseFileWatch`` the handler keeps nothing for the watch.

The in-memory answerer is driven by ``announce_file_change`` (what a test or an emulated environment calls when it
writes a file). The operating-system answerer is checked once against a real temporary directory.

No ``from __future__ import annotations`` here: the VM reads a handler's effect types from the annotation of its
first parameter.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from doeff_events.effects.files import CloseFileWatch, FilesChanged, FileWatch, NextFileChanges, WatchFiles, WatchRefused
from doeff_events.handlers.memory_files import MemoryFiles, announce_file_change, memory_file_watch_handler
from doeff_events.handlers.os_files import os_file_watch_handler
from events_test_support import run_scheduled

from doeff import do

if TYPE_CHECKING:
    from doeff import EffectGenerator


def test_memory_watch_keeps_changes_made_before_the_wait_and_names_each_path_once() -> None:
    files = MemoryFiles()

    @do
    def program() -> "EffectGenerator[object]":
        watch = yield WatchFiles("/work/.agora/tool-stream")
        assert isinstance(watch, FileWatch), watch
        # Written before anybody waits: kept for the next wait.
        yield announce_file_change(files, "/work/.agora/tool-stream/1-7.log")
        yield announce_file_change(files, "/work/.agora/tool-stream/1-7.log")
        yield announce_file_change(files, "/work/other/notes.txt")
        yield announce_file_change(files, "/work/.agora/tool-stream/1-7.done")
        changed = yield NextFileChanges(watch)
        yield CloseFileWatch(watch)
        return changed, files.watching()

    changed, watching = run_scheduled(memory_file_watch_handler(files)(program()))
    assert changed == FilesChanged(("/work/.agora/tool-stream/1-7.log", "/work/.agora/tool-stream/1-7.done"))
    assert watching == ()


def test_memory_watch_wakes_a_waiting_program() -> None:
    from doeff_core_effects.scheduler import Spawn, Wait

    files = MemoryFiles()

    @do
    def writer() -> "EffectGenerator[None]":
        yield announce_file_change(files, "/work/out/a.log")

    @do
    def program() -> "EffectGenerator[object]":
        watch = yield WatchFiles("/work/out")
        task = yield Spawn(writer())
        changed = yield NextFileChanges(watch)
        yield Wait(task)
        return changed

    assert run_scheduled(memory_file_watch_handler(files)(program())) == FilesChanged(("/work/out/a.log",))


def test_memory_watch_close_wakes_a_waiting_program_with_no_changes() -> None:
    """A wait in progress ends when another task closes the watch (the same as the operating-system answerer, whose
    close sets the notifier's stop flag): the waiter gets ``FilesChanged(())`` instead of waiting forever."""
    import asyncio

    from doeff_core_effects.effects import Await
    from doeff_core_effects.scheduler import Spawn, Wait

    files = MemoryFiles()

    @do
    def closer(watch: FileWatch) -> "EffectGenerator[None]":
        # Let the program reach its wait first (a close before the wait answers the empty change at once — not the case here).
        yield Await(asyncio.sleep(0.01))
        assert files.watched(watch) is not None and files.watched(watch).waiter is not None, "the program is not waiting yet"
        yield CloseFileWatch(watch)

    @do
    def program() -> "EffectGenerator[object]":
        watch = yield WatchFiles("/work/out")
        assert isinstance(watch, FileWatch), watch
        task = yield Spawn(closer(watch))
        changed = yield NextFileChanges(watch)
        yield Wait(task)
        return changed, files.watching()

    assert run_scheduled(memory_file_watch_handler(files)(program())) == (FilesChanged(()), ())


def test_memory_watch_refuses_a_directory_the_test_did_not_create() -> None:
    files = MemoryFiles(missing=("/gone",))

    @do
    def program() -> "EffectGenerator[object]":
        return (yield WatchFiles("/gone"))

    refused = run_scheduled(memory_file_watch_handler(files)(program()))
    assert isinstance(refused, WatchRefused), refused


def test_os_watch_sees_a_file_written_after_the_answer(tmp_path: Path) -> None:
    directory = tmp_path / "tool-stream"
    directory.mkdir()
    target = directory / "1-7.log"

    @do
    def program() -> "EffectGenerator[object]":
        watch = yield WatchFiles(str(directory))
        assert isinstance(watch, FileWatch), watch
        # Written after the answer and before the wait: the watch already listens, so it is seen.
        target.write_text("line 1\n", encoding="utf-8")
        changed = yield NextFileChanges(watch)
        yield CloseFileWatch(watch)
        return changed

    changed = run_scheduled(os_file_watch_handler()(program()))
    assert isinstance(changed, FilesChanged), changed
    assert str(target) in changed.paths, changed


def test_os_watch_refuses_a_missing_directory(tmp_path: Path) -> None:
    @do
    def program() -> "EffectGenerator[object]":
        return (yield WatchFiles(str(tmp_path / "absent")))

    refused = run_scheduled(os_file_watch_handler()(program()))
    assert isinstance(refused, WatchRefused), refused
