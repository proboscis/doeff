# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _scheduler_channel.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from doeff_core_effects._scheduler import CreatePromise as CreatePromise
from doeff_core_effects._scheduler import CompletePromise as CompletePromise
from doeff_core_effects._scheduler import Wait as Wait
from doeff_core_effects._channel_effects import Channel as Channel
from doeff_core_effects._channel_effects import CreateChannel as CreateChannel
from doeff_core_effects._channel_effects import PutChannel as PutChannel
from doeff_core_effects._channel_effects import TakeChannel as TakeChannel

def woken(promises: tuple) -> _Program[None, object]:
    ...

def filled(channel: Channel) -> _Program[None, object]:
    ...
scheduler_channel_handler: _Handler
