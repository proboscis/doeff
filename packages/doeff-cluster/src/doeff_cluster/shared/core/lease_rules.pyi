# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = lease_rules.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff_cluster.shared.intent.protocol import BodyInvalid as BodyInvalid
from doeff_cluster.shared.intent.semaphore_model import SEMAPHORE_PREFIX as SEMAPHORE_PREFIX
from doeff_cluster.shared.intent.semaphore_model import FENCE_MARGIN_MS as FENCE_MARGIN_MS
from doeff_cluster.shared.intent.semaphore_model import LEASE_OPS as LEASE_OPS
from doeff_cluster.shared.intent.semaphore_model import LEASE_MAX_TTL_MS as LEASE_MAX_TTL_MS
from doeff_cluster.shared.intent.semaphore_model import LeaseAnswer as LeaseAnswer

def lease_holder(job: str, instance: str) -> str:
    ...

def holder_tokens_prefix(holder: str) -> str:
    ...

def lease_timing_refusal(ttl_seconds: int | float, margin_ms: int) -> str | None:
    ...

def fence_verdict(hold: dict | None, now_ms: int, margin_ms: int) -> str | None:
    ...

def lease_op(row: dict | None, op: str, token: str, permits: int, ttl_ms: int, now_ms: int) -> tuple:
    ...

def semaphore_write_refusal(before: object, after: object, now_ms: int) -> str | None:
    ...

def semaphore_key(name: str) -> str:
    ...

def live_holders(row: dict | None, now_ms: int) -> dict:
    ...

def claim(row: dict | None, permits: int, token: str, now_ms: int, ttl_ms: int) -> dict | None:
    ...

def renew(row: dict | None, token: str, now_ms: int, ttl_ms: int) -> dict | None:
    ...

def release(row: dict | None, token: str, now_ms: int) -> tuple:
    ...

def drop_holders(row: object, prefix: str) -> dict | None:
    ...
