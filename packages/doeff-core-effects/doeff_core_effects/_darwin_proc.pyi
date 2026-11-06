# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _darwin_proc.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from doeff_core_effects._process_stat import ProcStat as ProcStat
PROC_PIDTBSDINFO: int
PROC_PIDTASKINFO: int
BSD_INFO_SIZE: int
TASK_INFO_SIZE: int
BSD_STATUS_OFFSET: int
BSD_START_OFFSET: int
TASK_THREADS_OFFSET: int
STATUS_STATES: dict[int, str]
LIBPROC_PATH: str

def sized(raw: bytes, size: int, name: str) -> _Program[bytes, object]:
    ...

def bsd_info_stat(raw: bytes) -> _Program[ProcStat, object]:
    ...

def task_info_threads(raw: bytes) -> _Program[int, object]:
    ...

def pid_info(pid: int, flavor: int, size: int) -> _Program[bytes | None, object]:
    ...

def darwin_proc_stat(pid: int) -> _Program[ProcStat | None, object]:
    ...

def darwin_thread_count() -> _Program[int, object]:
    ...
