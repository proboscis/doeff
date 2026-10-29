# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _limits.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_codex._lines import RateLimits as RateLimits
from doeff_codex._lines import TurnEnded as TurnEnded
from doeff_codex._lines import UsageLimitHit as UsageLimitHit
from doeff_codex._lines import USAGE_LIMIT_KIND as USAGE_LIMIT_KIND
SPENT_PERCENT: int

def merged_limits(kept: tuple, fresh: RateLimits) -> _Program[tuple, object]:
    ...

def usage_limit_hit_of(end: TurnEnded, limits: tuple) -> _Program[UsageLimitHit | None, object]:
    ...
