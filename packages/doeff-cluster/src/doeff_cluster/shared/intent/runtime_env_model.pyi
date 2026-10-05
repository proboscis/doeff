"""runtime_env_model.hy の公開面の型(型検査のための宣言 — 実行時は runtime_env_model.hy を読む)。

runtime_env_model.hy は Hy の module なので、pyright は中を読めず、`doeff_cluster.shared.intent.runtime_env_model` の名が全部
Unknown になる。SubmitDetached.environ(EnvVar の組)を使い手が組むと、書き手に直せない赤
(Type of "EnvVar" is unknown・Argument type is unknown ほか)が使い手の file ごとに出た。
ここで型を宣言する(service_model.pyi・doeff_hy/wire.pyi と同じ形)。

- defrecord(RepoCheckout・LocalPath・RemoteRepo・NativeWheel・PythonProject・ToolRequirement・EnvVar・RuntimeEnv・EnvFailure)は凍った・キーワード
  引数だけの dataclass。欄の型は runtime_env_model.hy の注記と __post_init__ の検めに合わせる(注記が素の tuple の所は、
  検めが入れさせる要素の型で書く)。
- defenum(InvalidKind・EnvFailureKind)は StrEnum。値は名の小文字・`-` 区切り。
- 関数(env-key・runtime-env->json・runtime-env-of-json・child-environ-refusal ほか)は doeff_cluster.shared.core.runtime_env_rules
  (型の宣言は runtime_env_rules.pyi)。ここは型と定数だけ。RuntimeEnvInvalid の check-* は宣言の型が作る時に欄を検める口。
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias


RUNTIME_ENV_FORMAT: int
CHILD_PROTOCOL: int
SUPPORTED_CHILD_PROTOCOLS: frozenset[int]
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
    LOCAL_REMOTE = "local-remote"
    SENDER_SOURCE_DIFFERS = "sender-source-differs"
    NOT_IN_CHECKOUT = "not-in-checkout"
    REVISION_DIFFERS = "revision-differs"

class RuntimeEnvInvalid(ValueError):
    """宣言が誤っている(呼び手の誤り・送れない)。"""

    kind: InvalidKind
    detail: str
    def __init__(self, kind: InvalidKind, detail: str) -> None: ...
    @staticmethod
    def check_name(what: str, name: str) -> None: ...
    @staticmethod
    def check_relative(what: str, path: str) -> None: ...
    @staticmethod
    def check_tuple(what: str, value: object, item_type: type) -> None: ...

@dataclass(frozen=True, kw_only=True)
class RepoCheckout:
    """root の下に並べる repo 1 つ。"""

    name: str
    url: str
    commit: str

@dataclass(frozen=True, kw_only=True)
class LocalPath:
    """手元の path を名指す url(綴りのまま)。"""

    path: str

@dataclass(frozen=True, kw_only=True)
class RemoteRepo:
    """網の上の repo の正体(小文字の host・owner・`.git` を外した name)。"""

    host: str
    owner: str
    name: str

RepoLocation: TypeAlias = LocalPath | RemoteRepo

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
    MEMORY_KILLED = "memory-killed"

RETRYABLE_KINDS: frozenset[EnvFailureKind]

@dataclass(frozen=True, kw_only=True)
class EnvFailure:
    """準備の失敗。retryable = 一時の失敗。"""

    kind: EnvFailureKind
    detail: str
    retryable: bool
