"""
doeff-traverse — collection comprehension as effects.

Provides Traverse, Reduce, Zip, Fail, SortBy, Take, and try_call for
handler-injected error recovery and execution strategy, and ItemOutcome
(with the progress_bars handler) for counting each item's outcome.

Comprehension macro (for/do) uses From, When, and Skip internally.
"""

from importlib import import_module
from typing import TYPE_CHECKING

from doeff_traverse._collection import Collection as Collection
from doeff_traverse._effects import (
    Fail as Fail,
)
from doeff_traverse._effects import (
    Inspect as Inspect,
)
from doeff_traverse._effects import (
    ItemOutcome as ItemOutcome,
)
from doeff_traverse._effects import (
    ItemOutcomeNotPassedError as ItemOutcomeNotPassedError,
)
from doeff_traverse._effects import (
    Reduce as Reduce,
)
from doeff_traverse._effects import (
    Skip as Skip,
)
from doeff_traverse._effects import (
    SortBy as SortBy,
)
from doeff_traverse._effects import (
    Take as Take,
)
from doeff_traverse._effects import (
    Traverse as Traverse,
)
from doeff_traverse._effects import (
    Zip as Zip,
)
from doeff_traverse._handlers import (
    fail_handler as fail_handler,
)
from doeff_traverse._handlers import (
    normalize_to_none as normalize_to_none,
)
from doeff_traverse._handlers import (
    parallel as parallel,
)
from doeff_traverse._handlers import (
    parallel_fail_fast as parallel_fail_fast,
)
from doeff_traverse._handlers import (
    progress_bars as progress_bars,
)
from doeff_traverse._handlers import (
    sequential as sequential,
)
from doeff_traverse._helpers import sequence as sequence
from doeff_traverse._helpers import try_call as try_call

# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers; `__all__` is not used (DOEFF021).
if TYPE_CHECKING:
    from doeff_traverse._collection import ItemResult as ItemResult
    from doeff_traverse._effects import ItemOutcomeKind as ItemOutcomeKind
    from doeff_traverse._handlers import BarFactory as BarFactory

_LAZY_EXPORTS: dict[str, str] = {
    "ItemResult": "doeff_traverse._collection",
    "ItemOutcomeKind": "doeff_traverse._effects",
    "BarFactory": "doeff_traverse._handlers",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
