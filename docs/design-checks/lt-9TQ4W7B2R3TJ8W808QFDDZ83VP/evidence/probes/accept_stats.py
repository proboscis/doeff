"""使い方: accept_stats.py <log> — accept.sh の log から、normal と always-current の CPU の中央値・組ごとの差の中央値・照らしの中の GC を出す。"""
import statistics
import sys

rows = [dict(kv.split("=", 1) for kv in line.split() if "=" in kv) for line in open(sys.argv[1])]
normal = [r for r in rows if r["mode"] == "normal"]
always = [r for r in rows if r["mode"] == "always-current"]
cpu = lambda rs: [float(r["cpu"]) for r in rs]  # noqa: E731
print("normal median", round(statistics.median(cpu(normal)), 3), "always median", round(statistics.median(cpu(always)), 3))
pairs = [a - b for a, b in zip(cpu(normal), cpu(always))]
print("pairwise diff median", round(statistics.median(pairs), 3), "mean", round(statistics.mean(pairs), 3), "stdev", round(statistics.stdev(pairs), 3), "n", len(pairs))
if "gc_in_check" in normal[0]:
    print("normal check_net median", round(statistics.median(float(r["check_net"]) for r in normal), 3),
          "gc_in_check median", round(statistics.median(float(r["gc_in_check"]) for r in normal), 3),
          "gc_n", [int(r["gc_n"]) for r in normal])
