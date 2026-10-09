# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = process_exit.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from collections.abc import Callable as Callable
from collections.abc import Generator as Generator
from doeff import run as run
from doeff_core_effects.effects import Await as Await
from doeff_core_effects.process_effects import AwaitProcessExit as AwaitProcessExit
from doeff_core_effects.process_effects import ProcessEnded as ProcessEnded
from doeff_core_effects.process_effects import ProcessNotChild as ProcessNotChild
from doeff_core_effects.warm_effects import AwaitWarmChildExit as AwaitWarmChildExit
from doeff_core_effects.os_process import STARTED_CHILDREN as STARTED_CHILDREN
from doeff_core_effects.os_warm_process import same_child_running as same_child_running
from doeff_core_effects.pidfd_exit import pidfd_of as pidfd_of
from doeff_core_effects.kqueue_exit import kqueue_fd_of as kqueue_fd_of
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
EXIT_FD_OPENERS: Incomplete

class ExitWaitUnavailable(RuntimeError):
    ...

def exit_fd_opener_for(system: str) -> _Program[Callable, object]:
    ...

def exit_fd_of(pid: int) -> _Program[int | None, object]:
    ...

def child_unreaped(pid: int) -> _Program[bool, object]:
    ...

def ended(pid: int, still_ours: Callable) -> None:
    ...

class ExitEnd:
    pid: int
    still_ours: Callable

    def __init__(self, pid: int, still_ours: Callable) -> None:
        ...

    def __await__(self) -> Generator[object, None, None]:
        ...
process_exit_handler: _Handler
