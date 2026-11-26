"""Measure coordinator startup memory (RSS & peak HWM) as state size (tasks/programs/WAL) grows."""


import argparse
import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcMem:
    rss: float
    hwm: float
    anon: float


@dataclass(frozen=True)
class SyntheticSizes:
    snapshot: int
    wal: int


def proc_mem_mib() -> ProcMem:
    rss = hwm = anon = 0.0
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) / 1024.0
            elif line.startswith("VmHWM:"):
                hwm = int(line.split()[1]) / 1024.0
            elif line.startswith("RssAnon:"):
                anon = int(line.split()[1]) / 1024.0
    return ProcMem(rss, hwm, anon)


def sealed_json(body: dict) -> bytes:
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    crc = format(zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF, "08x")
    return ('{"crc":"' + crc + '",' + text[1:]).encode("utf-8")


def build_synthetic_wal(
    wal_dir: str,
    n_tasks: int,
    n_programs: int,
    wal_lines: int,
    task_result_bytes: int = 16000,
    program_blob_bytes: int = 9200,
    wal_pad_to_checkpoint: bool = False,
) -> SyntheticSizes:
    now = 1795000000000
    kv: dict[str, object] = {
        "counter": {"nextTask": n_tasks + 100, "revision": 500000, "auditSeq": 1000, "aliveMs": now - 5000},
    }
    for i in range(15):
        kv[f"worker/node-{i}"] = {
            "name": f"node-{i}",
            "provides": ["linux", f"node-{i}"],
            "exclusive": [],
            "node": f"node-{i}",
            "versions": {"python": "3.14", "doeff": "0.1"},
            "lastSeenMs": now - 1000,
        }
    rt_envs = [
        {
            "repo": f"https://github.com/example/repo-{k}.git",
            "revision": f"{k:040x}",
            "python": "3.14",
            "packages": [f"pkg-{j}==1.0.{j}" for j in range(40)],
            "note": "実行環境の宣言 " + ("x" * 1800),
        }
        for k in range(max(1, min(120, n_tasks)))
    ]
    for i in range(n_programs):
        sha = f"{i:064x}"
        kv[f"program/{sha}"] = {
            "blob": "A" * program_blob_bytes,
            "versions": {"python": "3.14", "doeff": "0.1"},
            "putMs": now - 3600_000,
        }
    for i in range(n_tasks):
        tid = f"t{i + 1}"
        prog = f"{(i % max(1, n_programs)):064x}" if n_programs else None
        kv[f"task/{tid}"] = {
            "id": tid,
            "name": f"spot-task-{i % 50}",
            "program": prog,
            "revision": "0" * 40,
            "versions": {"python": "3.14", "doeff": "0.1"},
            "needs": ["linux"],
            "lease_ms": 60000,
            "lease_until_ms": now + 60000,
            "submitted_ms": now - 7200_000 + i,
            "phase": "finished",
            "worker": f"node-{i % 15}",
            "result": "R" * task_result_bytes,
            "detail": f"完了した task {i} の結果",
            "started_ms": now - 7100_000 + i,
            "finished_ms": now - 7000_000 + i,
            "detached": True,
            "key": f"detached-key-{i}",
            "boot": "boot-1",
            "retain_ms": 86400_000,
            "reported": True,
            "runtime_env": rt_envs[i % len(rt_envs)],
            "env_attempts": 1,
            "avoid": [],
            "failure_kind": "",
            "retryable": False,
            "environ": {f"ENV_{j}": f"val-{j}-{'v'*40}" for j in range(15)},
            "priority": 0,
            "limits": [],
            "shape": f"shape-{i % 20}",
            "peak_mib": 256,
        }
    seq = 1000
    snap_bytes = sealed_json({"seq": seq, "kv": kv})
    snap_path = os.path.join(wal_dir, "snapshot.json")
    with open(snap_path, "wb") as f:
        f.write(snap_bytes)

    wal_path = os.path.join(wal_dir, "wal.jsonl")
    wal_size = 0
    with open(wal_path, "wb") as f:
        for i in range(wal_lines):
            seq += 1
            delta = {
                "counter": {"nextTask": n_tasks + 100, "revision": 500000 + i, "auditSeq": 1000, "aliveMs": now - 4000 + i},
                f"board/k-{i % 200}": {"value": {"v": "b" * 300, "i": i}, "resourceVersion": i + 1},
            }
            if wal_pad_to_checkpoint and i == wal_lines - 1:
                # Pad WAL right near 32 MiB so resume-writes triggers checkpoint
                need = max(0, (32 * 1024 * 1024) - wal_size + 4096)
                if need > 0:
                    delta["board/pad"] = {"value": "P" * need, "resourceVersion": 1}
            line = sealed_json({"seq": seq, "delta": delta}) + b"\n"
            f.write(line)
            wal_size += len(line)
    return SyntheticSizes(len(snap_bytes), wal_size)


