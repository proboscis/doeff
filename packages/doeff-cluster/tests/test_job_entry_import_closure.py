"""子 process の入口(worker/entry/job_entry)と cluster の土台(shared/entry/cluster_foundation)が起動で読む module の閉包の検め。

どの job の子もこの 2 つを読むので、閉包の太りは job の本数だけ memory に掛かる。前は次の 3 つを全部の子が起動で読んでいた:
- task の道だけが使う結果の届け(worker/entry/result_delivery)と、その先の本物の HTTP の handler と httpx。
- httpx の頭が読む端末の client(httpx._main)と、その先の click・rich・pygments(同じ venv に開発の道具の依存で入る)。
- 測りの報告の fake(metrics-memory)が通す coordinator の型(MetricsPayload・checked-metrics)の閉包。
→ 届けは run-task の中で読み、端末の client は入口の main が断り(_decline-httpx-cli)、fake は metrics_memory.hy に分けた。
読んではいけない module が戻るか、module の数が上限を越えると赤になる(上限は今の数に余裕を足した値 — 越えたら何が増えたかを見る)。

閉包は今の process の sys.modules に依らないよう、新しい interpreter で読む。
"""

import json
import subprocess
import sys

PROBE = """\
import json, sys
import hy  # Hy の module を import する前に hy の import hook を有効にする
before = set(sys.modules)
import importlib
for name in sys.argv[2:]:
    importlib.import_module(name)
if sys.argv[1] == "entry-then-httpx":
    from doeff_cluster.worker.entry.job_entry import _decline_httpx_cli
    _decline_httpx_cli()
    import httpx
    assert callable(httpx.main) and httpx.AsyncClient
loaded = sorted(name for name in set(sys.modules) - before if sys.modules[name] is not None)
print(json.dumps(loaded))
"""

ENTRY = "doeff_cluster.worker.entry.job_entry"
FOUNDATION = "doeff_cluster.shared.entry.cluster_foundation"

# どの子も読まない module(名か、その名の下の module)。
NEVER = ("rich", "pygments", "click", "httpx._main", "doeff_cluster.coordinator")
# 入口だけ(service の子が Program を解く前)が読まない module — task の道の届けと、その先の HTTP の handler。
ENTRY_NEVER = (*NEVER, "httpx", "doeff_cluster.worker.entry.result_delivery", "doeff_core_effects._http_handlers_impl")

# 上限 = 直した時の数(入口 125・入口と土台 255)に 2 割ほどの余裕を足した値。
ENTRY_MODULE_LIMIT = 150
ENTRY_AND_FOUNDATION_MODULE_LIMIT = 300


def _loaded(mode: str, *modules: str) -> list[str]:
    """新しい interpreter で modules を読み、増えた module の名を返す(閉包の検めの材料にするため)。"""
    done = subprocess.run([sys.executable, "-c", PROBE, mode, *modules], capture_output=True, text=True, timeout=600, check=False)
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def _under(loaded: list[str], prefixes: tuple[str, ...]) -> list[str]:
    """loaded のうち、prefixes のどれかの名か、その下の module の名を返す。"""
    return [name for name in loaded if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)]


def test_entry_reads_no_delivery_http_cli_or_coordinator() -> None:
    loaded = _loaded("import", ENTRY)
    assert _under(loaded, ENTRY_NEVER) == []
    assert len(loaded) <= ENTRY_MODULE_LIMIT, f"入口の閉包が {len(loaded)} 個(上限 {ENTRY_MODULE_LIMIT})"


def test_foundation_reads_no_http_cli_or_coordinator() -> None:
    loaded = _loaded("import", ENTRY, FOUNDATION)
    assert _under(loaded, NEVER) == []
    assert len(loaded) <= ENTRY_AND_FOUNDATION_MODULE_LIMIT, (
        f"入口と土台の閉包が {len(loaded)} 個(上限 {ENTRY_AND_FOUNDATION_MODULE_LIMIT})"
    )


def test_httpx_after_entry_declines_cli_but_keeps_client() -> None:
    # 子の Program の module が httpx を読む時(service の HTTP の handler)も、端末の client と click・rich・pygments は読まない。
    loaded = _loaded("entry-then-httpx", ENTRY)
    assert "httpx" in loaded
    assert _under(loaded, NEVER) == []
