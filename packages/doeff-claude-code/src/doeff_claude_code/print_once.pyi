# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = print_once.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from dataclasses import field as field
import subprocess as subprocess
import threading as threading
from doeff_core_effects._offloaded_call import ThreadPerCall as ThreadPerCall
from doeff_core_effects._offloaded_call import offloaded as offloaded
from doeff_core_effects._offloaded_call import run_detached as run_detached
from doeff_core_effects._offloaded_call import keep_nothing as keep_nothing
from doeff_claude_code._effects import ClaudePrintOnce as ClaudePrintOnce
from doeff_claude_code._effects import PrintAnswered as PrintAnswered
from doeff_claude_code._effects import PrintFailed as PrintFailed
from doeff_claude_code._effects import PrintLimited as PrintLimited
from doeff_claude_code._effects import PrintTimedOut as PrintTimedOut
from doeff_claude_code._lines import AccountLimitHit as AccountLimitHit
from doeff_claude_code._lines import TurnResult as TurnResult
from doeff_claude_code._lines import parse_record as parse_record
from doeff_claude_code._lines import classify_record as classify_record
from doeff_claude_code._lines import stderr_tail_within as stderr_tail_within
from doeff_hy.json_lines import jsonl_lines as jsonl_lines
from doeff_claude_code._dialogue import limit_hit_after as limit_hit_after
from doeff_claude_code._dialogue import print_once_stdin as print_once_stdin
from doeff_claude_code._argv import print_once_argv as print_once_argv
from doeff_claude_code._argv import print_once_env as print_once_env
from doeff_claude_code._credential_home import open_credential_home as open_credential_home
from doeff_claude_code._credential_home import close_credential_home as close_credential_home
PRINT_THREADS: ThreadPerCall
DETAIL_CHARS: int
WORK_DIR_PREFIX: str

@dataclass(frozen=True, kw_only=True)
class RanPrint:
    exit_code: int | None
    stdout: str
    stderr: str
    elapsed_ms: int
    timed_out: bool
    launch_error: str | None

class PrintProcess:
    lock: threading.Lock
    process: subprocess.Popen | None
    stopped: bool

    def __init__(self) -> None:
        ...

    def start(self, argv: list, cwd: str, env: dict) -> subprocess.Popen:
        ...

    def stop(self) -> None:
        ...

def elapsed_ms_since(started: float) -> int:
    ...

def run_print_once(command: tuple, request: ClaudePrintOnce, holder: PrintProcess) -> RanPrint:
    ...

def failed_detail(result: TurnResult) -> _Program[str, object]:
    ...

def limit_in_stream(stdout: str) -> _Program[AccountLimitHit | None, object]:
    ...

def print_outcome(exit_code: int | None, stdout: str, stderr: str, elapsed_ms: int, timed_out: bool, seconds: float) -> _Program[PrintAnswered | PrintFailed | PrintLimited | PrintTimedOut, object]:
    ...

def print_once(command: tuple, request: ClaudePrintOnce) -> _Program[PrintAnswered | PrintFailed | PrintLimited | PrintTimedOut, object]:
    ...

def claude_print_handler(command: Incomplete) -> _Handler:
    ...
