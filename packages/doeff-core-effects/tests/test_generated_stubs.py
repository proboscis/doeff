"""doeff-core-effects の Hy の module の型の宣言のうち、doeff_hy._static_stub が作った .pyi の失敗ケース(#2842)。

使い手の repo の main が import する、この package の .hy のうち .pyi の無かった 13 module は、使い手がその名を使う行を
足すと、strict の型の門が書き手に直せない Unknown の赤で止まった(使い手の多い順: os_process・aiohttp_http_server・
scheduler_channel・scripted_freeze)。.pyi は道具が .hy から作る(手で書いた .pyi の module はここでは照らさない)。
残っていた公開の 4 module(offloaded_call・pooled_postgres_sql・python_bytecode・sql_transaction)は、pyright と mypy の両方で
import の行が「解けない」になった(公開の前の型の報告の抜け 8・2026-10-17)。

- 使い手が import する名を 1 つずつ束ねた検の module に、「型が分からない」の赤が出ない(.pyi を外すと赤になる)。
- 公開の .hy(名が _ で始まらない物)は全部、道具が作った .pyi を隣に持つ(内向きの _memo_handlers_impl などは除く)。
- 一致の検 = 作り直した物 == commit された物(.hy を変えたら `python -m doeff_hy._static_stub --write <.hy>` で作り直す)。
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from doeff_hy._static_stub import generated, stale_in

# stub(.pyi)は型の引数の構文(PEP 695 — `class SqlTransaction[T]`)で書き、3.11 の ast はそれを読めない。stub と module の
# 突き合わせは Python の版に依らないので、3.12 から見る。
pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="stub の型の引数の構文は Python 3.12 から")

SOURCE = Path(__file__).resolve().parents[1]

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

# 使い手の repo の main が import する名(module ごと・2026-10-02 の数え)。
USED = {
    "aiohttp_http_server": ("aiohttp-http-server",),
    "clickhouse_http_sql": ("ClickHouseDatabase", "clickhouse-http-sql-handler", "clickhouse-schema-statements"),
    "gc_freeze": ("gc-freeze-handler",),
    "heap_effects": ("CollectAndFreeze",),
    "inline_compute": ("inline-compute-handler",),
    "os_process": ("subprocess-handler",),
    "os_random": ("os-random-handler",),
    "postgres_sql": ("PostgresConnections", "PostgresDatabase", "postgres-sql-handler"),
    "process_latest": ("process-latest-handler",),
    "process_meter": ("process-meter-handler",),
    "_scheduler_channel": ("scheduler-channel-handler",),
    "scripted_freeze": ("scripted-freeze-handler",),
    "thread_pool_compute": ("thread-pool-compute-handler",),
}

# .pyi の無かった公開の 4 module の名のうち、この package の module と検が import する名(2026-10-17 の数え)。
USED_IN_PACKAGE = {
    "offloaded_call": ("ThreadPerCall", "offloaded", "run-detached", "keep-nothing"),
    "pooled_postgres_sql": ("pooled-postgres-sql-handler",),
    "python_bytecode": (
        "checked-hash-pyc",
        "compiled-pyc",
        "kept-pyc",
        "pyc-path",
        "compile-python-sources",
        "prepare-compile-path",
    ),
    "sql_transaction": ("TransactionFlush", "run-in-transaction", "run-in-batched-transaction", "stray-batch"),
}

PROBED = {**USED, **USED_IN_PACKAGE}


def _probe() -> str:
    """名を 1 つずつ別の名に束ねる検の module(束ねた名の型が Unknown なら、その行に strict の赤が出る)。"""
    imports = [f"(import doeff_core_effects.{module} [{' '.join(names)}])" for module, names in PROBED.items()]
    bindings = [
        f"(setv used-{index} {name})" for index, name in enumerate(name for names in PROBED.values() for name in names)
    ]
    return "\n".join([*imports, "", *bindings, ""])


def _errors(root: Path) -> list[str]:
    """検の module を、使い手の型の門と同じ strict で検めた赤の文。"""
    done = subprocess.run(
        [sys.executable, "-m", "doeff_hy._static_check", "--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")],
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    diagnostics = json.loads(done.stdout) if done.stdout.strip() else []
    return [f"{d['line']}: {d['rule']}: {d['message']}" for d in diagnostics if d["severity"] == "error"]


@needs_pyright
def test_names_the_users_import_are_not_unknown(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(_probe(), encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if "hy-compile" in e], errors
    assert not [e for e in errors if re.search(r"is unknown|could not be resolved|unknown import symbol", e)], errors


def test_generated_stubs_are_what_the_tool_makes() -> None:
    assert [f"{s.source.relative_to(SOURCE)}: {s.reason}" for s in stale_in(SOURCE)] == []


def test_every_public_hy_module_has_a_generated_stub() -> None:
    # .pyi が無いと、使い手の型の check で import の行が解けない。道具が作った .pyi なら上の一致の検も照らす。
    package = SOURCE / "doeff_core_effects"
    stubless = [
        source.name
        for source in sorted(package.glob("*.hy"))
        if not source.name.startswith("_") and not generated(source.with_suffix(".pyi"))
    ]
    assert stubless == []
