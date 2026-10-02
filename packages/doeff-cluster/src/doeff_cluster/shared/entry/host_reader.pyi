# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = host_reader.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff_hy.static_types import Handler as _Handler
from doeff_core_effects.effects import Ask as Ask
from doeff_cluster.foundation.host_contract import HOST_CONTRACT as HOST_CONTRACT
from doeff_cluster.foundation.host_contract import this_program_path as this_program_path
from doeff_cluster.foundation.process_versions import this_process_versions as this_process_versions
from doeff_cluster.shared.entry.run_context_env import context_from_env as context_from_env
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
host_reader: _Handler
