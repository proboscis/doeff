# Findings

## VM handler-chain infrastructure already exists

- `packages/doeff-vm-core/src/vm.rs:140-225` — `collect_rich_execution_context()` walks the fiber chain from `current_segment` upward, collects prompt-boundary handler names, and emits a `["handler", "chain", [names]]` `Value::List` entry. Verified by reading the file.
- `packages/doeff-vm-core/src/value.rs:29` — `Callable::name() -> Option<String>` trait method. Implemented by both Python callables (via `PythonCallable`) and Rust handlers. No rework needed in PR E to obtain names.
- `packages/doeff-vm-core/src/vm/dispatch.rs:73` — `self.last_error_context = Some(self.collect_rich_execution_context())` runs *before* `VMError::unhandled_effect` is raised, so the chain is captured at the correct execution point. Verified by reading dispatch.rs.
- `packages/doeff-vm-core/src/vm/step.rs:600` — second raise site (`NoMatchingHandler`) also calls `collect_rich_execution_context()`. Both paths feed PR E's new extractor.

## `lazy_ask` throws on miss, does not `Pass`

- `packages/doeff-core-effects/doeff_core_effects/handlers.py:357-360` (pre-PR-D) — `if effect.key in effective_env: ... else: return (yield ResumeThrow(k, KeyError(...)))`. No `Pass(effect, k)` fallback. This blocks the `env_var_ask → lazy_ask` chain-of-responsibility pattern until PR D changes the default.
- Confirmed by grep: only one `Pass(effect, k)` in the `lazy_ask` body at line 440, which is the `isinstance`-mismatch fallthrough for non-Ask/non-Local effects.

## `doeff_core_effects.CacheGet` / `CachePut` do not exist in the installed package

- `uv run python -c "from doeff_core_effects import CacheGetEffect"` → `ImportError`. Verified.
- `uv run python -c "from doeff_core_effects.cache import CacheGet"` → `ImportError`. Verified.
- `rg "CacheGet|CachePut" /Users/s22625/repos/proboscis-ema/.venv/lib/...` → zero matches in the installed package.
- Consequence: `claude_batch_handler.py`'s restart-recovery path was broken *before* this session (imports fail at module load). PR A3's migration (committed in earlier session) kept a `CachePut = _cache_put_noop` stub to allow the module to import. No new issue; recorded for future cleanup.

## `@do` on a non-generator function still returns a Program when called

- Verified by:
```
uv run python -c "
from doeff import do, Program, run
counter = {'n': 0}
@do
def foo():
    counter['n'] += 1
    return counter['n']
print('foo():', foo(), 'type:', type(foo()).__name__)
# Output: foo(): Expand(...) type: Expand
print('run:', run(foo()))
# Output: run: 1
print('counter:', counter)
# Output: counter: {'n': 1}
"
```
- This is why `env_var_ask`'s auto-call branch works — `{myapp.factory}` where `factory` is `@do`-decorated and takes no args produces a fresh `Expand` Program each call.

## `Gather` takes `*args`, not a list

- `packages/doeff-core-effects/doeff_core_effects/scheduler.py:83-86` — `class Gather: def __init__(self, *tasks)`. Verified by grep.
- Test had to use `yield Gather(*tasks)` after spawning, not `yield Gather(tasks)`.
- Attempting `[(yield Spawn(...)) for _ in range(8)]` fails with `SyntaxError: 'yield' inside list comprehension` — must be a regular for loop.

## `import_symbol` returns the module when the dotted path is a pure module path

- `doeff/cli/run_services.py:42-68` — progressive module-path walk tries longest prefix first. For `"doeff.runners.local"`, the whole string imports as a module, `attr_path == []`, so the module itself is returned.
- To get the function, the default runner path had to be `"doeff.runners.local.run_local"` (4 segments); the walk tries that module, fails, falls back to `"doeff.runners.local"` + `getattr(m, "run_local")`. Verified by running `doeff run --hy '...' --runner doeff.runners.local.run_local` → returns 42.

## pytest `rootdir` collection creates distinct module objects for the test module

- Verified by:
```
# Inside test, _counter["n"] = 0 after reset
# env_var_ask's {module.path} import ran the Program, incremented _counter
# Test saw _counter["n"] == 0 (same dict key, different dict object)
```
- Fix: `import tests.effects.test_env_var_ask as _mod; _mod._counter["n"]` — accessed via the fully-qualified path that `import_symbol` uses, forcing the same dict.

## `env_var_ask` semaphore race: multiple tasks evaluate before caching kicks in

- `packages/doeff-core-effects/doeff_core_effects/handlers.py` (new env_var_ask) — the pattern:
```
if effect.key not in sems:
    sems[effect.key] = yield CreateSemaphore(1)
yield AcquireSemaphore(sems[effect.key])
```
- With 8 concurrent Spawn'd asks, `_counter` reaches ~3 before the cache takes over. Not 8, because the scheduler serializes each task at the yield; not 1, because the `yield CreateSemaphore` yields control before `sems[key]` is populated.
- `lazy_ask` has the identical pattern at `handlers.py:374-378` — same race, same acceptance in production.

## Hy `.hy` tests in this project need special conftest handling

- `packages/proboscis-ema-core/src/proboscis_ema_core/app_effects/conftest.py:170` — calls `importlib.import_module(mod_name)` during collection. `.hy` files not discovered as pytest modules through the normal path.
- Alternative: existing `.hy` tests use `deftest` macro which expands to pytest functions via the `doeff-hy-macros` machinery.
- Workaround chosen for the k3s-runner helpers: write plain Python tests that `import hy; from nakagawa.runners.k3s import _strip_runner_arg` — Hy file loaded via the `doeff_hy` import hook.

## DeprecationWarning is silent by default in Python

- `uv run pytest tests/test_withhandler_shim_deprecation.py` — passes without any warnings printed. Must use `warnings.catch_warnings(record=True)` + `warnings.simplefilter("always", DeprecationWarning)` to observe in tests.
- With `-W error::DeprecationWarning` flag to pytest, 30+ existing tests fail because proboscis-ema-migrated code still uses `WithHandler(h, body)` internally — this is expected and tracked under backlog #5.

## `--hy` source auto-prelude

- `doeff/cli/hy_runner.py` prepends:
```
(require doeff-hy.macros [do! <-])
(require doeff-hy.handle [defhandler handle])
```
- Users can write inline `(handle p (Ask [k] (resume ...)))` or `(defhandler name ...)` with no boilerplate. Verified by `doeff run --hy '(import doeff [Ask]) (handle (do! (<- v (Ask "x")) v) (Ask [k] (resume 99)))'` → `99`.
