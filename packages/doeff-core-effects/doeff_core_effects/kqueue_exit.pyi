# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = kqueue_exit.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program

def kqueue_fd_of(pid: int) -> _Program[int | None, object]:
    ...
