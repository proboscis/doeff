# moved-submodule-shim: doeff_events.notice_laws -> doeff_events._notice_laws
# moved-submodule-shim-names doeff_events.notice_laws:
#   Announce Announcement BROKER_LAWS EVENT_LAWS EventLawHarness GAP_LAWS GapLawHarness LawBroken
#   LawNote LawState MarkGap NextAnnouncement NoticeGapMarked NoticeGapTold NoticeRoute NoticeSent
#   Publish PublishGap SourceMissed SourceResumed SourceStalled SourceStarted SubscribeChannels
#   WaitForEvent law_gap_is_told_before_the_next_notice law_gap_keeps_the_order_of_later_notices
#   law_gap_survives_a_second_cut law_latest_state_wins_after_a_gap
#   law_missed_notice_is_told_once_the_broker_is_back law_notice_reaches_only_current_subscribers
#   law_routes law_start_and_return_are_told_once law_start_is_told_after_the_subscription
#   law_subscription_precedes_the_body law_unsure_send_is_told_as_a_gap
"""Moved: ``doeff_events.notice_laws`` is now ``doeff_events._notice_laws`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_events.notice_laws…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_events._notice_laws import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_events._notice_laws", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_events._notice_laws")
