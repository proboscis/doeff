"""起動の root の置き場($WORK_DIR/boot/roots/<sha>)から、使われていない古い版の root を消す道具 — deploy/boot.sh が起動のたびに背後で呼ぶ。

訳: boot.sh は宣言された commit ごとに root(source の展開と venv — 1 本 約 0.24 GB)を作るが、前は消す者が居なかった。2026-11-08 の atlas で
worker の置き場に 33 本・6.8 GB、coordinator の置き場に 95 本・15.3 GB(09-26 から 1 本も消えていない)溜まり、根の disk を週に約 11 GB
減らしていた。作り手の boot.sh 自身が、作った root の終わりを持つ。

動詞(答えは標準出力の JSON 1 行・訳は標準エラーの行):
  gc     --boot の roots の下の root のうち、次のどれにも当たらない物を消す:
           今の版     --current の sha(呼んだ起動の root)。
           新しい物   最後に使った刻の新しい順に --keep 本(今の版を含めて数える)。最後に使った刻 = 印 .doeff-boot-use の変更の刻
                      (boot.sh が root を使って起こすたびに付け直す)。印の無い root(印を置く前の版の起動 script が作った物)は、
                      完成の印・展開の印・dir の変更の刻の遅い方。先に組んだ上げ先の版(役 prepare)と戻し先の版はここで残る。
           錠の持たれた物  印 .doeff-boot-use に共有の錠(flock)を持つ process が在る — boot.sh はその root で起こす process に錠を
                      持たせ、exec した worker・coordinator・records が終わるまで離さない。錠は同じ機体の別の Pod(同じ hostPath を
                      使う RollingUpdate の旧い Pod・coordinator の上げ先を先に組む Job と coordinator)の間でも効く。drain 中の世代も
                      終わるまで錠を持つ。
           使っている物  この名前空間の走っている process の作業 dir・実行 file・引数の path・読み込んだ file が root の下に在る
                      (錠を継がない子 — subprocess の close_fds — と、錠を持つ前の版の script の process)。/proc を読めない機体
                      (macOS ほか)では使っているかを判じられないので、何も消さない。
         消す物は、boot.lock(展開と準備の排他 — boot.sh の doeff_extract・doeff_prepare と同じ錠)を持ったまま判じて roots の下の隠れた
         名 .doomed-<sha>.<pid> へ rename し(次の起動は見えなくなった root を展開し直す)、錠を離してから中身を消す。前の回が消し
         終える前に止まった .doomed-* も消す。錠を待つ間に起動した process は、錠の後で root が無ければ展開し直す。
  count  gc と同じ判じだけをする(何も消さない)。

残す数の既定(DEFAULT_KEEP)はこの file 1 か所 — boot.sh は DOEFF_BOOT_ROOTS_KEEP が在る時だけ --keep を渡し、disk の分け前の宣言
(deploy/k8s/disk-share/derive.py)は置き場の大きさをこの値から導く。

標準の library だけを使う。process の path の読みは uv の cache の片付け(同じ dir の uv_cache.py の process_paths・used_under)と同じ物を使う。
"""


import argparse
import contextlib
import errno
import fcntl
import json
import os
import re
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uv_cache import process_paths, used_under  # noqa: E402

# 残す root の数(今の版を含む)の既定: 今の版・直前の版(drain 中の世代と、戻し先)・もう 1 つ前(先に組んだ上げ先の版が今の版より
# 新しい間の、走っている版)。
DEFAULT_KEEP = 3
# 残す数の外で、錠か process が使い続けて消せない root の数の見込み(置き場の大きさの上限を導く時に足す — 消す決まりは使わない):
# keeper の起動の版(keeper の process が錠を持ち続ける)と、旧い Pod が終わり切る前の版。
PINNED_ALLOWANCE = 2
USE_MARK = ".doeff-boot-use"
READY_MARK = ".doeff-boot-ready"
EXTRACTED_MARK = ".doeff-boot-extracted"
BOOT_LOCK = "boot.lock"
DOOMED_PREFIX = ".doomed-"
SHA = re.compile(r"[0-9a-f]{40}")


def say(line: str) -> None:
    print(f"boot-roots: {line}", file=sys.stderr, flush=True)


@dataclass(frozen=True)
class Root:
    path: Path
    last_used: float


def last_used(root: Path) -> float:
    """root を最後に使った刻: 印 .doeff-boot-use が在ればその変更の刻、無ければ完成の印・展開の印・dir の変更の刻の遅い方。"""
    with contextlib.suppress(OSError):
        return (root / USE_MARK).stat().st_mtime
    stamps = []
    for probe in (root / READY_MARK, root / EXTRACTED_MARK, root):
        with contextlib.suppress(OSError):
            stamps.append(probe.stat().st_mtime)
    return max(stamps, default=0.0)


