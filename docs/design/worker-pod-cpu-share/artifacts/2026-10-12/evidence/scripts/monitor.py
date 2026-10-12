# /// script
# requires-python = ">=3.11"
# ///
"""worker の Pod の中の CPU の取り合いを、scheduling の類ごとに窓を続けて測る(読むだけ・card ki-757b489f8a14)。

類(thread の policy・nice・親で分ける):
  service       state/jobs/<job> の job(task_* でない)の nice 0 の thread
  claude        comm が claude の process(会話の本体)
  hook          claude の子孫で SCHED_OTHER・nice < 10(hook・MCP)
  other0        その他の SCHED_OTHER・nice < 10(worker の本体・shim ほか)
  nice10        SCHED_OTHER / BATCH で nice ≥ 10(実行環境の準備・long-command の task ほか)
  idle          SCHED_IDLE(会話の Bash の子ほか)
窓ごとに 類の CPU core・走れる本数の平均(= (CPU + 待ち) ÷ 窓)・待ちの割合、service は主な job ごとの待ちの割合、
機体の CPU(/proc/stat — 機体の全体)と Pod の CPU(cgroup)、Pod の外 = 機体 − Pod を 1 行の JSON で出す。

使い方: uv run --script monitor.py <出力の JSONL> <窓の秒> <窓の数>
"""
import json, os, sys, time
from pathlib import Path

SELF = os.getpid()
KEY_JOBS = ("turn-host-zeus", "agora-records", "agora-records-twin", "agora-record", "agora-custody", "agora-screen")
HZ = os.sysconf("SC_CLK_TCK")


def fields(path):
    try:
        raw = open(path).read()
    except OSError:
        return None
    return raw[raw.rindex(")") + 2:].split()


def processes():
    procs = {}
    for e in os.scandir("/proc"):
        if not e.name.isdigit():
            continue
        f = fields(f"/proc/{e.name}/stat")
        if f is None:
            continue
        try:
            comm = open(f"/proc/{e.name}/comm").read().strip()
            cwd = os.readlink(f"/proc/{e.name}/cwd")
        except OSError:
            continue
        procs[int(e.name)] = (int(f[1]), int(f[16]), int(f[38]), comm, cwd)
    return procs


def klass(pid, procs):
    ppid, nice, policy, comm, cwd = procs[pid]
    if pid == SELF:
        return None
    if policy == 5:
        return "idle", None
    if comm == "claude":
        return "claude", None
    if nice >= 10:
        return "nice10", None
    if "/state/jobs/" in cwd:
        job = cwd.split("/state/jobs/", 1)[1].split("/", 1)[0].removesuffix(" (deleted)")
        if not job.startswith("task_"):
            return "service", job
    seen, up = set(), ppid
    while up in procs and up not in seen:
        seen.add(up)
        if up == SELF:
            return None
        if procs[up][3] == "claude":
            return "hook", None
        up = procs[up][0]
    return "other0", None


def sample():
    procs = processes()
    out = {}
    for pid in procs:
        k = klass(pid, procs)
        if k is None:
            continue
        try:
            tids = os.listdir(f"/proc/{pid}/task")
        except OSError:
            continue
        for t in tids:
            f = fields(f"/proc/{pid}/task/{t}/stat")
            try:
                run, wait, _ = open(f"/proc/{pid}/task/{t}/schedstat").read().split()
            except (OSError, ValueError):
                continue
            if f is None:
                continue
            out[(int(t), f[19])] = (k, int(run), int(wait))
    return out


def host_busy():
    v = [int(x) for x in open("/proc/stat").readline().split()[1:]]
    return sum(v) - v[3] - v[4], sum(v)  # idle・iowait を除く


def pod_usage():
    for line in open("/sys/fs/cgroup/cpu.stat"):
        k, val = line.split()
        if k == "usage_usec":
            return int(val)


def main():
    out, win, n = Path(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3])
    for _ in range(n):
        a, h0, p0, t0 = sample(), host_busy(), pod_usage(), time.monotonic()
        load = open("/proc/loadavg").read().split()[0]
        time.sleep(win)
        b, h1, p1, t1 = sample(), host_busy(), pod_usage(), time.monotonic()
        dt = t1 - t0
        acc = {}
        for key, (k, run, wait) in b.items():
            r0, w0 = (a[key][1], a[key][2]) if key in a else (0, 0)
            name = k[0] if k[0] != "service" else "service"
            for slot in ((name,) if k[0] != "service" else ("service", "svc:" + k[1])):
                x = acc.setdefault(slot, [0, 0]); x[0] += run - r0; x[1] += wait - w0
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "load": float(load), "window_s": round(dt, 1),
               "machine_cores": round((h1[0] - h0[0]) / HZ / dt, 2), "pod_cores": round((p1 - p0) / 1e6 / dt, 2)}
        row["outside_cores"] = round(row["machine_cores"] - row["pod_cores"], 2)
        for slot, (run, wait) in acc.items():
            if slot.startswith("svc:") and slot[4:] not in KEY_JOBS:
                continue
            row[slot] = {"cores": round(run / 1e9 / dt, 2), "runnable": round((run + wait) / 1e9 / dt, 2),
                         "wait": round(wait / (run + wait), 3) if run + wait else None}
        with out.open("a") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
