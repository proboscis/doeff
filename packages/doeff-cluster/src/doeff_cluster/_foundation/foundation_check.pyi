# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = foundation_check.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass

@dataclass(frozen=True, kw_only=True)
class FoundationClosure:
    gaps: tuple[str, ...]
    unknown: tuple[str, ...]
    unresolved: tuple[str, ...]

@dataclass(frozen=True, kw_only=True)
class UnreadDeclaration:
    part: str
    handles: tuple[type, ...]
    effects: tuple[type, ...]

def hyx_closedXquestion_markX(closure: FoundationClosure) -> bool:
    ...

def foundation_closure(job: Callable, *, foundation: Callable | None=None, parameter: str='foundation', fold: tuple[str, ...]=..., unread: tuple[UnreadDeclaration, ...]=...) -> _Program[FoundationClosure, object]:
    ...
