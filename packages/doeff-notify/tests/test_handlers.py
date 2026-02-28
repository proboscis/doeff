# ruff: noqa: E402
"""Tests for doeff-notify effects and built-in handlers."""

from __future__ import annotations

import importlib
import sys
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

from doeff import Effect, Pass, Resume, WithHandler, default_handlers, do, run
from doeff.effects import WriterTellEffect


def _is_ok(run_result: Any) -> bool:
    checker = run_result.is_ok
    return bool(checker()) if callable(checker) else bool(checker)


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
    result = run(
        WithHandler(console_handler, _console_program()),
        handlers=default_handlers(),
    )

    captured = capsys.readouterr()
    assert _is_ok(result)
    assert "[ALERT] Deploy: Deployment failed" in captured.out

    payload = result.value
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

    result = run(
        WithHandler(handler, _testing_program()),
        handlers=default_handlers(),
    )

    assert _is_ok(result)
    first, acknowledged = result.value
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
            logs.append(effect.message)
            return (yield Resume(k, None))
        yield Pass()

    result = run(
        WithHandler(capture_tell_handler, WithHandler(log_handler, _logging_program())),
        handlers=default_handlers(),
    )

    assert _is_ok(result)
    first, acknowledged = result.value
    assert isinstance(first, NotificationResult)
    assert first.channel == Channel.LOG
    assert acknowledged is False

    assert len(logs) == 3
    assert logs[0]["event"] == "notify"
    assert logs[0]["urgency"] == Urgency.CRITICAL
    assert logs[0]["metadata"] == {"budget": "marketing"}
    assert logs[1]["event"] == "notify_thread"
    assert logs[2]["event"] == "acknowledge"
