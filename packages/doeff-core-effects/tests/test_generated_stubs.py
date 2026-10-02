"""doeff-core-effects の Hy の module の型の宣言のうち、doeff_hy.static_stub が作った .pyi の失敗ケース(#2842)。

使い手の repo の main が import する、この package の .hy のうち .pyi の無かった 13 module は、使い手がその名を使う行を
足すと、strict の型の門が書き手に直せない Unknown の赤で止まった(使い手の多い順: os_process・aiohttp_http_server・
scheduler_channel・scripted_freeze)。.pyi は道具が .hy から作る(手で書いた .pyi の module はここでは照らさない)。

- 使い手が import する名を 1 つずつ束ねた検の module に、「型が分からない」の赤が出ない(.pyi を外すと赤になる)。
- 一致の検 = 作り直した物 == commit された物(.hy を変えたら `python -m doeff_hy.static_stub --write <.hy>` で作り直す)。
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from doeff_hy.static_stub import stale_in

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
    "scheduler_channel": ("scheduler-channel-handler",),
    "scripted_freeze": ("scripted-freeze-handler",),
    "thread_pool_compute": ("thread-pool-compute-handler",),
}


def _probe() -> str:
    """名を 1 つずつ別の名に束ねる検の module(束ねた名の型が Unknown なら、その行に strict の赤が出る)。"""
    imports = [f"(import doeff_core_effects.{module} [{' '.join(names)}])" for module, names in USED.items()]
    bindings = [
        f"(setv used-{index} {name})" for index, name in enumerate(name for names in USED.values() for name in names)
    ]
    return "\n".join([*imports, "", *bindings, ""])


def _errors(root: Path) -> list[str]:
    """検の module を、使い手の型の門と同じ strict で検めた赤の文。"""
    done = subprocess.run(
        [sys.executable, "-m", "doeff_hy.static_check", "--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")],
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
