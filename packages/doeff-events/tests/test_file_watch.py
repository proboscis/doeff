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
        self.mut_closes: tuple[bool, ...] = ()
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
        self.mut_closes = (*self.mut_closes, self.inside.is_set())
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
    assert _OverlapNotifier.instances[0].mut_closes == (False,), _OverlapNotifier.instances[0].mut_closes


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
        closes = _OverlapNotifier.instances[0].mut_closes
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


def test_os_watch_waits_leave_the_default_executor_free(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    """Waits on more watches than the event loop's default executor has threads all enter their notifiers, and a job
    sent to that executor still runs at once. An HTTP client's new connection resolves its name there
    (``loop.getaddrinfo``); when every wait held one of its threads, a turn-host with more turns than the 32 threads
    timed out its connections to the custody service and the screen's drafts (agora-redesign card ki-fe848da94d24)."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    import watchfiles._rust_notify
    from doeff_core_effects.effects import Await
    from doeff_core_effects.scheduler import Spawn, Wait

    monkeypatch.setattr(watchfiles._rust_notify, "RustNotify", _OverlapNotifier)
    monkeypatch.setattr(_OverlapNotifier, "instances", ())
    threads = 2
    waits = threads + 2
    small = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="small-default")
    # Looks for the waits from outside the default executor (that one may be full — what the test looks for).
    lookout = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lookout")

    async def use_small_executor() -> None:
        asyncio.get_running_loop().set_default_executor(small)

    async def restore_default_executor() -> None:
        # The loop is shared by the whole process (await_handler): give it back an executor like asyncio's own default.
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(thread_name_prefix="asyncio")
        )
        small.shutdown(wait=False)
        lookout.shutdown(wait=False)

    async def look_while_waiting() -> "tuple[int, bool]":
        loop = asyncio.get_running_loop()
        entered = await loop.run_in_executor(
            lookout, lambda: sum(n.inside.wait(2.0) for n in _OverlapNotifier.instances)
        )
        try:
            ran = await asyncio.wait_for(loop.run_in_executor(None, lambda: True), 1.0)
        except TimeoutError:
            ran = False
        return entered, ran

    @do
    def waiter(watch: FileWatch) -> "EffectGenerator[object]":
        return (yield NextFileChanges(watch))

    @do
    def program() -> "EffectGenerator[object]":
        yield Await(use_small_executor())
        try:
            watches: tuple[FileWatch, ...] = ()
            for index in range(waits):
                directory = tmp_path / f"stream-{index}"
                directory.mkdir()
                watch = yield WatchFiles(str(directory))
                assert isinstance(watch, FileWatch), watch
                watches = (*watches, watch)
            tasks: tuple[object, ...] = ()
            for watch in watches:
                tasks = (*tasks, (yield Spawn(waiter(watch))))
            seen = yield Await(look_while_waiting())
            for watch in watches:
                yield CloseFileWatch(watch)
            answers: tuple[object, ...] = ()
            for task in tasks:
                answers = (*answers, (yield Wait(task)))
            return seen, answers
        finally:
            yield Await(restore_default_executor())

    seen, answers = run_scheduled(os_file_watch_handler()(program()))
    assert seen == (waits, True), seen
    assert answers == (FilesChanged(()),) * waits, answers


class _QuietNotifier:
    """A stand-in for the library's notifier over files that never change: each wait ends at a short limit of its own
    with ``"timeout"``, and it keeps the threads its waits ran in (``waited_again`` once it was waited on 3 times)."""

    instances: "tuple[_QuietNotifier, ...]" = ()

    def __init__(self, *_args: object) -> None:
        import threading

        self.mut_threads: tuple[threading.Thread, ...] = ()
        self.waited_again = threading.Event()
        _QuietNotifier.instances = (*_QuietNotifier.instances, self)

    def watch(
        self, _debounce_ms: int, _step_ms: int, _timeout_ms: int, stop_event: "threading.Event"
    ) -> object:
        import threading

        self.mut_threads = (*self.mut_threads, threading.current_thread())
        if len(self.mut_threads) >= 3:
            self.waited_again.set()
        stop_event.wait(0.02)
        return "stop" if stop_event.is_set() else "timeout"

    def close(self) -> None:
        pass


def test_os_watch_cancelled_wait_leaves_no_thread_behind(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    """A wait whose task is cancelled stops waiting on the notifier after its current wait (its thread ends while the
    watch stays open) instead of waiting again for ever."""
    import asyncio
    import contextlib

    import watchfiles._rust_notify
    from doeff_core_effects.effects import Await
    from doeff_core_effects.scheduler import Cancel, Spawn, TaskCancelledError, Wait

    monkeypatch.setattr(watchfiles._rust_notify, "RustNotify", _QuietNotifier)
    monkeypatch.setattr(_QuietNotifier, "instances", ())

    async def thread_ended(notifier: _QuietNotifier) -> bool:
        def join() -> bool:
            for thread in set(notifier.mut_threads):
                thread.join(2.0)
            return not any(thread.is_alive() for thread in notifier.mut_threads)

        return await asyncio.to_thread(join)

    @do
    def waiter(watch: FileWatch) -> "EffectGenerator[object]":
        return (yield NextFileChanges(watch))

    @do
    def program() -> "EffectGenerator[object]":
        watch = yield WatchFiles(str(tmp_path))
        notifier = _QuietNotifier.instances[0]
        task = yield Spawn(waiter(watch))
        # A few quiet stretches first: the wait waits on the notifier again after each.
        waited_again = yield Await(asyncio.to_thread(notifier.waited_again.wait, 5.0))
        assert waited_again, "the wait did not wait on the notifier again"
        yield Cancel(task)
        with contextlib.suppress(TaskCancelledError):
            yield Wait(task)
        ended = yield Await(thread_ended(notifier))
        yield CloseFileWatch(watch)
        return ended, len(set(notifier.mut_threads))

    # One thread for the whole wait, ended after the cancel.
    assert run_scheduled(os_file_watch_handler()(program())) == (True, 1)


class _Panic(BaseException):
    """Stands for pyo3's ``PanicException`` (a BaseException) — what the library raises when its Rust code panics."""


class _PanickingNotifier:
    """A stand-in for the library's notifier whose wait panics."""

    def __init__(self, *_args: object) -> None:
        pass

    def watch(
        self, _debounce_ms: int, _step_ms: int, _timeout_ms: int, _stop_event: "threading.Event"
    ) -> object:
        raise _Panic("the notifier panicked")

    def close(self) -> None:
        pass


def test_os_watch_a_panic_in_the_notifier_reaches_the_waiting_program(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    """A wait whose notifier panics raises the panic in the waiting program instead of leaving it waiting for ever
    (the thread of the wait would otherwise die with the panic and never answer)."""
    import threading
    from concurrent.futures import Future

    import watchfiles._rust_notify

    monkeypatch.setattr(watchfiles._rust_notify, "RustNotify", _PanickingNotifier)

    @do
    def program() -> "EffectGenerator[object]":
        watch = yield WatchFiles(str(tmp_path))
        try:
            yield NextFileChanges(watch)
        except _Panic as panic:
            return str(panic)
        finally:
            yield CloseFileWatch(watch)
        return "no panic"

    # Run in a daemon thread so that a wait that never answers fails the test instead of hanging the run.
    outcome: Future[object] = Future()
    threading.Thread(
        target=lambda: outcome.set_result(run_scheduled(os_file_watch_handler()(program()))),
        daemon=True,
    ).start()
    assert outcome.result(timeout=5.0) == "the notifier panicked"


def test_os_watch_refuses_a_missing_directory(tmp_path: Path) -> None:
    @do
    def program() -> "EffectGenerator[object]":
        return (yield WatchFiles(str(tmp_path / "absent")))

    refused = run_scheduled(os_file_watch_handler()(program()))
    assert isinstance(refused, WatchRefused), refused
