"""The single definition of the formula that converts a time to an integer of epoch milliseconds (to turn the answer of GetTime into a yardstick comparable with times of other processes and records).

If 2 places spell the same time with different rounding (round-half and floor), a time written as 329.6 ms can look 1 ms later than a time stamped as 329.9 ms
(#3855). Rounding is done in this one place only, and every place that orders times spells them with this.
"""

from datetime import datetime, timedelta, timezone

from doeff_time._internals.validation import ensure_aware_datetime

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_MS = timedelta(milliseconds=1)


def epoch_ms_of(at: datetime) -> int:
    """Timezone-aware time -> epoch milliseconds (rounded down to the floor).

    Computed with integer division of timedelta, without going through float (multiplying a float timestamp by 1000 can be off by 1 ms). Times before the epoch
    are also rounded to the floor (1969-12-31T23:59:59.9995Z -> -1). Two times with different UTC offsets land on the same yardstick.
    """
    return (ensure_aware_datetime(at, name="at") - _EPOCH) // _ONE_MS
