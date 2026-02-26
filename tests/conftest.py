from __future__ import annotations

import _thread
import os
import subprocess
import threading
import warnings
from collections import defaultdict, deque
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

import pytest

from doeff import Program, async_run, default_async_handlers, default_handlers, run

T = TypeVar("T")

RunnerMode = Literal["sync", "async"]

_MEM_GUARD_TRIGGER_LOCK = threading.Lock()
_MEM_GUARD_TRIGGER: dict[str, str | None] = {"message": None}


class Interpreter(Protocol):
    def run(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any: ...


class RuntimeAdapter:
    """Adapter for rust-vm run/async_run with test interpreter protocol."""

    interpreter_type = "rust-vm"

    def __init__(self, mode: RunnerMode = "async") -> None:
        self.mode = mode

    def run(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any:
        return run(program, handlers=default_handlers(), env=env, store=store)

    async def run_async(
        self,
        program: Program[T],
        env: dict[Any, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> Any:
        """Run program with either run or async_run based on mode.

        This allows tests to be parameterized over both runner types while
        keeping the same async test interface.
        """
        if self.mode == "sync":
            return run(program, handlers=default_handlers(), env=env, store=state)
        return await async_run(program, handlers=default_async_handlers(), env=env, store=state)


def _read_process_table() -> tuple[dict[int, list[int]], dict[int, int]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,rss="],
        check=True,
        capture_output=True,
        text=True,
    )
    children_by_parent: dict[int, list[int]] = defaultdict(list)
    rss_kib: dict[int, int] = {}
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) != 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            rss = int(parts[2])
        except ValueError:
            continue
        children_by_parent[ppid].append(pid)
        rss_kib[pid] = rss
    return children_by_parent, rss_kib


def _collect_process_tree(root_pid: int) -> tuple[set[int], dict[int, int]]:
    children_by_parent, rss_kib = _read_process_table()
    seen: set[int] = set()
    queue: deque[int] = deque([root_pid])
    while queue:
        pid = queue.popleft()
        if pid in seen:
            continue
        seen.add(pid)
        for child in children_by_parent.get(pid, []):
            queue.append(child)
    return seen, rss_kib


def _rss_tree_mib(root_pid: int) -> float:
    process_tree, rss_kib = _collect_process_tree(root_pid)
    total_kib = sum(rss_kib.get(pid, 0) for pid in process_tree)
    return total_kib / 1024.0


def _set_mem_guard_trigger(message: str) -> None:
    with _MEM_GUARD_TRIGGER_LOCK:
        if _MEM_GUARD_TRIGGER["message"] is None:
            _MEM_GUARD_TRIGGER["message"] = message


def _get_mem_guard_trigger() -> str | None:
    with _MEM_GUARD_TRIGGER_LOCK:
        return _MEM_GUARD_TRIGGER["message"]


def _apply_rlimit_as(limit_mb: int) -> None:
    if limit_mb <= 0:
        return
    try:
        import resource
    except ImportError:
        warnings.warn(
            "resource module unavailable; skipping --rlimit-as-mb memory guard",
            stacklevel=2,
        )
        return

    if not hasattr(resource, "RLIMIT_AS"):
        warnings.warn(
            "resource.RLIMIT_AS unavailable on this platform; skipping --rlimit-as-mb",
            stacklevel=2,
        )
        return

    limit_bytes = int(limit_mb) * 1024 * 1024
    try:
        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            limit_bytes = min(limit_bytes, int(hard))
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, hard))
    except (ValueError, OSError) as exc:
        warnings.warn(
            f"Failed to apply RLIMIT_AS={limit_mb} MiB: {exc}",
            stacklevel=2,
        )


@dataclass
class _SessionMemoryGuard:
    limit_mb: int
    poll_interval: float
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._watch_loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=1.0)

    def _watch_loop(self) -> None:
        while not self.stop_event.wait(max(self.poll_interval, 0.1)):
            rss_mib = _rss_tree_mib(os.getpid())
            if rss_mib > self.limit_mb:
                message = (
                    "[pytest-mem-guard] RSS "
                    f"{rss_mib:.0f} MiB exceeded limit {self.limit_mb} MiB. "
                    "Requesting immediate pytest abort."
                )
                with suppress(OSError):
                    os.write(2, f"{message}\n".encode("utf-8", errors="replace"))
                _set_mem_guard_trigger(message)
                self.stop_event.set()
                _thread.interrupt_main()
                return


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("doeff-memory-guard")
    group.addoption(
        "--mem-guard-mb",
        action="store",
        type=int,
        default=int(os.environ.get("PYTEST_MEM_GUARD_MB", "8192")),
        help=(
            "Max RSS (MiB) for the full pytest process tree before failing fast. "
            "Set 0 to disable."
        ),
    )
    group.addoption(
        "--mem-guard-poll-interval",
        action="store",
        type=float,
        default=float(os.environ.get("PYTEST_MEM_GUARD_POLL_INTERVAL", "1.0")),
        help="Seconds between RSS checks for --mem-guard-mb.",
    )
    group.addoption(
        "--rlimit-as-mb",
        action="store",
        type=int,
        default=int(os.environ.get("PYTEST_RLIMIT_AS_MB", "0")),
        help=(
            "Apply OS RLIMIT_AS (MiB) at session start when supported. "
            "Set 0 to disable."
        ),
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    with _MEM_GUARD_TRIGGER_LOCK:
        _MEM_GUARD_TRIGGER["message"] = None
    config = session.config

    rlimit_as_mb = int(config.getoption("--rlimit-as-mb"))
    _apply_rlimit_as(rlimit_as_mb)

    mem_guard_mb = int(config.getoption("--mem-guard-mb"))
    if mem_guard_mb <= 0:
        return

    guard = _SessionMemoryGuard(
        limit_mb=mem_guard_mb,
        poll_interval=float(config.getoption("--mem-guard-poll-interval")),
    )
    guard.start()
    config._doeff_mem_guard = guard  # type: ignore[attr-defined]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    guard = getattr(session.config, "_doeff_mem_guard", None)
    if isinstance(guard, _SessionMemoryGuard):
        guard.stop()


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter,
    exitstatus: int,
    config: pytest.Config,
) -> None:
    message = _get_mem_guard_trigger()
    if message:
        terminalreporter.write_sep("!", message)


@pytest.fixture
def interpreter() -> Interpreter:
    """Default interpreter using async path when available."""
    return RuntimeAdapter(mode="async")


@pytest.fixture(params=["sync", "async"])
def parameterized_interpreter(request: pytest.FixtureRequest) -> RuntimeAdapter:
    """Parameterized interpreter that tests both run and async_run.

    Use this fixture to ensure effects work correctly with both runners.
    """
    return RuntimeAdapter(mode=request.param)
