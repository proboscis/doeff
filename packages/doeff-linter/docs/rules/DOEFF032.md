# DOEFF032: Workflow Nondeterminism

Workflow modules must keep glue code deterministic so workflow replay and
durable resume stay sound.

## When It Applies

Mark workflow modules with:

```python
# doeff: workflow
```

Files under a `workflows/` path component and files named `*_workflow.py` are
also treated as workflow modules.

## Banned Patterns

- `datetime.now`, `datetime.today`, `time.time`, `time.monotonic`:
  use `time!`.
- `random.*`: use `random!`.
- `open`, pathlib write methods, `subprocess`, `requests`, `httpx`, `socket`,
  and `urllib`: move the operation behind `gate!`.
- Non-allowlisted imports: pass external values through `:params`.

Severity is ERROR. This rule intentionally has no baseline or per-file
allowlist, and `noqa` does not suppress it.

## Good

```python
# doeff: workflow
from dataclasses import dataclass


@dataclass(frozen=True)
class PromptFacts:
    timestamp: str
    seed: int


def build_prompt(facts: PromptFacts) -> str:
    return f"Run at {facts.timestamp} with seed {facts.seed}"
```

## Bad

```python
# doeff: workflow
import random
from datetime import datetime


def build_prompt() -> str:
    return f"{datetime.now()} {random.choice(['a', 'b'])}"
```
