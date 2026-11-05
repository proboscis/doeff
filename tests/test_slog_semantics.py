"""ADR-DOE-CORE-EFFECTS-001: slog observability semantics.

slog is a SlogEffect (a different wire type from the Writer's Tell). slog_handler is a terminal sink to stderr
(the contract is "if installed, the log is visible"). Collecting slog is the value flow of Listen(prog, types=(SlogEffect,)),
while accumulating Tell is done by the writer (State collection) and read with yield ReadWriterLog().
The .log attribute side-channel on handler install is a removed API (the integrated form of main 0acce3a9 and this ADR).

plan: docs/doeff-2026-07-13-slog-semantics-architecture-plan.md
"""

import pytest
from doeff_core_effects import Listen, Tell, listen_handler, slog_handler, state, writer
from doeff_vm import UnhandledEffect

from doeff import do, slog
from doeff import run as doeff_run


class TestSlogVisibleByDefault:
    def test_slog_reaches_stderr_via_default_interpreter(self, capsys):
        """R2/R4: slog via default_interpreter is visible on stderr (stdout is not polluted)."""
        from doeff.cli.run_services import default_interpreter

        @do
        def body():
            yield slog("visible-message", step="one")
            return 42

        result = default_interpreter(body())
        captured = capsys.readouterr()
        assert result == 42
        assert "visible-message" in captured.err
        assert "step=one" in captured.err
        assert "visible-message" not in captured.out

    def test_slog_handler_formats_level(self, capsys):
        """R2: the level kwarg is shown in uppercase, and INFO if absent."""
        @do
        def body():
            yield slog("warned", level="warning")
            yield slog("plain")

        doeff_run(slog_handler(body()))
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "warned" in err
        assert "INFO" in err
        assert "plain" in err


class TestSlogTellTypesDisjoint:
    def test_listen_default_does_not_capture_slog(self, capsys):
        """R1: slog is not mixed into Listen's default (WriterTellEffect)."""
        @do
        def inner():
            yield slog("slog-entry", level="info")
            yield Tell("tell-entry")
            return "ok"

        @do
        def body():
            return (yield Listen(inner()))

        stack = state()(writer(slog_handler(listen_handler(body()))))
        result, collected = doeff_run(stack)
        assert result == "ok"
        assert [e.msg for e in collected] == ["tell-entry"]

    def test_slog_handler_passes_tell_through(self):
        """R1: slog_handler does not consume Tell (that is the Writer's domain)."""
        @do
        def body():
            yield Tell("data")
            return "ok"

        with pytest.raises(UnhandledEffect):
            doeff_run(slog_handler(body()))


class TestCaptureFlowsAsValues:
    def test_listen_captures_slog_while_stderr_emits(self, capsys):
        """R3: collection is the value flow of Listen(types=(SlogEffect,)). It runs alongside the sink's display."""
        from doeff_core_effects import SlogEffect

        @do
        def inner():
            yield slog("captured", user="alice")
            return "r"

        @do
        def body():
            return (yield Listen(inner(), types=(SlogEffect,)))

        result, collected = doeff_run(slog_handler(listen_handler(body())))
        assert result == "r"
        assert len(collected) == 1
        assert collected[0].msg == "captured"
        assert collected[0].kwargs == {"user": "alice"}
        assert "captured" in capsys.readouterr().err

    def test_handler_installs_expose_no_log_side_channel(self):
        """R3: handler install has no .log attribute (removed API)."""
        assert not hasattr(slog_handler, "log")
        assert not hasattr(writer, "log")


class TestUnhandledSlogIsLoud:
    def test_unhandled_slog_raises(self):
        """R4: slog with no handler is UnhandledEffect (no lastResort special case in the VM)."""
        @do
        def body():
            yield slog("nobody-listens")
            return 1

        with pytest.raises(UnhandledEffect):
            doeff_run(body())


class TestExplicitSilence:
    def test_slog_discard_handler_is_silent(self, capsys):
        """R5: silence is an explicit opt-in via slog_discard_handler."""
        from doeff_core_effects import slog_discard_handler

        @do
        def body():
            yield slog("quiet")
            return "done"

        assert doeff_run(slog_discard_handler(body())) == "done"
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out == ""
