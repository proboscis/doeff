# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = kube_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

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
