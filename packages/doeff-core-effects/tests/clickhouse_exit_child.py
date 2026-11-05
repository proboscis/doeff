"""Child process that exits via SIGTERM while a ClickHouse round trip is in flight (raised by the process-exit check in tests/test_sql_effects.hy).

Arguments: url (the fake ClickHouse endpoint) and shape.
  * shape = stop  -- the production service shape: installs os-signal-stop-handler (first StopRequested), fires a query in a separate task, and on the stop
                    signal exits without waiting for the query (the Program returns a value and Python ends normally -- the round-trip thread keeps running as a daemon).
  * shape = plain -- no SIGTERM receiver installed: receives the default SIGTERM while waiting for the query's answer.
In both, when the process exits leaving the running round trip behind, the fake server sees the connection close before it writes its answer (the failure path from before the fix).
"""

from __future__ import annotations

import sys

import hy  # noqa: F401  - to load doeff_core_effects' Hy modules
from doeff_core_effects import (
    AwaitStop,
    ClickHouseDatabase,
    Spawn,
    SqlQuery,
    StopRequested,
    clickhouse_http_sql_handler,
    os_signal_stop_handler,
    scheduled,
    state,
)

from doeff import do, run, with_handlers


@do
def ask():
    """Send one query to the cold store and return the answer."""
    answer = yield SqlQuery("cold", "SELECT 1", ())
    return answer


@do
def serve_until_stop():
    """Install the stop receiver, fire a query in a separate task, and exit on the stop signal (without waiting for the query) -- how the production service stops."""
    yield StopRequested()
    yield Spawn(ask(), daemon=True)
    reason = yield AwaitStop()
    return reason


def main() -> int:
    url, shape = sys.argv[1], sys.argv[2]
    database = ClickHouseDatabase(name="cold", url=url, database="default", user="default", password="", deadline=60.0)
    handlers = [state(), os_signal_stop_handler, clickhouse_http_sql_handler((database,))]
    program = serve_until_stop() if shape == "stop" else ask()
    run(scheduled(with_handlers(handlers, program)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
