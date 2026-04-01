"""
doeff-traverse — applicative functor operations as effects.

Provides Traverse, Reduce, Zip, Fail, and try_call for
handler-injected error recovery and execution strategy.
"""

from doeff_traverse.effects import Fail, Traverse, Reduce, Zip, Inspect
from doeff_traverse.helpers import try_call
from doeff_traverse.handlers import (
    sequential,
    parallel,
    fail_handler,
    normalize_to_none,
)
from doeff_traverse.collection import Collection
