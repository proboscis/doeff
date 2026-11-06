"""/proc の無い機体(macOS)の CPU の刻みと memory を OS の口で読む生の読み(生の OS の呼びに触ってよいのは foundation の層)。

使い手は worker の測りの答え手(worker/entry/host_load の os-host-counters — 問い ReadHostCounters に答える)。Linux の worker は /proc の
file を読む(worker/core/load_rules)ので、これを使うのは /proc の無い機体だけ。読む口(どれも libSystem の関数 — ctypes で呼ぶ):

  host_statistics(HOST_CPU_LOAD_INFO)  起動からの CPU の刻みの和(user・system・idle・nice の 4 つ — 全部の CPU の和)。/proc/stat の頭の
                                        行 cpu に当たる。欄は 32 bit の数え手で、一巡りすると小さい値に戻る(CPU 10 個の機体で約 50 日)—
                                        戻った読みは和が減るので、使いの判断(load_rules.cpu-busy-between)がその 1 回を None と数える。
  host_statistics64(HOST_VM_INFO64)    頁の数(wired・内部 = 匿名・purgeable・圧縮)。
  host_page_size                        頁の大きさ(Apple silicon は 16 KiB)。
  sysctl hw.memsize・hw.logicalcpu      memory の総量(byte)と今 online の CPU の数。

追い出さずに使える量 = 総量 - (wired + (内部 - purgeable) + 圧縮)× 頁の大きさ(Activity Monitor の「使用済みメモリ」= App メモリ +
wired + 圧縮 を総量から引いた物 — 使っていない頁と、捨てて取り返せる file の頁と purgeable の和。Linux の MemAvailable に当たる)。

答えは dict(欄は worker/intent/worker_model の HostCounters と同じ名 — 層 foundation は intent の型を読まない)か、読めない訳の str
(例外にしない — 読めない機体は測りを名乗らないだけ)。
"""

import ctypes
import sys
from functools import cache

# 層 foundation の文脈と役の名乗り(DOEFF104)— 隣の otlp_post.py と同じ。
MODULE_TAGS = {"context": "doeff-cluster", "role": "foundation"}

# mach/host_info.h の flavor と数(natural_t の個数)。
HOST_CPU_LOAD_INFO = 3
CPU_STATE_COUNT = 4
CPU_STATE_USER, CPU_STATE_SYSTEM, CPU_STATE_IDLE, CPU_STATE_NICE = 0, 1, 2, 3
HOST_VM_INFO64 = 4
KERN_SUCCESS = 0


class VmStatistics64(ctypes.Structure):
    """mach/vm_statistics.h の struct vm_statistics64(欄の順と幅はその宣言のまま — 152 byte)。"""

    _fields_ = [
        ("free_count", ctypes.c_uint32),
        ("active_count", ctypes.c_uint32),
        ("inactive_count", ctypes.c_uint32),
        ("wire_count", ctypes.c_uint32),
        ("zero_fill_count", ctypes.c_uint64),
        ("reactivations", ctypes.c_uint64),
        ("pageins", ctypes.c_uint64),
        ("pageouts", ctypes.c_uint64),
        ("faults", ctypes.c_uint64),
        ("cow_faults", ctypes.c_uint64),
        ("lookups", ctypes.c_uint64),
        ("hits", ctypes.c_uint64),
        ("purges", ctypes.c_uint64),
        ("purgeable_count", ctypes.c_uint32),
        ("speculative_count", ctypes.c_uint32),
        ("decompressions", ctypes.c_uint64),
        ("compressions", ctypes.c_uint64),
        ("swapins", ctypes.c_uint64),
        ("swapouts", ctypes.c_uint64),
        ("compressor_page_count", ctypes.c_uint32),
        ("throttled_count", ctypes.c_uint32),
        ("external_page_count", ctypes.c_uint32),
        ("internal_page_count", ctypes.c_uint32),
        ("total_uncompressed_pages_in_compressor", ctypes.c_uint64),
    ]


