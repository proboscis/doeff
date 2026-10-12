# /// script
# requires-python = ">=3.11"
# ///
"""A/B の窓ごとに、service の群の和の待ちの割合・主な service・会話の群を出し、off と on の平均と ABBA の組ごとの差を出す(読むだけ)。
使い方: uv run --script ab_pooled.py <dir>"""
import json, statistics, sys
from pathlib import Path

SVC = ["turn-host-zeus", "agora-records", "agora-records-twin", "agora-record", "agora-custody", "agora-screen",
       "agora-kanban", "agora-messaging", "deliver", "kanban-writes"]
KEY = ["turn-host-zeus", "agora-records", "agora-records-twin", "agora-record"]
rows = []
for f in sorted(Path(sys.argv[1]).glob("p*-*.json")):
    j = json.loads(f.read_text()); g = {r["group"]: r for r in j["rows"]}
    def pooled(names):
        c = sum(g[n]["cpu_s"] for n in names if n in g); w = sum(g[n]["wait_s"] for n in names if n in g)
        return w / (c + w) if c + w else None
    rows.append({"w": f.stem, "tag": f.stem.split("-")[1], "load": float(j["loadavg_before"][0]), "pod": j["pod_cpu_cores"],
                 "svc": pooled(SVC), "key": pooled(KEY), **{n: pooled([n]) for n in KEY},
                 "claude": pooled(["会話の claude の本体"]), "bash": pooled(["会話の Bash の子"])})
fmt = lambda x: "-" if x is None else f"{x:.0%}"
print("| 窓 | 形 | load | Pod core | service の和 | 4 つの和 | turn-host | records | records-twin | record | claude の本体 | Bash の子 |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for r in rows:
    print(f"| {r['w']} | {r['tag']} | {r['load']:.0f} | {r['pod']} | {fmt(r['svc'])} | {fmt(r['key'])} | " +
          " | ".join(fmt(r[n]) for n in KEY) + f" | {fmt(r['claude'])} | {fmt(r['bash'])} |")
for tag in ("off", "on"):
    sel = [r for r in rows if r["tag"] == tag]
    print(f"{tag}: 窓 {len(sel)}・load 平均 {statistics.mean(r['load'] for r in sel):.0f}・service の和 平均 {statistics.mean(r['svc'] for r in sel):.1%}"
          f"・4 つの和 平均 {statistics.mean(r['key'] for r in sel):.1%}・claude {statistics.mean(r['claude'] for r in sel):.1%}・Bash {statistics.mean(r['bash'] for r in sel):.1%}")
# ABBA の 4 つ組ごとの差(on − off)
diffs = []
for i in range(0, len(rows) - 3, 4):
    blk = rows[i:i + 4]
    on = [r["svc"] for r in blk if r["tag"] == "on"]; off = [r["svc"] for r in blk if r["tag"] == "off"]
    if on and off:
        diffs.append(statistics.mean(on) - statistics.mean(off))
print("組ごとの差(on − off・service の和):", ", ".join(f"{d:+.1%}" for d in diffs))
