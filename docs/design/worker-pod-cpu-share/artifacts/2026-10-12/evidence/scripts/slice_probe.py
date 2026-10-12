# /// script
# requires-python = ">=3.11"
# ///
"""自分の thread に EEVDF の custom slice(sched_attr.sched_runtime)と nice を掛けられるかを確かめ、
service に似た負荷(眠り → 短い計算)の run queue の待ちを schedstat で測る(読むだけ・自分の process だけを変える)。

使い方: uv run --script slice_probe.py <slice の μs(0 = 既定)> <nice> <秒> [眠りの ms = 2] [計算の ms = 0.5]
"""
import ctypes, os, sys, time

SYS_sched_setattr, SYS_sched_getattr = 314, 315  # x86_64

class Attr(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("policy", ctypes.c_uint32), ("flags", ctypes.c_uint64),
                ("nice", ctypes.c_int32), ("priority", ctypes.c_uint32), ("runtime", ctypes.c_uint64),
                ("deadline", ctypes.c_uint64), ("period", ctypes.c_uint64), ("util_min", ctypes.c_uint32),
                ("util_max", ctypes.c_uint32)]

libc = ctypes.CDLL(None, use_errno=True)

def setattr_(slice_us: int, nice: int) -> str:
    a = Attr(size=ctypes.sizeof(Attr), policy=int(os.environ.get("PROBE_POLICY", "0")), flags=0, nice=nice, runtime=slice_us * 1000)
    r = libc.syscall(SYS_sched_setattr, 0, ctypes.byref(a), 0)
    if r != 0:
        return f"setattr err {os.strerror(ctypes.get_errno())}"
    b = Attr()
    r = libc.syscall(SYS_sched_getattr, 0, ctypes.byref(b), ctypes.sizeof(Attr), 0)
    return f"getattr policy={b.policy} nice={b.nice} runtime_ns={b.runtime}" if r == 0 else f"getattr err {os.strerror(ctypes.get_errno())}"

def schedstat() -> tuple[int, int, int]:
    run, wait, n = open(f"/proc/self/task/{threading_id()}/schedstat").read().split()
    return int(run), int(wait), int(n)

def threading_id() -> int:
    return libc.syscall(186)  # gettid

def main() -> None:
    slice_us, nice, secs = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
    sleep_s = float(sys.argv[4]) / 1000 if len(sys.argv) > 4 else 0.002
    work_s = float(sys.argv[5]) / 1000 if len(sys.argv) > 5 else 0.0005
    state = setattr_(slice_us, nice)
    r0, w0, n0 = schedstat(); t0 = time.monotonic(); load0 = open("/proc/loadavg").read().split()[0]
    while time.monotonic() - t0 < secs:
        time.sleep(sleep_s)
        end = time.thread_time() + work_s
        while time.thread_time() < end:
            pass
    r1, w1, n1 = schedstat(); dt = time.monotonic() - t0
    run, wait, n = r1 - r0, w1 - w0, n1 - n0
    print(f"slice_us={slice_us} nice={nice} {state} load {load0}→{open('/proc/loadavg').read().split()[0]} "
          f"cpu={run/1e9:.2f}s wait={wait/1e9:.2f}s ratio={wait/(run+wait):.0%} switches={n} wait/switch={wait/max(n,1)/1e3:.0f}us")

if __name__ == "__main__":
    main()
