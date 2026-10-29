"""時刻を epoch ミリ秒の整数へ換算する式の唯一の定義(GetTime の答えを、他の process や記録の刻と並べる物差しへ)。

同じ刻を 2 か所が別の丸め方(四捨五入と床)で綴ると、329.6 ms に書いた刻が 329.9 ms に刻んだ後の刻より 1 ms 後に見える
(#3855)。丸めはここの 1 つだけにし、刻を並べる所はすべてこれで綴る。
"""

from datetime import datetime, timedelta, timezone

from doeff_time._internals.validation import ensure_aware_datetime

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_MS = timedelta(milliseconds=1)


def epoch_ms_of(at: datetime) -> int:
    """timezone つきの時刻 → epoch ミリ秒(床へ丸める)。

    timedelta の整数の割り算で求め、float を経ない(float の timestamp を 1000 倍すると 1 ms ずれることがある)。起点より前の
    時刻も床へ丸める(1969-12-31T23:59:59.9995Z → -1)。時差の付き方が違う 2 つの時刻も同じ物差しに乗る。
    """
    return (ensure_aware_datetime(at, name="at") - _EPOCH) // _ONE_MS
