"""ADR-DOE-CORE-EFFECTS-001: slog observability semantics.

slog は SlogEffect(Writer の Tell とは別ワイヤ型)。slog_handler は stderr への
terminal sink(「入れたらログは見える」が契約)。slog の収集は Listen(prog, types=(SlogEffect,)) の値フロー、
Tell の蓄積は writer + writer_log()(State 収集)。handler install への
.log 属性 side-channel は削除済み API(main 0acce3a9 と本 ADR の統合形)。

plan: docs/doeff-2026-07-13-slog-semantics-architecture-plan.md
"""

import pytest
from doeff_core_effects.effects import Listen, Tell
from doeff_core_effects.handlers import listen_handler, slog_handler, state, writer
from doeff_vm import UnhandledEffect

from doeff import do, slog
from doeff import run as doeff_run


class TestSlogVisibleByDefault:
    def test_slog_reaches_stderr_via_default_interpreter(self, capsys):
        """R2/R4: default_interpreter 経由の slog は stderr に見える(stdout は汚さない)。"""
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
        """R2: level kwarg は大文字で表示され、なければ INFO。"""
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
        """R1: Listen の既定(WriterTellEffect)に slog は混ざらない。"""
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
        """R1: slog_handler は Tell を consume しない(Writer の領分)。"""
        @do
        def body():
            yield Tell("data")
            return "ok"

        with pytest.raises(UnhandledEffect):
            doeff_run(slog_handler(body()))


class TestCaptureFlowsAsValues:
    def test_listen_captures_slog_while_stderr_emits(self, capsys):
        """R3: 収集は Listen(types=(SlogEffect,)) の値フロー。sink の表示と併走する。"""
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
        """R3: handler install に .log 属性(削除済み API)が無い。"""
        assert not hasattr(slog_handler, "log")
        assert not hasattr(writer, "log")


class TestUnhandledSlogIsLoud:
    def test_unhandled_slog_raises(self):
        """R4: handler 不在の slog は UnhandledEffect(VM に lastResort 特例なし)。"""
        @do
        def body():
            yield slog("nobody-listens")
            return 1

        with pytest.raises(UnhandledEffect):
            doeff_run(body())


class TestExplicitSilence:
    def test_slog_discard_handler_is_silent(self, capsys):
        """R5: 静音は slog_discard_handler の明示 opt-in。"""
        from doeff_core_effects import slog_discard_handler

        @do
        def body():
            yield slog("quiet")
            return "done"

        assert doeff_run(slog_discard_handler(body())) == "done"
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out == ""
