# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = clock.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from datetime import datetime as datetime
from datetime import timedelta as timedelta
from datetime import timezone as timezone
from doeff_time import GetTime as GetTime
from doeff_time import epoch_ms_of as epoch_ms_of
EPOCH: datetime
ONE_MS: timedelta

def datetime_of_epoch_ms(ms: int) -> datetime:
    ...

def monotonic_ms_of(seconds: float) -> int:
    ...

def now_epoch_ms() -> _Program[int, object]:
    ...
