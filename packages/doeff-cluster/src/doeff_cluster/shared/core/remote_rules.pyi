"""remote_rules.hy の公開面の型(型検査のための宣言 — 実行時は remote_rules.hy を読む)。

remote_rules.hy は Hy の module なので、pyright は中の defk の形を読めない(runtime_env_rules.pyi と同じ理由・同じ形)。
remote-job は needs を検めてから RemoteJob を出し、実行先の program の戻り値を返す Program(#2564)— 戻り値の型は program ごと。
program-sha は deff(普通の関数)、version-diffs・diffs-text・version-mismatch・failed-from は普通の関数。
"""

from typing import Any, TypeVar

from doeff import EffectBase, Program

from doeff_cluster.shared.intent.remote_model import TaskFailed, VersionDiff

T = TypeVar("T")

def remote_job(
    program: Program[T, Any] | EffectBase,
    *,
    needs: frozenset[str] = ...,
    name: str = ...,
    environ: dict[str, str] | None = ...,
) -> Program[T, Any]: ...
def version_diffs(expected: dict[str, str], actual: dict[str, str]) -> tuple[VersionDiff, ...]: ...
def diffs_text(diffs: tuple[VersionDiff, ...]) -> str: ...
def version_mismatch(expected: dict[str, str], actual: dict[str, str]) -> str | None: ...
def program_sha(blob: str) -> str: ...
def failed_from(error: BaseException) -> TaskFailed: ...
