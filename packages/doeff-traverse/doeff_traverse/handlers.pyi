"""handlers.py の公開面の型(型検査のための宣言・agora-redesign #2321)。

py.typed の package の注記の無い関数は、pyright が答えの型を推さず Unknown にするので、ここで宣言する。
どれも doeff.program.handler が包んだ Program → Program の handler。
"""

from typing import Protocol, TypeAlias

from doeff.program import ProgramHandler
from doeff_traverse.effects import Inspect, ItemOutcome, Reduce, Skip, SortBy, Take, Traverse, Zip

CollectionEffect: TypeAlias = (
    Skip
    | Traverse[object, object]
    | Reduce[object, object]
    | Zip[object, object]
    | Inspect
    | SortBy[object]
    | Take[object]
    | ItemOutcome
)

def sequential() -> ProgramHandler: ...
def parallel(concurrency: int = 10) -> ProgramHandler: ...
def parallel_fail_fast(concurrency: int = 10) -> ProgramHandler: ...

fail_handler: ProgramHandler
normalize_to_none: ProgramHandler

class ProgressBar(Protocol):
    """progress_bars が make_bar の作る bar に使う物(tqdm.tqdm は両方を持つ)。"""

    def update(self, n: int = 1) -> object: ...
    def close(self) -> object: ...

class BarFactory(Protocol):
    """progress_bars の make_bar: Traverse の件の数と label(無ければ "traverse")から bar を作る(tqdm.tqdm が合う)。"""

    def __call__(self, *, total: int, desc: str) -> ProgressBar: ...

def progress_bars(make_bar: BarFactory) -> ProgramHandler: ...
