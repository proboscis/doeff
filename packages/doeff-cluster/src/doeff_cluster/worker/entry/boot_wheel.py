"""起動の script(deploy/boot.sh の自己起動)が root の venv へ入れる doeff-vm の wheel を用意する入口(2026-10-06・#3860)。

Rust の部品を組む・引く入口は doeff の build の口 tools/doeff_cargo_backend.py の 1 つ(ADR-DOE-BUILD-001)。ここは自前の鍵も置き場も
持たず、実行環境の準備(worker の EnsureNativeWheel)と同じく `uv build --wheel` でその口を通るだけ: 口は source の中身の鍵で保存先
($WORK_DIR/state/wheels — env DOEFF_WHEEL_CACHE)を引き、無い時だけ組んで置き、保存先の中の wheel と組んだかを報告の file
(env DOEFF_WHEEL_REPORT)に書く。uv の子の環境・錠・報告の読みの定義点は doeff_cluster.shared.core.native_wheel の 1 つ。

標準ライブラリと native_wheel・foundation/process_environ だけを import する: boot.sh は doeff-vm を入れる前の venv(uv sync --no-install-package
doeff-vm)の python でこれを起こす。

使い方: python -m doeff_cluster.worker.entry.boot_wheel --root <doeff の root> --state <state dir> --uv-cache <uv の cache の dir>
        [--uv <uv の path(既定 uv)>]
  stdout = 1 行「<組んだ|使った> <wheel の path>」(boot.sh が起動の行に載せ、wheel を venv へ入れる — path は保存先の中の wheel)。
  stderr = 組んだか使ったかと秒の 1 行(口と uv build の出力も)。組めない・報告を読めない時は非 0 で終わり、理由を stderr に 1 行。
"""

import argparse
import fcntl
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
import time

from doeff_cluster.foundation.process_environ import child_environ
from doeff_cluster.shared.core import native_wheel

# 層 entry の文脈と役の名乗り(DOEFF104)— 隣の入口 shim.py と同じ。
MODULE_TAGS = {"context": "worker", "role": "main"}


class BootWheelFailed(RuntimeError):
    """wheel を用意できない(組めない・口の報告を読めない)— 入口が理由を stderr に出して非 0 で終わる。"""


def built_or_stored(root: str, state_dir: str, uv_cache: str, uv: str) -> native_wheel.StoredWheel:
    """root の doeff-vm の source を `uv build --wheel` で build の口に渡し、口が報告した保存先の中の wheel を返す。uv の --out-dir の
    写しと報告の file は一時の dir に置き、読んだら消す(入れるのは保存先の中の wheel)。uv の出力は stderr へ流す(stdout は答えの
    1 行だけ)。"""
    source_dir = posixpath.join(root, native_wheel.DOEFF_VM_SOURCE)
    os.makedirs(state_dir, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix=".boot-wheel-", dir=state_dir)
    try:
        report = posixpath.join(scratch, "report.jsonl")
        extra = (*native_wheel.uv_environment(state_dir, uv_cache), native_wheel.UvVariable(native_wheel.WHEEL_REPORT_ENV, report))
        done = subprocess.run(
            [uv, "build", "--wheel", "--out-dir", posixpath.join(scratch, "out"), source_dir],
            cwd=source_dir,
            env=child_environ(native_wheel.UV_DROP, extra),
            stdout=sys.stderr,
            check=False,
        )
        if done.returncode != 0:
            raise BootWheelFailed(f"uv build が終了コード {done.returncode} で終わった({source_dir} — 出力は上)")
        text = ""
        if os.path.isfile(report):
            with open(report, encoding="utf-8") as handle:
                text = handle.read()
        match native_wheel.stored_wheel_of(text, native_wheel.DOEFF_VM_PACKAGE):
            case native_wheel.StoredWheel() as found:
                return found
            case str() as problem:
                raise BootWheelFailed(problem)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def ensured_wheel(root: str, state_dir: str, uv_cache: str, uv: str) -> native_wheel.StoredWheel:
    """root の doeff-vm の wheel を build の口の保存先から用意する。package の錠(native_wheel.wheel_lock)を持つ間に口を通す — 同じ
    package を組む worker の準備と重ならない。"""
    lock = native_wheel.wheel_lock(state_dir, native_wheel.DOEFF_VM_PACKAGE)
    os.makedirs(posixpath.dirname(lock), exist_ok=True)
    held = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(held, fcntl.LOCK_EX)
        return built_or_stored(root, state_dir, uv_cache, uv)
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)


def main() -> None:
    """引数の root の doeff-vm の wheel を用意し、「<組んだ|使った> <path>」を stdout に 1 行出す。用意できなければ理由を stderr に出して
    終了コード 1 で終わる。"""
    parser = argparse.ArgumentParser(description="起動の root の doeff-vm の wheel を、build の口の保存先から用意する")
    parser.add_argument("--root", required=True, help="doeff の root(展開した commit の木)")
    parser.add_argument("--state", required=True, help="state dir(wheels/・locks/・python/ を置く)")
    parser.add_argument("--uv-cache", required=True, help="uv の cache の dir(起動の script の DOEFF_UV_CACHE_DIR)")
    parser.add_argument("--uv", default="uv", help="uv の命令(既定 uv — PATH で引く)")
    args = parser.parse_args()
    started = time.monotonic()
    try:
        ready = ensured_wheel(args.root, args.state, args.uv_cache, args.uv)
    except BootWheelFailed as failure:
        print(f"boot: doeff-vm の wheel を用意できない: {failure}", file=sys.stderr, flush=True)
        sys.exit(1)
    how = "組んだ" if ready.built else "使った"
    print(f"boot: doeff-vm の wheel を{how}({time.monotonic() - started:.1f} 秒・{ready.path})", file=sys.stderr, flush=True)
    print(f"{how} {ready.path}", flush=True)


if __name__ == "__main__":
    main()
