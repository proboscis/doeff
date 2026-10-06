# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = due_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass

@dataclass(frozen=True, kw_only=True)
class DueAt:
    at: int

@dataclass(frozen=True, kw_only=True)
class DueNow:
    ...

@dataclass(frozen=True, kw_only=True)
class DueNever:
    ...
