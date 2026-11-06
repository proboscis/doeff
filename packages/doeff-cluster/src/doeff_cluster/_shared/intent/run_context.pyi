# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = run_context.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from dataclasses import dataclass as dataclass

@dataclass(frozen=True)
class RunContext:
    coordinator_url: str
    worker: str
    revision: str
    job: str
    instance: str = ''
    attempt: str = ''
    spec_hash: str = ''
    placement: str = ''
    runtime_env: str = ''
    env_key: str = ''

    def identity(self) -> dict[str, str | int | None]:
        ...
