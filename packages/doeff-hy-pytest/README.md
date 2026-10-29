# doeff-hy-pytest

The pytest plugin for tests written with
[doeff-hy](https://github.com/proboscis/doeff/tree/main/packages/doeff-hy).

**This README is for** developers who run pytest on Hy code that uses doeff. After
reading it you know what the plugin does without any setting, and how to turn on its
optional time limits for tests.

## What it does

pytest loads the plugin by itself (entry point `pytest11`, name `doeff_hy`). It does
two things:

| Job | When it acts | Setting needed |
| --- | --- | --- |
| Fixes a Hy problem on Python 3.14 | At pytest start-up, before any conftest imports Hy | None |
| Time limits for Hy tests | During each test in a `.hy` file | Yes; off by default |

**The Python 3.14 fix.** Hy replaces `ast.unparse`. On Python 3.14, when pytest reads
fixture annotations as strings, that replacement recurses without end. The plugin
installs a corrected replacement (`doeff_hy._ast_unparse.install()`) as soon as pytest
starts, so it is in place even if a conftest or another plugin imports Hy first.

**Why this is a separate package.** pytest marks every module of a distribution that
declares a `pytest11` entry point for assertion rewriting. If doeff-hy itself declared
the entry point, pytest would try to rewrite doeff-hy's `.hy` files as Python and the
import would fail with a `SyntaxError`. This package contains no `.hy` files, so the
mark is harmless here.

The plugin does **not** collect `.hy` test files. The doeff-hy README shows the
`conftest.py` hook that collects them:
[Testing with deftest](https://github.com/proboscis/doeff/tree/main/packages/doeff-hy#testing-with-deftest).

## Installation

doeff-hy depends on this package, so it is already installed when doeff-hy is:

```bash
pip install "doeff[hy]"   # installs doeff-hy and doeff-hy-pytest
```

You do not list it yourself. The exception: if you install doeff-hy from a git
revision with uv, add a `[tool.uv.sources]` entry for doeff-hy-pytest from the same
revision.

To check that pytest loaded it, look for `doeff-hy-pytest` in the `plugins:` line of
`pytest` output, or in the plugins that `pytest -VV` lists:

```bash run
pytest -VV   # doeff-hy-pytest
```

## Time limits for Hy tests

Tests that run programs against in-memory or scripted handlers should be fast. The
limits tell you when one of them becomes slow. They apply only to tests defined in
`.hy` files (`deftest` and plain test functions in the same file); Python test files
are not measured. With none of the three limits set, the plugin measures nothing.

### The three limits

| Setting | What it limits | Unit | How it is measured |
| --- | --- | --- | --- |
| `doeff_test_call_budget_steps` | Running one test (pytest's `call` phase; fixture setup and teardown are not included) | doeff-vm steps (a positive integer, or `default` = 100,000) | Steps the VM executed. The count does not depend on machine load; CPU seconds are reported but not judged |
| `doeff_test_call_budget_by_marker` | Running one test that carries a given pytest mark | Wall-clock seconds, one line per mark: `mark=seconds` | For tests that do real I/O (child processes, sockets), whose cost does not show up as VM steps. A test that matches a line is judged by seconds even when a step limit is set; if several lines match, the longest limit applies |
| `doeff_test_collect_budget_seconds` | Importing one `.hy` test file | CPU seconds | Only when the file is imported through the `pytest_doeff_import_hy_module` hook, which the collector of [doeff-adr](https://github.com/proboscis/doeff/tree/main/packages/doeff-adr) calls. With the plain `conftest.py` collector from the doeff-hy README, nothing is measured |

A test that matches no `by_marker` line is judged by steps if a step limit is set, and
is not measured otherwise. A test that executes 0 steps (it only reads data and never
runs the VM) is within the limit.

Measured time excludes work that is not the test's own cost: compiling source to
bytecode when no cache exists, the first import of another module (a one-time cost of
a shared dependency), and garbage collection. Each is subtracted on the same clock
that is being judged.

### What happens when a test goes over

| Setting | Values |
| --- | --- |
| `doeff_test_budget_mode` | `report` (default): warn and list the slow tests at the end. `fail`: the slow test fails |
| `doeff_test_budget_registry` | Directories (relative to the pytest rootdir, one per line) that list tests allowed to go over. One file per test: `<first 12 hex chars of sha256(key)>.txt`; line 1 is the key (the pytest node id, or the file path relative to rootdir for the collection limit), the following lines give the reason. When a test goes over, the message names the file to add in the first directory |

In `fail` mode, a listed test that finishes in at most half of its limit also fails,
as a stale entry, and the message names the file to delete. Between half the limit and
the limit, the summary only reports that the entry can be removed.

### Example

```toml
[tool.pytest.ini_options]
doeff_test_call_budget_steps = "default"
doeff_test_call_budget_by_marker = ["real_world=10"]
doeff_test_collect_budget_seconds = 1.0
doeff_test_budget_mode = "fail"
doeff_test_budget_registry = ["tests/over-budget"]
```

With this configuration, each Hy test may run 100,000 VM steps; a test marked
`real_world` may take 10 seconds of wall-clock time instead; a test over its limit
fails unless `tests/over-budget` lists it.

### More settings

| Setting or API | What it does |
| --- | --- |
| `step_budget_multiplier` | While a step-limited test runs, the scheduler stops it once it reaches *limit × multiplier* steps (default 20), so a test that never finishes fails instead of hanging |
| `doeff_test_call_steps_counted` | `call` (default): count all steps of the test. `world-running`: for tests that run a simulated world recorded in `doeff_core_effects._world_phases`, count only the steps of the world's running phase |
| `doeff_hy_pytest.declare_step_budget(test, steps, reason)` | Gives one test its own step limit with a one-line reason, next to the test's definition. Over the declared value fails; at most half of it fails as stale |
| `doeff_hy_pytest.declare_process_tree_cpu(test, reason)` | For a test that falls under a per-mark seconds limit and spends its time in child processes' CPU (not waiting on sockets or HTTP): judge the same limit by the CPU seconds of the process tree instead of wall-clock time. Wall-clock time stays as a backstop |
| `--doeff-python-calls` | Command-line flag. Counts Python calls (`sys.monitoring` CALL events) during tests that declared a step limit, and prints one summary line. Reports only; never fails a test |

In Hy, put the declaration right after the `deftest`:

```hy excerpt
(import doeff_hy_pytest [declare-step-budget])

(deftest test-big-scenario ...)
(declare-step-budget test-big-scenario 400000 "the scenario runs 40 cards")
```

The two declarations are the package's whole Python API, next to the `pytest11` entry
point `doeff_hy`. The measuring code lives in private modules (`doeff_hy_pytest._budget`
and its neighbours) and may change in any release; to use the measurements elsewhere
(a daily report, a dashboard), read the summary lines the plugin prints.

The step limits need a doeff that reads step counts with the effect `ReadVmWorkCounts`
(answered by `doeff_core_effects._handlers.vm_probe_handler`). If it is missing, or the
doeff-vm build is too old to answer it, pytest stops at start-up with a `UsageError`
instead of silently falling back to seconds.

### Output

With `-v`, the summary prints one line per measured test: steps, handler calls, and
CPU seconds. Use these numbers to pick a step limit.

The report and failure messages are currently written in Japanese.

## How it fits with the other doeff packages

doeff is published as five core packages. `pip install doeff` installs the first three; the extra
`hy` (`pip install "doeff[hy]"`) adds doeff-hy and doeff-hy-pytest.

| Package | Role |
| --- | --- |
| [doeff](https://github.com/proboscis/doeff) | The Python API: `@do`, handler installers, `run`, the `doeff` CLI |
| [doeff-vm](https://github.com/proboscis/doeff/tree/main/packages/doeff-vm) | The Rust VM that executes programs |
| [doeff-core-effects](https://github.com/proboscis/doeff/tree/main/packages/doeff-core-effects) | Reference implementations of effects and their handlers |
| [doeff-hy](https://github.com/proboscis/doeff/tree/main/packages/doeff-hy) | Macros for writing doeff code in Hy, a Lisp that compiles to Python |
| **doeff-hy-pytest** | This package: the pytest plugin for tests written with doeff-hy |

Arrows point from a package to a package it depends on (the dotted one only with the extra `hy`):

```text
doeff ──► doeff-vm
  │
  └─────► doeff-core-effects ┄┄► doeff-hy ──► doeff-hy-pytest
```

doeff-hy also depends on doeff and doeff-core-effects, so the five packages are
released together with matching versions. doeff-hy-pytest declares no dependencies of
its own; it imports doeff-hy, which is always installed alongside it.

## License

MIT. See [LICENSE](https://github.com/proboscis/doeff/blob/main/LICENSE).
