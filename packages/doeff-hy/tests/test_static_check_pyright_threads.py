"""Failure case where doeff-hy-check (doeff_hy._static_check) could run pyright in only one thread.

The daily type measurement of agora-controllers (hy-type-trend, 2,551 .hy files in range) takes about 2,250 seconds for one post-expansion pyright on 1 core, and
adding the expansion seconds does not fit in 80% (2,640 seconds) of the processing stage limit of 3,300 seconds (reading of the atlas Pod in the 10-12 12:00 JST run: the pyright
process used 1 CPU core from 03:20:30 to 03:57 UTC, and in the second half it stuck at the heap limit of 4,288 MiB with GC threads at 1.6-1.8 cores). pyright can
split the files it checks across child processes with `--threads <n>`, but the run_pyright command had no way to pass it.

- The number of threads is taken from the environment variable DOEFF_HY_CHECK_PYRIGHT_THREADS (only the entry main takes the raw value from env_places and judges it).
    On a machine where the slot source of truth answers the parallelism within one slot, the number does not exceed it (a thread is a child process pyright spawns). The default is 1, and then `--threads` is not passed, as before.
- A value that is not an integer of 1 or more is rejected rather than silently falling back to the default (so the writer's mistake is visible).
"""

import contextlib
import io
import json
import shlex
import sys
from pathlib import Path

import pytest

import doeff_hy  # noqa: F401  # enable the Hy import hook
from doeff_hy import _static_check as static_check
from doeff_hy._check_slot import SlotHeld


def _recording_pyright(tmp_path: Path, log: Path) -> Path:
    """A fake pyright that writes its arguments to the log on one line when started and returns JSON with no diagnostics (only the shape of the command is observed)."""
    pyright = tmp_path / "recording-pyright"
    pyright.write_text(
        f'#!/bin/sh\necho "$@" >> {shlex.quote(str(log))}\necho \'{{"generalDiagnostics": []}}\'\n',
        encoding="utf-8",
    )
    pyright.chmod(0o755)
    return pyright


