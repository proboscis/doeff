from doeff import handler as _install_raw_handler

# ruff: noqa: E402
"""Tests for doeff-notify effects and built-in handlers."""


import importlib
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from doeff_notify.effects import Acknowledge, Notify, NotifyThread
from doeff_notify.handlers import (
    collected_notifications,
    console_handler,
    log_handler,
)
from doeff_notify.handlers import (
    testing_handler as build_testing_handler,
)
from doeff_notify.types import Channel, NotificationResult, Urgency

from doeff import Effect, Pass, Resume, Tell, WriterTellEffect, do, run


def test_effect_exports() -> None:
    exported = importlib.import_module("doeff_notify.effects")
    assert exported.Notify is Notify
    assert exported.NotifyThread is NotifyThread
    assert exported.Acknowledge is Acknowledge


def test_handler_exports() -> None:
    exported = importlib.import_module("doeff_notify.handlers")
    assert exported.console_handler is console_handler
    assert exported.testing_handler is build_testing_handler
    assert exported.log_handler is log_handler


@do
def _console_program():
    return (
        yield Notify(
            title="Deploy",
            message="Deployment failed",
            urgency=Urgency.HIGH,
            tags=("deploy", "failure"),
        )
    )


def test_console_handler_prints_and_returns_notification_result(capsys) -> None:
    # The built-in handlers are raw ``(effect, k)`` dispatchers; install them
    # with ``doeff.handler`` (the replacement of ``WithHandler(raw, program)``).
    payload = run(_install_raw_handler(console_handler)(_console_program()))

    captured = capsys.readouterr()
    assert "[ALERT] Deploy: Deployment failed" in captured.out

    assert isinstance(payload, NotificationResult)
    assert payload.channel == Channel.CONSOLE
    assert payload.thread_id == payload.notification_id


@do
def _testing_program():
    first = yield Notify(
        message="Deploy started",
        urgency=Urgency.LOW,
        tags=("deploy",),
    )
    yield NotifyThread(thread_id=first.thread_id or first.notification_id, message="Reviewer assigned")
    acknowledged = yield Acknowledge(notification_id=first.notification_id, timeout=0.1)
    return first, acknowledged


def test_testing_handler_collects_notifications_in_memory() -> None:
    handler, notifications = build_testing_handler(auto_acknowledge=True)

    first, acknowledged = run(_install_raw_handler(handler)(_testing_program()))

    assert isinstance(first, NotificationResult)
    assert first.channel == Channel.TESTING
    assert first.thread_id == first.notification_id
    assert acknowledged is True

    assert len(notifications) == 1
    assert notifications[0].message == "Deploy started"
    assert notifications[0].tags == ("deploy",)

    plain_list = collected_notifications(notifications)
    assert len(plain_list) == 1
    assert plain_list[0].urgency == Urgency.LOW

    assert len(notifications.thread_updates) == 1
    assert notifications.thread_updates[0].thread_id == first.thread_id
    assert len(notifications.acknowledgements) == 1
    assert notifications.acknowledgements[0].notification_id == first.notification_id


@do
def _logging_program():
    first = yield Notify(
        title="Budget Alert",
        message="Spend exceeded threshold",
        urgency=Urgency.CRITICAL,
        metadata={"budget": "marketing"},
        link="https://example.local/budget",
    )
    yield NotifyThread(thread_id=first.thread_id or first.notification_id, message="Escalating to on-call")
    acknowledged = yield Acknowledge(notification_id=first.notification_id, timeout=5.0)
    return first, acknowledged


def test_log_handler_emits_tell_events() -> None:
    logs: list[Any] = []

    @do
    def capture_tell_handler(effect: Effect, k: Any):
        if isinstance(effect, WriterTellEffect):
            logs.append(effect.msg)
            return (yield Resume(k, None))
        return (yield Pass(effect, k))

    first, acknowledged = run(
        _install_raw_handler(capture_tell_handler)(
            _install_raw_handler(log_handler)(_logging_program())
        )
    )

    assert isinstance(first, NotificationResult)
    assert first.channel == Channel.LOG
    assert acknowledged is False

    assert len(logs) == 3
    assert logs[0]["event"] == "notify"
    assert logs[0]["urgency"] == Urgency.CRITICAL
    assert logs[0]["metadata"] == {"budget": "marketing"}
    assert logs[1]["event"] == "notify_thread"
    assert logs[2]["event"] == "acknowledge"


@do
def _tell_only_program() -> Generator[Any, Any, Any]:
    return (yield Tell("not-a-notification"))


@do
def _outer_tell_handler(effect: Effect, k: Any) -> Generator[Any, Any, Any]:
    """Resume Tell with a marker so the program returns proof that Tell reached here."""
    if isinstance(effect, WriterTellEffect):
        return (yield Resume(k, f"outer-saw:{effect.msg}"))
    return (yield Pass(effect, k))


def test_handlers_pass_non_notification_effects_outward() -> None:
    """Unhandled effects must reach the outer handler through ``Pass(effect, k)``."""

    for raw in (console_handler, log_handler, build_testing_handler()[0]):
        result = run(
            _install_raw_handler(_outer_tell_handler)(
                _install_raw_handler(raw)(_tell_only_program())
            )
        )
        assert result == "outer-saw:not-a-notification"
