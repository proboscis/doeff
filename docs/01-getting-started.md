# Getting Started with doeff

doeff is an **algebraic effects** system for Python. You write programs that *perform* effects (read config, update state, log messages), and **handlers** interpret those effects. The runtime uses **one-shot continuations** backed by a **Rust VM** for performance.

## Installation

Install doeff using pip:

```bash
pip install doeff
```

The CLI ships with a Rust-powered indexer used for auto-discovery.
When you install `doeff`, the indexer is built automatically.
Make sure a Rust toolchain is available first:

```bash
curl https://sh.rustup.rs -sSf | sh
```

If you're using uv:

```bash
uv add doeff
```

## Requirements

- Python 3.11 or higher
- asyncio support (built into Python)

## Your First Program

Let's write a simple program that uses state management and logging:

```python
from doeff import do, run, Pure
from doeff_core_effects import Get, Put, Tell
from doeff_core_effects.handlers import state, writer

@do
def counter_program():
    yield Put("counter", 0)
    yield Tell("Counter initialized")

    count = yield Get("counter")
    yield Tell(f"Current count: {count}")

    yield Put("counter", count + 1)
    final_count = yield Get("counter")

    yield Tell(f"Final count: {final_count}")
    return final_count

def main():
    prog = counter_program()
    prog = writer(prog)
    prog = state()(prog)
    result = run(prog)
    print(f"Result: {result}")  # Result: 1

main()
```

Output:
```text
Result: 1
```

## Understanding the Example

Let's break down what's happening:

1. **`@do` decorator**: Converts a generator function into a callable that produces an `Expand` program node
2. **`yield` effects**: Each `yield` *performs* an algebraic effect — the program suspends and the handler processes it
3. **Effects** (algebraic effect operations):
   - `Put("counter", value)` - State effect: sets state (handled by the State handler)
   - `Get("counter")` - State effect: retrieves state
   - `Tell(message)` - Writer effect: appends to log (handled by the Writer handler)
4. **Handler composition**: Handlers are composed by wrapping the program: `state()(writer(prog))`
5. **`run`**: Executes the fully-handled program and returns the raw result value

Handler composition is done by applying each handler to the program.
Each handler is a `Program -> Program` installer — compose them by nesting calls rather than
passing handlers to `run()`. Some handlers like `writer` and `slog_handler` are pre-installed
(used directly), while others like `reader(env=...)` and `state(initial=...)` are factories
that return an installer.

`writer` keeps its log in state, so install `state()` outside it: `state()(writer(prog))`.
Without `state()` outside, the first `Tell` fails with
`UnhandledEffect: unhandled effect: Get (Get('__doeff_writer_log__'))`.
Read the collected log with `yield writer_log()` (from `doeff_core_effects.handlers`).

## Key Concepts

### Programs are Lazy

Programs don't execute until you call `run()`:

```python
from doeff import do, run
from doeff_core_effects import Tell
from doeff_core_effects.handlers import state, writer

@do
def my_program():
    yield Tell("This won't execute yet")
    return 42

# Just creates a Program, doesn't execute
program = my_program()

# Now it executes
prog = state()(writer(program))
result = run(prog)
print(result)  # 42
```

### Programs are Reusable

Unlike Python generators, `@do` functions can be called multiple times:

```python
from doeff import do, run
from doeff_core_effects import Tell
from doeff_core_effects.handlers import state, writer

@do
def format_label(label: str):
    yield Tell(f"Formatting {label}")
    return label.upper()

# Each call creates a fresh Program
prog1 = format_label("alpha")
prog2 = format_label("beta")

# Each execution is independent
result1 = run(state()(writer(prog1)))
result2 = run(state()(writer(prog2)))
print(result1, result2)  # ALPHA BETA
```

### Effects are Composable

You can compose effectful programs together — a key advantage of algebraic effects:

```python
from doeff import do, run
from doeff_core_effects import Get, Put, Tell
from doeff_core_effects.handlers import state, writer, writer_log

@do
def setup():
    yield Put("config", {"debug": True})
    yield Tell("Setup complete")

@do
def work():
    config = yield Get("config")
    yield Tell(f"Working with config: {config}")
    return "done"

@do
def full_program():
    yield setup()  # Run setup first
    result = yield work()  # Then do work
    log = yield writer_log()
    return result, log

result, log = run(state()(writer(full_program())))
print(result)  # done
print(log)  # ['Setup complete', "Working with config: {'debug': True}"]
```

## Running with Initial State

You can provide initial environment and state via handler arguments:

