"""handlers.py の公開面の型(型検査のための宣言・agora-redesign #2321)。

py.typed の package の注記の無い関数は、pyright が答えの型を推さず Unknown にするので、ここで宣言する。
どれも doeff.program.handler が包んだ Program → Program の handler。
"""

from typing import TypeAlias

from doeff.program import ProgramHandler
from doeff_traverse.effects import Inspect, Reduce, Skip, SortBy, Take, Traverse, Zip

CollectionEffect: TypeAlias = (
    Skip | Traverse[object, object] | Reduce[object, object] | Zip[object, object] | Inspect | SortBy[object] | Take[object]
)

def sequential() -> ProgramHandler: ...
def parallel(concurrency: int = 10) -> ProgramHandler: ...
def parallel_fail_fast(concurrency: int = 10) -> ProgramHandler: ...

fail_handler: ProgramHandler
normalize_to_none: ProgramHandler
