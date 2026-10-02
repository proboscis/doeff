# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = wal_store.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

import os as os
import sys as sys
import time as time
from collections.abc import Callable as Callable
from typing import BinaryIO as BinaryIO
from pathlib import Path as Path
MAX_LOG_BYTES: int
SLOW_FSYNC_SECONDS: float
MOVED_MARK: str

def write_durably(target: Path, data: bytes) -> None:
    ...

def fsync_dir(d: Path) -> None:
    ...

class WalStore:

    def __init__(self, directory: str, max_log_bytes: int=..., fsync: Callable=...) -> None:
        ...

    def exists(self) -> bool:
        ...

    def table(self) -> dict[str, object]:
        ...

    def replace_table(self, kv: dict[str, object]) -> None:
        ...

    def recovery(self) -> dict[str, int | str] | None:
        ...

    def check_place(self) -> None:
        ...

    def read_snapshot_bytes(self) -> bytes | None:
        ...

    def read_log_lines(self) -> list[bytes]:
        ...

    def drop_tail(self, kept: int, size: int, reason: str) -> None:
        ...

    def open_log(self) -> BinaryIO:
        ...

    def append_line(self, line: bytes) -> int:
        ...

    def write_snapshot(self, data: bytes) -> None:
        ...

    def fsync_stats(self) -> dict:
        ...
