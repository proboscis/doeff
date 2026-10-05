# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = env_prepare_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff import EffectBase as EffectBase
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv
from doeff_cluster.shared.intent.runtime_env_model import EnvFailure as EnvFailure
from doeff_cluster.shared.intent.env_marker_model import BytecodeCounts as BytecodeCounts
ROOTS_PTH: str

@dataclass(frozen=True, kw_only=True)
class KnownRoot:
    env: RuntimeEnv
    root: str
    made_ms: int
    hy_version: str | None

@dataclass(frozen=True, kw_only=True)
class PrepareRequest:
    env: RuntimeEnv
    key: str
    platform: str
    root: str
    known: tuple = ...
    min_free_bytes: int = 0
    launched_ms: int | None = None

@dataclass(frozen=True, kw_only=True)
class StagePart:
    name: str
    how: str
    seconds: float
TREE_COPY: str
TREE_EXPAND: str

@dataclass(frozen=True, kw_only=True)
class StageTime:
    name: str
    seconds: float
    files: int | None = None
    parts: tuple = ...

@dataclass(frozen=True, kw_only=True)
class VolumeKind:
    fs_type: str
    device: str
    mount: str

@dataclass(frozen=True, kw_only=True)
class MirrorReady:
    path: str

class FetchState(StrEnum):
    PRESENT = 'present'
    FETCHED = 'fetched'
    MISSING = 'missing'

@dataclass(frozen=True, kw_only=True)
class RepoMirror:
    name: str
    mirror: str

@dataclass(frozen=True, kw_only=True)
class EnvMarker:
    env: RuntimeEnv
    key: str
    platform: str
    stages: tuple
    downloaded: int
    built: int
    interpreter: str
    child_protocol: int
    bytecode: BytecodeCounts | None = None
    volume: VolumeKind | None = None
    startup_seconds: float | None = None
    hy_version: str | None = None

@dataclass(frozen=True, kw_only=True)
class WheelReady:
    path: str
    built: bool

@dataclass(frozen=True, kw_only=True)
class SyncReport:
    downloaded: int

@dataclass(frozen=True, kw_only=True)
class CarryFrom:
    tree: str
    commit: str

@dataclass(frozen=True, kw_only=True)
class BytecodeTree:
    tree: str
    roots: tuple
    mirror: str
    commit: str
    carry: CarryFrom | None
    declared: bool

@dataclass(frozen=True, kw_only=True)
class TreeProblem:
    tree: str
    detail: str

@dataclass(frozen=True, kw_only=True)
class BytecodeReport:
    interpreter: str
    counts: BytecodeCounts
    problems: tuple

@dataclass(frozen=True, kw_only=True)
class ProbeReport:
    child_protocol: int
    misplaced: tuple

@dataclass(frozen=True, kw_only=True)
class EnvReady:
    env: RuntimeEnv
    key: str
    root: str
    stages: tuple
    downloaded: int
    built: int
    interpreter: str

@dataclass(frozen=True, kw_only=True)
class PrepareState:
    mirrors: tuple = ...
    wheels: tuple = ...
    downloaded: int = 0
    built: int = 0
    interpreter: str = ''
    stages: tuple = ...
    bytecode: BytecodeCounts | None = None
    written: int | None = None
    parts: tuple = ...
    volume: VolumeKind | None = None
    hy_version: str | None = None

@dataclass(frozen=True)
class StageStarted(EffectBase):
    name: str

@dataclass(frozen=True)
class PrepareNote(EffectBase):
    text: str

@dataclass(frozen=True)
class DiskFree(EffectBase):
    path: str

@dataclass(frozen=True)
class ReadVolume(EffectBase):
    path: str

@dataclass(frozen=True)
class EnsureMirror(EffectBase):
    url: str

@dataclass(frozen=True)
class FetchCommit(EffectBase):
    mirror: str
    commit: str

@dataclass(frozen=True)
class MaterializeTree(EffectBase):
    mirror: str
    commit: str
    dest: str
    reuse: str | None

@dataclass(frozen=True)
class TreeHash(EffectBase):
    mirror: str
    commit: str
    path: str

@dataclass(frozen=True)
class EnsureNativeWheel(EffectBase):
    key: str
    package: str
    source_dir: str

@dataclass(frozen=True)
class SyncProject(EffectBase):
    project_dir: str
    python: str
    groups: tuple
    no_install: tuple

@dataclass(frozen=True)
class InstallWheels(EffectBase):
    project_dir: str
    wheels: tuple

@dataclass(frozen=True)
class WriteImportRoots(EffectBase):
    project_dir: str
    roots: tuple

@dataclass(frozen=True)
class ReadEditableRoots(EffectBase):
    project_dir: str
    root: str

@dataclass(frozen=True)
class ReadHyVersion(EffectBase):
    project_dir: str

@dataclass(frozen=True)
class CompileTrees(EffectBase):
    project_dir: str
    trees: tuple
    entries: tuple

@dataclass(frozen=True)
class ProbeImports(EffectBase):
    project_dir: str
    roots: tuple

@dataclass(frozen=True)
class WriteEnvMarker(EffectBase):
    root: str
    marker: EnvMarker
