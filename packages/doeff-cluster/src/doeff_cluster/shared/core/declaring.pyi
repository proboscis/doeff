# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = declaring.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.macros import _install_guard_globals as _install_guard_globals
from doeff_hy.macros import _guard_performed as _guard_performed
from collections.abc import Callable as Callable
import os as os
import sys as sys
from doeff_cluster.shared.core.runtime_env import checked_declaring_checkout as checked_declaring_checkout
from doeff_cluster.shared.intent.runtime_env_model import RepoCheckout as RepoCheckout
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnvInvalid as RuntimeEnvInvalid
from doeff_cluster.shared.core.service_rules import foundation_needs_refusal as foundation_needs_refusal
from doeff_cluster.shared.intent.service_model import System as System

def declaring_refusal(build: Callable, foundation: Callable, system: System, revision: str) -> _Program[str | None, object]:
    ...
