# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = runtime_facts.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from doeff_core_effects.process_effects import ReadEnvironment as ReadEnvironment
from doeff_core_effects.process_effects import ReadInterpreter as ReadInterpreter
from doeff_core_effects.process_effects import ResolveModule as ResolveModule
from doeff_core_effects.process_effects import InterpreterFacts as InterpreterFacts
from doeff_core_effects.process_effects import ModuleFound as ModuleFound
from doeff_core_effects.process_effects import ModuleNotFound as ModuleNotFound
from doeff_core_effects.file_effects import StatPath as StatPath
from doeff_core_effects.file_effects import ReadText as ReadText
from doeff_core_effects.file_effects import PathKind as PathKind
from doeff_core_effects.file_effects import PathStat as PathStat
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff_cluster.shared.intent.env_marker_model import ENV_MARKER as ENV_MARKER
from doeff_cluster.shared.intent.runtime_identity_model import ModuleOrigin as ModuleOrigin
from doeff_cluster.shared.intent.runtime_identity_model import ProcessFacts as ProcessFacts
from doeff_cluster.shared.intent.runtime_identity_model import ReadRuntimeFacts as ReadRuntimeFacts
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
DECLARED_ENV: str
KEY_ENV: str

def marked_root(start: str) -> _Program[str | None, object]:
    ...

def module_origin(name: str) -> _Program[ModuleOrigin, object]:
    ...

def env_value(env: tuple, name: str) -> _Program[str, object]:
    ...

def marker_absent() -> _Program[str, object]:
    ...

def marker_text(root: str) -> _Program[str, object]:
    ...

def process_facts(modules: tuple) -> _Program[ProcessFacts, object]:
    ...
process_runtime_facts: _Handler

def given_runtime_facts(facts: ProcessFacts) -> _Handler:
    ...
