# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = clock.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from datetime import datetime as datetime
from datetime import timedelta as timedelta
from datetime import timezone as timezone
from doeff_time import GetTime as GetTime
EPOCH: datetime
ONE_MS: timedelta

def epoch_ms_of(at: datetime) -> int:
    ...

def datetime_of_epoch_ms(ms: int) -> datetime:
    ...

def now_epoch_ms() -> _Program[int, object]:
    ...
