"""io_handlers.hy の公開面の型(本番の I/O の家)。"""

from collections.abc import Callable

from doeff import Program

def driver_io_handler(program: Program) -> Program: ...
def run_driver_io(program: Program) -> object: ...
