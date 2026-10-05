# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = env_prepare.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from datetime import datetime as datetime
import re as re
from doeff_time import GetMonotonic as GetMonotonic
from doeff_time import GetTime as GetTime
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv
from doeff_cluster.shared.intent.runtime_env_model import RepoCheckout as RepoCheckout
from doeff_cluster.shared.intent.runtime_env_model import RepoLocation as RepoLocation
from doeff_cluster.shared.intent.runtime_env_model import EnvFailure as EnvFailure
from doeff_cluster.shared.intent.runtime_env_model import EnvFailureKind as EnvFailureKind
from doeff_cluster.shared.intent.runtime_env_model import CHILD_PROTOCOL as CHILD_PROTOCOL
from doeff_cluster.shared.intent.runtime_env_model import SUPPORTED_CHILD_PROTOCOLS as SUPPORTED_CHILD_PROTOCOLS
from doeff_cluster.shared.core.runtime_env_rules import env_failure as env_failure
from doeff_cluster.shared.core.runtime_env_rules import native_key as native_key
from doeff_cluster.shared.core.runtime_env_rules import root_split as root_split
from doeff_cluster.shared.core.runtime_env_rules import hyx_runtime_env_XgreaterHthan_signXjson as hyx_runtime_env_XgreaterHthan_signXjson
from doeff_cluster.shared.core.runtime_env_rules import url_location as url_location
from doeff_cluster.shared.core.runtime_env import project_dir as project_dir
from doeff_cluster.worker.intent.env_prepare_model import PrepareRequest as PrepareRequest
from doeff_cluster.worker.intent.env_prepare_model import StageTime as StageTime
from doeff_cluster.worker.intent.env_prepare_model import StagePart as StagePart
from doeff_cluster.worker.intent.env_prepare_model import TREE_COPY as TREE_COPY
from doeff_cluster.worker.intent.env_prepare_model import TREE_EXPAND as TREE_EXPAND
from doeff_cluster.worker.intent.env_prepare_model import VolumeKind as VolumeKind
from doeff_cluster.worker.intent.env_prepare_model import MirrorReady as MirrorReady
from doeff_cluster.worker.intent.env_prepare_model import FetchState as FetchState
from doeff_cluster.worker.intent.env_prepare_model import RepoMirror as RepoMirror
from doeff_cluster.worker.intent.env_prepare_model import EnvMarker as EnvMarker
from doeff_cluster.worker.intent.env_prepare_model import WheelReady as WheelReady
from doeff_cluster.worker.intent.env_prepare_model import SyncReport as SyncReport
from doeff_cluster.worker.intent.env_prepare_model import CarryFrom as CarryFrom
from doeff_cluster.worker.intent.env_prepare_model import BytecodeTree as BytecodeTree
from doeff_cluster.worker.intent.env_prepare_model import BytecodeReport as BytecodeReport
from doeff_cluster.worker.intent.env_prepare_model import ProbeReport as ProbeReport
from doeff_cluster.worker.intent.env_prepare_model import EnvReady as EnvReady
from doeff_cluster.worker.intent.env_prepare_model import PrepareState as PrepareState
from doeff_cluster.worker.intent.env_prepare_model import StageStarted as StageStarted
from doeff_cluster.worker.intent.env_prepare_model import PrepareNote as PrepareNote
from doeff_cluster.worker.intent.env_prepare_model import DiskFree as DiskFree
from doeff_cluster.worker.intent.env_prepare_model import ReadVolume as ReadVolume
from doeff_cluster.worker.intent.env_prepare_model import EnsureMirror as EnsureMirror
from doeff_cluster.worker.intent.env_prepare_model import FetchCommit as FetchCommit
from doeff_cluster.worker.intent.env_prepare_model import MaterializeTree as MaterializeTree
from doeff_cluster.worker.intent.env_prepare_model import TreeHash as TreeHash
from doeff_cluster.worker.intent.env_prepare_model import EnsureNativeWheel as EnsureNativeWheel
from doeff_cluster.worker.intent.env_prepare_model import SyncProject as SyncProject
from doeff_cluster.worker.intent.env_prepare_model import InstallWheels as InstallWheels
from doeff_cluster.worker.intent.env_prepare_model import WriteImportRoots as WriteImportRoots
from doeff_cluster.worker.intent.env_prepare_model import ReadEditableRoots as ReadEditableRoots
from doeff_cluster.worker.intent.env_prepare_model import ReadHyVersion as ReadHyVersion
from doeff_cluster.worker.intent.env_prepare_model import CompileTrees as CompileTrees
from doeff_cluster.worker.intent.env_prepare_model import ProbeImports as ProbeImports
from doeff_cluster.worker.intent.env_prepare_model import WriteEnvMarker as WriteEnvMarker
from doeff_cluster.shared.intent.env_marker_model import ENV_MARKER_FORMAT as ENV_MARKER_FORMAT
from doeff_cluster.shared.intent.env_marker_model import FileSha256 as FileSha256

