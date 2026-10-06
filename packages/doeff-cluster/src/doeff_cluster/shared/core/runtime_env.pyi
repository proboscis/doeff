# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = runtime_env.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_cluster.shared.intent.runtime_env_model import RepoCheckout as RepoCheckout
from doeff_cluster.shared.intent.runtime_env_model import PythonProject as PythonProject
from doeff_cluster.shared.intent.runtime_env_model import EnvVar as EnvVar
from doeff_cluster.shared.intent.runtime_env_model import ToolRequirement as ToolRequirement
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnv as RuntimeEnv
from doeff_cluster.shared.intent.runtime_env_model import RuntimeEnvInvalid as RuntimeEnvInvalid
from doeff_cluster.shared.intent.runtime_env_model import InvalidKind as InvalidKind
from doeff_cluster.shared.intent.runtime_env_model import LocalPath as LocalPath
from doeff_cluster.shared.intent.runtime_env_model import RemoteRepo as RemoteRepo
from doeff_cluster.shared.intent.runtime_env_model import RepoLocation as RepoLocation
from doeff_cluster.shared.intent.checkout_model import LocalCheckout as LocalCheckout
from doeff_cluster.shared.intent.checkout_model import ProjectOfCheckout as ProjectOfCheckout
from doeff_cluster.shared.intent.checkout_model import CheckoutState as CheckoutState
from doeff_cluster.shared.intent.checkout_model import ReadCheckout as ReadCheckout
from doeff_cluster.shared.intent.checkout_model import CheckoutRoot as CheckoutRoot
from doeff_cluster.shared.intent.checkout_model import SenderSourceRoot as SenderSourceRoot
from doeff_cluster.shared.intent.env_marker_model import FileSha256 as FileSha256
from doeff_cluster.shared.core.runtime_env_rules import url_location as url_location

def checked_repo(checkout: LocalCheckout) -> _Program[RepoCheckout, object]:
    ...

def checked_declaring_checkout(path: str, revision: str) -> _Program[RepoCheckout, object]:
    ...

def check_sender_source(checkouts: tuple, repos: tuple, sender_repo: str) -> _Program[bool, object]:
    ...

def project_of_checkout(by_name: dict, project: ProjectOfCheckout) -> _Program[PythonProject, object]:
    ...

def runtime_env_of_checkouts(checkouts: tuple, project: ProjectOfCheckout, import_roots: tuple, env_vars: tuple=..., tools: tuple=..., sender_repo: str | None=None, extra_projects: tuple=...) -> _Program[RuntimeEnv, object]:
    ...

def project_dir(project: PythonProject, root: str) -> _Program[str, object]:
    ...
