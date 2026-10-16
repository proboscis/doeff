# 14. CLI Auto-Discovery

> **Needs a component outside `pip install doeff`.** Auto-discovery uses `doeff-indexer`, which
> is on PyPI:

```bash
pip install doeff doeff-indexer
```

> Without `doeff-indexer`, `doeff run` without `--interpreter` silently falls back to its
> built-in default interpreter. Passing `--interpreter` (and `--env`) explicitly works with
> `pip install doeff` alone.

> `doeff-indexer` 0.1.3 skips a Python file that mentions none of `Program`, `@do`,
> `ProgramInterpreter` and `KleisliProgram`, so a marker in such a file is not discovered (fixed in
> this repository for the release after 0.1.3; see [MARKERS.md](MARKERS.md)). Until then, add
> `from doeff import Program` to such a file.

Automatically discover and use default interpreters and environments when running programs with the `doeff run` command.

## Table of Contents

1. [Overview & Motivation](#overview--motivation)
2. [Quick Start](#quick-start)
3. [Core Concepts](#core-concepts)
4. [Interpreter Discovery](#interpreter-discovery)
5. [Environment Discovery](#environment-discovery)
6. [Marker Syntax Guide](#marker-syntax-guide)
7. [Discovery Algorithm](#discovery-algorithm)
8. [Real-World Examples](#real-world-examples)
9. [Manual Overrides](#manual-overrides)
10. [Troubleshooting](#troubleshooting)
11. [Best Practices](#best-practices)
12. [Advanced Topics](#advanced-topics)
13. [Migration Guide](#migration-guide)
14. [Reference](#reference)

---

## Overview & Motivation

### The Problem

Before auto-discovery, running a doeff program required explicitly specifying the interpreter:

```bash
doeff run \
  --program myapp.features.auth.login_program \
  --interpreter myapp.features.auth.auth_interpreter
```

For large projects with many interpreters and environments, this became:
- **Verbose**: Long command lines
- **Error-prone**: Easy to use wrong interpreter
- **Tedious**: Repetitive typing for common scenarios

### The Solution

Auto-discovery eliminates manual specification by using **markers**:

```bash
# Just specify the program - everything else is automatic!
doeff run --program myapp.features.auth.login_program
```

The CLI automatically:
1. **Finds the closest interpreter** marked with `# doeff: interpreter, default`
2. **Discovers all environments** marked with `# doeff: default`
3. **Merges environments** from root → program (later values override)
4. **Executes** with discovered resources

### Benefits

✅ **Less typing** - Most commands need only `--program`
✅ **Self-documenting** - Markers make intent explicit
✅ **Hierarchical** - Natural project organization
✅ **Flexible** - Can still override manually when needed
✅ **Type-safe** - Discovers only marked resources

---

## Quick Start

### 5-Minute Example

Discovery uses the `doeff-indexer` package, which `doeff` does not install:

```bash
pip install doeff-indexer
```

Without it, `doeff run` silently uses its built-in default interpreter and no envs.

**Step 1**: Mark your interpreter

```python
# myapp/__init__.py
from doeff import Program, Pure, run
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled


def my_interpreter(prog: Program, *, env: Program[dict] = Pure({})):
    """
    Execute programs for myapp.
    # doeff: interpreter, default
    """
    # writer keeps its log in state, so install state() outside writer.
    return run(scheduled(state()(writer(reader(env=run(env))(prog)))))
```

The CLI passes the merged envs to the interpreter's `env` keyword argument.
An interpreter without an `env` parameter does not receive them.

**Step 2**: Mark your environment (optional) — in the same file

```python
# myapp/__init__.py (continued)

# doeff: default
base_env: Program[dict] = Pure({
    'db_host': 'localhost',
    'timeout': 10
})
```

**Step 3**: Write a program in a module below `myapp`

```python
# myapp/features/__init__.py is an empty file.

# myapp/features/report.py
from doeff import Ask, do


@do
def show_config():
    host = yield Ask('db_host')
    timeout = yield Ask('timeout')
    return f"{host} (timeout {timeout}s)"


my_program = show_config()
```

`--program` names a Program value (`show_config()`), not the `@do` function itself.

**Step 4**: Run your program from the project root

```bash
doeff run --program myapp.features.report.my_program
# localhost (timeout 10s)
```

The indexer scans the current directory, so run `doeff` from the directory that
contains `myapp/`, with `myapp` importable (installed in your environment, or run
`python -m doeff run ...` from that directory).

That's it! The CLI automatically:
- Uses `my_interpreter` (closest default)
- Passes `base_env` to the interpreter's `env` argument
- Executes everything seamlessly

---

## Core Concepts

### 1. Markers

Special comments that mark functions and variables for auto-discovery:

```python
from doeff import Program, Pure

# For interpreters (functions)
def my_interpreter(prog: Program):
    """# doeff: interpreter, default"""
    ...

# For environments (variables)
# doeff: default
my_env: Program[dict] = Pure({...})
```

### 2. Interpreter Discovery: Closest Match

```mermaid
graph TB
    A[myapp/__init__.py] --> B[base_interpreter ⭕]
    C[myapp/features/__init__.py] --> D[No interpreter]
    E[myapp/features/auth/__init__.py] --> F[auth_interpreter ✓]
    G[myapp/features/auth/login.py] --> H[login_program]

    H -.search.-> E
    E -.found!.-> F

    style F fill:#90EE90
    style B fill:#FFE4B5
```

**Rule**: Selects the **closest** (rightmost in hierarchy) default interpreter.

### 3. Environment Discovery: Accumulation

```mermaid
graph LR
    A[base_env<br/>timeout: 10] --> B[features_env<br/>api_key: xxx]
    B --> C[auth_env<br/>timeout: 30]
    C --> D[Merged Result<br/>timeout: 30<br/>api_key: xxx]

    style D fill:#87CEEB
```

**Rule**: Discovers **all** default environments, merges left-to-right (later overrides earlier).

### 4. Discovery Flow

```mermaid
graph LR
    A[User runs<br/>doeff run] --> B{--interpreter<br/>specified?}
    B -->|No| C[Auto-discover<br/>interpreter]
    B -->|Yes| D[Use specified]
    C --> X{--env<br/>specified?}
    X -->|No| E[Discover envs]
    X -->|Yes| Y[Use only<br/>--env envs]
    D --> Y
    E --> F[Merge envs]
    Y --> F
    F --> G[Execute program]

    style C fill:#90EE90
    style E fill:#90EE90
    style F fill:#90EE90
```

`--interpreter` turns off discovery of envs too, and `--env` replaces the
discovered envs (see [Manual Overrides](#manual-overrides)).

---

## Interpreter Discovery

### How It Works

The CLI searches for functions marked with `# doeff: interpreter, default`:

1. **Extract module path** from program (e.g., `myapp.features.auth.login.my_program` → `myapp.features.auth.login`)
2. **Build hierarchy**: `["myapp", "myapp.features", "myapp.features.auth", "myapp.features.auth.login"]`
3. **Query indexer** for all default interpreters (the indexer scans the current directory)
4. **Filter to hierarchy**: Keep only interpreters defined in a module on that path
   (the program's own module, or the `__init__.py` of a package above it)
5. **Select closest**: Choose the rightmost (deepest) match

If no marked interpreter is found, `doeff run` does not stop with an error: it runs the
program with its built-in default interpreter (`doeff.cli.run_services:default_interpreter`),
which ignores envs.

### Example: Closest Match Selection

```python
# myapp/__init__.py
from doeff import Program, run
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled


def base_interpreter(prog: Program):
    """# doeff: interpreter, default"""
    return run(scheduled(state()(writer(reader(env={})(prog)))))

# myapp/features/__init__.py
# (no interpreter)

# myapp/features/auth/__init__.py
from doeff import Program, run
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled


def auth_interpreter(prog: Program):
    """# doeff: interpreter, default"""
    # Custom auth handling goes here
    return run(scheduled(state()(writer(reader(env={})(prog)))))

# myapp/features/auth/login.py
from doeff import Tell, do


@do
def login():
    yield Tell("Logging in...")
    return "success"


login_program = login()
```

**Running the program:**

```bash
doeff run --program myapp.features.auth.login.login_program
```

**Discovery process:**

```
1. Module: myapp.features.auth.login
2. Hierarchy: [myapp, myapp.features, myapp.features.auth, myapp.features.auth.login]
3. Found interpreters:
   - myapp.base_interpreter (index 0)
   - myapp.features.auth.auth_interpreter (index 2)
4. Selected: auth_interpreter (index 2 > index 0, closest!)
```

### Validation

Interpreters must:
- Be callable (function or class with `__call__`)
- Accept exactly 1 positional parameter
- Be non-async (synchronous function)
- Have the marker `# doeff: interpreter, default`

**Valid:**
```python
def my_interpreter(prog: Program):
    """# doeff: interpreter, default"""
    return run(scheduled(state()(writer(reader(env={})(prog)))))
```

**Invalid** (async not allowed):
```python
async def my_interpreter(prog: Program):  # ❌ async
    """# doeff: interpreter, default"""
    ...
```

---

## Environment Discovery

### How It Works

The CLI discovers **all** environments marked with `# doeff: default`:

1. **Build hierarchy** from program module path
2. **Query indexer** for all default variables
3. **Collect all envs** in hierarchy order (root → program)
4. **Merge** using Program composition (later overrides earlier)

### Example: Environment Accumulation

```python
# myapp/__init__.py
from doeff import Program, Pure, run
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled


def base_interpreter(prog: Program, *, env: Program[dict] = Pure({})):
    """# doeff: interpreter, default"""
    return run(scheduled(state()(writer(reader(env=run(env))(prog)))))

# doeff: default
base_env: Program[dict] = Pure({
    'db_host': 'localhost',
    'timeout': 10,
    'debug': False
})

# myapp/features/__init__.py
from doeff import Program, Pure

# doeff: default
features_env: Program[dict] = Pure({
    'api_key': 'secret123',
    'timeout': 20  # Override base
})

# myapp/features/auth/__init__.py
from doeff import Program, Pure

# doeff: default
auth_env: Program[dict] = Pure({
    'oauth_provider': 'google',
    'timeout': 30  # Override again
})

# myapp/features/auth/login.py
from doeff import Ask, do


@do
def login():
    timeout = yield Ask('timeout')
    return f"Timeout: {timeout}"  # Will be 30!


login_program = login()
```

```bash
doeff run --program myapp.features.auth.login.login_program
# Timeout: 30
```

**Discovery process:**

```
1. Hierarchy: [myapp, myapp.features, myapp.features.auth, ...]
2. Found envs:
   - myapp.base_env (timeout: 10, debug: False)
   - myapp.features.features_env (api_key: secret123, timeout: 20)
   - myapp.features.auth.auth_env (oauth_provider: google, timeout: 30)
3. Merge order: base → features → auth
4. Result: {
     'db_host': 'localhost',
     'timeout': 30,           # Overridden twice!
     'debug': False,
     'api_key': 'secret123',
     'oauth_provider': 'google'
   }
```

### Merge Strategy

**Key Principle**: Later values override earlier values

```mermaid
graph LR
    A[base_env<br/>x: 1, y: 2] --> B[middle_env<br/>y: 3, z: 4]
    B --> C[leaf_env<br/>z: 5]
    C --> D[Final<br/>x: 1<br/>y: 3<br/>z: 5]

    style D fill:#87CEEB
```

**Use cases:**
- **Base config** at root (sensible defaults)
- **Feature overrides** in feature modules
- **Specific config** at leaf modules

---

## Marker Syntax Guide

### Interpreter Markers

**Required tags**: `interpreter, default` (both tags required)

**Placement options:**

1. **Docstring** (recommended):
```python
def my_interpreter(prog: Program):
    """
    Execute programs with custom settings.
    # doeff: interpreter, default
    """
    return run(scheduled(state()(writer(reader(env={})(prog)))))
```

2. **Inline comment**:
```python
def my_interpreter(prog: Program):  # doeff: interpreter, default
    return run(scheduled(state()(writer(reader(env={})(prog)))))
```

3. **Multi-line signature**:
```python
def my_interpreter(  # doeff: interpreter, default
    prog: Program,
    *,
    verbose: bool = False,
):
    return run(scheduled(state()(writer(reader(env={})(prog)))))
```

Parameters after `prog` go after `*` (keyword-only), so the interpreter still takes exactly
1 positional parameter (see [Validation](#validation)).

### Environment Markers

**Required tag**: `default`

**Placement options:**

1. **Preceding comment** (recommended):
```python
from doeff import Program, Pure

# doeff: default
my_env: Program[dict] = Pure({
    'key': 'value'
})
```

2. **Same line**:
```python
my_env: Program[dict] = Pure({'key': 'value'})  # doeff: default
```

The variable needs a type annotation (`my_env: Program[dict] = ...` or `my_env: dict = ...`):
the indexer skips plain assignments such as `my_env = Pure({...})`.

### Syntax Rules

✅ **Correct**:
```python
# doeff: interpreter, default
# doeff: default
# doeff: interpreter,default  (no space after comma - also works)
```

❌ **Incorrect**:
```python
# doeff interpreter, default  (missing colon)
# doeff: interpreter default  (missing comma)
#doeff: default  (missing space after #)
```

---

## Discovery Algorithm

### Step-by-Step Process

```mermaid
graph TB
    Start[User: doeff run --program X] --> A[Parse program path X]
    A --> B{--interpreter<br/>provided?}
    B -->|Yes| C[Use specified interpreter]
    B -->|No| D[Extract module path]
    D --> E[Build module hierarchy]
    E --> F[Query indexer for<br/>interpreter, default]
    F --> I[Filter to hierarchy]
    I --> G{Found any?}
    G -->|No| H[Use built-in<br/>default interpreter]
    G -->|Yes| J[Select closest match]
    H --> Q{--env<br/>provided?}
    J --> Q
    Q -->|No| L[Query indexer for<br/>default envs]
    Q -->|Yes| R[Use only<br/>--env envs]
    C --> R
    L --> M[Collect all envs<br/>in hierarchy]
    M --> N[Merge envs<br/>left-to-right]
    R --> N
    N --> O[Pass merged env to the<br/>interpreter's env argument]
    O --> P[Execute program]

    style D fill:#E6F3FF
    style F fill:#E6F3FF
    style L fill:#E6F3FF
    style J fill:#90EE90
    style N fill:#90EE90
    style H fill:#FFB6C1
```

### Internal Implementation

**Discovery service** (`doeff/cli/discovery.py`):

```python
from doeff_indexer import Indexer

# 1. Create indexer for module
indexer = Indexer.for_module("myapp.features.auth")

# 2. Find default interpreters
symbols = indexer.find_symbols(
    tags=["interpreter", "default"],
    symbol_type="function"
)

# 3. Filter to hierarchy
hierarchy = ["myapp", "myapp.features", "myapp.features.auth"]
candidates = [s for s in symbols if s.module_path in hierarchy]

# 4. Select closest
closest = max(candidates, key=lambda s: hierarchy.index(s.module_path))
```

**Env merging** (using `@do` composition):

```python
@do
def merge_envs(env_sources: list[str]) -> dict:
    merged = {}
    for env_path in env_sources:
        env = load_symbol(env_path)
        if isinstance(env, Program):
            env_dict = yield env  # Evaluate Program[dict]
        else:
            env_dict = env
        merged.update(env_dict)  # Later overrides earlier
    return merged
```

---

## Real-World Examples

### Example 1: Simple Single-Level Project

```
myapp/
  __init__.py          # base_interpreter, base_env
  program.py           # my_program
```

**Setup:**

```python
# myapp/__init__.py
from doeff import Program, Pure, run
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled


def base_interpreter(prog: Program, *, env: Program[dict] = Pure({})):
    """# doeff: interpreter, default"""
    return run(scheduled(state()(writer(reader(env=run(env))(prog)))))

# doeff: default
base_env: Program[dict] = Pure({'debug': True})

# myapp/program.py
from doeff import Ask, do


@do
def main():
    debug = yield Ask('debug')
    return f"Debug mode: {debug}"


my_program = main()
```

**Usage:**

```bash
doeff run --program myapp.program.my_program
# Debug mode: True
# Uses: base_interpreter, base_env
```

### Example 2: Multi-Feature Project

```
myapp/
  __init__.py          # base_interpreter, base_env
  features/
    __init__.py        # features_env
    auth/
      __init__.py      # auth_interpreter, auth_env
      login.py         # login_program
    billing/
      __init__.py      # billing_interpreter, billing_env
      charge.py        # charge_program
```

**Setup:**

```python
# myapp/__init__.py
from doeff import Program, Pure, run
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled


def base_interpreter(prog: Program, *, env: Program[dict] = Pure({})):
    """# doeff: interpreter, default"""
    return run(scheduled(state()(writer(reader(env=run(env))(prog)))))

# doeff: default
base_env: Program[dict] = Pure({
    'db_host': 'localhost',
    'timeout': 10
})

# myapp/features/__init__.py
from doeff import Program, Pure

# doeff: default
features_env: Program[dict] = Pure({
    'api_key': 'secret123'
})

# myapp/features/auth/__init__.py
from doeff import Program, Pure


def auth_interpreter(prog: Program, *, env: Program[dict] = Pure({})):
    """
    Custom auth interpreter with OAuth.
    # doeff: interpreter, default
    """
    return custom_auth_run(prog, env=env)  # your own runner

# doeff: default
auth_env: Program[dict] = Pure({
    'oauth_provider': 'google',
    'timeout': 30
})

# myapp/features/billing/__init__.py
from doeff import Program, Pure


def billing_interpreter(prog: Program, *, env: Program[dict] = Pure({})):
    """# doeff: interpreter, default"""
    return stripe_run(prog, env=env)  # your own runner

# doeff: default
billing_env: Program[dict] = Pure({
    'stripe_key': 'sk_test_...'
})
```

**Usage:**

```bash
# Auth program
doeff run --program myapp.features.auth.login.login_program
# Uses: auth_interpreter
# Envs: base_env + features_env + auth_env
# Result: {db_host: localhost, timeout: 30, api_key: secret123, oauth_provider: google}

# Billing program
doeff run --program myapp.features.billing.charge.charge_program
# Uses: billing_interpreter
# Envs: base_env + features_env + billing_env
# Result: {db_host: localhost, timeout: 10, api_key: secret123, stripe_key: sk_test_...}
```

### Example 3: Microservices Architecture

```
services/
  __init__.py          # common_interpreter, common_env
  users/
    __init__.py        # users_interpreter, users_env
    api.py             # user_api_program
  orders/
    __init__.py        # orders_interpreter, orders_env
    api.py             # order_api_program
  payments/
    __init__.py        # payments_interpreter, payments_env
    api.py             # payment_api_program
```

Each service has:
- **Shared base**: Common timeout, logging, database config
- **Service-specific**: Custom interpreter with service logic
- **Service environment**: API keys, service-specific config

**Benefits:**
- No repetition of common config
- Service isolation through interpreters
- Clear override hierarchy

---

## Manual Overrides

### When to Override

Use manual flags when:
- **Testing**: Use a test interpreter
- **Debugging**: Use a verbose interpreter
- **One-off runs**: Temporary config override
- **Multiple envs**: Replace the discovered envs with your own layers

### Override Flags

#### 1. `--interpreter` (overrides discovery)

```bash
# Force use of test interpreter
doeff run \
  --program myapp.program \
  --interpreter tests.test_interpreter
```

**Behavior**: Skips discovery entirely — of the interpreter and of the envs. Only the
`--env` envs (if any) reach the interpreter.

#### 2. `--env` (replaces discovered envs)

```bash
# Use the test environment instead of the discovered envs
doeff run \
  --program myapp.program \
  --env tests.test_env
```

**Behavior**: Still discovers the interpreter, but does not discover envs: only `test_env` is used.

#### 3. Multiple `--env` flags

```bash
# Layer multiple environments
doeff run \
  --program myapp.program \
  --env myapp.dev_env \
  --env myapp.local_overrides
```

**Merge order**: dev_env → local_overrides (discovered envs are not used)

### Combined Usage

```bash
doeff run \
  --program myapp.features.auth.login_program \
  --interpreter tests.mock_interpreter \
  --env tests.mock_env
```

**Result**:
- Interpreter: `tests.mock_interpreter` (manual)
- Envs: `mock_env` only (manual)

---

## Troubleshooting

### Built-in Default Interpreter Used Instead of Yours

**Symptom**: `doeff run` does not stop with an error when it finds no marked interpreter.
It runs the program with its built-in default interpreter, which ignores envs, so
`Ask('key')` fails. The handlers listed are those of the built-in default interpreter:

```
UnhandledEffect: no handler found for effect: Ask (Ask('timeout'))
  handlers in scope (innermost→outermost): await_handler → _listen_handler → _slog_handler → _try_handler → _writer_handler → state → lazy_ask → _scheduled_python
  hint: Ask is handled by reader(env={...}) or lazy_ask(env={...}) holding the key: run(reader(env={...})(prog))
```

For a program that succeeds, `--format json` shows
`"interpreter": "doeff.cli.run_services:default_interpreter"` and `"envs": []`.

**Diagnosis:**

```mermaid
graph TD
    A[default_interpreter used] --> F{doeff-indexer<br/>installed?}
    F -->|No| G[Fix: pip install doeff-indexer]
    F -->|Yes| R{Run from the<br/>project root?}
    R -->|No| S[Fix: cd to the directory<br/>that contains the myapp package]
    R -->|Yes| B{Marker syntax<br/>correct?}
    B -->|No| C[Fix: Add comma between tags<br/># doeff: interpreter, default]
    B -->|Yes| D{Marker in<br/>hierarchy?}
    D -->|No| E[Fix: Add marker to parent module<br/>or use --interpreter]
    D -->|Yes| H{Marker<br/>on function?}
    H -->|No| I[Fix: Ensure marker on<br/>interpreter function not variable]
    H -->|Yes| J[Run with --profile<br/>see Debugging Discovery]
```

**Solutions:**

1. **Add marker to a parent module:**
```python
# myapp/__init__.py
from doeff import Program, Pure, run
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled


def base_interpreter(prog: Program, *, env: Program[dict] = Pure({})):
    """# doeff: interpreter, default"""
    return run(scheduled(state()(writer(reader(env=run(env))(prog)))))
```

2. **Or specify manually:**
```bash
doeff run --program myapp.program --interpreter myapp.base_interpreter
```

3. **Verify marker syntax:**
```python
# ✅ Correct
"""# doeff: interpreter, default"""

# ❌ Wrong (missing comma)
"""# doeff: interpreter default"""

# ❌ Wrong (missing colon)
"""# doeff interpreter, default"""
```

### Error: Wrong Interpreter Selected

**Symptom**: Program runs but uses unexpected interpreter.

**Cause**: Multiple default interpreters, closest match not what you expected.

**Debug:**

```bash
# Show discovered interpreter
doeff run --program myapp.program --format json | jq '.interpreter'
```

**Solution:**

1. **Remove unwanted marker** from closer module
2. **Or specify explicitly:**
```bash
doeff run --program myapp.program --interpreter myapp.desired_interpreter
```

### Error: Environment Values Not Applied

**Symptom**: `Ask('key')` fails with `KeyError: "Ask: key not found: 'key'"`, or returns a wrong value.

**Debug:**

```bash
# Show discovered envs
doeff run --program myapp.program --format json | jq '.envs'
```

**Common causes:**

1. **Marker syntax error:**
```python
# ❌ Wrong
doeff: default  # Missing #
my_env: dict = {...}

# ✅ Correct
# doeff: default
my_env: dict = {...}
```

2. **Override order confusion:**
```python
# Later overrides earlier!
# myapp/__init__.py
base_env = {'timeout': 10}

# myapp/features/__init__.py
features_env = {'timeout': 20}  # This wins!
```

3. **Missing type annotation:**
```python
from doeff import Program, Pure

# ❌ Won't be indexed (plain assignment)
# doeff: default
my_env = Pure({'key': 'value'})

# ✅ Will be indexed
# doeff: default
my_env: Program[dict] = Pure({'key': 'value'})
```

4. **The interpreter has no `env` parameter:** the CLI passes the merged envs only as the
   `env` keyword argument (see [Quick Start](#quick-start)).

5. **`--interpreter` or `--env` was given:** both turn off env discovery
   (see [Manual Overrides](#manual-overrides)).

### Debugging Discovery

`doeff run` has no `--verbose` flag. To see what discovery chose:

```bash
# Show the chosen interpreter and envs
doeff run --program myapp.program --format json
```

`--profile` (or `DOEFF_PROFILE=1`) prints how long each CLI step takes as `[DOEFF][PROFILE]`
lines on stderr. They are off by default, and they also name each env discovery loaded:

```bash
doeff run --program myapp.program --profile
# [DOEFF][PROFILE] Profiling enabled by --profile or DOEFF_PROFILE.
# [DOEFF][PROFILE]     Load symbol myapp.base_env: 0.02ms
```

**Manual verification** (needs `doeff-indexer`; run from the project root):

```python
# Test indexer directly
from doeff_indexer import Indexer

indexer = Indexer.for_module("myapp")

# Find interpreters
interps = indexer.find_symbols(
    tags=["interpreter", "default"],
    symbol_type="function"
)
print(f"Found interpreters: {[i.full_path for i in interps]}")

# Find envs
envs = indexer.find_symbols(
    tags=["default"],
    symbol_type="variable"
)
print(f"Found envs: {[e.full_path for e in envs]}")
```

---

## Best Practices

### 1. Interpreter Organization

**Recommended structure:**

```text
# Option A: One interpreter per feature
myapp/
  __init__.py          # base_interpreter (fallback)
  features/
    auth/__init__.py   # auth_interpreter (auth-specific)
    billing/__init__.py # billing_interpreter (billing-specific)

# Option B: One interpreter at root
myapp/
  __init__.py          # shared_interpreter (used by all)
  features/
    auth/__init__.py   # (no interpreter)
    billing/__init__.py # (no interpreter)
```

**Guideline**: Use **Option A** when features need different execution logic, **Option B** for uniform behavior.

### 2. Environment Layering

**Best practice hierarchy:**

```
base_env (root)          ← Shared config, sensible defaults
  ↓
feature_env (feature)    ← Feature-specific overrides
  ↓
module_env (leaf)        ← Module-specific config
```

**Example:**

```python
# myapp/__init__.py - Base defaults
from doeff import Program, Pure

# doeff: default
base_env: Program[dict] = Pure({
    'db_host': 'localhost',
    'db_port': 5432,
    'timeout': 10,
    'debug': False,
    'log_level': 'INFO'
})

# myapp/features/__init__.py - Feature overrides
from doeff import Program, Pure

# doeff: default
features_env: Program[dict] = Pure({
    'api_base_url': 'https://api.example.com',
    'timeout': 30  # Features need longer timeout
})

# myapp/features/auth/__init__.py - Specific config
from doeff import Program, Pure

# doeff: default
auth_env: Program[dict] = Pure({
    'oauth_provider': 'google',
    'oauth_callback': '/auth/callback',
    'debug': True  # Auth debugging enabled
})
```

### 3. Naming Conventions

**Interpreters:**
```text
# Pattern: <feature>_interpreter
base_interpreter       # Base/default
auth_interpreter       # Authentication
billing_interpreter    # Billing
test_interpreter       # Testing
```

**Environments:**
```text
# Pattern: <scope>_env
base_env               # Base config
dev_env                # Development
prod_env               # Production
test_env               # Testing
auth_env               # Auth-specific
```

### 4. Marker Placement

**Recommended: Docstrings for interpreters**

```python
def my_interpreter(prog: Program):
    """
    Execute programs with custom error handling.

    # doeff: interpreter, default
    """
    return run(scheduled(state()(writer(reader(env={})(prog)))))
```

**Recommended: Preceding comment for envs**

```python
from doeff import Program, Pure

# doeff: default
my_env: Program[dict] = Pure({
    'key': 'value'
})
```

**Why?**
- Clear visual separation
- Easy to search (`grep "# doeff: default"`)
- Works with formatters/linters

### 5. Testing Discovery

**Always test discovery in CI:**

```bash
# Verify auto-discovery works
doeff run --program myapp.test_program --format json

# Verify manual override works
doeff run --program myapp.test_program \
  --interpreter tests.test_interpreter \
  --format json
```

---

## Advanced Topics

### Custom Discovery Strategies

**(Future feature - not yet implemented)**

Extend discovery with custom strategies:

```python
from doeff.cli.discovery import InterpreterDiscovery

class ProductionOnlyDiscovery(InterpreterDiscovery):
    """Only discover interpreters marked 'production'."""

    def find_default_interpreter(self, program_path: str):
        # Custom logic: filter by 'production' tag
        ...
```

### Performance Optimization

**Discovery overhead**: < 100ms for typical projects

**When it matters**:
- Very large projects (1000+ modules)
- Frequent CLI invocations (build scripts)

**Solutions**:
1. **Cache discovery results** (future feature)
2. **Use manual flags** in performance-critical scripts
3. **Pre-build index** as part of deployment

### IDE Integration

**PyCharm Plugin** already uses doeff-indexer for:
- Function navigation
- Type checking
- Quick documentation

**Auto-discovery compatibility**:
- IDE shows all interpreters (broad view)
- CLI selects default (narrow view)
- Same underlying indexer, different filtering

### Programmatic Usage

**Use discovery in your code:**

```python
from doeff.cli.discovery import IndexerBasedDiscovery

discovery = IndexerBasedDiscovery()

# Find interpreter for a program
interpreter_path = discovery.find_default_interpreter(
    "myapp.features.auth.login.login_program"
)

print(f"Would use: {interpreter_path}")
# Output: myapp.features.auth.auth_interpreter
```

---

## Migration Guide

### Adding Discovery to Existing Projects

**Step 1: Audit existing interpreters**

```bash
# Find all interpreter functions
grep -r "def.*interpreter" --include="*.py"
```

**Step 2: Add markers**

```python
# Before
def my_interpreter(prog: Program):
    return run(scheduled(state()(writer(reader(env={})(prog)))))

# After
def my_interpreter(prog: Program):
    """# doeff: interpreter, default"""
    return run(scheduled(state()(writer(reader(env={})(prog)))))
```

**Step 3: Test discovery**

```bash
# Test that discovery works
doeff run --program your.program --format json

# Verify discovered interpreter
doeff run --program your.program --format json | jq '.interpreter'
```

**Step 4: Remove explicit flags**

```bash
# Before
doeff run --program myapp.program --interpreter myapp.interpreter

# After
doeff run --program myapp.program
```

### Gradual Adoption

**Phase 1**: Add markers, keep using explicit flags
```bash
doeff run --program X --interpreter Y  # Still works!
```

**Phase 2**: Test auto-discovery in development
```bash
doeff run --program X  # Try without flags
```

**Phase 3**: Update CI/CD to use auto-discovery
```bash
# Update scripts to remove --interpreter flags
```

**Phase 4**: Remove explicit flags from documentation

### Backward Compatibility

✅ **All existing commands still work**

```bash
# This will ALWAYS work (no breaking changes)
doeff run --program X --interpreter Y
```

✅ **Discovery is opt-in**

Without markers, behavior is unchanged:
- No markers → the built-in default interpreter runs the program (no envs)
- With markers → Auto-discovery works

---

## Reference

### Marker Syntax

| Marker | Purpose | Syntax | Example |
|--------|---------|--------|---------|
| `interpreter, default` | Default interpreter | `# doeff: interpreter, default` | Function that executes programs |
| `default` | Default environment | `# doeff: default` | Variable with config dict |

### CLI Flags

| Flag | Description | Example |
|------|-------------|---------|
| `--program` | Program to execute (required) | `--program myapp.program` |
| `--interpreter` | Use this interpreter (turns off discovery of the interpreter and the envs) | `--interpreter myapp.interpreter` |
| `--env` | Use this environment instead of the discovered ones (can repeat) | `--env myapp.dev_env` |
| `--format` | Output format (text or json) | `--format json` |
| `--profile` | Print how long each CLI step takes as `[DOEFF][PROFILE]` lines on stderr (off by default; `DOEFF_PROFILE=1` does the same) | `--profile` |

### Error Messages

| Error | Cause | Solution |
|-------|-------|----------|
| `UnhandledEffect: no handler found for effect: Ask`, with `lazy_ask` among the handlers | No marked interpreter found (or `doeff-indexer` not installed): the built-in default interpreter ran | Install `doeff-indexer`, add marker, or use `--interpreter` |
| `KeyError: "Ask: key not found: ..."` | The env did not reach the program | See [Environment Values Not Applied](#error-environment-values-not-applied) |
| `expected DoExpr or EffectBase, got: <class 'function'>` | `--program` names a `@do` function, not a Program value | Point `--program` at a value such as `my_program = main()` |
| `ModuleNotFoundError: No module named 'myapp'` | `myapp` is not importable where `doeff` runs | Install your project, or run `python -m doeff run ...` from the project root |

### JSON Output Format

```json
{
  "status": "ok",
  "program": "myapp.features.auth.login.login_program",
  "interpreter": "myapp.features.auth.auth_interpreter",
  "envs": [
    "myapp.base_env",
    "myapp.features.features_env",
    "myapp.features.auth.auth_env"
  ],
  "result": "success",
  "result_type": "str"
}
```

### Links

- **Marker Specification**: See [MARKERS.md](MARKERS.md) for general marker usage
- **Implementation Specs**: See `specs/features/cli-auto-discovery/` for technical details
- **PyO3 Indexer**: See `packages/doeff-indexer/` for indexer internals

---

## See Also

- **[MARKERS.md](MARKERS.md)** - General marker system for IDE plugins
- **[01-getting-started.md](01-getting-started.md)** - Getting started with doeff
- **[13-api-reference.md](13-api-reference.md)** - Complete API reference

---

**Questions or issues?** File an issue on [GitHub](https://github.com/proboscis/doeff/issues).