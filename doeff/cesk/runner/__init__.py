"""Runner implementations for the CESK machine.

Runners provide the step loop and handle PythonAsyncSyntaxEscape:
- SyncRunner: handles escapes via thread pool, returns T
- AsyncRunner: handles escapes via await, returns async T

Unlike Runtime classes (which have hardcoded handlers), Runners accept
user-provided handler lists for explicit composition.
"""

from doeff.cesk.runner.async_ import AsyncRunner
from doeff.cesk.runner.sync import SyncRunner

__all__ = [
    "AsyncRunner",
    "SyncRunner",
]
