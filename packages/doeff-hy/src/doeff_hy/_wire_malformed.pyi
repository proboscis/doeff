# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _wire_malformed.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

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
