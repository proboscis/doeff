# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _stop_signal_handlers.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
import signal as signal
import types as types
from doeff_core_effects._stop_signal_effects import AwaitStop as AwaitStop
from doeff_core_effects._stop_signal_effects import RaiseStop as RaiseStop
from doeff_core_effects._stop_signal_effects import StopBell as StopBell
from doeff_core_effects._stop_signal_effects import StopRequested as StopRequested
from doeff_core_effects._scheduler import CompletePromise as CompletePromise
from doeff_core_effects._scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects._scheduler import CreatePromise as CreatePromise
from doeff_core_effects._scheduler import ExternalPromise as ExternalPromise
from doeff_core_effects._scheduler import Wait as Wait
STOP_SIGNALS: tuple[signal.Signals, ...]

class StopBox:
    reason: str | None
    waiters: tuple[ExternalPromise[str], ...]

    def __init__(self) -> None:
        ...

    def park(self, waiter: ExternalPromise[str]) -> None:
        ...

    def forget(self, waiter: ExternalPromise[str]) -> None:
        ...

    def receive(self, number: int, frame: types.FrameType | None) -> None:
        ...

def install_stop_box() -> _Program[StopBox, object]:
    ...
os_signal_stop_handler: _Handler
scripted_stop_handler: _Handler
