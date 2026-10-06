# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = run_context.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

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
    env_root: str = ''

    def identity(self) -> dict[str, str | int | None]:
        ...