def roots_of(boot: Path) -> list[Root]:
    """roots の下の root(名が 40 字の sha の dir)を、最後に使った刻の新しい順に。"""
    try:
        entries = list((boot / "roots").iterdir())
    except FileNotFoundError:
        return []
    found = [Root(e, last_used(e)) for e in entries if SHA.fullmatch(e.name) and e.is_dir() and not e.is_symlink()]
    return sorted(found, key=lambda r: (r.last_used, r.path.name), reverse=True)


@contextlib.contextmanager
def boot_lock(boot: Path) -> Iterator[None]:
    fd = os.open(boot / BOOT_LOCK, os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


@contextlib.contextmanager
def unheld(root: Path) -> Iterator[bool]:
    """root の印 .doeff-boot-use に、誰も共有の錠を持っていなければ真(判じて rename する間、排他の錠を持つ)。印の無い root は
    錠を持つ process の無い root(印を置かない版の script が作った物)— 印を作らない(作ると最後に使った刻が今になる)。"""
    try:
        fd = os.open(root / USE_MARK, os.O_RDONLY)
    except FileNotFoundError:
        yield True
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise
            yield False
            return
        yield True
    finally:
        os.close(fd)


def judge(boot: Path, current: str, keep: int, proc: Path, *, remove: bool) -> dict[str, object]:
    """roots の下を判じ、remove なら消す物を .doomed-* へ rename する(boot.lock を持って呼ぶ)。答え = 名ごとの判じ。"""
    roots = roots_of(boot)
    # 今の版を数に含めて残す — 今の版の印が古くても(印を付ける前の版の script が起こした)、ほかの新しい物は keep − 1 本。
    others = [r.path.name for r in roots if r.path.name != current]
    kept = {current, *others[: keep - 1]}
    paths = process_paths(proc)
    held: list[str] = []
    used: list[str] = []
    doomed: list[str] = []
    for root in roots:
        name = root.path.name
        if name in kept or paths is None:
            continue
        with unheld(root.path) as free:
            if not free:
                held.append(name)
                continue
            if used_under(paths, root.path) or used_under(paths, root.path.resolve()):
                used.append(name)
                continue
            if remove:
                root.path.rename(root.path.with_name(f"{DOOMED_PREFIX}{name}.{os.getpid()}"))
            doomed.append(name)
    return {
        "roots": len(roots),
        "kept": sorted(kept & {r.path.name for r in roots}),
        "held": sorted(held),
        "used": sorted(used),
        "doomed": sorted(doomed),
        "proc": paths is not None,
    }


def leftovers(boot: Path) -> list[Path]:
    with contextlib.suppress(FileNotFoundError):
        return sorted(p for p in (boot / "roots").iterdir() if p.name.startswith(DOOMED_PREFIX))
    return []


def gc(boot: Path, current: str, keep: int, proc: Path, *, remove: bool) -> dict[str, object]:
    """判じて(remove なら)消す。答えに消した数と file system の空きの前と後を載せる。"""
    before = shutil.disk_usage(boot).free if boot.exists() else 0
    with boot_lock(boot):
        result = judge(boot, current, keep, proc, remove=remove)
    if remove:
        for gone in leftovers(boot):
            shutil.rmtree(gone, ignore_errors=True)
    if result["proc"] is False:
        say("/proc を読めない — 使っているかを判じられないので、何も消さない")
    result["freeBefore"] = before
    result["freeAfter"] = shutil.disk_usage(boot).free if boot.exists() else 0
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="boot_roots.py")
    parser.add_argument("verb", choices=("gc", "count"))
    parser.add_argument("--boot", type=Path, required=True, help="起動の置き場($WORK_DIR/boot)")
    parser.add_argument("--current", required=True, help="今の版の sha(呼んだ起動の root)")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help=f"残す root の数(今の版を含む・既定 {DEFAULT_KEEP})")
    parser.add_argument("--proc", type=Path, default=Path("/proc"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not SHA.fullmatch(args.current):
        parser.error(f"--current は 40 字の sha: {args.current!r}")
    if args.keep < 1:
        parser.error(f"--keep は 1 以上: {args.keep}")
    result = gc(args.boot, args.current, args.keep, args.proc, remove=args.verb == "gc")
    say(
        f"root {result['roots']} 本 — 残す {len(result['kept'])}・錠 {len(result['held'])}・使用中 {len(result['used'])}・"
        f"{'消した' if args.verb == 'gc' else '消せる'} {len(result['doomed'])}"
    )
    print(json.dumps({"verb": args.verb, **result}, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
