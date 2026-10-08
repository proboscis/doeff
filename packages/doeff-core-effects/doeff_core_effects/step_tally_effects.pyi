# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = step_tally_effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass

@dataclass(frozen=True, kw_only=True)
class StepTally:
    steps: int
    wall_ns: int
    cpu_ns: int
    longest_wall_ns: int
    longest_cpu_ns: int
    longest_site: str | None
    longest_effect: str | None
    ready: int

    def __post_init__(self) -> None:
        ...
EMPTY_STEP_TALLY: StepTally

@_doeff_dataclass(frozen=True)
class OpenStepTally(_doeff_effect_base[None]):
    key: str

@_doeff_dataclass(frozen=True)
class CloseStepTally(_doeff_effect_base[StepTally | None]):
    key: str

@dataclass(frozen=True, kw_only=True)
class TaskTally:
    run: int
    tid: int | None
    parent: int | None
    steps: int
    wall_ns: int
    cpu_ns: int
    vm_steps: int
    handler_calls: int

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class OpenTaskTally(_doeff_effect_base[None]):
    key: str

@_doeff_dataclass(frozen=True)
class ReadTaskTally(_doeff_effect_base[tuple[TaskTally, ...] | None]):
    key: str

@_doeff_dataclass(frozen=True)
class CloseTaskTally(_doeff_effect_base[tuple[TaskTally, ...] | None]):
    key: str
