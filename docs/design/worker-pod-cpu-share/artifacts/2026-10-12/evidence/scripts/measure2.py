# /// script
# requires-python = ">=3.11"
# ///
"""worker の Pod の中で、群ごとの CPU の時間と run queue の待ちを測る(読むだけ・card ki-bb74c5116086)。

/proc/<pid>/task/<tid>/schedstat(CPU の ns・run queue の待ちの ns・切り替えの回数)を、窓の始めと終わりに読み、thread ごとの差を群へ足す。
待ちの割合 = 待ち ÷ (CPU + 待ち)。窓の中で生まれた thread は 0 からの差、窓の中で死んだ thread は数えない(調べ ki-60f663a97a00 の節 3 と同じ)。

群の分け方(命令の行は読まない — 資格が混ざるため):
  - comm が claude の process = 会話の claude の本体
  - claude の子孫で nice が 10 以上 = 会話の Bash の子(包み tool-stream-shell.sh が起こした道具の命令)/ nice が 10 未満 = hook・MCP
  - それ以外は cwd の state/jobs/<job> の job の名(task_* は long-command の task)/ 外は「その他」
  - 測る process 自身は数えない

使い方: uv run --script measure_sched_wait.py <出力の JSON> [窓の秒 = 20]
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

POLICY_NAMES = {0: "SCHED_OTHER", 1: "SCHED_FIFO", 2: "SCHED_RR", 3: "SCHED_BATCH", 5: "SCHED_IDLE", 6: "SCHED_DEADLINE"}
SELF = os.getpid()


def _stat_fields(path: Path) -> list[str] | None:
    """/proc の stat の行を field 3 以降の列にする — nice・policy・生まれた刻を読むため(読めなければ None)。"""
    try:
        raw = path.read_text()
    except OSError:
        return None
    # comm は括弧の中に空白や括弧を含みうるので、最後の ')' の後ろで割る。field 3 以降。
    return raw[raw.rindex(")") + 2 :].split()


def _processes() -> dict[int, dict]:
    """Pod の中の process ごとに、群を決める材料(親・nice・policy・comm・cwd)を集める。"""
    procs: dict[int, dict] = {}
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        base = Path("/proc", entry.name)
        fields = _stat_fields(base / "stat")
        if fields is None:
            continue
        try:
            comm = (base / "comm").read_text().strip()
            cwd = os.readlink(base / "cwd")
        except OSError:
            continue
        procs[pid] = {"ppid": int(fields[1]), "nice": int(fields[16]), "policy": int(fields[38]), "comm": comm, "cwd": cwd}
    return procs


def _group(pid: int, procs: dict[int, dict]) -> str | None:
    """process を測りの群の名へ写す — 待ちの割合を service・会話の本体・Bash の子ごとに比べるため(測る自分は None)。"""
    if pid == SELF:
        return None
    me = procs[pid]
    if me["comm"] == "claude":
        return "会話の claude の本体"
    seen, up = set(), me["ppid"]
    while up in procs and up not in seen:
        seen.add(up)
        if up == SELF:
            return None
        if procs[up]["comm"] == "claude":
            return "会話の Bash の子" if me["nice"] >= 10 else "hook・MCP"
        up = procs[up]["ppid"]
    marker = "/state/jobs/"
    if marker in me["cwd"]:
        # 入れ替わった job の旧い process は cwd の末尾に " (deleted)" が付く — 同じ job の群に入れる。
        job = me["cwd"].split(marker, 1)[1].split("/", 1)[0].removesuffix(" (deleted)")
        return "long-command の task" if job.startswith("task_") else job
    return "その他"


def _thread_sample(pid: int, tid: str, group: str) -> tuple[tuple[int, str], tuple[str, int, int]] | None:
    """(群, comm・policy・nice) を群の名に足した版は _threads が付ける。"""
    """thread 1 つの schedstat を (tid, 生まれた刻) の鍵で読む — pid の使い回しで別の thread と差を取らないため。"""
    base = Path(f"/proc/{pid}/task/{tid}")
    fields = _stat_fields(base / "stat")
    try:
        run_ns, wait_ns, _ = (base / "schedstat").read_text().split()
    except (OSError, ValueError):
        return None
    if fields is None:
        return None
    return (int(tid), fields[19]), (group, int(run_ns), int(wait_ns))


def _label(pid: int, procs: dict[int, dict]) -> str | None:
    """群の名に comm・policy・nice を | で足す(後で群ごとと細目ごとの両方に足すため)。"""
    group = _group(pid, procs)
    if group is None:
        return None
    me = procs[pid]
    return f"{group}|{me['comm']}|{POLICY_NAMES.get(me['policy'], me['policy'])}|{me['nice']}"


def _tids(pid: int) -> list[str]:
    """process の thread の id の一覧(消えた process は空)。"""
    try:
        return os.listdir(f"/proc/{pid}/task")
    except OSError:
        return []


def _threads(procs: dict[int, dict]) -> dict[tuple[int, str], tuple[str, int, int]]:
    """測る窓の端の 1 回分: (tid, 生まれた刻) → (群, CPU の ns, 待ちの ns)。"""
    grouped = ((pid, _label(pid, procs)) for pid in procs)
    samples = (_thread_sample(pid, tid, group) for pid, group in grouped if group is not None for tid in _tids(pid))
    return dict(sample for sample in samples if sample is not None)


def _pod_cpu_usec() -> int | None:
    """Pod の cgroup が使った CPU の μs — 窓の間に Pod が使った core の数を出すため。"""
    try:
        for line in Path("/sys/fs/cgroup/cpu.stat").read_text().splitlines():
            key, value = line.split()
            if key == "usage_usec":
                return int(value)
    except OSError:
        return None
    return None


def main() -> None:
    """窓の始めと終わりの thread の schedstat の差を群へ足し、JSON と表を出す。"""
    out_path = Path(sys.argv[1])
    window = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    load_before = Path("/proc/loadavg").read_text().split()[:3]
    cpu0, t0 = _pod_cpu_usec(), time.monotonic()
    first = _threads(_processes())
    time.sleep(window)
    procs = _processes()
    last = _threads(procs)
    cpu1, t1 = _pod_cpu_usec(), time.monotonic()
    elapsed = t1 - t0

    raw = [(group, run_ns - first.get(key, (group, 0, 0))[1], wait_ns - first.get(key, (group, 0, 0))[2])
              for key, (group, run_ns, wait_ns) in last.items()]
    detail = {}
    for label, r, w in raw:
        g, comm, pol, nice = label.split("|")
        key = (g, pol, nice) if g not in ("その他", "hook・MCP") else (g + ":" + comm, pol, nice)
        a = detail.setdefault(key, [0, 0, 0]); a[0] += r; a[1] += w; a[2] += 1
    deltas = [(label.split("|")[0], r, w) for label, r, w in raw]
    groups = sorted({group for group, _, _ in deltas})
    sums = {g: (sum(r for gg, r, _ in deltas if gg == g), sum(w for gg, _, w in deltas if gg == g),
                sum(1 for gg, _, _ in deltas if gg == g)) for g in groups}
    bash_policies = Counter(POLICY_NAMES.get(procs[pid]["policy"], str(procs[pid]["policy"]))
                            for pid in procs if _group(pid, procs) == "会話の Bash の子")
    rows = [{"group": group,
             "cpu_s": round(run_ns / 1e9, 1),
             "wait_s": round(wait_ns / 1e9, 1),
             "wait_ratio": round(wait_ns / (run_ns + wait_ns), 3) if run_ns + wait_ns else None,
             "threads": threads}
            for group, (run_ns, wait_ns, threads) in sorted(sums.items(), key=lambda kv: -kv[1][0] - kv[1][1])]
    result = {
        "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "window_s": round(elapsed, 1),
        "loadavg_before": load_before,
        "loadavg_after": Path("/proc/loadavg").read_text().split()[:3],
        "pod_cpu_cores": round((cpu1 - cpu0) / 1e6 / elapsed, 2) if cpu0 is not None and cpu1 is not None else None,
        "bash_child_policies": dict(bash_policies),
        "rows": rows,
        "detail": [{"key": "/".join(k), "cpu_s": round(v[0] / 1e9, 2), "wait_s": round(v[1] / 1e9, 2), "threads": v[2]}
                   for k, v in sorted(detail.items(), key=lambda kv: -kv[1][0] - kv[1][1])],
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"{result['at_utc']} 窓 {result['window_s']} 秒・load {' '.join(load_before)} → {' '.join(result['loadavg_after'])}"
          f"・Pod の CPU {result['pod_cpu_cores']} core・Bash の子の policy {dict(bash_policies)}")
    print(f"{'群':<28} {'CPU 秒':>8} {'待ち秒':>8} {'待ちの割合':>8} {'thread':>7}")
    for row in rows:
        if row["cpu_s"] + row["wait_s"] < 0.5:
            continue
        ratio = f"{row['wait_ratio']:.0%}" if row["wait_ratio"] is not None else "-"
        print(f"{row['group']:<28} {row['cpu_s']:>8} {row['wait_s']:>8} {ratio:>8} {row['threads']:>7}")
    print("--- 細目(群/policy/nice・その他と hook は comm ごと)— 走れる本数の平均 = (CPU + 待ち) ÷ 窓")
    for d in result["detail"][:40]:
        if d["cpu_s"] + d["wait_s"] < 0.3:
            continue
        tot = d["cpu_s"] + d["wait_s"]
        print(f"{d['key']:<48} cpu {d['cpu_s']:>7} wait {d['wait_s']:>7} ratio {d['wait_s']/tot:>4.0%} runnable {tot/elapsed:>5.2f}")


if __name__ == "__main__":
    main()
