"""runtime_env_model.hy の公開面の型(型検査のための宣言 — 実行時は runtime_env_model.hy を読む)。

runtime_env_model.hy は Hy の module なので、pyright は中を読めず、`doeff_cluster.shared.intent.runtime_env_model` の名が全部
Unknown になる。SubmitDetached.environ(EnvVar の組)を使い手が組むと、書き手に直せない赤
(Type of "EnvVar" is unknown・Argument type is unknown ほか)が使い手の file ごとに出た。
ここで型を宣言する(service_model.pyi・doeff_hy/wire.pyi と同じ形)。

- defrecord(RepoCheckout・NativeWheel・PythonProject・ToolRequirement・EnvVar・RuntimeEnv・EnvFailure)は凍った・キーワード
  引数だけの dataclass。欄の型は runtime_env_model.hy の注記と __post_init__ の検めに合わせる(注記が素の tuple の所は、
  検めが入れさせる要素の型で書く)。
- defenum(InvalidKind・EnvFailureKind)は StrEnum。値は名の小文字・`-` 区切り。
- defk(env-failure・root-split・key-material・env-key・native-key・runtime-env->json・runtime-env-of-json)は呼ぶと Program を返す。
  defn(child-environ-refusal・current-platform)は普通の関数。`runtime-env->json` の Python の名は Hy の mangle の形。
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from doeff import Program
from doeff_hy.json_value import JsonValue

RUNTIME_ENV_FORMAT: int
CHILD_PROTOCOL: int
SUPPORTED_CHILD_PROTOCOLS: frozenset[int]
ENV_KEY_LENGTH: int
NAME_PATTERN: re.Pattern[str]
COMMIT_PATTERN: re.Pattern[str]
SHA256_PATTERN: re.Pattern[str]
ENV_VAR_PATTERN: re.Pattern[str]
RESERVED_ENV_PREFIXES: tuple[str, ...]
RESERVED_ENV_NAMES: frozenset[str]
SECRET_ENV_SUFFIXES: tuple[str, ...]
PATH_ENV_SUFFIXES: tuple[str, ...]

class InvalidKind(StrEnum):
    INVALID_NAME = "invalid-name"
    DUPLICATE_REPO = "duplicate-repo"
    BAD_COMMIT = "bad-commit"
    BAD_URL = "bad-url"
    BAD_SHA256 = "bad-sha256"
    UNKNOWN_REPO = "unknown-repo"
    BAD_PATH = "bad-path"
    RESERVED_ENV_VAR = "reserved-env-var"
    SECRET_ENV_VAR = "secret-env-var"
    EMPTY = "empty"
    BAD_JSON = "bad-json"
    DIRTY_TREE = "dirty-tree"
    COMMIT_NOT_ON_REMOTE = "commit-not-on-remote"
    SENDER_SOURCE_DIFFERS = "sender-source-differs"
    NOT_IN_CHECKOUT = "not-in-checkout"
    REVISION_DIFFERS = "revision-differs"

class RuntimeEnvInvalid(ValueError):
    """宣言が誤っている(呼び手の誤り・送れない)。"""

    kind: InvalidKind
    detail: str
    def __init__(self, kind: InvalidKind, detail: str) -> None: ...

@dataclass(frozen=True, kw_only=True)
class RepoCheckout:
    """root の下に並べる repo 1 つ。"""

    name: str
    url: str
    commit: str

@dataclass(frozen=True, kw_only=True)
class NativeWheel:
    """wheel で入れる native の package。"""

    package: str
    repo: str
    paths: tuple[str, ...]

@dataclass(frozen=True, kw_only=True)
class PythonProject:
    """pyproject.toml と uv.lock を持つ uv の project。"""

    repo: str
    path: str
    lock_sha256: str
    python: str
    groups: tuple[str, ...] = ()
    native: tuple[NativeWheel, ...] = ()

@dataclass(frozen=True, kw_only=True)
class ToolRequirement:
    """worker が名乗る道具。"""

    name: str
    version: str = ""

@dataclass(frozen=True, kw_only=True)
class EnvVar:
    """子 process に足す環境変数 1 つ(名と文字列の値)。名の規則は作る時に検める。"""

    name: str
    value: str

def child_environ_refusal(environ: object) -> str | None: ...

@dataclass(frozen=True, kw_only=True)
class RuntimeEnv:
    """実行環境の宣言。"""

    repos: tuple[RepoCheckout, ...]
    project: PythonProject
    import_roots: tuple[str, ...]
    env_vars: tuple[EnvVar, ...] = ()
    tools: tuple[ToolRequirement, ...] = ()
    format: int = ...
    bytecode_entries: tuple[str, ...] = ()

class EnvFailureKind(StrEnum):
    REPO_DENIED = "repo-denied"
    REPO_UNREACHABLE = "repo-unreachable"
    COMMIT_MISSING = "commit-missing"
    LOCK_MISMATCH = "lock-mismatch"
    LOCK_STALE = "lock-stale"
    SYNC_FAILED = "sync-failed"
    NATIVE_BUILD_FAILED = "native-build-failed"
    PYTHON_UNAVAILABLE = "python-unavailable"
    TOOL_MISSING = "tool-missing"
    DISK_FULL = "disk-full"
    ENV_INCOMPATIBLE = "env-incompatible"
    PREPARE_TIMEOUT = "prepare-timeout"

RETRYABLE_KINDS: frozenset[EnvFailureKind]

@dataclass(frozen=True, kw_only=True)
class EnvFailure:
    """準備の失敗。retryable = 一時の失敗。"""

    kind: EnvFailureKind
    detail: str
    retryable: bool

def env_failure(kind: EnvFailureKind, detail: str) -> Program[EnvFailure, Any]: ...
def root_split(root: str) -> Program[tuple[str, str], Any]: ...
def current_platform() -> str: ...
def key_material(env: RuntimeEnv, platform: str) -> Program[dict[str, JsonValue], Any]: ...
def env_key(env: RuntimeEnv, platform: str) -> Program[str, Any]: ...
def native_key(wheel: NativeWheel, tree_hashes: tuple[str, ...], python: str, platform: str) -> Program[str, Any]: ...
def hyx_runtime_env_XgreaterHthan_signXjson(env: RuntimeEnv) -> Program[dict[str, JsonValue], Any]: ...
def runtime_env_of_json(value: dict[str, JsonValue]) -> Program[RuntimeEnv, Any]: ...
