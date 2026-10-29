# moved-submodule-shim: doeff_events.event_loop -> doeff_events._event_loop
"""Moved: ``doeff_events.event_loop`` is now ``doeff_events._event_loop`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_events.event_loop…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_events._event_loop import LoopStop as LoopStop
    from doeff_events._event_loop import Publish as Publish
    from doeff_events._event_loop import StopArrived as StopArrived
    from doeff_events._event_loop import T as T
    from doeff_events._event_loop import WaitForEvent as WaitForEvent
    from doeff_events._event_loop import Watch as Watch
    from doeff_events._event_loop import begin_watch as begin_watch
    from doeff_events._event_loop import end_watch as end_watch
    from doeff_events._event_loop import next_event as next_event
    from doeff_events._event_loop import stopped_value as stopped_value

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_events._event_loop", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_events._event_loop")
