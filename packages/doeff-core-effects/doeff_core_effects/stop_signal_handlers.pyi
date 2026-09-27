"""stop_signal_handlers.hy の公開面の型(止めの合図の handler — 型検査を受ける消費者向け)。"""

import signal
import types

from doeff import Program

STOP_SIGNALS: tuple[signal.Signals, ...]

class StopBox:
    reason: str | None
    def __init__(self) -> None: ...
    def receive(self, number: int, frame: types.FrameType | None) -> None: ...

def install_stop_box() -> StopBox: ...
def os_signal_stop_handler(program: Program) -> Program: ...
def scripted_stop_handler(program: Program) -> Program: ...
