# /// script
# requires-python = ">=3.11"
# ///
"""ab_run.sh の結果の dir から、群ごとの待ちの割合を off / on の窓ごとに表にする(読むだけ)。
使い方: uv run --script ab_table.py <dir> [<dir> ...]"""
import json, sys
from pathlib import Path

GROUPS = ["turn-host-zeus", "agora-records", "agora-records-twin", "agora-record", "agora-custody", "agora-screen",
          "agora-kanban", "agora-messaging", "deliver", "kanban-writes", "会話の claude の本体", "会話の Bash の子", "その他"]
cols, data = [], {}
for d in sys.argv[1:]:
    for f in sorted(Path(d).glob("p*-*.json")):
        j = json.loads(f.read_text())
        name = f"{Path(d).name}/{f.stem}"
        cols.append((name, j["loadavg_before"][0], j["pod_cpu_cores"]))
        data[name] = {r["group"]: r for r in j["rows"]}
print("| 群 | " + " | ".join(f"{n} (load {l}・Pod {c})" for n, l, c in cols) + " |")
print("|---|" + "---|" * len(cols))
for g in GROUPS:
    cells = []
    for n, _, _ in cols:
        r = data[n].get(g)
        cells.append("-" if r is None or r["wait_ratio"] is None else f"{r['wait_ratio']:.0%} ({r['cpu_s']}s)")
    print(f"| {g} | " + " | ".join(cells) + " |")
# off と on の和(service だけ): 待ち秒 ÷ (CPU + 待ち)
svc = [g for g in GROUPS if g not in ("会話の claude の本体", "会話の Bash の子", "その他")]
for kind in ("off", "-on", "onall"):
    run = wait = 0.0
    for n, _, _ in cols:
        if n.endswith(kind):
            for g in svc:
                r = data[n].get(g)
                if r:
                    run += r["cpu_s"]; wait += r["wait_s"]
    print(f"service の和({kind}): CPU {run:.1f}s・待ち {wait:.1f}s・割合 {wait / (run + wait):.1%}" if run + wait else kind)