def measure_child(
    n_tasks: int,
    n_programs: int,
    wal_lines: int,
    mode: str,
    wal_pad: bool,
) -> None:
    tmp = tempfile.mkdtemp(prefix="coord-bench-")
    try:
        wal_dir = os.path.join(tmp, "wal")
        os.makedirs(wal_dir)
        sizes = build_synthetic_wal(
            wal_dir, n_tasks, n_programs, wal_lines, wal_pad_to_checkpoint=wal_pad
        )
        snap_sz, wal_sz = sizes.snapshot, sizes.wal
        gc.collect()
        # Run actual load in a clean child process so synthetic builder memory isn't counted!
        cmd = [
            sys.executable,
            __file__,
            "--worker-dir",
            tmp,
            "--mode",
            mode,
            "--label",
            f"tasks={n_tasks} progs={n_programs} snap={snap_sz/1048576:.1f}M wal={wal_sz/1048576:.1f}M({wal_lines}L) pad={wal_pad}",
        ]
        subprocess.run(cmd, check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_worker(tmp_dir: str, mode: str, label: str) -> None:
    import hy  # noqa: F401
    from doeff import run, with_handlers
    from doeff_core_effects import os_file_handler, scheduled, slog_handler, subprocess_handler
    from doeff_time import sync_time_handler

    from doeff_cluster._coordinator.core.api_policy import resume_after_downtime
    from doeff_cluster._coordinator.entry.main import state_on_start
    from doeff_cluster._coordinator.protocol.durable_kv import resume_writes, state_from_kv
    from doeff_cluster._coordinator.protocol.store import durable_load, durable_persist
    from doeff_cluster._coordinator.protocol.wal_files import wal_read_log_lines, wal_read_snapshot
    from doeff_cluster._coordinator.protocol.wal_format import read_snapshot, scan_log
    from doeff_cluster._foundation.wal_store import WalStore

    mem = proc_mem_mib()
    r0 = mem.rss
    h0 = mem.hwm
    a0 = mem.anon
    wal_dir = os.path.join(tmp_dir, "wal")
    store = WalStore(wal_dir, sync=False)
    handlers = [slog_handler, os_file_handler, subprocess_handler, sync_time_handler()]

    if mode == "e2e":
        t0 = time.monotonic()
        state = run(
            scheduled(
                with_handlers(
                    handlers,
                    state_on_start(os.path.join(tmp_dir, "state.json"), store),
                )
            )
        )
        dt = time.monotonic() - t0
        mem = proc_mem_mib()
        r1 = mem.rss
        h1 = mem.hwm
        a1 = mem.anon
        gc.collect()
        mem = proc_mem_mib()
        r2 = mem.rss
        h2 = mem.hwm
        a2 = mem.anon
        print(
            f"[e2e] {label} | time={dt:.2f}s | import={r0:.1f}M | end_rss={r1:.1f}M gc_rss={r2:.1f}M | PEAK_HWM={h2:.1f}M (anon={a2:.1f}M) | tasks={state.tasks.size()} kv={len(store.table())}",
            flush=True,
        )
    else:
        print(f"[steps] {label} | import RSS={r0:.1f}M HWM={h0:.1f}M", flush=True)
        t0 = time.monotonic()
        snap = run(scheduled(with_handlers(handlers, wal_read_snapshot(store))))
        mem = proc_mem_mib()
        r = mem.rss
        h = mem.hwm
        print(f"  1. wal_read_snapshot ({time.monotonic()-t0:.2f}s): RSS={r:.1f}M HWM={h:.1f}M", flush=True)

        t0 = time.monotonic()
        base = run(scheduled(with_handlers(handlers, read_snapshot(snap, store.snapshot))))
        mem = proc_mem_mib()
        r = mem.rss
        h = mem.hwm
        print(f"  2. read_snapshot     ({time.monotonic()-t0:.2f}s): RSS={r:.1f}M HWM={h:.1f}M", flush=True)

        table = {row.key: row.value for row in base.rows}
        base_seq = base.seq
        mem = proc_mem_mib()
        r = mem.rss
        h = mem.hwm
        print(f"  3. build table       : RSS={r:.1f}M HWM={h:.1f}M", flush=True)

        t0 = time.monotonic()
        lines = run(scheduled(with_handlers(handlers, wal_read_log_lines(store))))
        mem = proc_mem_mib()
        r = mem.rss
        h = mem.hwm
        print(f"  4. wal_read_log_lines({time.monotonic()-t0:.2f}s): RSS={r:.1f}M HWM={h:.1f}M", flush=True)

        t0 = time.monotonic()
        scan = run(scheduled(with_handlers(handlers, scan_log(lines, base_seq, table, store.log))))
        mem = proc_mem_mib()
        r = mem.rss
        h = mem.hwm
        print(f"  5. scan_log          ({time.monotonic()-t0:.2f}s): RSS={r:.1f}M HWM={h:.1f}M", flush=True)

        del snap, base, lines
        store.replace_table(table)
        store.seq = scan.seq
        gc.collect()
        mem = proc_mem_mib()
        r = mem.rss
        h = mem.hwm
        print(f"  5b. after del+gc     : RSS={r:.1f}M HWM={h:.1f}M", flush=True)

        now = int(time.time() * 1000)
        t0 = time.monotonic()
        stored = run(scheduled(with_handlers(handlers, state_from_kv(store.table(), now))))
        mem = proc_mem_mib()
        r = mem.rss
        h = mem.hwm
        print(f"  6. state_from_kv     ({time.monotonic()-t0:.2f}s): RSS={r:.1f}M HWM={h:.1f}M", flush=True)

        t0 = time.monotonic()
        state, _gap = resume_after_downtime(stored, now)
        mem = proc_mem_mib()
        r = mem.rss
        h = mem.hwm
        print(f"  7. resume_after_dt   ({time.monotonic()-t0:.2f}s): RSS={r:.1f}M HWM={h:.1f}M", flush=True)

        t0 = time.monotonic()
        writes = run(scheduled(with_handlers(handlers, resume_writes(store.table(), state))))
        mem = proc_mem_mib()
        r = mem.rss
        h = mem.hwm
        print(f"  8. resume_writes     ({time.monotonic()-t0:.2f}s, writes={len(writes)}): RSS={r:.1f}M HWM={h:.1f}M", flush=True)

        t0 = time.monotonic()
        run(scheduled(with_handlers(handlers, durable_persist(store, writes))))
        mem = proc_mem_mib()
        r = mem.rss
        h = mem.hwm
        print(f"  9. durable_persist   ({time.monotonic()-t0:.2f}s): RSS={r:.1f}M HWM={h:.1f}M", flush=True)

        gc.collect()
        mem = proc_mem_mib()
        r = mem.rss
        h = mem.hwm
        print(f"  9b. after gc         : RSS={r:.1f}M HWM={h:.1f}M", flush=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--worker-dir", default=None)
    p.add_argument("--mode", choices=("e2e", "steps"), default="e2e")
    p.add_argument("--label", default="")
    p.add_argument("--counts", default="250,1000,2000,4000")
    p.add_argument("--wal-lines", type=int, default=2600)
    p.add_argument("--wal-pad", action="store_true")
    args = p.parse_args()
    if args.worker_dir:
        run_worker(args.worker_dir, args.mode, args.label)
        return
    for n in (int(x) for x in args.counts.split(",") if x.strip()):
        measure_child(n, n, args.wal_lines, args.mode, args.wal_pad)


if __name__ == "__main__":
    main()
