# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = readiness_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase
from typing import ClassVar as ClassVar
from doeff_cluster.shared.intent.record_spec import RecordSpec as RecordSpec
from doeff_cluster.shared.intent.record_spec import RecordMode as RecordMode
from doeff_cluster.shared.intent.record_spec import Unexecuted as Unexecuted
ROLE_ACTIVE: str
ROLE_STANDBY: str
HANDOFF_TIMEOUT_SECONDS: int
READINESS_KEYS: tuple[str, ...]
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
