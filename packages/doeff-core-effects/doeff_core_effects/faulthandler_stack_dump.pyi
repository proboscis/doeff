# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = faulthandler_stack_dump.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff_hy.static_types import Handler as _Handler
import faulthandler as faulthandler
import sys as sys
import time as time
from doeff_core_effects.stack_dump_effects import ArmStackDump as ArmStackDump
from doeff_core_effects.stack_dump_effects import DisarmStackDump as DisarmStackDump
from doeff_core_effects.stack_dump_effects import ReadStackDumps as ReadStackDumps
from doeff_core_effects.stack_dump_effects import StackDumpLedger as StackDumpLedger
from doeff_core_effects.stack_dump_effects import stack_dumps_at as stack_dumps_at
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
MINIMUM_SECONDS: float
faulthandler_stack_dump_handler: _Handler
