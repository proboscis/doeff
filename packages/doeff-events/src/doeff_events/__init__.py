"""Public API for generic doeff event effects and handlers."""

from importlib import import_module
from typing import TYPE_CHECKING

from ._effects import (
    AckAnnouncement,
    Announce,
    Announcement,
    ArmedTimer,
    ArmedTimers,
    ArmedTimersEffect,
    ArmTimer,
    ArmTimerEffect,
    AwaitBrokerBack,
    BrokerPatience,
    BrokerUnreachable,
    ChannelSubscription,
    CloseSubscription,
    DisarmTimer,
    DisarmTimerEffect,
    NextAnnouncement,
    ProbeBroker,
    Publish,
    PublishEffect,
    PublishGap,
    PublishGapEffect,
    ReadBrokerPatience,
    SourceFailed,
    SourceMissed,
    SourceResumed,
    SourceStalled,
    SourceStarted,
    StopArrived,
    SubscribeChannels,
    TimerFired,
    WaitForEvent,
    WaitForEventEffect,
    WaitForEvents,
    WaitForEventsEffect,
    publish,
    publish_gap,
    wait_for_event,
    wait_for_events,
)
from ._handlers import (
    BrokerConnectionsExhausted,
    Drop,
    EventBus,
    GapAnswer,
    MarkGap,
    MemoryBroker,
    NoticeDropped,
    NoticeGapMarked,
    NoticeGapTold,
    NoticeRoute,
    NoticeSent,
    NoticeSourceUnreachable,
    PublishAnswer,
    UnroutedNotice,
    WhenUnsent,
    broker_back_by_retry,
    broker_patience_handler,
    cut_broker,
    event_handler,
    memory_notice_handler,
    notice_events_handler,
    redis_notice_handler,
    restore_broker,
    subscribed_event_handler,
    timer_handler,
)

__all__ = [
    "AckAnnouncement",
    "Announce",
    "Announcement",
    "ArmTimer",
    "ArmTimerEffect",
    "ArmedTimer",
    "ArmedTimers",
    "ArmedTimersEffect",
    "AwaitBrokerBack",
    "BrokerAnswerMalformed",
    "BrokerConnectionsExhausted",
    "BrokerPatience",
    "BrokerUnreachable",
    "ChannelSubscription",
    "CloseFileWatch",
    "CloseSubscription",
    "DisarmTimer",
    "DisarmTimerEffect",
    "Drop",
    "EventBus",
    "FileWatch",
    "FilesChanged",
    "GapAnswer",
    "KeptReaders",
    "MarkGap",
    "MemoryBroker",
    "MemoryFiles",
    "NextAnnouncement",
    "NextFileChanges",
    "NoticeDropped",
    "NoticeGapMarked",
    "NoticeGapTold",
    "NoticeRoute",
    "NoticeSent",
    "NoticeSourceUnreachable",
    "ProbeBroker",
    "Publish",
    "PublishAnswer",
    "PublishEffect",
    "PublishGap",
    "PublishGapEffect",
    "ReadBrokerPatience",
    "SourceFailed",
    "SourceMissed",
    "SourceResumed",
    "SourceStalled",
    "SourceStarted",
    "StopArrived",
    "SubscribeChannels",
    "TimerFired",
    "UnroutedNotice",
    "WaitForEvent",
    "WaitForEventEffect",
    "WaitForEvents",
    "WaitForEventsEffect",
    "WatchFiles",
    "WatchRefused",
    "WhenUnsent",
    "broker_back_by_retry",
    "broker_patience_handler",
    "cut_broker",
    "event_handler",
    "memory_file_watch_handler",
    "memory_notice_handler",
    "notice_events_handler",
    "os_file_watch_handler",
    "publish",
    "publish_gap",
    "redis_notice_handler",
    "restore_broker",
    "subscribed_event_handler",
    "timer_handler",
    "wait_for_event",
    "wait_for_events",
]


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers; `__all__` is not used (DOEFF021).
if TYPE_CHECKING:
    from doeff_events._effects.files import CloseFileWatch as CloseFileWatch
    from doeff_events._effects.files import FilesChanged as FilesChanged
    from doeff_events._effects.files import FileWatch as FileWatch
    from doeff_events._effects.files import NextFileChanges as NextFileChanges
    from doeff_events._effects.files import WatchFiles as WatchFiles
    from doeff_events._effects.files import WatchRefused as WatchRefused
    from doeff_events._handlers.memory_files import MemoryFiles as MemoryFiles
    from doeff_events._handlers.memory_files import (
        memory_file_watch_handler as memory_file_watch_handler,
    )
    from doeff_events._handlers.memory_notices import KeptReaders as KeptReaders
    from doeff_events._handlers.os_files import os_file_watch_handler as os_file_watch_handler
    from doeff_events._handlers.redis_notices import BrokerAnswerMalformed as BrokerAnswerMalformed

_LAZY_EXPORTS: dict[str, str] = {
    "CloseFileWatch": "doeff_events._effects.files",
    "FileWatch": "doeff_events._effects.files",
    "FilesChanged": "doeff_events._effects.files",
    "NextFileChanges": "doeff_events._effects.files",
    "WatchFiles": "doeff_events._effects.files",
    "WatchRefused": "doeff_events._effects.files",
    "BrokerAnswerMalformed": "doeff_events._handlers.redis_notices",
    "KeptReaders": "doeff_events._handlers.memory_notices",
    "MemoryFiles": "doeff_events._handlers.memory_files",
    "memory_file_watch_handler": "doeff_events._handlers.memory_files",
    "os_file_watch_handler": "doeff_events._handlers.os_files",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
