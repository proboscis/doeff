# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = notice_pipe.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
import threading as threading
from doeff_core_effects.scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects.scheduler import ExternalPromise as ExternalPromise
from doeff_core_effects.scheduler import Wait as Wait

@dataclass(frozen=True, kw_only=True)
class NoticeWaiter:
    after: str | None
    promise: ExternalPromise

class NoticeBox:
    lock: threading.Lock
    last: str | None
    waiters: tuple[NoticeWaiter, ...]

    def __init__(self) -> None:
        ...

    def park(self, waiter: NoticeWaiter) -> str | None:
        ...

    def forget(self, promise: ExternalPromise) -> None:
        ...

    def receive(self, word: str) -> None:
        ...

def read_notice_lines(fd: int, box: NoticeBox) -> None:
    ...

@dataclass(frozen=True, kw_only=True)
class NoticePipe:
    fd: int
    inode: int

def notice_pipe_of(env_name: str, given: str) -> _Program[NoticePipe, object]:
    ...

def own_pipe(pipe: NoticePipe) -> _Program[bool, object]:
    ...

def open_notice_box(env_name: str) -> _Program[NoticeBox, object]:
    ...

def next_notice(box: NoticeBox, after: str | None) -> _Program[str, object]:
    ...