class MachHost:
    """libSystem の口と、この process の host の port(1 度だけ取る — mach_host_self は呼ぶたびに port の権利を 1 つ増やす)。"""

    def __init__(self) -> None:
        """libSystem を開き、使う関数の引数と答えの型を宣言して、host の port と頁の大きさを読むため。"""
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        lib.mach_host_self.argtypes = ()
        lib.mach_host_self.restype = ctypes.c_uint32
        lib.host_statistics.argtypes = (
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        )
        lib.host_statistics.restype = ctypes.c_int
        lib.host_statistics64.argtypes = (
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        )
        lib.host_statistics64.restype = ctypes.c_int
        lib.host_page_size.argtypes = (ctypes.c_uint32, ctypes.POINTER(ctypes.c_size_t))
        lib.host_page_size.restype = ctypes.c_int
        lib.sysctlbyname.argtypes = (
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
            ctypes.c_size_t,
        )
        lib.sysctlbyname.restype = ctypes.c_int
        self.lib = lib
        self.host = lib.mach_host_self()
        page = ctypes.c_size_t(0)
        status = lib.host_page_size(self.host, ctypes.byref(page))
        if status != KERN_SUCCESS:
            raise OSError(f"host_page_size が {status} で断った")
        self.page_bytes = page.value


@cache
def _mach_host() -> MachHost:
    """この process の MachHost を初めの読みで作り、以後は同じ物を返すため(作れなかった時の OSError は覚えない — 次の読みで作り直す)。"""
    return MachHost()


def _sysctl_int(
    host: MachHost, name: str, kind: type[ctypes.c_int32] | type[ctypes.c_uint64]
) -> int | str:
    """sysctl の整数の値 1 つを読むため(読めない時は訳の str)。"""
    value = kind(0)
    size = ctypes.c_size_t(ctypes.sizeof(value))
    if host.lib.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
        return f"sysctl {name} が errno {ctypes.get_errno()} で断った"
    return int(value.value)


def _cpu_ticks(host: MachHost) -> tuple[int, int, int, int] | str:
    """起動からの CPU の刻みの和(user・system・idle・nice)を読むため(読めない時は訳の str)。"""
    ticks = (ctypes.c_uint32 * CPU_STATE_COUNT)()
    count = ctypes.c_uint32(CPU_STATE_COUNT)
    status = host.lib.host_statistics(
        host.host, HOST_CPU_LOAD_INFO, ctypes.byref(ticks), ctypes.byref(count)
    )
    if status != KERN_SUCCESS:
        return f"host_statistics(HOST_CPU_LOAD_INFO) が {status} で断った"
    return (
        int(ticks[CPU_STATE_USER]),
        int(ticks[CPU_STATE_SYSTEM]),
        int(ticks[CPU_STATE_IDLE]),
        int(ticks[CPU_STATE_NICE]),
    )


def _used_bytes(host: MachHost) -> int | str:
    """追い出せない memory の量(wired + App = 内部 - purgeable+ 圧縮 — 頭の註)を読むため(読めない時は訳の str)。"""
    vm = VmStatistics64()
    count = ctypes.c_uint32(ctypes.sizeof(VmStatistics64) // ctypes.sizeof(ctypes.c_int32))
    status = host.lib.host_statistics64(
        host.host, HOST_VM_INFO64, ctypes.byref(vm), ctypes.byref(count)
    )
    if status != KERN_SUCCESS:
        return f"host_statistics64(HOST_VM_INFO64) が {status} で断った"
    app_pages = max(0, int(vm.internal_page_count) - int(vm.purgeable_count))
    return (int(vm.wire_count) + app_pages + int(vm.compressor_page_count)) * host.page_bytes


def read_host_counters() -> dict[str, int] | str:
    """機体の CPU の刻みの和と memory の総量・空きを OS の口で 1 回読むため(頭の註)。答え = HostCounters と同じ名の欄の dict か、
    読めない訳の str(macOS でない機体・libSystem の呼びが断った)。"""
    if sys.platform != "darwin":
        return f"この OS({sys.platform})に mach の host_statistics が無い"
    try:
        host = _mach_host()
    except OSError as error:
        return f"libSystem の mach の口を開けない: {error}"
    reads = (
        _cpu_ticks(host),
        _used_bytes(host),
        _sysctl_int(host, "hw.logicalcpu", ctypes.c_int32),
        _sysctl_int(host, "hw.memsize", ctypes.c_uint64),
    )
    match reads:
        case ((user, system, idle, nice), int(used_bytes), int(cpus), int(total_bytes)):
            return {
                "cpus": cpus,
                "busy_ticks": user + system + nice,
                "total_ticks": user + system + idle + nice,
                "nice_ticks": nice,
                "memory_total_bytes": total_bytes,
                "memory_available_bytes": min(total_bytes, max(0, total_bytes - used_bytes)),
            }
        case _:
            # どれかの読みが断った — 断った読みの訳を全部並べる。
            return "・".join(read for read in reads if isinstance(read, str))


if __name__ == "__main__":
    # 機体の上で 1 回読んで見せる(macOS の worker の機体で読みを確かめる時の口 — 例 python -m doeff_cluster._foundation.host_counters)。
    print(read_host_counters())
