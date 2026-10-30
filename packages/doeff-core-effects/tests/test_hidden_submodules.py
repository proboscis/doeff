"""doeff_core_effects の子を `_<名>` へ移した後も、どちらの package を先に import しても読み込みの順が保たれる事の検。

決まり: ADR-DOE-API-001 の R5 — 名は入口(`__init__`)から出し、子は名に `_` を付けて隠す(card ki-c09b3b527fad)。
doeff は読み込みの時にこの package の `_effects` と `_scheduler` を読み、この package の入口は先に doeff を読む。入口が
遅延で出す名(`__getattr__` の `_LAZY_EXPORTS`)の module は、`import doeff` で読まない(起動を重くしない)。元の名の所の
移し替えの module(1 行目に `# moved-submodule-shim:`)は、`_<名>` の module そのものを返す。

失敗ケース: doeff を先に読む・doeff_core_effects を先に読む・古い path を先に読むの、どれかで import が落ちる・
入口の名が doeff の名と別の物になる・`import doeff` が遅延の名の module を読む・古い path が別の写しの module を返すと赤。
"""

import subprocess
import sys

import pytest

#: 子の古い path と新しい path(card ki-c09b3b527fad で移した中核の 10 個 — storage は下の module も・card ki-32623e1651c6 で
#: 移した計算・VM・乱数・測りの 23 個・card ki-375e0a35b7a0 で移した file・memo・cache の 11 個・card ki-8d33df2c82df で移した
#: process の 19 個・card ki-4fbed45b5863 と ki-d4ba562a1367 で移した http・ws の 6 個・card ki-4e56dc4a0ecd で移した sql の
#: 7 個)。
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

#: doeff と入口の両方が出す名(doeff が読み込みの時に `_effects` と `_scheduler` から結ぶ)。
SHARED_NAMES = ("Ask", "Get", "Put", "Tell", "Spawn", "Gather", "Wait", "Promise", "Task", "MissingEnvKeyError")

#: 入口が遅延で出す名と、その module(`import doeff` では読まない)。
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

#: `import doeff` が読まない module(入口の遅延の名だけが読む)。
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
