# /// script
# requires-python = ">=3.11"
# ///
"""worker の Pod の service の thread に EEVDF の custom slice を掛ける・外す(card ki-757b489f8a14 の生の A/B の実験)。

  uv run --script sched_ab.py on <slice の μs>   service の job(cwd が state/jobs/<job> で task_* でない)の全 thread に slice を掛ける
  uv run --script sched_ab.py on-all <slice の μs> on に加えて、Pod の中の nice 0 の SCHED_OTHER の thread(会話の claude の本体・hook ほか)すべてに掛ける
  uv run --script sched_ab.py off               Pod の中で既定と違う slice を持つ SCHED_OTHER / SCHED_BATCH の全 thread を既定(runtime 0)へ戻す
  uv run --script sched_ab.py show              既定と違う slice を持つ thread を群ごとに数える

変えるのは sched_attr.sched_runtime(slice)だけ。policy・nice・reset-on-fork の印は読んだ値のまま渡す(nice は下げない・上げない)。
戻せる事: runtime 0 で既定の slice に戻る(特権は要らない — 2026-10-12 に long-command の task で確かめた)。
"""
import ctypes, os, sys
from collections import Counter

SYS_sched_setattr, SYS_sched_getattr, RESET_ON_FORK = 314, 315, 0x01
FAIR = (0, 3)


class Attr(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("policy", ctypes.c_uint32), ("flags", ctypes.c_uint64),
                ("nice", ctypes.c_int32), ("priority", ctypes.c_uint32), ("runtime", ctypes.c_uint64),
                ("deadline", ctypes.c_uint64), ("period", ctypes.c_uint64), ("util_min", ctypes.c_uint32),
                ("util_max", ctypes.c_uint32)]


libc = ctypes.CDLL(None, use_errno=True)


def get(tid: int) -> Attr | None:
    a = Attr()
    return a if libc.syscall(SYS_sched_getattr, tid, ctypes.byref(a), ctypes.sizeof(Attr), 0) == 0 else None


def set_runtime(tid: int, runtime_ns: int) -> bool:
    cur = get(tid)
    if cur is None or cur.policy not in FAIR:
        return False
    new = Attr(size=ctypes.sizeof(Attr), policy=cur.policy, flags=cur.flags & RESET_ON_FORK, nice=cur.nice,
               runtime=runtime_ns)
    if libc.syscall(SYS_sched_setattr, tid, ctypes.byref(new), 0) != 0:
        return False
    after = get(tid)
    assert after is None or (after.nice == cur.nice and after.policy == cur.policy), f"tid {tid} changed nice/policy"
    return True


def default_slice() -> int:
    """既定の slice — fork した子は親の custom slice を継ぐので、自分の値ではなく runtime 0 を掛けた子の値を読む。"""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        cur = get(0)
        if cur is not None and cur.policy in FAIR:
            set_runtime(0, 0)
        os.write(w, str(get(0).runtime).encode()); os._exit(0)
    os.waitpid(pid, 0); os.close(w)
    return int(os.read(r, 64))


def job_of(pid: str) -> str | None:
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
        comm = open(f"/proc/{pid}/comm").read().strip()
    except OSError:
        return None
    if comm == "claude" or "/state/jobs/" not in cwd:
        return None
    job = cwd.split("/state/jobs/", 1)[1].split("/", 1)[0].removesuffix(" (deleted)")
    return None if job.startswith("task_") else job


def tids(pid: str) -> list[int]:
    try:
        return [int(t) for t in os.listdir(f"/proc/{pid}/task")]
    except OSError:
        return []


def main() -> None:
    mode = sys.argv[1]
    me = str(os.getpid())
    pids = [p for p in os.listdir("/proc") if p.isdigit() and p != me]
    if mode in ("on", "on-all"):
        ns = int(sys.argv[2]) * 1000
        done = Counter()
        for p in pids:
            job = job_of(p)
            if job is None and mode == "on-all":
                try:
                    job = "(会話の側)" + open(f"/proc/{p}/comm").read().strip()
                except OSError:
                    continue
            if job is None:
                continue
            for t in tids(p):
                cur = get(t)
                if cur is not None and cur.policy in FAIR and cur.nice == 0 and set_runtime(t, ns):
                    done[job] += 1
        print("on", ns, dict(done))
    elif mode in ("off", "show"):
        dflt = default_slice()
        seen = Counter()
        for p in pids:
            for t in tids(p):
                cur = get(t)
                if cur is None or cur.policy not in FAIR or cur.runtime == dflt:
                    continue
                key = job_of(p) or open(f"/proc/{p}/comm").read().strip() if os.path.exists(f"/proc/{p}") else "?"
                if mode == "off" and not set_runtime(t, 0):
                    key = "失敗:" + str(key)
                seen[key] += 1
        print(mode, "default", dflt, dict(seen))


if __name__ == "__main__":
    main()
