"""反例の確認: 同じ入力(失敗した cache ping の stdout の result 行 + stderr の 1 行)を 3 つの置き場で
cache-host-probe に読ませ、HostCacheRecord.reason が置き場で変わるかを見る。"""
import shutil
import sys
import tempfile
from pathlib import Path

import doeff_hy  # noqa: F401

sys.path.insert(0, str(Path.cwd() / "packages/doeff-agents/tests"))

import doeff_agents
from doeff import run
from doeff_agents.sessionhost.acp.cache_operation import MaintenanceState
from doeff_agents.sessionhost.cache_host import cache_host_probe
from doeff_agents.sessionhost.cache_host_model import HostCacheRecord
from doeff_agents.sessionhost.headless_events import (
    FileEventStore, HeadlessEventAppend, MemoryEventStore, key_of_locator)
from doeff_agents.sessionhost.headless_outbox import OutboxEventStore
from doeff_agents.sessionhost.headless_process import HeadlessRegistry
from doeff_agents.sessionhost.store import StoreActor
from doeff_agents.sessionhost.substrate_headless import headless_substrate
from sessionhost_cache_maintenance_deftests import residency_world

print("doeff_agents from:", doeff_agents.__file__)
AT = "1970-01-01T00:55:00+00:00"
for kind in ("memory", "file", "outbox"):
    root = Path(tempfile.mkdtemp(prefix=f"probe-{kind}-"))
    actor = None
    if kind == "memory":
        store = MemoryEventStore()
    elif kind == "file":
        store = FileEventStore()
    else:
        actor = StoreActor(str(root / "agentd.sqlite"))
        store = OutboxEventStore(actor.submit)
    locator = f"{root}/resident.events.jsonl.cache-ping-err"
    store.open_stream(locator)
    store.append(HeadlessEventAppend(locator, "stdout", '{"type": "result", "is_error": true}', AT))
    store.append(HeadlessEventAppend(locator, "stderr", "API Error: 529 overloaded", AT))
    pending = HostCacheRecord("ping-err", "resident", 3600000, "process", locator,
                              state=MaintenanceState.RUNNING, started_at=3300000)
    state = {"now": 3300000, "receipts": []}
    hosted = headless_substrate(HeadlessRegistry(store))
    done = run(hosted(residency_world(state)(cache_host_probe(pending))))
    print(f"{kind:7s} state={done.state.value:9s} reason={done.reason!r}")
    if actor is not None:
        actor.close()
    shutil.rmtree(root, ignore_errors=True)
print("key_of_locator(stdout locator)       =", key_of_locator("/r/resident.events.jsonl.cache-ping-err"))
print("key_of_locator(locator + '.stderr')  =", key_of_locator("/r/resident.events.jsonl.cache-ping-err.stderr"))
