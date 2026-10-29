"""store の書き込みの健康と host の readiness の判断(純粋・I/O なし)。

実弾 2026-09-25: k3s の agentd-pool-0 / -1 の状態の volume(agentd-state・2Gi)が 100% になり、sqlite の
書き込みが全部 ``database or disk is full`` で落ちた。pool-0 は Running のまま書き込みが全部失敗し、器は
socket で答え続けたので readiness(socket の connect)は緑のまま —— 書けない host が新しい手番を受け続けた。

ここが持つのは 2 つの判断だけ:

* ``storage_failure_name`` — op の例外が「保管の書き込みの失敗」(容量・入出力・読み取り専用・壊れた file)か。
  制約違反や program の誤り(IntegrityError・ProgrammingError・RuntimeError)は数えない —— それは store の
  健康ではなく呼び手の不具合で、readiness を落としても直らない。lock の待ち(SQLITE_BUSY / LOCKED)も数えない。
* ``next_health`` / ``readiness_of`` — 続けて何回失敗したか・何回で readiness を落とすか。書き込みが 1 度でも
  成功したら数え直す(level-triggered — 容量が戻れば自分で ready に戻る)。

数える点は store の actor(store.hy ``StoreActor``)の 1 点 —— すべての読み書きがそこを通る。
"""

# pyright: strict
import sqlite3
from dataclasses import dataclass, replace

#: 保管の書き込みの失敗として数える SQLite の基本の結果の名(拡張の名 ``SQLITE_IOERR_WRITE`` 等は基本の名へ畳む)。
STORAGE_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "SQLITE_FULL",
        "SQLITE_IOERR",
        "SQLITE_READONLY",
        "SQLITE_CANTOPEN",
        "SQLITE_CORRUPT",
        "SQLITE_NOTADB",
    }
)

#: 何回続けて失敗したら readiness を落とすかの既定(env ``DOEFF_AGENTD_STORE_WRITE_FAILURE_LIMIT`` で変える)。
#: heartbeat は lease の TTL の 1/3 ごとに書くので、書く手番が無くても数十秒で 3 回に届く。
DEFAULT_STORE_WRITE_FAILURE_LIMIT = 3


@dataclass(frozen=True)
class StoreWriteHealth:
    """store の書き込みの健康(actor が op ごとに進める値)。"""

    consecutive_failures: int = 0
    last_error: str | None = None
    failing_since: str | None = None


@dataclass(frozen=True)
class Readiness:
    """host が新しい手番を受けられるか。ready = False の時は reason が理由を名乗る。"""

    ready: bool
    reason: str | None


def storage_failure_name(error: BaseException) -> str | None:
    """op の例外が保管の書き込みの失敗なら、その SQLite の基本の結果の名。それ以外は None。"""
    if not isinstance(error, sqlite3.DatabaseError):
        return None
    name = getattr(error, "sqlite_errorname", None)
    if not isinstance(name, str):
        return None
    base = "_".join(name.split("_")[:2])
    return base if base in STORAGE_FAILURE_CODES else None


def next_health(health: StoreWriteHealth, failure: str | None, detail: str, wrote: bool, at: str) -> StoreWriteHealth:
    """op 1 つの結末で健康を進める。failure = ``storage_failure_name`` の答え・wrote = op が store を変えたか
    (conn.total_changes が進んだ)・at = 観測の時刻(ISO)。失敗でも書き込みでもない op(読み)は値を変えない。"""
    if failure is not None:
        return StoreWriteHealth(
            consecutive_failures=health.consecutive_failures + 1,
            last_error=f"{failure}: {detail}",
            failing_since=health.failing_since if health.failing_since is not None else at,
        )
    if wrote and health.consecutive_failures > 0:
        return replace(health, consecutive_failures=0, failing_since=None)
    return health


def readiness_of(health: StoreWriteHealth, limit: int) -> Readiness:
    """続けた失敗が limit に届いたら ready = False。limit は 1 以上。"""
    if limit < 1:
        raise ValueError(f"store write failure limit must be >= 1 (got {limit})")
    if health.consecutive_failures >= limit:
        return Readiness(
            ready=False,
            reason=(
                f"store writes failed {health.consecutive_failures} times in a row "
                f"since {health.failing_since}: {health.last_error}"
            ),
        )
    return Readiness(ready=True, reason=None)
