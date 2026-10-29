# moved-submodule-shim: doeff_events.handlers -> doeff_events._handlers
"""Moved: ``doeff_events.handlers`` is now ``doeff_events._handlers`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_events.handlers…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import importlib.abc
import importlib.util
import sys
from collections.abc import Sequence
from importlib import import_module
from importlib.machinery import ModuleSpec
from types import CodeType, ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_events._handlers import GAP_NOTICE as GAP_NOTICE
    from doeff_events._handlers import BrokerConnectionsExhausted as BrokerConnectionsExhausted
    from doeff_events._handlers import Drop as Drop
    from doeff_events._handlers import EventBus as EventBus
    from doeff_events._handlers import GapAnswer as GapAnswer
    from doeff_events._handlers import MarkGap as MarkGap
    from doeff_events._handlers import MemoryBroker as MemoryBroker
    from doeff_events._handlers import MemoryFiles as MemoryFiles
    from doeff_events._handlers import NoticeDropped as NoticeDropped
    from doeff_events._handlers import NoticeGapMarked as NoticeGapMarked
    from doeff_events._handlers import NoticeGapTold as NoticeGapTold
    from doeff_events._handlers import NoticeRoute as NoticeRoute
    from doeff_events._handlers import NoticeSent as NoticeSent
    from doeff_events._handlers import NoticeSourceUnreachable as NoticeSourceUnreachable
    from doeff_events._handlers import PublishAnswer as PublishAnswer
    from doeff_events._handlers import SubscriberQueue as SubscriberQueue
    from doeff_events._handlers import UnroutedNotice as UnroutedNotice
    from doeff_events._handlers import WhenUnsent as WhenUnsent
    from doeff_events._handlers import announce_file_change as announce_file_change
    from doeff_events._handlers import broker_back_by_retry as broker_back_by_retry
    from doeff_events._handlers import broker_patience_handler as broker_patience_handler
    from doeff_events._handlers import cut_broker as cut_broker
    from doeff_events._handlers import event_handler as event_handler
    from doeff_events._handlers import hold_broker as hold_broker
    from doeff_events._handlers import memory_file_watch_handler as memory_file_watch_handler
    from doeff_events._handlers import memory_notice_handler as memory_notice_handler
    from doeff_events._handlers import notice_events_handler as notice_events_handler
    from doeff_events._handlers import os_file_watch_handler as os_file_watch_handler
    from doeff_events._handlers import redis_notice_handler as redis_notice_handler
    from doeff_events._handlers import release_broker as release_broker
    from doeff_events._handlers import restore_broker as restore_broker
    from doeff_events._handlers import subscribed_event_handler as subscribed_event_handler
    from doeff_events._handlers import timer_handler as timer_handler

_OLD = "doeff_events.handlers"
_NEW = "doeff_events._handlers"


class _MovedSubmodules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Answers ``<old>.<sub>`` with the ``<new>.<sub>`` module itself (one module, not a second copy)."""

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if (
            not fullname.startswith(_OLD + ".")
            or importlib.util.find_spec(self._target(fullname)) is None
        ):
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec: ModuleSpec) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        sys.modules[module.__name__] = import_module(self._target(module.__name__))

    def get_code(self, fullname: str) -> CodeType:
        # python -m <old>.<sub>: run the moved module as __main__.
        target = self._target(fullname)
        source = (
            f"import runpy\nrunpy.run_module({target!r}, run_name='__main__', alter_sys=True)\n"
        )
        return compile(source, f"<moved {fullname}>", "exec")

    @staticmethod
    def _target(fullname: str) -> str:
        return _NEW + fullname[len(_OLD) :]


if not any(isinstance(finder, _MovedSubmodules) for finder in sys.meta_path):
    sys.meta_path.insert(0, _MovedSubmodules())

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_events._handlers", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_events._handlers")
