"""detached_model.hy の公開面の型(型検査のための宣言 — 実行時は detached_model.hy を読む・#2564)。

detached_model.hy は Hy の module なので、pyright は中を読めず、`doeff_cluster.shared.intent.detached_model` の名が全部 Unknown になる。
構築関数 submit-detached-task(core/detached_rules.pyi)の答えの型 DetachedSubmitAnswer がここに在るので、構築関数を呼ぶ使い手の file
ごとに、書き手に直せない赤(Type of "submit_detached_task" is partially unknown ほか)が出た。ここで型を宣言する
(runtime_env_model.pyi・semaphore_model.pyi と同じ形)。

- effect(SubmitDetached ほか)は凍った dataclass の EffectBase[答えの型]。答えの値(DetachedSubmitted ほか)は凍った dataclass、
  defrecord(RunnerFact ほか)は凍った・キーワード引数だけの dataclass。
- 欄の型は detached_model.hy の注記に合わせる(注記が素の Program / frozenset / tuple の所は、検めと換算が入れる要素の型で書く)。
"""

from dataclasses import dataclass
from typing import Any, TypeAlias

from doeff import EffectBase, Program

from doeff_cluster.shared.intent.remote_model import VersionDiff
from doeff_cluster.shared.intent.runtime_env_model import EnvVar

DETACHED_DEFAULT_LEASE_SECONDS: float
DETACHED_DEFAULT_RETAIN_SECONDS: float
OPEN_PHASES: tuple[str, ...]
WARMING_PHASE: str

# --- 答え ---

@dataclass(frozen=True)
class DetachedSubmitted:
    key: str
    created: bool

@dataclass(frozen=True)
class DetachedSucceeded:
    """Program が値を返した。"""

    value: object

@dataclass(frozen=True)
class DetachedFailed:
    """Program が例外を投げた(業務の失敗)。error は例外そのもの(復元できなければ None)。"""

    kind: str
    message: str
    traceback: str
    error: object = None

@dataclass(frozen=True)
class DetachedLost:
    """task が消えた(走らせ直さない)。"""

    reason: str

@dataclass(frozen=True)
class DetachedCancelled:
    """取り消した。"""

@dataclass(frozen=True)
class DetachedVersionMismatch:
    """送り手と受け側の版が合わない。diffs = 食い違った欄・env-key = 実行した env のキー(子 process が断った時だけ)。"""

    detail: str
    diffs: tuple[VersionDiff, ...] = ()
    env_key: str = ""

@dataclass(frozen=True)
class DetachedUnrunnable:
    """版は合うが走らせられない。"""

    detail: str

@dataclass(frozen=True)
class DetachedEnvUnavailable:
    """実行環境を準備できなかった。kind = EnvFailureKind の値。"""

    kind: str
    detail: str
    retryable: bool

@dataclass(frozen=True)
class DetachedUnknown:
    """その key を知らない。"""

    key: str

@dataclass(frozen=True)
class DetachedPending:
    """await の timeout を過ぎても終わっていない。"""

    key: str
    phase: str
    runner: str = ""

@dataclass(frozen=True, kw_only=True)
class RunnerFact:
    """担い手 1 つ。"""

    name: str
    provides: tuple[str, ...]
    exclusive: tuple[str, ...]
    live: bool
    draining: bool
    task_room: int

@dataclass(frozen=True, kw_only=True)
class RunnersUnreachable:
    detail: str

RunnersAnswer: TypeAlias = tuple[RunnerFact, ...] | RunnersUnreachable

@dataclass(frozen=True, kw_only=True)
class ServiceFact:
    """Service 1 つ(担い手の報告が無い欄は None)。"""

    name: str
    replicas: int | None
    failures: int | None
    last_exit_code: int | None
    last_exit_at_ms: int | None

@dataclass(frozen=True, kw_only=True)
class ServicesUnreachable:
    detail: str

ServicesAnswer: TypeAlias = tuple[ServiceFact, ...] | ServicesUnreachable

@dataclass(frozen=True, kw_only=True)
class RunnersChange:
    revision: int
    changed: bool

@dataclass(frozen=True, kw_only=True)
class RunnersWatchMissing:
    detail: str

RunnersChangeAnswer: TypeAlias = RunnersChange | RunnersWatchMissing | RunnersUnreachable

# AwaitServiceReady の答えと、coordinator の GET /resources/Service/<名> の返事の読む欄(#3470)。
@dataclass(frozen=True, kw_only=True)
class ServiceReady:
    name: str
    revision: int

@dataclass(frozen=True, kw_only=True)
class ServiceStatusWire:
    ready: str

@dataclass(frozen=True, kw_only=True)
class ServiceViewWire:
    status: ServiceStatusWire

DetachedOutcome: TypeAlias = (
    DetachedSucceeded
    | DetachedFailed
    | DetachedLost
    | DetachedCancelled
    | DetachedVersionMismatch
    | DetachedUnrunnable
    | DetachedEnvUnavailable
    | DetachedUnknown
)

@dataclass(frozen=True, kw_only=True)
class DetachedUnreachable:
    """coordinator に届かなかった(task の生死は分からない)。"""

    detail: str

DetachedAwaited: TypeAlias = DetachedOutcome | DetachedPending | DetachedUnreachable
DetachedSubmitAnswer: TypeAlias = DetachedSubmitted | DetachedUnreachable

class DetachedRefused(Exception):
    """coordinator が要求を断った(呼び手の誤り)。"""

    status: int
    message: str
    def __init__(self, status: int, message: str) -> None: ...

# --- effect ---

@dataclass(frozen=True)
class SubmitDetached(EffectBase[DetachedSubmitAnswer]):
    """切り離した task を送る。作り手は構築関数 detached_rules.submit-detached-task を通す。"""

    program: Program[object, Any]
    key: str
    needs: frozenset[str] = ...
    name: str = ""
    lease_seconds: float = ...
    retain_seconds: float = ...
    environ: tuple[EnvVar, ...] = ()

@dataclass(frozen=True)
class AwaitDetached(EffectBase[DetachedAwaited]):
    key: str
    timeout_seconds: float | None = None

@dataclass(frozen=True)
class CancelDetached(EffectBase[bool]):
    key: str

@dataclass(frozen=True)
class ReleaseDetached(EffectBase[bool]):
    key: str

@dataclass(frozen=True)
class ReadRunners(EffectBase[RunnersAnswer]):
    """task を受ける担い手の名簿を読む。"""

@dataclass(frozen=True)
class ReadServices(EffectBase[ServicesAnswer]):
    """coordinator の Service の一覧と、置き先の担い手が報告した落ちた事実を読む。"""

@dataclass(frozen=True)
class AwaitRunnersChange(EffectBase[RunnersChangeAnswer]):
    after: int
    timeout_seconds: float = 1.0

@dataclass(frozen=True)
class AwaitServiceReady(EffectBase[ServiceReady]):
    name: str