```python
from doeff import do, run
from doeff_core_effects import Ask, Get
from doeff_core_effects.handlers import reader, state

@do
def my_program():
    db_url = yield Ask("database_url")
    user_id = yield Get("user_id")
    return f"Connected to {db_url} as user {user_id}"

# Pass initial environment to reader and initial state to state
prog = my_program()
prog = state(initial={"user_id": 123})(prog)
prog = reader(env={"database_url": "postgresql://localhost/mydb"})(prog)
result = run(prog)
print(result)  # Connected to postgresql://localhost/mydb as user 123
```

## Error Handling

`run()` returns the raw result value on success, or raises an exception on failure:

```python
from doeff import do, run
from doeff_core_effects import Tell
from doeff_core_effects.handlers import state, writer

@do
def my_program():
    yield Tell("working...")
    return 42

prog = state()(writer(my_program()))

try:
    result = run(prog)
    print(f"Success: {result}")  # Success: 42
except Exception as e:
    print(f"Error: {e}")
```

Output:
```text
Success: 42
```

## Common Patterns

### Combining Multiple Effects

```python
from doeff import do, run
from doeff_core_effects import Ask, Get, Put, Tell
from doeff_core_effects.handlers import reader, state, writer

@do
def complex_workflow():
    # Reader effect - get configuration
    db_url = yield Ask("database_url")

    # State effect - manage local state
    yield Put("connection", f"Connected to {db_url}")

    # Writer effect - log progress
    yield Tell("Processing data...")

    connection = yield Get("connection")
    return connection

prog = complex_workflow()
prog = writer(prog)
prog = state()(prog)
prog = reader(env={"database_url": "postgresql://localhost/mydb"})(prog)
result = run(prog)
print(result)  # Connected to postgresql://localhost/mydb
```

### Conditional Logic

```python
from doeff import do, run
from doeff_core_effects import Get, Put, Tell
from doeff_core_effects.handlers import state, writer

@do
def conditional_program():
    count = yield Get("count")

    if count > 10:
        yield Tell("Count is high")
        yield Put("status", "high")
    else:
        yield Tell("Count is low")
        yield Put("status", "low")

    status = yield Get("status")
    return count, status

print(run(state(initial={"count": 12})(writer(conditional_program()))))  # (12, 'high')
print(run(state(initial={"count": 3})(writer(conditional_program()))))  # (3, 'low')
```

## Writing Programs in Hy

