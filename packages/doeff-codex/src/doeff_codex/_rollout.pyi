# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _rollout.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from pathlib import Path as Path
SESSIONS_DIR: str
ROLLOUT_PREFIX: str
ROLLOUT_SUFFIX: str
SESSION_META: str
THREAD_ID_PATTERN: Incomplete
STAMP_PATTERN: Incomplete
CARRY_TEMPORARY_SUFFIX: str

def sessions_of(env: dict) -> _Program[Path | None, object]:
    ...

def rollout_of(sessions: Path, thread_id: str) -> _Program[Path | None, object]:
    ...

def whole_lines(data: bytes) -> _Program[bytes, object]:
    ...

def read_rollout(path: Path, tail_bytes: int | None, from_byte: int | None) -> _Program[str | None, object]:
    ...

def window_of(size: int, tail_bytes: int | None, from_byte: int | None) -> _Program[int | None, object]:
    ...

def text_of(data: bytes, cut_head: bool) -> _Program[str | None, object]:
    ...

def carry_place_of(carry: str, thread_id: str) -> _Program[str | None, object]:
    ...

def restore_rollout(sessions: Path, thread_id: str, carry: str) -> _Program[str | None, object]:
    ...

def read_thread_rollout(sessions: Path, thread_id: str, from_byte: int | None, tail_bytes: int | None) -> _Program[str | None, object]:
    ...
