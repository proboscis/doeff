# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = job_rules.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from dataclasses import replace as replace
import re as re
from doeff_cluster._shared.intent.job_model import JobSpec as JobSpec

def spec_hash(spec: JobSpec) -> str:
    ...
CONTENT_KEY: re.Pattern[str]

def content_refusal(value: object) -> str | None:
    ...

def memory_refusal(peak: object, settle: object) -> str | None:
    ...

def single_host_refusal(value: object) -> str | None:
    ...

def spec_hash_matches(spec: JobSpec, reported: str | None) -> bool:
    ...
