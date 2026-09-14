"""store.hy の公開面の型(Python の読み手 = 検)。部分的な stub: 検が触る StoreActor だけ。"""

from collections.abc import Callable

class TerminalCause:
    """effects.hy TerminalCause の写し(検が decode の答えを読む欄だけ)。"""

    category: str
    reason: str | None
    retryable: bool
    observed_at: str

class StoreActor:
    def __init__(self, db_path: str) -> None: ...
    def submit(self, op: Callable[[object], object]) -> object: ...
    def close(self) -> None: ...

def terminal_cause_from_dict(payload: dict[str, object]) -> TerminalCause | None: ...
