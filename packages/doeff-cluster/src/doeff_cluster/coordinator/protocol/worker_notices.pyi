# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = worker_notices.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff_events import MarkGap as MarkGap
from doeff_events import NoticeRoute as NoticeRoute
from doeff_cluster.coordinator.intent.worker_notices import WorkerBack as WorkerBack
from doeff_cluster.coordinator.intent.worker_notices import WorkerGone as WorkerGone
from doeff_hy.wire_malformed import Malformed as Malformed

class MalformedNotice(ValueError):
    ...

def notice_of(wire_type: type, body: str) -> WorkerGone | WorkerBack:
    ...
WORKER_NOTICE_CHANNEL: str

def notice_text(event: object) -> str:
    ...

def gone_of(body: str) -> WorkerGone:
    ...

def back_of(body: str) -> WorkerBack:
    ...

def notice_channel(event: object) -> str:
    ...
WORKER_NOTICE_ROUTES: tuple[NoticeRoute[object], ...]
WORKER_NOTICE_READS: tuple[NoticeRoute[object], ...]
