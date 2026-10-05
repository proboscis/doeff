"""検の入口(tests/test_warm_child.py): 待ちの子から分かれた子が、読み込み済みの module として同じ process の中で走らせる小さな main。
引数で振る舞いを選ぶ:
  exit <code>                                   — その終了コードで終わる
  inspect <出力の path> [<関所の socket> [<印の path>]]
      — 開いている fd の行き先・env・process の命令行・thread の本数・doeff の VM の数を JSON で書く。関所が在れば、検が開いた unix socket
        へ繋いで「書いた」を送り、検が切るまで待ってから印を書く(眠らずに、検の合図で進む)。
"""

import json
import os
import socket
import sys

MODULE_TAGS = {"context": "doeff-cluster-test", "role": "main"}


def environ_seen() -> dict[str, str]:
    """入口が読む環境変数(os.environ)をそのまま写すため — 分かれた子に頼みの env が在る事を検が見る。/proc/self/environ は exec の時の
    環境しか見せず、分かれた子 A が置き換えた環境を映さないので使えない。"""
    return dict(os.environ)  # noqa: DOEFF004 — 検の入口が、分かれた子の入口の読む環境をそのまま写す 1 か所(上の docstring)


def facts() -> dict[str, object]:
    """この process の見え方(fd の行き先・env・命令行・thread の本数・VM の数)を集めるため。"""
    import doeff_vm  # noqa: PLC0415 — 走った後の数を読むだけ

    names = sorted(os.listdir("/proc/self/fd"))
    targets = tuple(os.readlink(f"/proc/self/fd/{name}") for name in names if os.path.exists(f"/proc/self/fd/{name}"))
    with open("/proc/self/cmdline", "rb") as handle:
        command = handle.read().replace(b"\0", b" ").decode("utf-8", "replace")
    return {
        "pid": os.getpid(),
        "fds": list(targets),
        "env": environ_seen(),
        "cmdline": command,
        "argv": list(sys.argv),
        "threads": len(os.listdir("/proc/self/task")),
        "vmLive": list(doeff_vm.vm_live_counts()),
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
