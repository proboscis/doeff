# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = retirement_notices.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff_hy.static_types import Handler as _Handler
from doeff_cluster.foundation.host_contract import HOST_CONTRACT as HOST_CONTRACT
from doeff_cluster.foundation.notice_pipe import open_notice_box as open_notice_box
from doeff_cluster.foundation.notice_pipe import next_notice as next_notice
from doeff_cluster.worker.intent.worker_model import Retired as Retired
from doeff_cluster.worker.intent.worker_model import HandoffAbandoned as HandoffAbandoned
from doeff_cluster.worker.intent.retirement_model import AwaitRetirement as AwaitRetirement
from doeff_cluster.worker.protocol.process_host import stop_reason_word as stop_reason_word
from doeff_cluster.worker.protocol.process_host import retirement_of_word as retirement_of_word
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
pipe_retirement_notices: _Handler
