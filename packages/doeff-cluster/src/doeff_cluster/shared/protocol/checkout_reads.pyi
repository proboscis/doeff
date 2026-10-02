# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = checkout_reads.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from doeff_core_effects.process_effects import ProcessOutcome as ProcessOutcome
from doeff_core_effects.process_effects import RunProcess as RunProcess
from doeff_core_effects.file_effects import PathKind as PathKind
from doeff_core_effects.file_effects import PathStat as PathStat
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff_core_effects.file_effects import StatPath as StatPath
from doeff_core_effects.file_effects import ReadBytes as ReadBytes
from doeff_cluster.shared.intent.checkout_model import CheckoutState as CheckoutState
from doeff_cluster.shared.intent.checkout_model import ReadCheckout as ReadCheckout
from doeff_cluster.shared.intent.checkout_model import CheckoutRoot as CheckoutRoot
from doeff_cluster.shared.intent.checkout_model import SenderSourceRoot as SenderSourceRoot
from doeff_cluster.shared.intent.env_marker_model import FileSha256 as FileSha256
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
SENDER_SOURCE_DIR: str

def git_output(path: str, args: tuple) -> _Program[str, object]:
    ...

def checkout_state_at(path: str, remote: str) -> _Program[CheckoutState, object]:
    ...

def checkout_root(path: str) -> _Program[str | None, object]:
    ...

def file_sha256(path: str) -> _Program[str | None, object]:
    ...
checkout_reads: _Handler
