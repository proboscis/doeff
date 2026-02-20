# Task 1 Baseline - KPC Macro Migration + doeff-13 Hang Repro

Timestamp: 2026-02-15T05:50:00Z
Session: ses_3a07f8917ffe0u1EjSjNBtR22z
Plan: `.sisyphus/plans/kpc-implementation-hang-fix.md`

## Compatibility Policy (Locked)

- Policy: **hard break now** for old KPC-as-effect behavior.
- Meaning:
  - no temporary KPC compatibility shim
  - remove runtime dependency on `kpc` handler path
  - migrate `@do` call path to macro model (`__call__()` -> `Call` DoCtrl)

## Baseline Commands + Observed Outcomes

1) Current default handler inventory still includes KPC handler sentinel

Command:

```bash
uv run python - <<'PY'
from doeff import default_handlers
handlers = default_handlers()
print(len(handlers))
print([getattr(h, 'name', repr(h)) for h in handlers])
PY
```

Observed:

- Handler count: `7`
- Includes `RustHandler(KpcHandlerFactory)`

2) Current `@do` call still returns KPC effect object (`PyKPC`) and is `EffectBase`

Command:

```bash
uv run python - <<'PY'
from doeff import EffectBase, KleisliProgramCall, do

@do
def add_one(x: int):
    return x + 1

kpc = add_one(1)
print(type(kpc).__name__)
print(isinstance(kpc, KleisliProgramCall), isinstance(kpc, EffectBase))
PY
```

Observed:

- Type name: `PyKPC`
- `isinstance(kpc, KleisliProgramCall) == True`
- `isinstance(kpc, EffectBase) == True`

3) doeff-13 hang path reproduction (`@do`-decorated handler with `WithHandler`) times out at 3.0s

Command:

```bash
uv run python - <<'PY'
import subprocess, sys, textwrap
snippet = textwrap.dedent('''
from doeff import Delegate, EffectBase, WithHandler, default_handlers, do, run

class _CustomEffect(EffectBase):
    def __init__(self, value):
        self.value = value

def _prog(gen_factory):
    @do
    def _wrapped():
        return (yield from gen_factory())
    return _wrapped()

@do
def handler(effect, _k):
    if isinstance(effect, _CustomEffect):
        return f"wrapped:{effect.value}"
    yield Delegate()

def body():
    result = yield _CustomEffect("x")
    return result

def main():
    result = yield WithHandler(handler=handler, expr=_prog(body))
    return result

print(run(_prog(main), handlers=default_handlers()).value)
''')
try:
    subprocess.run([sys.executable, '-c', snippet], check=True, timeout=3.0)
    print('UNEXPECTED_SUCCESS')
except subprocess.TimeoutExpired:
    print('TIMEOUT_EXPIRED_3S')
except subprocess.CalledProcessError as e:
    print('PROCESS_ERROR', e.returncode)
PY
```

Observed:

- `TIMEOUT_EXPIRED_3S`
- Repro is deterministic in this environment and suitable for RED hang-regression tests.

4) Negative control (plain handler with explicit `Resume`) completes within 3.0s

Command:

```bash
uv run python - <<'PY'
import subprocess, sys, textwrap
snippet = textwrap.dedent('''
from doeff import Delegate, EffectBase, Resume, WithHandler, default_handlers, do, run

class _CustomEffect(EffectBase):
    def __init__(self, value):
        self.value = value

def _prog(gen_factory):
    @do
    def _wrapped():
        return (yield from gen_factory())
    return _wrapped()

def handler(effect, k):
    if isinstance(effect, _CustomEffect):
        return (yield Resume(k, f"wrapped:{effect.value}"))
    yield Delegate()

def body():
    result = yield _CustomEffect("x")
    return result

def main():
    result = yield WithHandler(handler=handler, expr=_prog(body))
    return result

print(run(_prog(main), handlers=default_handlers()).value)
''')
r = subprocess.run([sys.executable, '-c', snippet], check=True, timeout=3.0, capture_output=True, text=True)
print(r.stdout.strip())
print('OK_WITHIN_3S')
PY
```

Observed:

- Output includes `wrapped:x`
- `OK_WITHIN_3S`

## Root-Cause Hypothesis (for RED tests)

- High-confidence hypothesis from code + docs:
  - `@do` handler path re-enters KPC auto-unwrap/eval flow and can self-capture handler chain.
  - Anchors:
    - `tests/public_api/test_types_001_handler_protocol.py:389` (existing skip)
    - `packages/doeff-vm/src/handler.rs:626` (`extract_kpc_arg`)
    - `packages/doeff-vm/src/handler.rs:808` (`KpcHandlerProgram`)
    - `packages/doeff-vm/src/vm.rs:2084` (`handle_get_handlers`)
    - `specs/core/SPEC-KPC-001-kleisli-program-call-macro.md:277` (documented recursion mechanism)

## Task 1 Acceptance Check

- [x] Compatibility policy recorded
- [x] Baseline hanging command documented and reproducible
- [x] Evidence file exists at `.sisyphus/evidence/kpc-hang-baseline.md`
