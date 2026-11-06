# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = boundary_recorder.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from doeff_core_effects import Ask as Ask
from doeff_cluster._shared.intent.run_context import RunContext as RunContext
from doeff_cluster._foundation.host_contract import HostContract as HostContract
from doeff_cluster._foundation.record_handlers import RecordingInstaller as RecordingInstaller
from doeff_cluster._foundation.record_handlers import ReplayState as ReplayState
from doeff_cluster._foundation.record_handlers import recording_handler as recording_handler
from doeff_cluster._foundation.record_handlers import effect_replayer as effect_replayer
from doeff_cluster._foundation.record_handlers import RECORD_MODE_KEY as RECORD_MODE_KEY
from doeff_cluster._foundation.record_handlers import RECORD_OTLP_KEY as RECORD_OTLP_KEY
from doeff_cluster._foundation.record_handlers import REPLAY_STATE_KEY as REPLAY_STATE_KEY
from doeff_cluster._foundation.record_handlers import RECORD_MODES as RECORD_MODES

def recording_header(ctx: RunContext, program_path: str, versions: dict) -> _Program[dict, object]:
    ...

def boundary_recorder(contract: HostContract) -> _Program[list, object]:
    ...
