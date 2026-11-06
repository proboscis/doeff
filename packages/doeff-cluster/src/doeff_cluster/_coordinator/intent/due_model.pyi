# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = due_model.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from dataclasses import dataclass as dataclass

class CoordinatorUnsettled(Exception):
    ...

@dataclass(frozen=True, kw_only=True)
class StepPace:
    judged_ms: int
    waited_ms: int
    planned_ms: int | None

@dataclass(frozen=True, kw_only=True)
class Stall:
    busy_ms: int
    overslept_ms: int
    gap_ms: int
