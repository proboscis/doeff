# moved-submodule-shim: doeff_events.effects -> doeff_events._effects
"""Moved: ``doeff_events.effects`` is now ``doeff_events._effects`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_events.effects…`` runs the moved module. Deleted by
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
    from doeff_events._effects import AckAnnouncement as AckAnnouncement
    from doeff_events._effects import Announce as Announce
    from doeff_events._effects import Announcement as Announcement
    from doeff_events._effects import ArmedTimer as ArmedTimer
    from doeff_events._effects import ArmedTimers as ArmedTimers
    from doeff_events._effects import ArmedTimersEffect as ArmedTimersEffect
    from doeff_events._effects import ArmTimer as ArmTimer
    from doeff_events._effects import ArmTimerEffect as ArmTimerEffect
    from doeff_events._effects import AwaitBrokerBack as AwaitBrokerBack
    from doeff_events._effects import BrokerPatience as BrokerPatience
    from doeff_events._effects import BrokerUnreachable as BrokerUnreachable
    from doeff_events._effects import ChannelSubscription as ChannelSubscription
    from doeff_events._effects import CloseFileWatch as CloseFileWatch
    from doeff_events._effects import CloseSubscription as CloseSubscription
    from doeff_events._effects import DisarmTimer as DisarmTimer
    from doeff_events._effects import DisarmTimerEffect as DisarmTimerEffect
    from doeff_events._effects import FilesChanged as FilesChanged
    from doeff_events._effects import FileWatch as FileWatch
    from doeff_events._effects import NextAnnouncement as NextAnnouncement
    from doeff_events._effects import NextFileChanges as NextFileChanges
    from doeff_events._effects import ProbeBroker as ProbeBroker
    from doeff_events._effects import Publish as Publish
    from doeff_events._effects import PublishEffect as PublishEffect
    from doeff_events._effects import PublishGap as PublishGap
    from doeff_events._effects import PublishGapEffect as PublishGapEffect
    from doeff_events._effects import ReadBrokerPatience as ReadBrokerPatience
    from doeff_events._effects import SourceFailed as SourceFailed
    from doeff_events._effects import SourceMissed as SourceMissed
    from doeff_events._effects import SourceResumed as SourceResumed
    from doeff_events._effects import SourceStalled as SourceStalled
    from doeff_events._effects import SourceStarted as SourceStarted
    from doeff_events._effects import StopArrived as StopArrived
    from doeff_events._effects import SubscribeChannels as SubscribeChannels
    from doeff_events._effects import TimerFired as TimerFired
    from doeff_events._effects import WaitForEvent as WaitForEvent
    from doeff_events._effects import WaitForEventEffect as WaitForEventEffect
    from doeff_events._effects import WaitForEvents as WaitForEvents
    from doeff_events._effects import WaitForEventsEffect as WaitForEventsEffect
    from doeff_events._effects import WatchFiles as WatchFiles
    from doeff_events._effects import WatchRefused as WatchRefused
    from doeff_events._effects import publish as publish
    from doeff_events._effects import publish_effect_type as publish_effect_type
    from doeff_events._effects import publish_gap as publish_gap
    from doeff_events._effects import wait_for_event as wait_for_event
    from doeff_events._effects import wait_for_events as wait_for_events

_OLD = "doeff_events.effects"
_NEW = "doeff_events._effects"


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

    runpy.run_module("doeff_events._effects", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_events._effects")
