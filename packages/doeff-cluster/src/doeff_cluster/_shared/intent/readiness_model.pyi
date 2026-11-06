# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = readiness_model.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from typing import TypeAlias
from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase
from typing import ClassVar as ClassVar
from doeff_cluster._shared.intent.record_spec import RecordSpec as RecordSpec
from doeff_cluster._shared.intent.record_spec import RecordMode as RecordMode
from doeff_cluster._shared.intent.record_spec import Unexecuted as Unexecuted
ROLE_ACTIVE: str
ROLE_STANDBY: str
HANDOFF_TIMEOUT_SECONDS: int
READINESS_KEYS: tuple[str, ...]
RETIREMENT_HOLD: str
RETIREMENT_HOLD_PERMITS: int
REASON_KEPT_CHARS: int
JsonField: TypeAlias = dict | list | str | int | float | bool | None

@dataclass(frozen=True)
class ReportReady(EffectBase[None]):
    __record_spec__: ClassVar[RecordSpec]
    ready: bool
    reason: str = ''
    role: str = ...

@dataclass(frozen=True, kw_only=True)
class ReadinessClaim:
    ready: bool
    reason: str
    role: str