def absolute_roots(env: RuntimeEnv, root: str) -> _Program[tuple, object]:
    ...

def repo_roots(env: RuntimeEnv, name: str) -> _Program[tuple, object]:
    ...

def editable_repo_roots(editable: tuple, name: str) -> _Program[tuple, object]:
    ...

def bytecode_roots(env: RuntimeEnv, editable: tuple, name: str) -> _Program[tuple, object]:
    ...

def reuse_tree(known: tuple, repo: RepoCheckout) -> _Program[str | None, object]:
    ...
MACRO_REPO: str

@dataclass(frozen=True, kw_only=True)
class CarryCandidate:
    tree: str
    root: str
    commit: str
    made_ms: int
    same_commit: bool
    same_macros: bool

def located_repos(repos: tuple) -> _Program[tuple, object]:
    ...

def carry_candidates(known: tuple, env: RuntimeEnv, name: str, hy_version: str | None) -> _Program[tuple, object]:
    ...

def carry_source(known: tuple, env: RuntimeEnv, name: str, hy_version: str | None) -> _Program[CarryFrom | None, object]:
    ...

def hyx_env_marker_XgreaterHthan_signXjson(marker: EnvMarker) -> _Program[dict, object]:
    ...
MOUNT_ESCAPE: re.Pattern[str]

def mount_unescaped(text: str) -> _Program[str, object]:
    ...

def volume_of_mountinfo(text: str, path: str) -> _Program[VolumeKind | None, object]:
    ...

def timing_line(stages: tuple, volume: VolumeKind | None, startup: float | None) -> _Program[str, object]:
    ...

def stage_disk(request: PrepareRequest, state: PrepareState) -> _Program[PrepareState | EnvFailure, object]:
    ...

def stage_mirrors(request: PrepareRequest, state: PrepareState) -> _Program[PrepareState | EnvFailure, object]:
    ...

def stage_trees(request: PrepareRequest, state: PrepareState) -> _Program[PrepareState, object]:
    ...

def stage_lock(request: PrepareRequest, state: PrepareState) -> _Program[PrepareState | EnvFailure, object]:
    ...

def stage_native(request: PrepareRequest, state: PrepareState) -> _Program[PrepareState | EnvFailure, object]:
    ...

def stage_sync(request: PrepareRequest, state: PrepareState) -> _Program[PrepareState | EnvFailure, object]:
    ...

def stage_wheels(request: PrepareRequest, state: PrepareState) -> _Program[PrepareState | EnvFailure, object]:
    ...

def stage_roots(request: PrepareRequest, state: PrepareState) -> _Program[PrepareState, object]:
    ...
BYTECODE_STAGE: str

def bytecode_trees(request: PrepareRequest, state: PrepareState, editable: tuple) -> _Program[tuple, object]:
    ...

def bytecode_outcome(trees: tuple, report: BytecodeReport | EnvFailure, state: PrepareState) -> _Program[PrepareState | EnvFailure, object]:
    ...

def stage_bytecode(request: PrepareRequest, state: PrepareState) -> _Program[PrepareState | EnvFailure, object]:
    ...

def stage_probe(request: PrepareRequest, state: PrepareState) -> _Program[PrepareState | EnvFailure, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class Stage:
    name: str
    run: Callable
STAGES: tuple[Stage, ...]

def prepare_env(request: PrepareRequest) -> _Program[EnvReady | EnvFailure, object]:
    ...
