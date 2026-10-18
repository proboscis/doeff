# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = process.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from dataclasses import dataclass as dataclass
EOF_GRACE_SECONDS: float
TERM_GRACE_SECONDS: float
STDERR_TAIL_LINES: int
LINE_CAP_BYTES: int
CUT_HEAD_BYTES: int
READ_CHUNK_BYTES: int
LOG: Incomplete

@dataclass(frozen=True, kw_only=True)
class CutLine:
    head: str
    size: int

class StdinWriter:
    stdin: Incomplete
    pending: Incomplete
    closed: Incomplete
    broken: Incomplete
    thread: Incomplete

    def __init__(self, stdin: Incomplete) -> None:
        ...

    def send(self, line: str) -> Incomplete:
        ...

    def close(self) -> Incomplete:
        ...

    def run(self) -> Incomplete:
        ...

class CodexProcess:
    argv: Incomplete
    on_line: Incomplete
    on_exit: Incomplete
    on_down: Incomplete
    line_cap: Incomplete
    cut_lines: Incomplete
    stderr_lines: Incomplete
    retire_lock: Incomplete
    retiring: Incomplete
    went_down: Incomplete
    process: Incomplete
    writer: Incomplete
    stderr_reader: Incomplete
    reader: Incomplete
    exit_watcher: Incomplete

    def __init__(self, argv: list, cwd: str, env: dict, on_line: Incomplete, on_exit: Incomplete, on_down: Incomplete, line_cap: Incomplete=...) -> None:
        ...

    @property
    def pid(self) -> int:
        ...

    def alive(self) -> bool:
        ...

    def exit_code(self) -> Incomplete:
        ...

    def stderr_tail(self) -> str:
        ...

    def send(self, line: str) -> Incomplete:
        ...

    def close_stdin(self) -> Incomplete:
        ...

    def drop(self) -> bool:
        ...

    def went_down_within(self, grace: float) -> bool:
        ...

    def escort_down(self) -> Incomplete:
        ...

    def retire(self) -> Incomplete:
        ...

    def retire_finished(self) -> bool:
        ...

    def watch_exit(self) -> Incomplete:
        ...

    def read_stderr(self) -> Incomplete:
        ...

    def read_line(self, stream: Incomplete) -> Incomplete:
        ...

    def cut_line(self, stream: Incomplete, head: bytes, size: int, whole: bool) -> Incomplete:
        ...

    def drain_line(self, stream: Incomplete) -> int:
        ...

    def read_stdout(self) -> Incomplete:
        ...
