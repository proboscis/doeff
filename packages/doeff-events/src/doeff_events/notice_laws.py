# moved-submodule-shim: doeff_events.notice_laws -> doeff_events._notice_laws
"""Moved: ``doeff_events.notice_laws`` is now ``doeff_events._notice_laws`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_events.notice_laws…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_events._notice_laws import BROKER_LAWS as BROKER_LAWS
    from doeff_events._notice_laws import EVENT_LAWS as EVENT_LAWS
    from doeff_events._notice_laws import GAP_LAWS as GAP_LAWS
    from doeff_events._notice_laws import Announce as Announce
    from doeff_events._notice_laws import Announcement as Announcement
    from doeff_events._notice_laws import EventLawHarness as EventLawHarness
    from doeff_events._notice_laws import GapLawHarness as GapLawHarness
    from doeff_events._notice_laws import LawBroken as LawBroken
    from doeff_events._notice_laws import LawNote as LawNote
    from doeff_events._notice_laws import LawState as LawState
    from doeff_events._notice_laws import MarkGap as MarkGap
    from doeff_events._notice_laws import NextAnnouncement as NextAnnouncement
    from doeff_events._notice_laws import NoticeGapMarked as NoticeGapMarked
    from doeff_events._notice_laws import NoticeGapTold as NoticeGapTold
    from doeff_events._notice_laws import NoticeRoute as NoticeRoute
    from doeff_events._notice_laws import NoticeSent as NoticeSent
    from doeff_events._notice_laws import Publish as Publish
    from doeff_events._notice_laws import PublishGap as PublishGap
    from doeff_events._notice_laws import SourceMissed as SourceMissed
    from doeff_events._notice_laws import SourceResumed as SourceResumed
    from doeff_events._notice_laws import SourceStalled as SourceStalled
    from doeff_events._notice_laws import SourceStarted as SourceStarted
    from doeff_events._notice_laws import SubscribeChannels as SubscribeChannels
    from doeff_events._notice_laws import WaitForEvent as WaitForEvent
    from doeff_events._notice_laws import (
        law_gap_is_told_before_the_next_notice as law_gap_is_told_before_the_next_notice,
    )
    from doeff_events._notice_laws import (
        law_gap_keeps_the_order_of_later_notices as law_gap_keeps_the_order_of_later_notices,
    )
    from doeff_events._notice_laws import (
        law_gap_survives_a_second_cut as law_gap_survives_a_second_cut,
    )
    from doeff_events._notice_laws import (
        law_latest_state_wins_after_a_gap as law_latest_state_wins_after_a_gap,
    )
    from doeff_events._notice_laws import (
        law_missed_notice_is_told_once_the_broker_is_back as law_missed_notice_is_told_once_the_broker_is_back,
    )
    from doeff_events._notice_laws import (
        law_notice_reaches_only_current_subscribers as law_notice_reaches_only_current_subscribers,
    )
    from doeff_events._notice_laws import law_routes as law_routes
    from doeff_events._notice_laws import (
        law_start_and_return_are_told_once as law_start_and_return_are_told_once,
    )
    from doeff_events._notice_laws import (
        law_start_is_told_after_the_subscription as law_start_is_told_after_the_subscription,
    )
    from doeff_events._notice_laws import (
        law_subscription_precedes_the_body as law_subscription_precedes_the_body,
    )
    from doeff_events._notice_laws import (
        law_unsure_send_is_told_as_a_gap as law_unsure_send_is_told_as_a_gap,
    )

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_events._notice_laws", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_events._notice_laws")
