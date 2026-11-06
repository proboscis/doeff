# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = due_model.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

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
