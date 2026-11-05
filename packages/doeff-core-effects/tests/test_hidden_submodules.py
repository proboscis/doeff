"""Check that after moving doeff_core_effects children to `_<name>`, load order is preserved whichever package is imported first.

Rule: R5 of ADR-DOE-API-001 - names are exported from the entry point (`__init__`); children are hidden by prefixing `_` to the name.
At load time, doeff reads this package's `_effects` and `_scheduler`, and this package's entry point reads doeff first. Modules for names the entry point
exports lazily (`_LAZY_EXPORTS` in `__getattr__`) are not loaded by `import doeff` (so as not to slow startup). The relocation
shim modules at the old names (with `# moved-submodule-shim:` on line 1) return the `_<name>` module itself.

Failure cases: red if importing doeff first, importing doeff_core_effects first, or importing an old path first makes the import fail,
an entry-point name becomes a different object from doeff's name, `import doeff` loads a lazy name's module, or an old path returns a separate copy of the module.
"""

import subprocess
import sys

import pytest

#: Old and new paths of the children (10 core ones moved - storage also moved the modules below it;
#: 23 for compute, VM, random numbers, and measurement; 11 for the next moved files, memo, and cache; 19 for process;
#: 6 for http and ws; 7 for sql).
MOVED = {
    "doeff_core_effects.effects": "doeff_core_effects._effects",
    "doeff_core_effects.handlers": "doeff_core_effects._handlers",
    "doeff_core_effects.scheduler": "doeff_core_effects._scheduler",
    "doeff_core_effects.scheduler_channel": "doeff_core_effects._scheduler_channel",
    "doeff_core_effects.scheduler_step_tally": "doeff_core_effects._scheduler_step_tally",
    "doeff_core_effects.outcomes": "doeff_core_effects._outcomes",
    "doeff_core_effects.world_phases": "doeff_core_effects._world_phases",
    "doeff_core_effects.extra_hints": "doeff_core_effects._extra_hints",
    "doeff_core_effects.unhandled_hints": "doeff_core_effects._unhandled_hints",
    "doeff_core_effects.storage": "doeff_core_effects._storage",
    "doeff_core_effects.storage.sqlite": "doeff_core_effects._storage.sqlite",
    "doeff_core_effects.storage.memory": "doeff_core_effects._storage.memory",
    "doeff_core_effects.python_bytecode": "doeff_core_effects._python_bytecode",
    "doeff_core_effects.vm_probe_effects": "doeff_core_effects._vm_probe_effects",
    "doeff_core_effects.step_tally_effects": "doeff_core_effects._step_tally_effects",
    "doeff_core_effects.stack_dump_effects": "doeff_core_effects._stack_dump_effects",
    "doeff_core_effects.memory_stack_dump": "doeff_core_effects._memory_stack_dump",
    "doeff_core_effects.faulthandler_stack_dump": "doeff_core_effects._faulthandler_stack_dump",
    "doeff_core_effects.gc_freeze": "doeff_core_effects._gc_freeze",
    "doeff_core_effects.scripted_freeze": "doeff_core_effects._scripted_freeze",
    "doeff_core_effects.heap_effects": "doeff_core_effects._heap_effects",
    "doeff_core_effects.apart_call": "doeff_core_effects._apart_call",
    "doeff_core_effects.offloaded_call": "doeff_core_effects._offloaded_call",
    "doeff_core_effects.inline_compute": "doeff_core_effects._inline_compute",
    "doeff_core_effects.compute_effects": "doeff_core_effects._compute_effects",
    "doeff_core_effects.thread_pool_compute": "doeff_core_effects._thread_pool_compute",
    "doeff_core_effects.random_effects": "doeff_core_effects._random_effects",
    "doeff_core_effects.os_random": "doeff_core_effects._os_random",
    "doeff_core_effects.seeded_random": "doeff_core_effects._seeded_random",
    "doeff_core_effects.channel_effects": "doeff_core_effects._channel_effects",
    "doeff_core_effects.latest_effects": "doeff_core_effects._latest_effects",
    "doeff_core_effects.memory_latest": "doeff_core_effects._memory_latest",
    "doeff_core_effects.meter_effects": "doeff_core_effects._meter_effects",
    "doeff_core_effects.memory_meter": "doeff_core_effects._memory_meter",
    "doeff_core_effects.meter_prometheus": "doeff_core_effects._meter_prometheus",
    "doeff_core_effects.file_effects": "doeff_core_effects._file_effects",
    "doeff_core_effects.os_file": "doeff_core_effects._os_file",
    "doeff_core_effects.memory_file": "doeff_core_effects._memory_file",
    "doeff_core_effects.rooted_file": "doeff_core_effects._rooted_file",
    "doeff_core_effects.memo_effects": "doeff_core_effects._memo_effects",
    "doeff_core_effects.memo_handlers": "doeff_core_effects._memo_handlers",
    "doeff_core_effects.memo_policy": "doeff_core_effects._memo_policy",
    "doeff_core_effects.cache_effects": "doeff_core_effects._cache_effects",
    "doeff_core_effects.cache_handlers": "doeff_core_effects._cache_handlers",
    "doeff_core_effects.cache_policy": "doeff_core_effects._cache_policy",
    "doeff_core_effects.cache": "doeff_core_effects._cache",
    "doeff_core_effects.process_effects": "doeff_core_effects._process_effects",
    "doeff_core_effects.os_process": "doeff_core_effects._os_process",
    "doeff_core_effects.scripted_process": "doeff_core_effects._scripted_process",
    "doeff_core_effects.process_exit": "doeff_core_effects._process_exit",
    "doeff_core_effects.process_latest": "doeff_core_effects._process_latest",
    "doeff_core_effects.process_meter": "doeff_core_effects._process_meter",
    "doeff_core_effects.process_stat": "doeff_core_effects._process_stat",
    "doeff_core_effects.pidfd_exit": "doeff_core_effects._pidfd_exit",
    "doeff_core_effects.kqueue_exit": "doeff_core_effects._kqueue_exit",
    "doeff_core_effects.darwin_proc": "doeff_core_effects._darwin_proc",
    "doeff_core_effects.darwin_volume": "doeff_core_effects._darwin_volume",
    "doeff_core_effects.proc_table_effects": "doeff_core_effects._proc_table_effects",
    "doeff_core_effects.os_proc_table": "doeff_core_effects._os_proc_table",
    "doeff_core_effects.scripted_proc_table": "doeff_core_effects._scripted_proc_table",
    "doeff_core_effects.warm_effects": "doeff_core_effects._warm_effects",
    "doeff_core_effects.os_warm_process": "doeff_core_effects._os_warm_process",
    "doeff_core_effects.scripted_warm_process": "doeff_core_effects._scripted_warm_process",
    "doeff_core_effects.stop_signal_effects": "doeff_core_effects._stop_signal_effects",
    "doeff_core_effects.stop_signal_handlers": "doeff_core_effects._stop_signal_handlers",
    "doeff_core_effects.http_effects": "doeff_core_effects._http_effects",
    "doeff_core_effects.http_handlers": "doeff_core_effects._http_handlers",
    "doeff_core_effects.http_server_effects": "doeff_core_effects._http_server_effects",
    "doeff_core_effects.ws_client_effects": "doeff_core_effects._ws_client_effects",
    "doeff_core_effects.aiohttp_ws_client": "doeff_core_effects._aiohttp_ws_client",
    "doeff_core_effects.scripted_ws_client": "doeff_core_effects._scripted_ws_client",
    "doeff_core_effects.sql_effects": "doeff_core_effects._sql_effects",
    "doeff_core_effects.postgres_sql": "doeff_core_effects._postgres_sql",
    "doeff_core_effects.pooled_postgres_sql": "doeff_core_effects._pooled_postgres_sql",
    "doeff_core_effects.sqlite_sql": "doeff_core_effects._sqlite_sql",
    "doeff_core_effects.sqlite_file_sql": "doeff_core_effects._sqlite_file_sql",
    "doeff_core_effects.clickhouse_http_sql": "doeff_core_effects._clickhouse_http_sql",
    "doeff_core_effects.sql_transaction": "doeff_core_effects._sql_transaction",
}

