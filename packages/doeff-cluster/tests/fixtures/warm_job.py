"""検の入口(tests/test_warm_child.py): 待ちの子から分かれた子が、読み込み済みの module として同じ process の中で走らせる小さな main。
引数で振る舞いを選ぶ:
  exit <code>                                   — その終了コードで終わる
  inspect <出力の path> [<関所の socket> [<印の path>]]
      — 開いている fd の行き先・env・process の命令行・thread の本数・doeff の VM の数・GC が凍らせた object の数を JSON で書く。関所が在れば、検が開いた unix socket
        へ繋いで「書いた」を送り、検が切るまで待ってから印を書く(眠らずに、検の合図で進む)。
"""

import fcntl
import gc
import json
import os
import socket
import stat
import subprocess
import sys

from doeff import run
from doeff_core_effects.os_warm_process import own_thread_count

MODULE_TAGS = {"context": "doeff-cluster-test", "role": "main"}
LINUX = sys.platform.startswith("linux")


def environ_seen() -> dict[str, str]:
    """入口が読む環境変数(os.environ)をそのまま写すため — 分かれた子に頼みの env が在る事を検が見る。/proc/self/environ は exec の時の
    環境しか見せず、分かれた子 A が置き換えた環境を映さないので使えない。"""
    return dict(os.environ)  # noqa: DOEFF004 — 検の入口が、分かれた子の入口の読む環境をそのまま写す 1 か所(上の docstring)


def fd_targets() -> tuple[str, ...]:
    """開いている fd の行き先を並べるため — 分かれた子が待ちの子の socket ともう 1 本の log を継いでいない事を検が見る。Linux は
    /proc/self/fd の link(socket は socket:[…])、macOS は /dev/fd を数えて、socket と pipe は種類の名(socket:・pipe:)、file は
    fcntl の F_GETPATH の path にする(macOS の /dev/fd は link でない)。"""
    if LINUX:
        names = sorted(os.listdir("/proc/self/fd"))
        return tuple(os.readlink(f"/proc/self/fd/{name}") for name in names if os.path.exists(f"/proc/self/fd/{name}"))
    seen = tuple(darwin_target(descriptor) for descriptor in sorted(int(name) for name in os.listdir("/dev/fd")))
    return tuple(target for target in seen if target is not None)


def darwin_target(descriptor: int) -> str | None:
    """macOS で fd 1 つの行き先を名乗るため(fd_targets の註)。閉じている fd(並べるために開いた /dev/fd の dir 自身 — listdir の後に
    閉じている)は None。"""
    try:
        seen = os.fstat(descriptor)
    except OSError:
        return None
    if stat.S_ISSOCK(seen.st_mode):
        return f"socket:[{seen.st_ino}]"
    if stat.S_ISFIFO(seen.st_mode):
        return f"pipe:[{seen.st_ino}]"
    if sys.platform != "darwin":
        raise OSError(f"fd {descriptor} の path の読みを知らない機体: {sys.platform}")
    path: bytes = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024))
    return path.split(b"\0", 1)[0].decode("utf-8", "replace")


def command_line() -> str:
    """この process の命令行を読むため — 分かれた子が exec せずに待ちの子の命令行のまま走る事を検が見る(Linux = /proc/self/cmdline・
    macOS = ps の command の欄)。"""
    if LINUX:
        with open("/proc/self/cmdline", "rb") as handle:
            return handle.read().replace(b"\0", b" ").decode("utf-8", "replace")
    shown = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(os.getpid())], check=True, capture_output=True, text=True)
    return shown.stdout.strip()


def facts() -> dict[str, object]:
    """この process の見え方(fd の行き先・env・命令行・thread の本数・VM の数・GC が凍らせた object の数 — 待ちの子から受け継いだ分)を
    集めるため。"""
    import doeff_vm  # noqa: PLC0415 — 走った後の数を読むだけ

    threads: int = run(own_thread_count())
    return {
        "pid": os.getpid(),
        "fds": list(fd_targets()),
        "env": environ_seen(),
        "cmdline": command_line(),
        "argv": list(sys.argv),
        "threads": threads,
        "vmLive": list(doeff_vm.vm_live_counts()),
        "gcFrozen": gc.get_freeze_count(),
    }


def held_at(gate: str) -> None:
    """検が開いた関所の socket へ繋いで「書いた」を送り、検が切るまで待つため(検の合図で進む)。"""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as link:
        link.connect(gate)
        link.sendall(b"inspected\n")
        while link.recv(4096):
            pass


def main() -> None:
    """引数の振る舞いを 1 つ行うため。"""
    match sys.argv[1:]:
        case ["exit", code]:
            print(f"warm_job: exit {code}", flush=True)
            sys.exit(int(code))
        case ["inspect", out, *rest]:
            with open(out, "w", encoding="utf-8") as handle:
                json.dump(facts(), handle)
            match rest:
                case [gate, *marker]:
                    held_at(gate)
                    for path in marker:
                        with open(path, "w", encoding="utf-8") as handle:
                            handle.write("done")
                case _:
                    pass
            print("warm_job: inspect done", flush=True)
        case other:
            print(f"warm_job: 知らない引数 {other}", file=sys.stderr, flush=True)
            sys.exit(2)