def _checked_once(tmp_path: Path) -> list[str]:
    """Run the entry main once on a root with one .hy file and return the argument list the fake pyright received."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "probe.hy").write_text("(setv answer 1)\n", encoding="utf-8")
    log = tmp_path / "pyright.log"
    pyright = _recording_pyright(tmp_path, log)
    with contextlib.redirect_stdout(io.StringIO()) as out:
        rc = static_check.main(
            ["--root", str(root), "--json", "--no-cache", "--pyright", str(pyright), str(root / "probe.hy")]
        )
    assert rc == 0, out.getvalue()
    assert isinstance(json.loads(out.getvalue()), list)  # the fake's answer (no diagnostics) could be read - the probe's setv warning is not counted
    started = log.read_text(encoding="utf-8").splitlines()
    assert len(started) == 1, started
    return started[0].split()


def test_the_pyright_threads_default_to_one_and_are_set_by_the_environment() -> None:
    """The number of threads defaults to 1. It can be changed with the environment variable DOEFF_HY_CHECK_PYRIGHT_THREADS, and a value that is not an integer of 1 or more is rejected rather than silently falling back to the default."""
    assert static_check.pyright_threads(None) == 1
    assert static_check.pyright_threads("") == 1
    assert static_check.pyright_threads("2") == 2
    for wrong in ("0", "-1", "two", "1.5"):
        with pytest.raises(ValueError, match="DOEFF_HY_CHECK_PYRIGHT_THREADS"):
            static_check.pyright_threads(wrong)


def test_the_thread_count_never_exceeds_the_width_the_slot_canon_answered() -> None:
    """A thread is a child process pyright spawns, so it does not exceed the parallelism within one slot answered by the slot source of truth - the environment variable is a way to lower the number,
    not a way to raise it past the declaration. On a machine where the declaration does not arrive (None), it is as before."""
    assert static_check.pyright_threads("8", 2) == 2
    assert static_check.pyright_threads("2", 2) == 2
    assert static_check.pyright_threads("1", 2) == 1
    assert static_check.pyright_threads(None, 2) == 1
    assert static_check.pyright_threads("8", None) == 8
    with pytest.raises(ValueError, match="DOEFF_HY_CHECK_PYRIGHT_THREADS"):
        static_check.pyright_threads("0", 2)


def test_a_thread_count_clamped_by_the_width_is_named_not_dropped_silently() -> None:
    """When the request is capped at the declared number, state in one line the environment variable name, the requested number, the declared number, and the actual number. When not capped (within the declaration,
    declaration not arriving, or no request), say nothing."""
    note = static_check.thread_clamp_note("4", 2)
    assert note is not None
    for part in ("DOEFF_HY_CHECK_PYRIGHT_THREADS=4", "declared for the host check slot", "run with 2 threads"):
        assert part in note, note
    assert static_check.thread_clamp_note("2", 2) is None
    assert static_check.thread_clamp_note("1", 2) is None
    assert static_check.thread_clamp_note(None, 2) is None
    assert static_check.thread_clamp_note("4", None) is None


def test_the_entry_says_so_on_stderr_when_the_environment_is_clamped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Daily failure shape: even with env set to 4, if the declaration is 2, pyright runs with 2 (the value is not changed). That is stated on stderr
    (before the fix nothing was said, and the fact that a run that asked for 4 ran with 2 stayed invisible until another conversation found it)."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(static_check, "take_slot", lambda *_: SlotHeld(None, 2))
    monkeypatch.setenv("DOEFF_HY_CHECK_PYRIGHT_THREADS", "4")
    argv = _checked_once(tmp_path)
    assert argv[argv.index("--threads") + 1] == "2", argv
    assert "DOEFF_HY_CHECK_PYRIGHT_THREADS=4 exceeds the parallelism 2 declared for the host check slot" in capsys.readouterr().err


def test_the_entry_is_silent_when_the_environment_is_within_the_width(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(static_check, "take_slot", lambda *_: SlotHeld(None, 2))
    monkeypatch.setenv("DOEFF_HY_CHECK_PYRIGHT_THREADS", "2")
    _checked_once(tmp_path)
    assert "exceeds the parallelism" not in capsys.readouterr().err


def test_the_entry_passes_the_thread_count_to_pyright(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the environment variable names a number of 2 or more, the entry main runs pyright with `--threads <n>` (within the number answered by the slot source of truth)."""
    monkeypatch.setattr(sys, "path", list(sys.path))  # main adds the root to the head of sys.path
    monkeypatch.setattr(static_check, "take_slot", lambda *_: SlotHeld(None, 4))  # independent of the machine's source-of-truth declaration
    monkeypatch.setenv("DOEFF_HY_CHECK_PYRIGHT_THREADS", "3")
    argv = _checked_once(tmp_path)
    assert "--threads" in argv, argv
    assert argv[argv.index("--threads") + 1] == "3", argv


def test_the_entry_without_the_setting_runs_pyright_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When there is no setting (default 1), `--threads` is not passed, as before (one pyright)."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(static_check, "take_slot", lambda *_: SlotHeld())
    monkeypatch.delenv("DOEFF_HY_CHECK_PYRIGHT_THREADS", raising=False)
    argv = _checked_once(tmp_path)
    assert "--threads" not in argv, argv


def test_the_entry_refuses_a_wrong_thread_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A value that is not an integer of 1 or more could not run (exit 2) - reject before running pyright and name the variable."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(static_check, "take_slot", lambda *_: SlotHeld())
    monkeypatch.setenv("DOEFF_HY_CHECK_PYRIGHT_THREADS", "0")
    assert static_check.main(["--root", str(tmp_path), "--no-cache"]) == 2
    assert "DOEFF_HY_CHECK_PYRIGHT_THREADS" in capsys.readouterr().err