#: Names exported by both doeff and the entry point (doeff binds them from `_effects` and `_scheduler` at load time).
SHARED_NAMES = ("Ask", "Get", "Put", "Tell", "Spawn", "Gather", "Wait", "Promise", "Task", "MissingEnvKeyError")

#: Names the entry point exports lazily, and their modules (not loaded by `import doeff`).
LAZY_NAMES = {
    "StepBudget": "doeff_core_effects._scheduler",
    "DurableStorage": "doeff_core_effects._storage",
    "SQLiteStorage": "doeff_core_effects._storage.sqlite",
    "AbsentAsToken": "doeff_core_effects._outcomes",
    "RandomBytes": "doeff_core_effects._random_effects",
    "Compute": "doeff_core_effects._compute_effects",
    "MeterSnapshot": "doeff_core_effects._meter_effects",
    "ArmStackDump": "doeff_core_effects._stack_dump_effects",
    "run_apart": "doeff_core_effects._apart_call",
    "thread_pool_compute_handler": "doeff_core_effects._thread_pool_compute",
    "ReadText": "doeff_core_effects._file_effects",
    "os_file_handler": "doeff_core_effects._os_file",
    "MemoGet": "doeff_core_effects._memo_effects",
    "memo_rewriters": "doeff_core_effects._memo_handlers",
    "CachePolicy": "doeff_core_effects._cache_policy",
    "RunProcess": "doeff_core_effects._process_effects",
    "subprocess_handler": "doeff_core_effects._os_process",
    "ProcessScript": "doeff_core_effects._scripted_process",
    "ForkFromWarm": "doeff_core_effects._warm_effects",
    "WarmScript": "doeff_core_effects._scripted_warm_process",
    "StopBell": "doeff_core_effects._stop_signal_effects",
    "HttpFailed": "doeff_core_effects._http_effects",
    "WsConnect": "doeff_core_effects._ws_client_effects",
    "aiohttp_ws_link_handler": "doeff_core_effects._aiohttp_ws_client",
    "scripted_ws_link_handler": "doeff_core_effects._scripted_ws_client",
    "SqlQuery": "doeff_core_effects._sql_effects",
    "PostgresConnections": "doeff_core_effects._postgres_sql",
    "sqlite_sql_handler": "doeff_core_effects._sqlite_sql",
    "SqliteFiles": "doeff_core_effects._sqlite_file_sql",
    "ClickHouseDatabase": "doeff_core_effects._clickhouse_http_sql",
}

