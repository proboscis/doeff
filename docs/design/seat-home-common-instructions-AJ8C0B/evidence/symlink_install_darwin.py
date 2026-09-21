"""張り替え中の読みの失敗を errno 別に分ける(受入 11 の枡をどちらで書くかの根拠)。

「根が消えた読み」(`os.path.lexists` が false = ENOENT)と、APFS が張り替えを跨いだ
`readdir` に返す一過性の `EINVAL` を分けて数える。前者だけが設計の守る対象で、後者は
古い形にも同率で出る。計器の形は依頼者の会話 c-3JYBNJMC2RZTM1S8V43939MP42 から借り、
値は計画段 c-AJ8C0BK9RF29HQ92ZQ986FXQVT が個人 Mac で撃った。

撃ち方:
    python docs/design/seat-home-common-instructions-AJ8C0B/evidence/symlink_install_darwin.py
"""

from __future__ import annotations

import errno
import os
import platform
import sys
import tempfile
import time
from multiprocessing import Process, Value


def atomic(link: str, target: str) -> None:
    """直した形: 一意な仮 symlink を張って rename で被せる。"""
    staged = f"{link}.{os.getpid()}.tmp"
    os.symlink(target, staged)
    os.rename(staged, link)


def broken(link: str, target: str) -> None:
    """古い形: unlink してから張る(その谷で根が消える)。"""
    if os.path.lexists(link):
        os.unlink(link)
    os.symlink(target, link)


FORMS = {"atomic": atomic, "broken": broken}


def writer(form: str, link: str, targets: list[str], reps: int, done) -> None:  # noqa: ANN001
    for index in range(reps):
        FORMS[form](link, targets[index % len(targets)])
        time.sleep(0.0004)
    done.value = 1


def reader(link: str, counters, done) -> None:  # noqa: ANN001
    """読み続けて数える。数え口は共有の整数ちょうど(引数の中身を差し替えない)。"""
    reads, rootless, enoent, einval, other = counters
    while not done.value:
        reads.value += 1
        if not os.path.lexists(link):
            rootless.value += 1
        try:
            os.listdir(link)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                enoent.value += 1
            elif exc.errno == errno.EINVAL:
                einval.value += 1
            else:
                other.value += 1


def run(label: str, form: str, base: str, targets: list[str], reps: int = 400) -> None:
    home = os.path.join(base, f"e-{label}")
    os.makedirs(home)
    link = os.path.join(home, "skills")
    os.symlink(targets[0], link)
    counters = tuple(Value("i", 0) for _ in range(5))
    done = Value("i", 0)
    w = Process(target=writer, args=(form, link, targets, reps, done))
    r = Process(target=reader, args=(link, counters, done))
    w.start()
    r.start()
    w.join()
    r.join()
    reads, rootless, enoent, einval, other = (c.value for c in counters)
    print(
        f"  {label:26s} 読み {reads:6d} → "
        f"根が消えた読み {rootless:5d} / ENOENT {enoent:5d} / EINVAL {einval:5d} / その他 {other:5d}"
    )


def main() -> int:
    print(f"=== {platform.platform()} / python {sys.version.split()[0]} ===")
    base = tempfile.mkdtemp(prefix="einval-probe-")
    first = os.path.join(base, "s1")
    second = os.path.join(base, "s2")
    for target in (first, second):
        os.makedirs(target)
        with open(os.path.join(target, "a.md"), "w") as handle:
            handle.write("x")
    run("直した形・同じ先へ", "atomic", base, [first])
    run("直した形・2 つの先を交互", "atomic", base, [first, second])
    run("古い形・2 つの先を交互", "broken", base, [first, second])
    return 0


if __name__ == "__main__":  # macOS の multiprocessing の既定は spawn — この囲みが要る
    sys.exit(main())
