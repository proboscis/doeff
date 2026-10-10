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

from doeff_events.effects.files import (
    CloseFileWatch,
    FilesChanged,
    FileWatch,
    NextFileChanges,
    WatchFiles,
    WatchRefused,
)
from doeff_events.handlers.memory_files import (
    MemoryFiles,
    announce_file_change,
    memory_file_watch_handler,
)
from doeff_events.handlers.os_files import os_file_watch_handler
from events_test_support import run_scheduled

from doeff import do

if TYPE_CHECKING:
    import threading

    import pytest

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
    assert changed == FilesChanged(
        ("/work/.agora/tool-stream/1-7.log", "/work/.agora/tool-stream/1-7.done")
    )
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

    assert run_scheduled(memory_file_watch_handler(files)(program())) == FilesChanged(
        ("/work/out/a.log",)
    )


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
        assert files.watched(watch) is not None and files.watched(watch).waiter is not None, (
            "the program is not waiting yet"
        )
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


class _OverlapNotifier:
    """A stand-in for the library's notifier (``watchfiles._rust_notify.RustNotify``) that records whether each close
    came while a wait was inside it — the meeting that made the library's Rust code panic with ``PyBorrowError`` on a
    Python without the GIL (agora-redesign card ki-715d1af556eb). Its wait ends only after the stop flag, and then
    lingers the way the library does until its next step, so a close made right after the flag is caught inside."""

    instances: "tuple[_OverlapNotifier, ...]" = ()

    def __init__(self, *_args: object) -> None:
        import threading

        self.inside = threading.Event()
        self.closed_now = threading.Event()
        self.closes: tuple[bool, ...] = ()
        _OverlapNotifier.instances = (*_OverlapNotifier.instances, self)

    def watch(
        self, _debounce_ms: int, _step_ms: int, _timeout_ms: int, stop_event: "threading.Event"
    ) -> object:
        self.inside.set()
        try:
            stop_event.wait()
            self.closed_now.wait(0.2)  # the library looks at the stop flag only at its next step
            return "stop"
        finally:
            self.inside.clear()

    def close(self) -> None:
        self.closes = (*self.closes, self.inside.is_set())
        self.closed_now.set()


def test_os_watch_never_closes_the_library_while_a_wait_is_inside_it(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    """Closing a watch while another task waits on it ends the wait with no changes, and the library's notifier is
    closed once, after the wait has left it (before the fix the close came at once, inside the wait)."""
    import asyncio

    import watchfiles._rust_notify
    from doeff_core_effects.effects import Await
    from doeff_core_effects.scheduler import Spawn, Wait

    monkeypatch.setattr(watchfiles._rust_notify, "RustNotify", _OverlapNotifier)
    monkeypatch.setattr(_OverlapNotifier, "instances", ())

    @do
    def closer(watch: FileWatch) -> "EffectGenerator[None]":
        notifier = _OverlapNotifier.instances[0]
        entered = yield Await(asyncio.to_thread(notifier.inside.wait, 5.0))
        assert entered, "the wait never entered the notifier"
        yield CloseFileWatch(watch)

    @do
    def program() -> "EffectGenerator[object]":
        watch = yield WatchFiles(str(tmp_path))
        assert isinstance(watch, FileWatch), watch
        task = yield Spawn(closer(watch))
        changed = yield NextFileChanges(watch)
        yield Wait(task)
        return changed

    assert run_scheduled(os_file_watch_handler()(program())) == FilesChanged(())
    assert _OverlapNotifier.instances[0].closes == (False,), _OverlapNotifier.instances[0].closes


def test_os_watch_closed_with_no_wait_inside_closes_the_library_at_once(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    import watchfiles._rust_notify

    monkeypatch.setattr(watchfiles._rust_notify, "RustNotify", _OverlapNotifier)
    monkeypatch.setattr(_OverlapNotifier, "instances", ())

    @do
    def program() -> "EffectGenerator[object]":
        watch = yield WatchFiles(str(tmp_path))
        yield CloseFileWatch(watch)
        closes = _OverlapNotifier.instances[0].closes
        # A wait asked for after the close answers no changes at once (the handler keeps nothing for the watch).
        changed = yield NextFileChanges(watch)
        return closes, changed

    assert run_scheduled(os_file_watch_handler()(program())) == ((False,), FilesChanged(()))


def test_os_watch_closed_by_another_task_during_a_wait_ends_the_wait_without_a_panic(
    tmp_path: Path,
) -> None:
    """The same against the real library: a wait in progress ends with no changes when another task closes the watch,
    with no panic. Repeated with different delays, because the meeting with the library is a race (it does not prove
    the fix by itself — the stand-in above does)."""
    import asyncio

    from doeff_core_effects.effects import Await
    from doeff_core_effects.scheduler import Spawn, Wait

    directory = tmp_path / "tool-stream"
    directory.mkdir()

    @do
    def closer(watch: FileWatch, after: float) -> "EffectGenerator[None]":
        yield Await(asyncio.sleep(after))
        yield CloseFileWatch(watch)

    @do
    def program(after: float) -> "EffectGenerator[object]":
        watch = yield WatchFiles(str(directory))
        assert isinstance(watch, FileWatch), watch
        task = yield Spawn(closer(watch, after))
        changed = yield NextFileChanges(watch)
        yield Wait(task)
        return changed

    for round_ in range(40):
        after = 0.01 + (round_ % 8) * 0.013
        assert run_scheduled(os_file_watch_handler()(program(after))) == FilesChanged(()), round_


def test_os_watch_refuses_a_missing_directory(tmp_path: Path) -> None:
    @do
    def program() -> "EffectGenerator[object]":
        return (yield WatchFiles(str(tmp_path / "absent")))

    refused = run_scheduled(os_file_watch_handler()(program()))
    assert isinstance(refused, WatchRefused), refused
