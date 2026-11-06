# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = kube_model.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase

class KubeUnavailable(Exception):
    ...

@dataclass(frozen=True)
class ScaleDeployment(EffectBase):
    namespace: str
    name: str
    replicas: int
    dry_run: bool = False

@dataclass(frozen=True)
class AnnotateDeployment(EffectBase):
    namespace: str
    name: str
    annotations: dict

@dataclass(frozen=True)
class FollowDeployments(EffectBase):
    keys: tuple[str, ...]
    now_ms: int

@dataclass(frozen=True)
class FollowNodes(EffectBase):
    names: tuple[str, ...]
    now_ms: int
