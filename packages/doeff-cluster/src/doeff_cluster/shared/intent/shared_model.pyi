# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = shared_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase
from typing import ClassVar as ClassVar
from doeff_cluster.shared.intent.record_spec import RecordSpec as RecordSpec
from doeff_cluster.shared.intent.record_spec import RecordMode as RecordMode
from doeff_cluster.shared.intent.record_spec import Unexecuted as Unexecuted
from doeff_hy.json_value import OpaqueJson as OpaqueJson

@dataclass(frozen=True)
class AnyExpect:

    def __repr__(self) -> str:
        ...
ANY: AnyExpect

@dataclass(frozen=True)
class ReadShared(EffectBase):
    __record_spec__: ClassVar[RecordSpec]
    prefix: str

@dataclass(frozen=True)
class WriteShared(EffectBase):
    __record_spec__: ClassVar[RecordSpec]
    key: str
    value: OpaqueJson
    expect: OpaqueJson | AnyExpect | None = ...
    ttl_seconds: int | float | None = None
