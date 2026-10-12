# /// script
# requires-python = ">=3.11"
# ///
"""monitor.py の JSONL を表にし、service の待ちを類ごとの走れる本数と Pod の外の core で読む(読むだけ)。
使い方: uv run --script mon_table.py <JSONL> [--rows]"""
import json, statistics, sys
rows = [json.loads(l) for l in open(sys.argv[1])]
g = lambda r, k, f: (r.get(k) or {}).get(f) or 0.0
if "--rows" in sys.argv:
    print("| 刻 UTC | load | 機体 | Pod | 外 | service 待ち | turn-host | records | record | claude 本数 | nice10 本数・core | idle 本数 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['at'][11:19]} | {r['load']:.0f} | {r['machine_cores']:.1f} | {r['pod_cores']:.1f} | {r['outside_cores']:.1f} | "
              f"{g(r,'service','wait'):.0%} | {g(r,'svc:turn-host-zeus','wait'):.0%} | {g(r,'svc:agora-records','wait'):.0%} | "
              f"{g(r,'svc:agora-record','wait'):.0%} | {g(r,'claude','runnable'):.1f} | {g(r,'nice10','runnable'):.1f}・{g(r,'nice10','cores'):.1f} | "
              f"{g(r,'idle','runnable'):.1f} |")
print(f"窓 {len(rows)}・{rows[0]['at']} 〜 {rows[-1]['at']}")
# 窓を組に分けて service の待ちの平均を見る
def bucket(name, pred):
    sel = [r for r in rows if pred(r)]
    if sel:
        print(f"{name}: 窓 {len(sel)}・service 待ち 平均 {statistics.mean(g(r,'service','wait') for r in sel):.0%}"
              f"・中央 {statistics.median(g(r,'service','wait') for r in sel):.0%}・load 平均 {statistics.mean(r['load'] for r in sel):.0f}"
              f"・外 平均 {statistics.mean(r['outside_cores'] for r in sel):.1f}・nice10 本数 平均 {statistics.mean(g(r,'nice10','runnable') for r in sel):.1f}"
              f"・claude 本数 平均 {statistics.mean(g(r,'claude','runnable') for r in sel):.1f}")
bucket("nice10 ≥ 8 本", lambda r: g(r, "nice10", "runnable") >= 8)
bucket("nice10 < 8 本・外 ≥ 8 core", lambda r: g(r, "nice10", "runnable") < 8 and r["outside_cores"] >= 8)
bucket("nice10 < 8 本・外 < 8 core", lambda r: g(r, "nice10", "runnable") < 8 and r["outside_cores"] < 8)
bucket("load ≥ 100", lambda r: r["load"] >= 100)
# 単回帰の代わりに相関
def corr(xs, ys):
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** .5; sy = sum((y - my) ** 2 for y in ys) ** .5
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else float("nan")
y = [g(r, "service", "wait") for r in rows]
for name, xs in [("nice10 本数", [g(r, "nice10", "runnable") for r in rows]), ("外の core", [r["outside_cores"] for r in rows]),
                 ("claude 本数", [g(r, "claude", "runnable") for r in rows]), ("idle 本数", [g(r, "idle", "runnable") for r in rows]),
                 ("other0 本数", [g(r, "other0", "runnable") for r in rows]), ("service 本数", [g(r, "service", "runnable") for r in rows]),
                 ("load", [r["load"] for r in rows])]:
    print(f"相関(service 待ち, {name}) = {corr(xs, y):+.2f}")
# Pod の中の重みの和(走れる本数 × 重み: nice 0 = 1024・nice 10 = 110・SCHED_IDLE = 3)のうち service の分
def share(r):
    w0 = 1024 * (g(r, "service", "runnable") + g(r, "claude", "runnable") + g(r, "other0", "runnable") + g(r, "hook", "runnable"))
    tot = w0 + 110 * g(r, "nice10", "runnable") + 3 * g(r, "idle", "runnable")
    return 1024 * g(r, "service", "runnable") / tot if tot else 0.0
print(f"相関(service 待ち, Pod の重みのうち service の分) = {corr([share(r) for r in rows], y):+.2f}")
print("service の分の分布:", " ".join(f"{share(r):.2f}" for r in rows[:60]))
