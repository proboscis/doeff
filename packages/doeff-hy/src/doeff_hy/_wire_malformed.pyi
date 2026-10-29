# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _wire_malformed.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass

@dataclass(frozen=True, kw_only=True)
class MalformedField:
    field: str
    reason: str

@dataclass(frozen=True, kw_only=True)
class Malformed:
    wire_type: str
    fields: tuple[MalformedField, ...]

    def __post_init__(self) -> None:
        ...