#: Modules that `import doeff` does not load (only the entry point's lazy names load them).
NOT_LOADED_BY_DOEFF = (
    "doeff_core_effects._storage",
    "doeff_core_effects._scheduler_channel",
    "doeff_core_effects._scheduler_step_tally",
    "doeff_core_effects._world_phases",
    "doeff_core_effects._python_bytecode",
    "doeff_core_effects._step_tally_effects",
    "doeff_core_effects._stack_dump_effects",
    "doeff_core_effects._memory_stack_dump",
    "doeff_core_effects._faulthandler_stack_dump",
    "doeff_core_effects._gc_freeze",
    "doeff_core_effects._scripted_freeze",
    "doeff_core_effects._heap_effects",
    "doeff_core_effects._apart_call",
    "doeff_core_effects._offloaded_call",
    "doeff_core_effects._inline_compute",
    "doeff_core_effects._compute_effects",
    "doeff_core_effects._thread_pool_compute",
    "doeff_core_effects._random_effects",
    "doeff_core_effects._os_random",
    "doeff_core_effects._seeded_random",
    "doeff_core_effects._channel_effects",
    "doeff_core_effects._latest_effects",
    "doeff_core_effects._memory_latest",
    "doeff_core_effects._meter_effects",
    "doeff_core_effects._memory_meter",
    "doeff_core_effects._meter_prometheus",
    "doeff_core_effects._file_effects",
    "doeff_core_effects._os_file",
    "doeff_core_effects._memory_file",
    "doeff_core_effects._rooted_file",
    "doeff_core_effects._memo_effects",
    "doeff_core_effects._memo_handlers",
    "doeff_core_effects._memo_policy",
    "doeff_core_effects._cache_effects",
    "doeff_core_effects._cache_handlers",
    "doeff_core_effects._cache_policy",
    "doeff_core_effects._cache",
    "doeff_core_effects._process_effects",
    "doeff_core_effects._os_process",
    "doeff_core_effects._scripted_process",
    "doeff_core_effects._process_exit",
    "doeff_core_effects._process_latest",
    "doeff_core_effects._process_meter",
    "doeff_core_effects._process_stat",
    "doeff_core_effects._pidfd_exit",
    "doeff_core_effects._kqueue_exit",
    "doeff_core_effects._darwin_proc",
    "doeff_core_effects._darwin_volume",
    "doeff_core_effects._proc_table_effects",
    "doeff_core_effects._os_proc_table",
    "doeff_core_effects._scripted_proc_table",
    "doeff_core_effects._warm_effects",
    "doeff_core_effects._os_warm_process",
    "doeff_core_effects._scripted_warm_process",
    "doeff_core_effects._stop_signal_effects",
    "doeff_core_effects._stop_signal_handlers",
    "doeff_core_effects._http_effects",
    "doeff_core_effects._http_handlers",
    "doeff_core_effects._http_server_effects",
    "doeff_core_effects._ws_client_effects",
    "doeff_core_effects._aiohttp_ws_client",
    "doeff_core_effects._scripted_ws_client",
    "doeff_core_effects._sql_effects",
    "doeff_core_effects._postgres_sql",
    "doeff_core_effects._pooled_postgres_sql",
    "doeff_core_effects._sqlite_sql",
    "doeff_core_effects._sqlite_file_sql",
    "doeff_core_effects._clickhouse_http_sql",
    "doeff_core_effects._sql_transaction",
)


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=120, check=False)


