# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _pidfd_exit.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
import ctypes as ctypes
PIDFD_OPEN_SYSCALLS: dict[str, int]
LIBC: ctypes.CDLL

class PidfdUnavailable(RuntimeError):
    ...

def pidfd_of(pid: int) -> _Program[int | None, object]:
    ...
