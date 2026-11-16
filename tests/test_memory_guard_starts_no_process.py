"""The session memory guard of tests/conftest.py reads the process table without starting a process.

Failure case (daily 2026-11-14, ETXTBSY): the guard's thread ran `ps` once a second beside every test. A child forked while a test
had a file open for writing kept the write descriptor until the child execs, and the test could not run the file it had just written
(tests/test_rust_build_target_outside_package.py::test_two_builds_at_once_each_get_their_own_wheel).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

CONFTEST = Path(__file__).with_name("conftest.py")


def _guard_module(config: pytest.Config) -> ModuleType:
    """The tests/conftest.py module pytest loaded for this session."""
    (module,) = [
        plugin
        for plugin in config.pluginmanager.get_plugins()
        if isinstance(plugin, ModuleType) and getattr(plugin, "__file__", None) == str(CONFTEST)
    ]
    return module


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="the table is read from /proc only where there is /proc")
def test_reading_the_process_table_starts_no_process(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    guard = _guard_module(request.config)

    def no_process(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        raise AssertionError(f"the memory guard started a process: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", no_process)
    table = guard._read_process_table()

    assert os.getpid() in table.children_by_parent[os.getppid()]
    assert table.rss_kib[os.getpid()] > 0
