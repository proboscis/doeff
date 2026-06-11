"""
doeff-traverse — collection comprehension as effects.

Provides Traverse, Reduce, Zip, Fail, SortBy, Take, and try_call for
handler-injected error recovery and execution strategy.

Comprehension macro (for/do) uses From, When, and Skip internally.
"""

from doeff_traverse.collection import Collection as Collection
from doeff_traverse.effects import (
    Fail as Fail,
)
from doeff_traverse.effects import (
    Inspect as Inspect,
)
from doeff_traverse.effects import (
    Reduce as Reduce,
)
from doeff_traverse.effects import (
    Skip as Skip,
)
from doeff_traverse.effects import (
    SortBy as SortBy,
)
from doeff_traverse.effects import (
    Take as Take,
)
from doeff_traverse.effects import (
    Traverse as Traverse,
)
from doeff_traverse.effects import (
    Zip as Zip,
)
from doeff_traverse.handlers import (
    fail_handler as fail_handler,
)
from doeff_traverse.handlers import (
    normalize_to_none as normalize_to_none,
)
from doeff_traverse.handlers import (
    parallel as parallel,
)
from doeff_traverse.handlers import (
    parallel_fail_fast as parallel_fail_fast,
)
from doeff_traverse.handlers import (
    sequential as sequential,
)
from doeff_traverse.helpers import try_call as try_call
