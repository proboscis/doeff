"""Types for the public surface of handlers.py (declarations for type checking).

For functions in a py.typed package without annotations, pyright does not infer the answer type and yields Unknown, so they are declared here.
Every one is a Program -> Program handler wrapped by doeff.program.handler.
"""

from typing import Protocol, TypeAlias

from doeff.program import FormerFactoryHandler, ProgramHandler
from doeff_traverse._effects import Inspect, ItemOutcome, Reduce, Skip, SortBy, Take, Traverse, Zip

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

sequential: FormerFactoryHandler
def parallel(concurrency: int = 10) -> ProgramHandler: ...
def parallel_fail_fast(concurrency: int = 10) -> ProgramHandler: ...

fail_handler: ProgramHandler
normalize_to_none: ProgramHandler

class ProgressBar(Protocol):
    """What progress_bars uses for the bar that make_bar creates (tqdm.tqdm has both)."""

    def update(self, n: int = 1) -> object: ...
    def close(self) -> object: ...

class BarFactory(Protocol):
    """make_bar of progress_bars: builds a bar from the number of Traverse items and a label ("traverse" if none) (tqdm.tqdm fits)."""

    def __call__(self, *, total: int, desc: str) -> ProgressBar: ...

def progress_bars(make_bar: BarFactory) -> ProgramHandler: ...
