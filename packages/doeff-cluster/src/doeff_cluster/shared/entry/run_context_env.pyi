# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = run_context_env.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import run as run
from doeff_cluster.shared.intent.run_context import RunContext as RunContext
from doeff_cluster.shared.core.run_context_rules import context_of_environ as context_of_environ

def context_from_env() -> RunContext:
    ...
