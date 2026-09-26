"""S3(仮想の時計)の実験: cache-host-probe の時刻は検の世界の ClockNow だけから来る。

正常例: 読まれたら落ちる時計を持つ登記簿を package の headless-substrate に組み、検の世界(ClockNow を答える)で
probe を走らせると成功する(登記簿の時計は読まれない)。
反例: 検の世界を外し package の handler だけで同じ probe を走らせると、ClockNow が未処理のまま止まる
(package の handler が壁時計で黙って答えることは無い)。

repo の根で `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python <この file>`。
"""

import json
import sys
from pathlib import Path

import doeff_hy  # noqa: F401

sys.path.insert(0, str(Path.cwd() / "packages/doeff-agents/tests"))

from doeff_agents.sessionhost.acp.cache_operation import MaintenanceState
from doeff_agents.sessionhost.cache_host import cache_host_probe
from doeff_agents.sessionhost.cache_host_model import HostCacheRecord
from doeff_agents.sessionhost.headless_events import (
    CACHE_MARK,
    EVENTS_SUFFIX,
    HeadlessEventAppend,
    MemoryEventStore,
)
from doeff_agents.sessionhost.headless_process import HeadlessRegistry
from doeff_agents.sessionhost.substrate_headless import headless_substrate
from doeff_vm import UnhandledEffect
from sessionhost_cache_maintenance_deftests import residency_world

from doeff import run


def raising_clock() -> str:
    raise AssertionError("the registry clock was read")


AT = "1970-01-01T00:55:00+00:00"
store = MemoryEventStore()
locator = "/state/resident" + EVENTS_SUFFIX + CACHE_MARK + "ping-s3"
assistant = {
    "type": "assistant",
    "timestamp": AT,
    "message": {
        "id": "response-s3",
        "model": "model",
        "usage": {
            "cache_read_input_tokens": 64000,
            "cache_creation_input_tokens": 42,
            "cache_creation": {"ephemeral_1h_input_tokens": 42},
        },
    },
}
for line in (json.dumps(assistant), json.dumps({"type": "result", "is_error": False})):
    store.append(HeadlessEventAppend(locator, "stdout", line, AT))
pending = HostCacheRecord(
    "ping-s3", "resident", 3600000, "process", locator,
    state=MaintenanceState.RUNNING, started_at=3300000,
)
hosted = headless_substrate(HeadlessRegistry(store, raising_clock))

done = run(hosted(residency_world({"now": 3300000, "receipts": []})(cache_host_probe(pending))))
print(f"positive: world answers ClockNow, registry clock raises -> state={done.state.value} reply.completed_at={done.reply.completed_at if done.reply else None}")

try:
    run(hosted(cache_host_probe(pending)))
    print("negative: probe finished without the world's ClockNow (the package handler answered the time)")
except UnhandledEffect as error:
    print(f"negative: without the world's ClockNow -> UnhandledEffect: {str(error).splitlines()[0][:120]}")
