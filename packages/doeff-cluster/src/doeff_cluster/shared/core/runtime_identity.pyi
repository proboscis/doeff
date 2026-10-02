# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = runtime_identity.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv
from doeff_cluster.shared.core.runtime_env_rules import runtime_env_of_json as runtime_env_of_json
from doeff_cluster.shared.core.runtime_env_rules import env_key as env_key
from doeff_cluster.shared.intent.runtime_identity_model import IdentityFailureKind as IdentityFailureKind
from doeff_cluster.shared.intent.runtime_identity_model import ModuleOrigin as ModuleOrigin
from doeff_cluster.shared.intent.runtime_identity_model import RootMarker as RootMarker
from doeff_cluster.shared.intent.runtime_identity_model import RuntimeFacts as RuntimeFacts
from doeff_cluster.shared.intent.runtime_identity_model import RepoCommit as RepoCommit
from doeff_cluster.shared.intent.runtime_identity_model import RuntimeIdentity as RuntimeIdentity
from doeff_cluster.shared.intent.runtime_identity_model import RuntimeIdentityMismatch as RuntimeIdentityMismatch
from doeff_cluster.shared.intent.runtime_identity_model import ProcessFacts as ProcessFacts
from doeff_cluster.shared.intent.runtime_identity_model import ReadRuntimeFacts as ReadRuntimeFacts
from doeff_cluster.shared.intent.env_marker_model import ENV_MARKER_FORMAT as ENV_MARKER_FORMAT
from doeff_cluster.shared.core.runtime_env import project_dir as project_dir

def outside_origins(declared: RuntimeEnv, root: str, origins: tuple) -> _Program[tuple, object]:
    ...

def judge_identity(facts: RuntimeFacts, pid: int) -> _Program[RuntimeIdentity | RuntimeIdentityMismatch, object]:
    ...

def decode_env(text: str) -> _Program[RuntimeEnv | None, object]:
    ...

def decode_marker(text: str) -> _Program[RootMarker | None, object]:
    ...

def check_runtime_identity(modules: tuple) -> _Program[RuntimeIdentity | RuntimeIdentityMismatch, object]:
    ...
