# /// script
# requires-python = ">=3.11"
# ///
"""短い slice を持つ process から起こした子を、`chrt --other 0` と sched_setattr(runtime 0)で既定の slice に戻せるかを確かめる(自分と子だけ)。"""
import ctypes, os, subprocess, sys

class Attr(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("policy", ctypes.c_uint32), ("flags", ctypes.c_uint64),
                ("nice", ctypes.c_int32), ("priority", ctypes.c_uint32), ("runtime", ctypes.c_uint64),
                ("deadline", ctypes.c_uint64), ("period", ctypes.c_uint64), ("util_min", ctypes.c_uint32),
                ("util_max", ctypes.c_uint32)]
libc = ctypes.CDLL(None, use_errno=True)

def get() -> str:
    b = Attr(); libc.syscall(315, 0, ctypes.byref(b), ctypes.sizeof(Attr), 0)
    return f"policy={b.policy} nice={b.nice} runtime={b.runtime}"

def set_runtime(ns: int) -> str:
    cur = Attr(); libc.syscall(315, 0, ctypes.byref(cur), ctypes.sizeof(Attr), 0)
    a = Attr(size=ctypes.sizeof(Attr), policy=cur.policy, nice=cur.nice, runtime=ns)
    return "ok" if libc.syscall(314, 0, ctypes.byref(a), 0) == 0 else "err " + os.strerror(ctypes.get_errno())

if len(sys.argv) > 1 and sys.argv[1] == "show":
    print("  child:", get()); sys.exit(0)
print("set 200us:", set_runtime(200_000), get())
me = [sys.executable, os.path.abspath(__file__), "show"]
print("plain child"); subprocess.run(me)
print("chrt --other 0 child"); subprocess.run(["chrt", "--other", "0", *me])
print("set back to 0 on self:", set_runtime(0), get())
