"""ClickHouse の往復が走っている間に SIGTERM で終わる子 process(tests/test_sql_effects.hy の process の終わりの検が起こす・card ki-41b402ad072d)。

引数: url(偽の ClickHouse の口)と shape。
  * shape = stop  — 本番の service の形: os-signal-stop-handler を据え(最初の StopRequested)、問い合わせを別の task で撃ち、止めの合図を
                    待ったら問い合わせを待たずに終わる(Program が値を返し、Python が普通に終わる — 往復の thread は daemon のまま走っている)。
  * shape = plain — SIGTERM の受け手を据えない形: 問い合わせの答えを待つ間に既定のままの SIGTERM を受ける。
どちらも、走っている往復を置いて終わると、偽の server は答えを書く前に接続の閉じを見る(直す前の落ちの道)。
"""

from __future__ import annotations

import sys

import hy  # noqa: F401  - doeff_core_effects の Hy の module を読むため
from doeff_core_effects import Spawn, scheduled, state
from doeff_core_effects.clickhouse_http_sql import ClickHouseDatabase, clickhouse_http_sql_handler
from doeff_core_effects.sql_effects import SqlQuery
from doeff_core_effects.stop_signal_effects import AwaitStop, StopRequested
from doeff_core_effects.stop_signal_handlers import os_signal_stop_handler

from doeff import do, run, with_handlers


@do
def ask():
    """置き場 cold へ問い合わせ 1 つを流し、答えを返す。"""
    answer = yield SqlQuery("cold", "SELECT 1", ())
    return answer


@do
def serve_until_stop():
    """止めの受け手を据え、問い合わせを別の task で撃ち、止めの合図で(問い合わせを待たずに)終わる — 本番の service の止め方。"""
    yield StopRequested()
    yield Spawn(ask(), daemon=True)
    reason = yield AwaitStop()
    return reason


def main() -> int:
    url, shape = sys.argv[1], sys.argv[2]
    database = ClickHouseDatabase(name="cold", url=url, database="default", user="default", password="", deadline=60.0)
    handlers = [state(), os_signal_stop_handler, clickhouse_http_sql_handler((database,))]
    program = serve_until_stop() if shape == "stop" else ask()
    run(scheduled()(with_handlers(handlers, program)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
