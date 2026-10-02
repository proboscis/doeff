# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = runtime_identity_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff import EffectBase as EffectBase
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv

class IdentityFailureKind(StrEnum):
    UNDECLARED = 'undeclared'
    ROOT_UNMARKED = 'root-unmarked'
    MARKER_MISMATCH = 'marker-mismatch'
    MODULE_OUTSIDE_ROOT = 'module-outside-root'

@dataclass(frozen=True, kw_only=True)
class ModuleOrigin:
    module: str
    file: str

@dataclass(frozen=True, kw_only=True)
class RootMarker:
    format: int
    key: str
    platform: str
    env: RuntimeEnv | None

@dataclass(frozen=True, kw_only=True)
class RuntimeFacts:
    declared: RuntimeEnv | None
    key: str
    root: str
    marker: RootMarker | None
    origins: tuple[ModuleOrigin, ...]

@dataclass(frozen=True, kw_only=True)
class RepoCommit:
    name: str
    commit: str

@dataclass(frozen=True, kw_only=True)
class RuntimeIdentity:
    key: str
    root: str
    commits: tuple[RepoCommit, ...]
    pid: int

@dataclass(frozen=True, kw_only=True)
class RuntimeIdentityMismatch:
    kind: IdentityFailureKind
    detail: str
    pid: int

@dataclass(frozen=True, kw_only=True)
class ProcessFacts:
    declared_json: str
    key: str
    root: str
    marker_json: str
    origins: tuple[ModuleOrigin, ...]
    pid: int

@dataclass(frozen=True)
class ReadRuntimeFacts(EffectBase):
    modules: tuple[str, ...]
