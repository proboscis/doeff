---
name: DoeffExpert
description: >
  Expert guidance on writing Python programs using the doeff library (free monad,
  do-notation, effects).
---

# DoeffExpert

## Explain Pipeline Oriented Programming

- Treat each `@do` function as a pipeline step (a Kleisli arrow returning `Program[T]`).
- Compose steps with `>>` or `.and_then_k` and keep effects explicit via `yield`.
- Isolate cross-cutting changes in transforms (`Program[T] -> Program[U]`) instead of editing
  business steps.
- Keep environment dependencies in `Program[dict]` values and read them via `Ask`/`Local`.

## Use Core doeff Building Blocks

- Use `@do` to define generator-based programs; `yield` effects and `return` values.
- Treat `Program[T]` as the monadic container and interpret only at the boundary.
- Choose effect types (Ask, Log, Put, Fail, etc.) to model context, state, IO, and errors.

## Mark and Discover Kleisli Tools (T -> Program[U])

- Mark Kleisli arrows with `# doeff: kleisli` so `find-kleisli` can discover them.
- Prefer `@do` so Program arguments auto-unwrap for natural composition.
- Use `find-kleisli --type-arg <T>` when narrowing candidates.

```python
from doeff import do, Effect

@do
def fetch_user(user_id: str):  # doeff: kleisli
    user = yield Effect("fetch_user", user_id=user_id)
    return user
```

```bash
find-kleisli .
find-kleisli --type-arg str .
```

## Mark and Discover Transforms (Program[T] -> Program[U])

- Mark transforms with `# doeff: transform` so `find-transforms` can discover them.
- Apply transforms sequentially with `doeff run --transform path.to.transform`.

```python
from doeff import Program

def add_trace(program: Program[dict]) -> Program[dict]:  # doeff: transform
    return program.map(lambda data: {**data, "trace": "enabled"})
```

```bash
find-transforms .
doeff run --program myapp.features.auth.login_program --transform myapp.transforms.add_trace
```

## Enable Auto-Discovery of Interpreters and Environments

- Mark interpreter functions with `# doeff: interpreter, default` (inline or docstring).
- Mark default environments with `# doeff: default` and declare them as `Program[dict]`.
- Rely on auto-discovery to pick the closest default interpreter and merge envs root-to-leaf.

```python
from doeff import Program, ProgramInterpreter

def app_interpreter(program: Program):
    """
    Execute programs for the app.
    # doeff: interpreter, default
    """
    return ProgramInterpreter().run(program).value

# doeff: default
base_env: Program[dict] = Program.pure({"timeout": 10, "db_host": "localhost"})
```

## Run Programs via the CLI

- Run `doeff run --program package.module.program` to use auto-discovered interpreter and envs.
- Override discovery with `--interpreter` or add environments with `--env`.
- Combine `--transform` (Program -> Program) and `--apply` (Kleisli) for pipeline customization.
