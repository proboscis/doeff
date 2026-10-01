"""remote_model.hy の公開面の型(型検査のための宣言 — 実行時は remote_model.hy を読む・#2564)。

remote_model.hy は Hy の module なので、pyright は中を読めず、`doeff_cluster.shared.intent.remote_model` の名が全部 Unknown になる。
core/remote_rules.pyi(構築関数 remote-job と版の突き合わせ)と detached_model.pyi(DetachedVersionMismatch.diffs)がここの型を
読むので、ここで型を宣言する(runtime_env_model.pyi と同じ形)。

- effect(RemoteJob)は凍った dataclass の EffectBase。答え = 実行先の Program の戻り値で、型は program ごと(構築関数 remote-job の
  宣言が program の型から答えの型を引く)— effect の型引数は object。
- 欄の型は remote_model.hy の注記に合わせる(注記が素の frozenset / dict の所は、検めが入れさせる要素の型で書く)。
"""

from dataclasses import dataclass
from typing import Any, TypeAlias

from doeff import EffectBase, Program

@dataclass(frozen=True)
class RemoteJob(EffectBase[object]):
    """未実行の Program を走らせ、戻り値を返す。作り手は構築関数 remote_rules.remote-job を通す。"""

    program: Program[object, Any] | EffectBase
    needs: frozenset[str] = ...
    name: str = ...
    environ: dict[str, str] = ...

class RemoteJobFailed(Exception):
    """実行先で Program を走らせられなかった(業務の例外ではない)。"""

class UnsendableProgram(RemoteJobFailed):
    """送れない値を捕まえた Program。"""

@dataclass(frozen=True)
class VersionDiff:
    """版の辞書の食い違い 1 欄(無い欄は None)。"""

    field: str
    sender: str | None
    env: str | None

class VersionMismatch(RemoteJobFailed):
    """送り手と受け側の版が違う。"""

    diffs: tuple[VersionDiff, ...]
    env_key: str
    def __init__(self, message: str, diffs: tuple[VersionDiff, ...] = ..., env_key: str = ...) -> None: ...

class EnvUnavailable(RemoteJobFailed):
    """実行環境を準備できなかった。kind = EnvFailureKind の値。"""

    kind: str
    detail: str
    def __init__(self, kind: str, detail: str) -> None: ...

@dataclass(frozen=True)
class TaskSucceeded:
    value: object

@dataclass(frozen=True)
class TaskFailed:
    """kind / message / traceback は文字列。error は例外そのもの(pickle できなければ None)。"""

    kind: str
    message: str
    traceback: str
    error: BaseException | None = None

TaskOutcome: TypeAlias = TaskSucceeded | TaskFailed