Programs, effects, and handlers can also be written in [Hy](https://hylang.org), a Lisp that runs on
Python. Hy comes with the extra `hy`:

```bash
pip install "doeff[hy]"
```

A Hy file starts with the `(require ...)` lines that bring in the macros it uses: `do!` and `<-` come
from `doeff-hy.macros`, `defhandler` from `doeff-hy.handle`. Save this as `greet_hy.hy` and run it with
`hy greet_hy.hy`:

```hy
(require doeff-hy.macros [do! <-])
(import doeff [Ask run])
(import doeff-core-effects.handlers [reader])

(setv greet
  (do!
    (<- greeting (Ask "greeting"))
    greeting))

(print (run ((reader :env {"greeting" "hello"}) greet)))  ; hello
```

### Importing a Hy module from Python

Python reads a `.hy` file only after `import hy` has run. Put `import hy` before the import of a Hy
module (Python files that never import `hy` cannot see `.hy` files at all):

```hy file=greet.hy
(require doeff-hy.macros [do! <-])
(import doeff [Ask])

(setv greet
  (do!
    (<- greeting (Ask "greeting"))
    greeting))
```

```python
import hy  # first: registers Hy's import hook, so Python can find greet.hy
from doeff import run
from doeff_core_effects.handlers import reader
from greet import greet

print(run(reader(env={"greeting": "hello"})(greet)))  # hello
```

## Troubleshooting

### "NameError: name 'hyx_doXexclamation_markX' is not defined"

Or `name 'defk' is not defined`, `name 'defhandler' is not defined`. A Hy macro was used without a
`require`, so Hy compiled the use as a call to a function that does not exist. doeff prints a
`HyMacroUseWarning` with the exact line to add just before the traceback. Add the `require` for the
macros the module uses:

```hy excerpt
; Wrong - do! and <- are macros that were never required
(import doeff [Ask])
(setv greet (do! (<- greeting (Ask "greeting")) greeting))

; Right - the first line brings the macros in
(require doeff-hy.macros [do! <-])
(import doeff [Ask])
(setv greet (do! (<- greeting (Ask "greeting")) greeting))
```

### "ImportError: cannot import name 'defhandler' from 'doeff_hy.handle'"

A macro cannot be brought in with `import`; macros are not attributes of their module. Use `require`
(doeff prints a `HyMacroUseWarning` with the corrected line before the traceback):

```hy excerpt
; Wrong
(import doeff-hy.handle [defhandler])

; Right
(require doeff-hy.handle [defhandler])
```

### "ModuleNotFoundError: No module named 'greet'" (and `greet.hy` is next to the script)

Python does not know `.hy` files until `import hy` has run. Add `import hy` above the import of the Hy
module (see "Importing a Hy module from Python" above):

```python excerpt
# Wrong - Python cannot find greet.hy
from greet import greet

# Right - import hy first
import hy
from greet import greet
```

### "TypeError: ... expected DoExpr or EffectBase, got: <class 'generator'>"

Or, when another program yields it: "TypeError: yield got a generator from 'my_program',
which has no @do". Make sure you're using `@do` decorator:

```python excerpt
from doeff import do
from doeff_core_effects import Tell

# Wrong - missing @do
def my_program():
    yield Tell("test")

# Right - has @do
@do
def my_program():
    yield Tell("test")
```

### "TypeError: yield got the @do function 'sub' itself, not a program"

Call the `@do` function to build the program, then yield the program:

```python excerpt
from doeff import do

@do
def sub():
    return 1

# Wrong - yields the function itself
@do
def wrong():
    value = yield sub  # Error!

# Right - yields the program the call builds
@do
def right():
    value = yield sub()  # OK
```

### "TypeError: yield got 42 (int), which is neither an effect nor a program"

Only yield `Effect` or `Program` instances:

```python excerpt
from doeff import do, Pure

# Wrong - yielding a plain value
@do
def wrong():
    value = yield 42  # Error!

# Right - wrap in Pure()
@do
def right():
    value = yield Pure(42)  # OK
    # Or just return it
    return 42
```

### "TypeError: state() got a program, not the initial state: state is a handler factory"

`state`, `reader` and `lazy_ask` are handler factories. Call the factory first (with the
initial values, if any), then apply the handler it returns to the program:

```python excerpt
from doeff import run
from doeff_core_effects.handlers import state

# Wrong - gives the program to the factory
run(state(my_program()))  # Error!

# Right - builds the handler, then applies it to the program
run(state()(my_program()))
run(state({"count": 0})(my_program()))
```

### "TypeError: writer() got no program: writer is a handler, not a handler factory"

`writer`, `try_handler`, `local_handler`, `listen_handler` and `slog_handler` are handlers
already. Apply them to the program without calling them first:

```python excerpt
from doeff import run
from doeff_core_effects.handlers import state, writer

# Wrong - calls writer as if it were a factory
run(state()(writer()(my_program())))  # Error!

# Right - applies writer to the program
run(state()(writer(my_program())))
```

### "TypeError: fetch_handler takes (effect, k): it is a handler, not a function that builds a program"

A function you write with the parameters `(effect, k)` handles effects; it does not take
the program. Wrap it with `handler()` to get the handler that applies it to a program:

```python excerpt
from doeff import Resume, do, handler, run

@do
def fetch_handler(effect: Fetch, k):
    return (yield Resume(k, f"got {effect.url}"))

# Wrong - calls the handler with the program
run(fetch_handler(my_program()))  # Error!

# Right - wraps the handler, then applies it to the program
run(handler(fetch_handler)(my_program()))
```

### "TypeError: unsupported operand type(s) for +: 'Get' and 'int'"

An effect, a program or the result of `Try` was used as a value. An effect and a program give
their result only where they are yielded, and `Try` gives `Ok(value)` or `Err(error)`, not the
value. The error ends with a line that names the variable and the fix, for example
"count is the effect Get('counter'), not its result. Yield the effect to get its result:
count = yield Get('counter')". The type is `'Call'` for a program and `'Ok'` / `'Err'` for the
result of `Try`:

```python excerpt
from doeff import Err, Ok, Try, do
from doeff_core_effects import Get

@do
def child():
    return 20

# Wrong - uses the effect, the program and the result of Try as values
@do
def wrong():
    count = Get("counter")  # count + 1: 'Get' and 'int'
    x = child()  # x + 1: 'Call' and 'int'
    r = yield Try(child())  # r + 1: 'Ok' and 'int'
    return count + x + r + 1  # Error!

# Right - yields the effect and the program, and takes the value out of the result
@do
def right():
    count = yield Get("counter")
    x = yield child()
    match (yield Try(child())):
        case Ok(value):
            return count + x + value
        case Err(error):
            raise error
```

### "TypeError: Gather takes the tasks that Spawn returns, got a program"

`Gather`, `Wait` and `Race` wait on tasks that already run. Spawn each program first, then pass
the task `Spawn` returns:

```python excerpt
from doeff import do
from doeff_core_effects.scheduler import Gather, Spawn

@do
def worker(label):
    return label

# Wrong - passes the programs themselves
@do
def wrong():
    return (yield Gather(worker("a"), worker("b")))  # Error!

# Right - spawns each program and gathers the tasks
@do
def right():
    a = yield Spawn(worker("a"))
    b = yield Spawn(worker("b"))
    return (yield Gather(a, b))  # ['a', 'b']
```

### "TypeError: Cancel takes the task that Spawn returns, got a program"

`Cancel` and `Discard` stop a task that already runs, so they take the task `Spawn` returns, never
the program. Spawn the program first, keep its task, and cancel that:

```python excerpt
from doeff import do
from doeff_core_effects.scheduler import Cancel, Spawn

@do
def worker():
    return "a"

# Wrong - passes the program itself
@do
def wrong():
    yield Cancel(worker())  # Error!

# Right - spawns the program and cancels the task Spawn returns
@do
def right():
    task = yield Spawn(worker())
    yield Cancel(task)
```

### "SchedulerDeadlockError: scheduler deadlock: parked waiters that no external completion can wake: ..."

The program waits on something nothing can finish any more. The message lists each waiter
(`root (wait) on promise 0`), and the doeff Traceback above it points at the `yield` where your
program waits. Here nothing completes the promise:

```python excerpt
from doeff import do
from doeff_core_effects.scheduler import CompletePromise, CreatePromise, Spawn, Wait

# Wrong - nothing ever completes the promise
@do
def wrong():
    p = yield CreatePromise()
    return (yield Wait(p.future))  # SchedulerDeadlockError

# Right - one task waits, and the other completes the promise
@do
def waiter(future):
    return (yield Wait(future))

@do
def right():
    p = yield CreatePromise()
    task = yield Spawn(waiter(p.future))
    yield CompletePromise(p, "done")
    return (yield Wait(task))  # 'done'
```

### A second run still sees the state of the first run

`run()` can run the same Program again; each run starts it fresh.
But `state()` keeps its store in the installer it returns, so a program wrapped once
carries its state — and the writer log — into the next run.
Wrap the program again for each run:

```python
from doeff import do, run
from doeff_core_effects import Tell
from doeff_core_effects.handlers import state, writer, writer_log

@do
def my_program():
    yield Tell("test")
    return (yield writer_log())

# Wrapped once, run twice: the second run sees the first run's log
wrapped = state()(writer(my_program()))
print(run(wrapped))  # ['test']
print(run(wrapped))  # ['test', 'test']

# Wrapped again for each run: each run starts with an empty state
prog = my_program()
print(run(state()(writer(prog))))  # ['test']
print(run(state()(writer(prog))))  # ['test']
```

## Next Steps

Now that you understand the basics, explore:

- **[Core Concepts](02-core-concepts.md)** - Algebraic effects, handlers, one-shot continuations, and the Rust VM
- **[Basic Effects](03-basic-effects.md)** - Reader, State, Writer effects
- **[Async Effects](04-async-effects.md)** - Parallel execution and futures
- **[Error Handling](05-error-handling.md)** - Result, Try for error handling

## Quick Reference

### Common Imports

```python excerpt
# Core
from doeff import (
    do,                    # Decorator for creating programs
    run,                   # Execute a program, returns raw value
    Pure,                  # Wrap a plain value as a Program
    WithObserve,           # Install an observer around a body program
    Cancel,                # Cancel a spawned task
)

# Effects
from doeff_core_effects import (
    Get, Put,              # State effects
    Ask, Local,            # Reader effects
    Tell,                  # Writer effect
    slog,                  # Structured logging helper
)

# Handlers
from doeff_core_effects.handlers import (
    reader,                # Reader handler (resolves Ask)
    state,                 # State handler (resolves Get/Put)
    writer,                # Writer handler (resolves Tell; install state() outside it)
    writer_log,            # Read the writer log: log = yield writer_log()
)

# Scheduler (for concurrency)
from doeff_core_effects.scheduler import (
    scheduled,             # Wrap program for concurrent execution
    Spawn, Wait,           # Spawn a task and wait for it
    Gather, Race,          # Gather/race multiple tasks
)
```

### Basic Program Template

```python
from doeff import do, run
from doeff_core_effects import Tell
from doeff_core_effects.handlers import state, writer

@do
def my_program():
    # Your effects here
    yield Tell("Hello, doeff!")
    return "result"

def main():
    # writer keeps its log in state, so install state() outside writer
    prog = state()(writer(my_program()))
    result = run(prog)
    print(result)  # result

if __name__ == "__main__":
    main()
```
