"""store.hy の公開面の型(Python の読み手 = 検)。部分的な stub: 検が触る StoreActor だけ。"""

from collections.abc import Callable

class TerminalCause:
    """effects.hy TerminalCause の写し(検が decode の答えを読む欄だけ)。"""

    category: str
    reason: str | None
    retryable: bool
    observed_at: str

class StoreActor:
    #: 出来事の journal(agent_session_events)の先端 — actor が store を変えた op の後に読み直す(段 12 lane 12b)。
    journal_seq: int
    def __init__(self, db_path: str) -> None: ...
    def submit(self, op: Callable[[object], object]) -> object: ...
    def wait_journal(self, after: int, timeout: float) -> int: ...
    def close(self) -> None: ...

def db_journal_seq(conn: object) -> int: ...

def terminal_cause_from_dict(payload: dict[str, object]) -> TerminalCause | None: ...
