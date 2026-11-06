# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = wal_store.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

MAX_LOG_BYTES: int
FSYNC_WINDOW: int

class WalStore:
    directory: str
    max_log_bytes: int
    sync: bool
    snapshot: str
    log: str
    kv: dict[str, object]
    seq: int
    fsync_seconds: list[float]
    recovered: dict[str, int | str] | None

    def __init__(self, directory: str, max_log_bytes: int=..., sync: bool=True) -> None:
        ...

    def table(self) -> dict[str, object]:
        ...

    def replace_table(self, kv: dict[str, object]) -> None:
        ...

    def recovery(self) -> dict[str, int | str] | None:
        ...

    def note_recovered(self, kept: int, size: int, reason: str) -> None:
        ...

    def note_fsync(self, seconds: float) -> None:
        ...

    def fsync_stats(self) -> dict:
        ...
