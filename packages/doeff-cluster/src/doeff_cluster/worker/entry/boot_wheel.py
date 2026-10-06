"""起動の script(deploy/boot.sh の自己起動)が root の venv へ入れる doeff-vm の wheel を用意する入口(2026-10-06)。

実行環境の準備(worker の EnsureNativeWheel)と同じ鍵・同じ置き場・同じ錠の wheel を使い、無ければ組んでその置き場へ置く — 鍵・置き場・
uv の子の環境の定義点は doeff_cluster.shared.core.native_wheel の 1 つ。2026-10-06 00:02〜00:05 の版上げで、起動の uv sync が doeff-vm を
source から 94 秒かけて組み、同じ PVC に実行環境の準備が置いた同じ中身の wheel が在るのに使わなかった(その間 利用者の画面が切れた)。

鍵の材料は worker の準備(env_prepare の stage-native)と同じ: doeff-vm の wheel の中身を決める dir(native_wheel.DOEFF_VM_PATHS)ごとの
git の tree hash(mirror の commit から)・Python(root の .python-version の 1 行 — 実行環境の宣言の python と同じ綴り 例 3.14.3t)・この
機体の platform。組むのは worker と同じ命令(uv build --wheel --out-dir <途中の dir> <source> — cwd = source・呼び手の venv と uv・Python の
設定を外し、cache と Python は state の下 — 子の環境は foundation/process_environ が組む)。錠は worker の AcquireLock と同じ fcntl.flock の排他。

標準ライブラリと native_wheel・foundation/process_environ だけを import する: boot.sh は doeff-vm を入れる前の venv(uv sync --no-install-package doeff-vm)の python で
これを起こす。

使い方: python -m doeff_cluster.worker.entry.boot_wheel --root <doeff の root> --mirror <doeff の bare repo> --commit <sha>
        --state <state dir> --uv-cache <uv の cache の dir> [--uv <uv の path(既定 uv)>]
  stdout = 1 行「<組んだ|使った> <wheel の path>」(boot.sh が起動の行に載せ、wheel を venv へ入れる)。
  stderr = 組んだか使ったかと秒の 1 行(組む時は uv build の出力も)。組めない・鍵の材料を読めない時は非 0 で終わり、理由を stderr に 1 行。
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
from dataclasses import dataclass

from doeff_cluster.foundation.process_environ import child_environ
from doeff_cluster.shared.core import native_wheel

# 層 entry の文脈と役の名乗り(DOEFF104)— 隣の入口 shim.py と同じ。
MODULE_TAGS = {"context": "worker", "role": "main"}

# 鍵の材料の Python の版を読む file(root の根 — uv も venv を作る時にこれを読む)。
PYTHON_VERSION_FILE = ".python-version"


class BootWheelFailed(RuntimeError):
    """wheel を用意できない(鍵の材料を読めない・組めない)— 入口が理由を stderr に出して非 0 で終わる。"""


@dataclass(frozen=True)
class BootWheel:
    """用意した wheel: path = wheel の file・built = この起動で組んだ(置き場に無かった)。"""

    path: str
    built: bool


def tree_hashes(mirror: str, commit: str) -> tuple[str, ...]:
    """doeff-vm の wheel の中身を決める dir ごとの git の tree hash(native_wheel.DOEFF_VM_PATHS の順)。"""
    specs = [f"{commit}:{path}" for path in native_wheel.DOEFF_VM_PATHS]
    done = subprocess.run(["git", "-C", mirror, "rev-parse", *specs], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise BootWheelFailed(f"{commit} の doeff-vm の tree hash を読めない: {done.stderr.strip()}")
    return tuple(done.stdout.split())


def python_version(root: str) -> str:
    """root の .python-version の 1 行(前後の空白を除く — 実行環境の宣言の python と同じ綴り)。"""
    path = posixpath.join(root, PYTHON_VERSION_FILE)
    if not os.path.isfile(path):
        raise BootWheelFailed(f"{path} が無い(doeff-vm の wheel の鍵の材料)")
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


def boot_key(root: str, mirror: str, commit: str) -> str:
    """root の doeff-vm の wheel の鍵(worker の準備と同じ関数 native_wheel.native_key・同じ材料)。"""
    return native_wheel.native_key(
        native_wheel.DOEFF_VM_PACKAGE,
        native_wheel.DOEFF_VM_PATHS,
        tree_hashes(mirror, commit),
        python_version(root),
        native_wheel.current_platform(),
    )


def wheel_in(target: str) -> str | None:
    """dir の wheel の path(名の順の先頭 — worker の wheel-in と同じ・dir が無いか wheel が無ければ None)。"""
    if not os.path.isdir(target):
        return None
    wheels = sorted(name for name in os.listdir(target) if name.endswith(".whl"))
    return posixpath.join(target, wheels[0]) if wheels else None


def built_wheel(source_dir: str, target: str, state_dir: str, uv_cache: str, uv: str) -> str:
    """source_dir から wheel を途中の dir へ組み、target へ名を変えて置く。答え = 置いた wheel の path。uv の出力は stderr へ流す
    (stdout は wheel の path の 1 行だけ)。"""
    tmp = native_wheel.wheel_tmp(target)
    removed_tree(tmp)
    done = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", tmp, source_dir],
        cwd=source_dir,
        env=child_environ(native_wheel.UV_DROP, native_wheel.uv_environment(state_dir, uv_cache)),
        stdout=sys.stderr,
        check=False,
    )
    made = wheel_in(tmp)
    if done.returncode != 0 or made is None:
        removed_tree(tmp)
        raise BootWheelFailed(f"uv build が終了コード {done.returncode} で終わった({source_dir} — 出力は上)")
    os.rename(tmp, target)
    return posixpath.join(target, posixpath.basename(made))


def removed_tree(path: str) -> None:
    """書きかけの途中の dir を中身ごと消す(無ければ何もしない)。"""
    if os.path.isdir(path):
        shutil.rmtree(path)


def marked_used(target: str) -> None:
    """使った印(native_wheel.WHEEL_USED)を別名に書いてから置き換える — dir の mtime が進み、掃除(7 日使われない wheel の dir を消す)が
    消さない(worker の EnsureNativeWheel と同じ置き方)。"""
    handle, staged = tempfile.mkstemp(dir=target, prefix=".used.")
    os.close(handle)
    os.replace(staged, posixpath.join(target, native_wheel.WHEEL_USED))


def ensured_wheel(root: str, mirror: str, commit: str, state_dir: str, uv_cache: str, uv: str) -> BootWheel:
    """root の doeff-vm の wheel を鍵の置き場から用意する(在ればそれ・無ければ組んで置く)。錠(native_wheel.wheel_lock)を持つ間に
    見て・組んで・使った印を置く — 同じ鍵を組む worker の準備と重ならない。"""
    key = boot_key(root, mirror, commit)
    target = native_wheel.wheel_dir(state_dir, native_wheel.DOEFF_VM_PACKAGE, key)
    lock = native_wheel.wheel_lock(state_dir, key)
    os.makedirs(posixpath.dirname(lock), exist_ok=True)
    os.makedirs(posixpath.dirname(target), exist_ok=True)
    held = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(held, fcntl.LOCK_EX)
        existing = wheel_in(target)
        source_dir = posixpath.join(root, native_wheel.DOEFF_VM_PATHS[0])
        ready = (
            BootWheel(path=existing, built=False)
            if existing is not None
            else BootWheel(path=built_wheel(source_dir, target, state_dir, uv_cache, uv), built=True)
        )
        marked_used(target)
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)
    return ready


def main() -> None:
    """引数の root の doeff-vm の wheel を用意し、「<組んだ|使った> <path>」を stdout に 1 行出す。用意できなければ理由を stderr に出して
    終了コード 1 で終わる。"""
    parser = argparse.ArgumentParser(description="起動の root の doeff-vm の wheel を、実行環境の準備と同じ鍵の置き場から用意する")
    parser.add_argument("--root", required=True, help="doeff の root(展開した commit の木)")
    parser.add_argument("--mirror", required=True, help="doeff の bare repo(tree hash を読む)")
    parser.add_argument("--commit", required=True, help="root の commit")
    parser.add_argument("--state", required=True, help="state dir(wheels/・locks/・python/ を置く)")
    parser.add_argument("--uv-cache", required=True, help="uv の cache の dir(起動の script の DOEFF_UV_CACHE_DIR)")
    parser.add_argument("--uv", default="uv", help="uv の命令(既定 uv — PATH で引く)")
    args = parser.parse_args()
    started = time.monotonic()
    try:
        ready = ensured_wheel(args.root, args.mirror, args.commit, args.state, args.uv_cache, args.uv)
    except BootWheelFailed as failure:
        print(f"boot: doeff-vm の wheel を用意できない: {failure}", file=sys.stderr, flush=True)
        sys.exit(1)
    how = "組んだ" if ready.built else "使った"
    print(f"boot: doeff-vm の wheel を{how}({time.monotonic() - started:.1f} 秒・{ready.path})", file=sys.stderr, flush=True)
    print(f"{how} {ready.path}", flush=True)


if __name__ == "__main__":
    main()
