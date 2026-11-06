# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = declaring.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from collections.abc import Callable as Callable
from doeff_cluster._shared.core.runtime_env import checked_declaring_checkout as checked_declaring_checkout
from doeff_cluster._shared.intent.runtime_env_model import RepoCheckout as RepoCheckout
from doeff_cluster._shared.intent.runtime_env_model import RuntimeEnvInvalid as RuntimeEnvInvalid
from doeff_cluster._shared.core.service_rules import foundation_needs_refusal as foundation_needs_refusal
from doeff_cluster._shared.intent.service_model import RecordArgument as RecordArgument
from doeff_cluster._shared.intent.service_model import System as System

def declaring_refusal(build: Callable[..., object], foundation: Callable[..., object] | RecordArgument, system: System, revision: str) -> _Program[str | None, object]:
    ...
