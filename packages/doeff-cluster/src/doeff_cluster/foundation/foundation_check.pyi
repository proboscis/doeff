# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = foundation_check.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass

@dataclass(frozen=True, kw_only=True)
class FoundationClosure:
    gaps: tuple
    unknown: tuple
    unresolved: tuple

def hyx_closedXquestion_markX(closure: FoundationClosure) -> bool:
    ...

def foundation_closure(job: Callable, *, foundation: Callable | None=None, parameter: str='foundation', fold: tuple=...) -> _Program[FoundationClosure, object]:
    ...
