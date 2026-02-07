# 16. Python run_program API

Programmatic entry point that mirrors `doeff run`, including interpreter/env discovery, Kleisli
application, and optional reporting. Use this when you want the CLI behavior inside tests or Python
scripts without shelling out.

## Table of Contents

- [Overview](#overview)
- [Quick Start](#quick-start)
- [Parameter Reference](#parameter-reference)
- [Discovery Behavior](#discovery-behavior)
- [ProgramRunResult](#programrunresult)
- [Testing Patterns](#testing-patterns)

---

## Overview

`run_program()` lets you execute Programs from Python while keeping the CLI's conveniences:
auto-discovered interpreters/environments, marker-based apply/transform hooks, and optional run
reports. It accepts either a string path to a Program (for full discovery) or a Program instance
when you want to build everything in memory.

## Quick Start

**Auto-discovered run (mirrors `doeff run`)**

```python
from doeff import run_program

result = run_program(
    "myapp.features.auth.login_program",
    quiet=True,  # silence discovery logging in tests
    report=True,  # include RunResult report (CLI-style)
)

assert result.value == "Login successful"
print(result.interpreter_path)  # resolved interpreter
print(result.env_sources)       # discovered environments
```

**Run an in-memory Program with custom envs and Kleisli**

```python
from doeff import Program, do, run_program
from doeff.effects import Ask

@do
def greet():
    name = yield Ask("name")
    return f"hello, {name}"

result = run_program(
    greet(),
    envs=[{"name": "doeff"}],           # dicts or Program[dict] instances work
    apply=lambda prog: prog.map(str.upper),  # Kleisli/callable transform
    load_default_env=False,             # skip ~/.doeff.py in hermetic tests
)

assert result.value == "HELLO, DOEFF"
```

## Parameter Reference

```python
run_program(
    program,
    *,
    interpreter=None,
    envs=None,
    apply=None,
    transform=None,
    report=False,
    report_verbose=False,
    quiet=False,
    load_default_env=True,
)
```

- `program` (`str | Program`): Program path (enables discovery) or Program instance.
- `interpreter` (`str | ProgramInterpreter | callable | None`): Override interpreter. Strings use
  marker resolution; callables receive the Program.
- `envs` (`list[str | Program[dict] | Mapping] | None`): Add environments by path, Program[dict],
  or plain dict.
- `apply` (`str | KleisliProgram | callable | None`): Kleisli to apply before execution.
- `transform` (`list[str | callable] | None`): Additional Program transformers applied in order.
- `report` / `report_verbose`: Request CLI-style run reports (string-path mode).
- `quiet`: Suppress discovery stderr output (helpful in pytest).
- `load_default_env`: Load `__default_env__` from `~/.doeff.py` (set False for hermetic runs when
  using Program instances or object inputs).

## Discovery Behavior

- **String-only inputs** (program/interpreter/env/apply/transform are all strings or omitted):
  executes through the CLI discovery pipeline. It auto-discovers interpreters/envs via markers,
  loads `__default_env__` from `~/.doeff.py`, honors `--report` flags, and prints nothing when
  `quiet=True`.
- **Object inputs** (Program instances, callables, dict envs, etc.): bypasses discovery and runs
  directly. Kleislis/transforms are applied in-process, `ProgramInterpreter()` is used when no
  interpreter is supplied, and `load_default_env=False` skips the `~/.doeff.py` injection.

## ProgramRunResult

`run_program()` returns a `ProgramRunResult` dataclass:

- `value`: Final value (None when execution raises; inspect `run_result`).
- `run_result`: Full `RunResult` with context/logs/graph for debugging.
- `interpreter_path`: Resolved interpreter path or description of the callable used.
- `env_sources`: Environment sources applied (paths, `<Program[dict]>`, `<dict>`, or
  `~/.doeff.py:__default_env__`).
- `applied_kleisli`: Description of the applied Kleisli (if any).
- `applied_transforms`: Descriptions of applied transforms.

## Testing Patterns

- Use `quiet=True` to keep pytest output clean while still exercising discovery.
- Pass `load_default_env=False` when you need deterministic environments.
- Assert on `result.run_result` to validate logs, state, or graph output without rerunning the
  Program.