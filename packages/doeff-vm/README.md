# doeff-vm

The Rust virtual machine that runs [doeff](https://github.com/proboscis/doeff) programs.

**This README is for** Python developers who use doeff and want to know what doeff-vm
does. After reading it you can tell whether you need to import it directly, install
it, and use its low-level program nodes.

## What it does

doeff-vm is a Python extension module written in Rust (PyO3). It executes doeff
programs:

- it keeps the stack of installed handlers,
- it sends each performed effect to the innermost handler that accepts it,
- it resumes the program through one-shot continuations (a continuation `k` can be
  resumed at most once).

The scheduler behind doeff's concurrency effects (`Spawn`, `Gather`, …) also runs on
it.

Most users never import `doeff_vm` directly. `doeff.run(...)` drives this VM, and
`pip install doeff` installs it for you.

How one effect is handled:

```text
run(program)
   │
   ▼
doeff-vm steps through the program
   │  the program performs an effect
   ▼
innermost handler in scope ──Resume(k, value)──► the program continues with `value`
   │  Pass(effect, k)
   ▼
next handler outward … ──(no handler left)──► UnhandledEffect is raised
```

## Installation

```bash
pip install doeff        # recommended: installs doeff-vm together with the Python API
pip install doeff-vm     # the VM alone
```

Requires Python 3.11 or newer. If pip finds no prebuilt wheel for your platform, it
builds from the source distribution, which needs a Rust toolchain (`cargo`).

## Minimal example

`@do` functions and handler installers build a tree of program nodes, and the VM
evaluates that tree. You can also build the tree by hand with the node classes that
`doeff_vm` exports:

```python
from dataclasses import dataclass

import doeff_vm
from doeff import do, run


@dataclass(frozen=True)
class Double(doeff_vm.EffectBase):
    n: int


@do
def double_handler(effect, k):
    if isinstance(effect, Double):
        return (yield doeff_vm.Resume(k, effect.n * 2))
    yield doeff_vm.Pass(effect, k)  # not ours: hand it to the next handler outward


# The tree that `@do` code and handler installers normally build for you.
program = doeff_vm.WithHandler(double_handler, doeff_vm.Perform(Double(21)))
print(run(program))  # 42
```

## What it exports

| Name | What it is |
| --- | --- |
| `EffectBase` | Base class of every effect |
| `Pure`, `Perform`, `WithHandler`, `Apply`, `Expand`, `Call` | Program nodes: a value, performing an effect, installing a handler, calls |
| `Resume`, `Transfer`, `ResumeThrow`, `TransferThrow`, `Pass` | What a handler does with a continuation `k`: resume it, throw an exception into it, or pass the effect outward |
| `K` | A one-shot continuation |
| `Ok`, `Err` | Result values (for example the answer of `Try`) |
| `UnhandledEffect` | Raised when no handler in scope accepts an effect |
| `PyVM` | The VM object itself (`PyVM().run(program)`); `doeff.run` is the supported entry point |

Type stubs ship with the package (`py.typed`).

## How it fits with the other doeff packages

doeff is published as five core packages. `pip install doeff` installs the first three; the extra
`hy` (`pip install "doeff[hy]"`) adds doeff-hy and doeff-hy-pytest.

| Package | Role |
| --- | --- |
| [doeff](https://github.com/proboscis/doeff) | The Python API: `@do`, handler installers, `run`, the `doeff` CLI |
| **doeff-vm** | This package: the Rust VM that executes programs |
| [doeff-core-effects](https://github.com/proboscis/doeff/tree/main/packages/doeff-core-effects) | Reference implementations of effects and their handlers (state, reader, writer, concurrency, HTTP, SQL, files, processes, …) |
| [doeff-hy](https://github.com/proboscis/doeff/tree/main/packages/doeff-hy) | Macros for writing doeff code in [Hy](https://hylang.org/), a Lisp that compiles to Python |
| [doeff-hy-pytest](https://github.com/proboscis/doeff/tree/main/packages/doeff-hy-pytest) | pytest plugin for tests written with doeff-hy |

Arrows point from a package to a package it depends on (the dotted one only with the extra `hy`):

```text
doeff ──► doeff-vm
  │
  └─────► doeff-core-effects ┄┄► doeff-hy ──► doeff-hy-pytest
```

doeff-hy also depends on doeff and doeff-core-effects, so the five packages are
released together with matching versions.

The VM core is the Rust crate
[`doeff-vm-core`](https://github.com/proboscis/doeff/tree/main/packages/doeff-vm-core).
It is compiled into this extension and is not published separately.

## Developing

Build and test from a clone of the repository:

```bash
git clone https://github.com/proboscis/doeff
cd doeff
make sync                         # builds the extension into the uv environment
uv run pytest packages/doeff-vm/tests
```

`make sync` rebuilds the extension whenever the Rust sources change. Do not run
`maturin develop` directly; the build goes through `tools/doeff_cargo_backend.py`.
Dispatch micro-benchmarks live in `packages/doeff-vm-core/benches/` (see the
[root README](https://github.com/proboscis/doeff#performance-benchmarks)).

## License

MIT. See [LICENSE](https://github.com/proboscis/doeff/blob/main/LICENSE).
