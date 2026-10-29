# moved-submodule-shim: doeff_events.effects -> doeff_events._effects
# moved-submodule-shim-names doeff_events.effects:
#   AckAnnouncement Announce Announcement ArmTimer ArmTimerEffect ArmedTimer ArmedTimers
#   ArmedTimersEffect AwaitBrokerBack BrokerPatience BrokerUnreachable ChannelSubscription
#   CloseFileWatch CloseSubscription DisarmTimer DisarmTimerEffect FileWatch FilesChanged
#   NextAnnouncement NextFileChanges ProbeBroker Publish PublishEffect PublishGap PublishGapEffect
#   ReadBrokerPatience SourceFailed SourceMissed SourceResumed SourceStalled SourceStarted
#   StopArrived SubscribeChannels TimerFired WaitForEvent WaitForEventEffect WaitForEvents
#   WaitForEventsEffect WatchFiles WatchRefused publish publish_effect_type publish_gap
#   wait_for_event wait_for_events
# moved-submodule-shim-names doeff_events.effects.events:
#   Publish PublishEffect PublishGap PublishGapEffect SourceFailed SourceResumed SourceStalled
#   SourceStarted StopArrived WaitForEvent WaitForEventEffect WaitForEvents WaitForEventsEffect
#   publish publish_effect_type publish_gap wait_for_event wait_for_events
# moved-submodule-shim-names doeff_events.effects.files:
#   CloseFileWatch FileWatch FilesChanged NextFileChanges WatchFiles WatchRefused
# moved-submodule-shim-names doeff_events.effects.notices:
#   AckAnnouncement Announce Announcement AwaitBrokerBack BrokerPatience BrokerUnreachable
#   ChannelSubscription CloseSubscription KEPT_REDELIVERY_SECONDS NextAnnouncement ProbeBroker
#   ReadBrokerPatience SubscribeChannels
# moved-submodule-shim-names doeff_events.effects.timers:
#   ArmTimer ArmTimerEffect ArmedTimer ArmedTimers ArmedTimersEffect DisarmTimer DisarmTimerEffect
#   TimerFired
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
    from doeff_events._effects import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

_OLD = "doeff_events.effects"
_NEW = "doeff_events._effects"


class _MovedSubmodules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Answers ``<old>.<sub>`` with the ``<new>.<sub>`` module itself (one module, not a second copy)."""

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if not fullname.startswith(_OLD + ".") or importlib.util.find_spec(self._target(fullname)) is None:
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec: ModuleSpec) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        sys.modules[module.__name__] = import_module(self._target(module.__name__))

    def get_code(self, fullname: str) -> CodeType:
        # python -m <old>.<sub>: run the moved module as __main__.
        target = self._target(fullname)
        source = f"import runpy\nrunpy.run_module({target!r}, run_name='__main__', alter_sys=True)\n"
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
