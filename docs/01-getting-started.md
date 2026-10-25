# Getting Started with doeff

doeff brings **algebraic effects** to Python. A program `yield`s *effects*: values that name an
operation, such as `Get("count")`, `Tell("done")` or an effect class of your own. A **handler**
receives each effect together with `k`, the *continuation*: the rest of the program from that
`yield` on. What the handler does with `k` decides what the effect means: it can resume `k` with an
answer, throw an error into it, pass the effect on, hold it while other tasks run, or drop it and
end the program with a value of its own. State, logging, errors, concurrency and I/O all work this
way. Continuations are **one-shot**: a handler resumes each `k` at most once. A VM written in
Rust runs the programs and the handlers.

[![A narrated video, under two minutes: a program yields an effect and a handler gets it with k, counter_program's effects answered by state() and writer, an effect of your own, the same program given two meanings by two handlers, and handlers that start with values](assets/videos/getting-started.jpg)](assets/videos/getting-started.mp4)

This page installs doeff, runs a program on the reference handlers of `doeff_core_effects`, then
writes an effect and a handler of your own. [What doeff Is](positioning/what-doeff-is.md) defines the mechanism in full.

## Installation

Install doeff using pip:

```bash
pip install doeff
```

The Rust VM ships as wheels for Linux, macOS and Windows, so pip needs no Rust toolchain there. On
other platforms pip builds the VM from source, which needs a Rust toolchain.

If you're using uv:

```bash
uv add doeff
```

## Requirements

- Python 3.11 or higher

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
2. **`yield` effects**: Each `yield` *performs* an algebraic effect — the program suspends, and the handler for that effect receives it with the continuation `k` and decides how the program goes on
3. **Effects** (algebraic effect operations):
   - `Put("counter", value)` - State effect: sets state (handled by the State handler)
   - `Get("counter")` - State effect: retrieves state
   - `Tell(message)` - Writer effect: appends to log (handled by the Writer handler)
4. **Handler composition**: Handlers are composed by wrapping the program: `state()(writer(prog))`
5. **`run`**: Executes the fully-handled program and returns the raw result value

Here the handlers answer each effect and resume `k` at once: `state` answers `Get` with the stored
value, and `writer` adds the message to its log. [Your First Effect and Handler](#your-first-effect-and-handler)
below writes a handler that decides otherwise.

Handler composition is done by applying each handler to the program.
Each handler is a `Program -> Program` installer — compose them by nesting calls rather than
passing handlers to `run()`. Some handlers like `writer` and `slog_handler` are pre-installed
(used directly), while others like `reader(env=...)` and `state(initial=...)` are factories
that return an installer.

`writer` keeps its log in state, so install `state()` outside it: `state()(writer(prog))`.
Without `state()` outside, the first `Tell` fails with
`UnhandledEffect: unhandled effect: Get (Get('__doeff_writer_log__'))`.
Read the collected log with `yield writer_log()` (from `doeff_core_effects.handlers`).

## Your First Effect and Handler

An effect of your own is a frozen dataclass under `EffectBase`, and a handler is a `def` of
`(effect, k)` marked with `@handler`; the annotation of `effect` picks the effects it receives. `shrink_all` reports its
progress with an effect and leaves the meaning of that report to a handler:

```python
from dataclasses import dataclass
from doeff import EffectBase, Resume, do, handler, run

@dataclass(frozen=True)
class Progress(EffectBase):              # your own effect: "this much of the work is done"
    done: int
    total: int

@do
def shrink_all(images):                  # yields Progress; knows nothing about what it means
    sizes = []
    for width, height in images:
        sizes.append((width // 2, height // 2))
        yield Progress(len(sizes), len(images))
    return sizes

shown = []

@handler
def show(effect: Progress, k):           # one meaning: note it, and resume k
    shown.append(f"{effect.done}/{effect.total}")
    return (yield Resume(k, None))

def stop_after(limit):                   # another meaning: stop the work after `limit` steps
    @handler
    def on_progress(effect: Progress, k):
        if effect.done >= limit:
            return f"stopped after {effect.done} of {effect.total}"   # drops k
        return (yield Resume(k, None))

    return on_progress

images = [(640, 480), (1024, 768), (800, 600)]
print(run(show(shrink_all(images))))  # [(320, 240), (512, 384), (400, 300)]
print(shown)  # ['1/3', '2/3', '3/3']
print(run(stop_after(2)(shrink_all(images))))  # stopped after 2 of 3
```

- `Resume(k, None)` answers the effect with `None` and runs the rest of `shrink_all`. When the
  rest finishes, `yield Resume(...)` gives the handler its result, and the handler returns it.
- A handler that returns without resuming `k` drops the rest: `shrink_all` stops at the `yield`,
  the third image is never shrunk, and the handler's return value is the result of the run.
- `@handler` makes the `def` a handler that you apply to a program, the same way as `writer` and
  `state()`. A handler body yields what it hands to doeff (`Resume`, `Pass`, an effect of its own)
  and returns what that gives back.

`shrink_all` has no cancel flag and no callback parameter: how it goes on is decided by the handler
installed around it. Other handlers can throw an error into `k` with `ResumeThrow(k, error)`, pass
the effect to the next handler out with `Pass(effect, k)`, or perform effects of their own while
they answer. [Core Concepts](02-core-concepts.md#handler-contract) lists them.

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

A program yields other programs, and their effects go to the same handlers, installed once around
the whole:

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

A program handles a failure of a sub-program with the `Try` effect. `yield Try(child)` runs
`child` and resumes with `Ok(value)` when it returns, or `Err(error)` when it raises, so the
program decides what a failure means with an ordinary `match`. `try_handler` is the handler that
answers `Try`; install it around the program like any other handler:

```python
from doeff import Err, Ok, Try, do, run
from doeff_core_effects.handlers import try_handler

@do
def parse_port(raw: str):
    return int(raw)

@do
def load_port(raw: str):
    match (yield Try(parse_port(raw))):
        case Ok(port):
            return f"port {port}"
        case Err(e):
            return f"bad port {raw!r}: {e}"

print(run(try_handler(load_port("8000"))))  # port 8000
print(run(try_handler(load_port("eighty"))))  # bad port 'eighty': invalid literal for int() with base 10: 'eighty'
```

An exception that no `Try` catches leaves `run()` as it was raised. See
[Error Handling](05-error-handling.md) for `Ok`/`Err`, the guarantees `Try` keeps, and more
patterns.

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

## Adding Types

The examples above use plain `yield` and leave out annotations: they run, but pyright and mypy see
`Any` for every value an effect or a program gives. To have them checked, use `yield from` and
annotate the parameters and the return type `Generator[effects, Any, result]` of each `@do`
function:

```python
from collections.abc import Generator
from typing import Any

from doeff import do, run
from doeff_core_effects import Ask, Get, Put
from doeff_core_effects.handlers import reader, state

@do
def visit(page: str) -> Generator[Get | Put, Any, int]:
    count: int = yield from Get(page)  # Get answers Any: annotate what you stored
    yield from Put(page, count + 1)
    return count + 1

@do
def visit_twice(page: str) -> Generator[Ask | Get | Put, Any, str]:
    site: str = yield from Ask("site")
    yield from visit(page)
    count = yield from visit(page)  # count: int, the result type of visit
    return f"{site}/{page}: {count} visits"

result = run(
    reader(env={"site": "example.com"})(state(initial={"home": 0})(visit_twice("home")))
)  # result: str
print(result)  # example.com/home: 2 visits
```

- `yield from` runs exactly like `yield`; only the type differs. On an effect it gives the effect's
  answer type, and on a program it gives the program's result type (`count` is an `int`). `yield`
  gives `Any`.
- In `Generator[Ask | Get | Put, Any, str]`, the first argument lists the effects the function may
  yield, the second stays `Any` (each effect gives its own type), and the third is the result.
  `yield from visit(page)` adds the effects of `visit` to the caller's, so leaving `Get | Put` out of
  the declaration of `visit_twice` is an error.
- `Ask` and `Get` answer `Any`, because the environment and the state can hold any value. Annotate
  the variable with the type you put there.
- `Tell(message)` builds a `WriterTellEffect`, so a function that tells lists `WriterTellEffect`
  (from `doeff_core_effects`) in its declaration.
- `run` keeps the result type through the handlers: `result` is a `str`.
- mypy needs the annotations. It does not infer the types of a function without them: to mypy, an
  unannotated `@do` function takes and returns `Any`, and its body is not checked. pyright infers
  part of the types without annotations, and reads the same annotations.
- mypy fixes the type of a variable at its first assignment. After `prog = visit_twice("home")`,
  `prog = state()(prog)` is an error in mypy: a program under a handler is a `Program[str, Any]`,
  not the `Expand` the `@do` function returned. Wrap the program in one expression, as above.

[Static Typing](23-static-typing.md) covers the scheduler's effects, handlers and what stays `Any`.

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

### "TypeError: fetch_handler takes (effect, k): it is a handler body, not a function that builds a program"

A `def` with the parameters `(effect, k)` handles effects; under `@do` alone it does not take
the program. Mark the `def` with `@handler` in place of `@do` to get the handler that applies it
to a program:

```python excerpt
from doeff import Resume, do, handler, run

# Wrong - @do makes a handler body, not a handler
@do
def fetch_handler(effect: Fetch, k):
    return (yield Resume(k, f"got {effect.url}"))

run(fetch_handler(my_program()))  # Error!

# Right - @handler makes the def a handler, which applies to the program
@handler
def fetch_handler(effect: Fetch, k):
    return (yield Resume(k, f"got {effect.url}"))

run(fetch_handler(my_program()))
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

- **[What doeff Is](positioning/what-doeff-is.md)** - The mechanism, what it covers (state, exceptions, concurrency, observation, budgets, I/O) and where it stops
- **[Core Concepts](02-core-concepts.md)** - Algebraic effects, handlers, one-shot continuations, and the Rust VM
- **[Basic Effects](03-basic-effects.md)** - Reader, State, Writer effects
- **[Async Effects](04-async-effects.md)** - Parallel execution and futures
- **[Error Handling](05-error-handling.md)** - Result, Try for error handling
- **[Static Typing](23-static-typing.md)** - What pyright and mypy check in effects, programs and handlers

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
