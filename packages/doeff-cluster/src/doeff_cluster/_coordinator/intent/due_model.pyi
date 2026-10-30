# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = due_model.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

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
