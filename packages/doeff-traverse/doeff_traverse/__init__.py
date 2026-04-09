"""
doeff-traverse — collection comprehension as effects.

Provides Traverse, Reduce, Zip, Fail, SortBy, Take, and try_call for
handler-injected error recovery and execution strategy.

Comprehension macro (for/do) uses From, When, and Skip internally.
"""

from doeff_traverse.effects import (
    Fail, Traverse, Reduce, Zip, Inspect,
    Skip, SortBy, Take,
)
from doeff_traverse.helpers import try_call
from doeff_traverse.handlers import (
    sequential,
    parallel,
    parallel_fail_fast,
    fail_handler,
    normalize_to_none,
)
from doeff_traverse.collection import Collection