@pytest.mark.parametrize(
    "first",
    ["doeff", "doeff_core_effects", "doeff_core_effects.effects", "doeff_core_effects.scheduler", "doeff_core_effects._handlers"],
)
def test_the_names_are_the_same_whichever_module_is_imported_first(first: str) -> None:
    code = (
        f"import importlib\nimportlib.import_module({first!r})\n"
        "import doeff, doeff_core_effects\n"
        f"for name in {SHARED_NAMES!r}:\n"
        "    assert getattr(doeff, name) is getattr(doeff_core_effects, name), name\n"
        f"for name, module in {LAZY_NAMES!r}.items():\n"
        "    assert getattr(doeff_core_effects, name) is getattr(importlib.import_module(module), name), name\n"
        "    assert name in dir(doeff_core_effects), name\n"
    )
    done = _run(code)
    assert done.returncode == 0, done.stderr


def test_import_doeff_does_not_load_the_modules_of_the_lazy_names() -> None:
    code = (
        "import sys, doeff\n"
        f"loaded = sorted(m for m in {NOT_LOADED_BY_DOEFF!r} if m in sys.modules)\n"
        "assert not loaded, loaded\n"
    )
    done = _run(code)
    assert done.returncode == 0, done.stderr


def test_the_old_path_gives_the_moved_module_itself() -> None:
    code = (
        "import importlib\n"
        f"for old, new in {MOVED!r}.items():\n"
        "    assert importlib.import_module(old) is importlib.import_module(new), old\n"
    )
    done = _run(code)
    assert done.returncode == 0, done.stderr
