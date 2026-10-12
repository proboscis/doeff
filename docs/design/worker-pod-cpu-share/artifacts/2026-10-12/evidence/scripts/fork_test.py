# /// script
# requires-python = ">=3.11"
# ///
"""custom slice(sched_attr.sched_runtime)が fork・thread・exec の後に残るか、SCHED_FLAG_RESET_ON_FORK で子が既定に戻るかを確かめる
(自分と自分の子だけ・読むだけ)。SCHED_OTHER の process から走らせる。"""
import ctypes, os, subprocess, sys, threading

SYS_sched_setattr, SYS_sched_getattr = 314, 315
RESET_ON_FORK = 0x01

class Attr(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("policy", ctypes.c_uint32), ("flags", ctypes.c_uint64),
                ("nice", ctypes.c_int32), ("priority", ctypes.c_uint32), ("runtime", ctypes.c_uint64),
                ("deadline", ctypes.c_uint64), ("period", ctypes.c_uint64), ("util_min", ctypes.c_uint32),
                ("util_max", ctypes.c_uint32)]

libc = ctypes.CDLL(None, use_errno=True)

def get(tid: int = 0) -> str:
    b = Attr()
    r = libc.syscall(SYS_sched_getattr, tid, ctypes.byref(b), ctypes.sizeof(Attr), 0)
    return f"policy={b.policy} flags={b.flags} nice={b.nice} runtime={b.runtime}" if r == 0 else f"err {os.strerror(ctypes.get_errno())}"

def set_(runtime_ns: int, flags: int) -> str:
    cur = Attr(); libc.syscall(SYS_sched_getattr, 0, ctypes.byref(cur), ctypes.sizeof(Attr), 0)
    a = Attr(size=ctypes.sizeof(Attr), policy=cur.policy, flags=flags, nice=cur.nice, runtime=runtime_ns)
    r = libc.syscall(SYS_sched_setattr, 0, ctypes.byref(a), 0)
    return "ok" if r == 0 else f"err {os.strerror(ctypes.get_errno())}"

for flags in (0, RESET_ON_FORK):
    print(f"--- set runtime 200us flags={flags}: {set_(200_000, flags)} → self {get()}")
    out = []
    t = threading.Thread(target=lambda: out.append(get(0)))
    t.start(); t.join()
    print("thread:", out[0])
    pid = os.fork()
    if pid == 0:
        print("forked child:", get(), flush=True)
        os._exit(0)
    os.waitpid(pid, 0)
    code = ("import ctypes,os\n"
            "class A(ctypes.Structure):\n _fields_=[('size',ctypes.c_uint32),('policy',ctypes.c_uint32),('flags',ctypes.c_uint64),('nice',ctypes.c_int32),('priority',ctypes.c_uint32),('runtime',ctypes.c_uint64),('deadline',ctypes.c_uint64),('period',ctypes.c_uint64),('a',ctypes.c_uint32),('b',ctypes.c_uint32)]\n"
            "b=A(); ctypes.CDLL(None).syscall(315,0,ctypes.byref(b),ctypes.sizeof(A),0); print('exec child: policy=%d flags=%d nice=%d runtime=%d'%(b.policy,b.flags,b.nice,b.runtime))")
    print(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip())
    print("nice -n 5 child:", subprocess.run(["nice", "-n", "5", sys.executable, "-c", code], capture_output=True, text=True).stdout.strip())
print("--- back to default (runtime 0, flags 0):", set_(0, 0), "→", get())
