"""組み立て点の道具 — handler の積み方は 1 か所に書く。

外側から順に: scheduled → await(httpx の async)→ try → slog の出口(journal / stderr / 捨てる)
→ HTTP の実体 → cache の実体 → jev(Judge → HttpRequest)→ memo(cache の照合)→ program。
効果は内側の handler から外へ流れる: program の Judge は memo が受け、外れなら jev が受けて
HttpRequest を出し、それを HTTP の実体が捌く。
"""

import os
from collections.abc import Callable, Sequence
from typing import Any

from doeff_core_effects import (
    await_handler,
    http_production_handler,
    slog_discard_handler,
    slog_handler,
    try_handler,
)
from doeff_core_effects.cache_handlers import in_memory_cache_handler, sqlite_cache_handler
from doeff_core_effects.scheduler import scheduled

from doeff import run, with_handlers
from doeff_jev.handlers.journal import journal_handler
from doeff_jev.handlers.production import jev_handler, jev_memo_handler
from doeff_jev.target import JevTarget, target_from_process_environment

DEFAULT_CACHE_DB = "~/.cache/doeff-jev/answers.sqlite"
DEFAULT_JOURNAL = "~/.local/state/doeff-jev/journal.log"


def judge_stack(
    target: JevTarget, *,
    http: Callable[[Any], Any] | None = None,
    cache: Callable[[Any], Any] | None = None,
    journal: str | None = DEFAULT_JOURNAL,
    caller: str = "",
    log_stderr: bool = False,
) -> list:
    """本番の handler の列(外側が先)。`http` / `cache` を差し替えるとテストや別の実体になる。

    journal=None で記録なし。cache=None で in-memory(process 内だけ)。
    """
    sink = slog_handler if log_stderr else slog_discard_handler
    # 効果は内側から外へ流れる: jev の slog("jev_judge") は journal(内)が消費し、
    # それ以外の slog は sink(外)へ抜ける。journal を sink の外に置くと sink が先に飲む。
    layers: list = [await_handler(), try_handler, sink]
    if journal:
        layers.append(journal_handler(os.path.expanduser(journal), caller=caller))
    layers.append(http if http is not None else http_production_handler())
    layers.append(cache if cache is not None else in_memory_cache_handler())
    layers.append(jev_handler(target))
    layers.append(jev_memo_handler(target))
    return layers


def durable_cache(path: str = DEFAULT_CACHE_DB) -> Callable[[Any], Any]:
    """process をまたいで答えを残す cache の実体(SQLite)。"""
    expanded = os.path.expanduser(path)
    os.makedirs(os.path.dirname(expanded), exist_ok=True)
    return sqlite_cache_handler(expanded)


def run_judgment(program: Any, *, target: JevTarget | None = None,
                 layers: Sequence[Callable[[Any], Any]] | None = None, **stack_kwargs: Any) -> Any:
    """program を本番の積みで走らせて結果を返す(CLI・hook の入口)。

    `target` を省くと process の環境から解く。`layers` を渡すとその積みをそのまま使う。
    """
    resolved = target if target is not None else target_from_process_environment()
    stack = list(layers) if layers is not None else judge_stack(resolved, **stack_kwargs)
    return run(scheduled(with_handlers(stack, program)))
