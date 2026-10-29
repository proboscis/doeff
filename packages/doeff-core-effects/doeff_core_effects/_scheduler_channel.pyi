# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _scheduler_channel.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

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
