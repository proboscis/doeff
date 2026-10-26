# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = job_rules.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from dataclasses import replace as replace
import re as re
from doeff_cluster.shared.intent.job_model import JobSpec as JobSpec

def spec_hash(spec: JobSpec) -> str:
    ...
CONTENT_KEY: re.Pattern[str]

def content_refusal(value: object) -> str | None:
    ...

def spec_hash_matches(spec: JobSpec, reported: str | None) -> bool:
    ...
