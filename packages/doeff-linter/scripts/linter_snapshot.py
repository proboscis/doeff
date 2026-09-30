#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""doeff の 1 つの commit から組んだ doeff-linter の置き場 — 機体で sha ごとに 1 度だけ組む(agora-redesign #1582・#2001)。

各 repo(agora-controllers・merge-queue ほか)の commit の hook が、組み立ての論理を写して持たずにこの 1 か所を呼ぶ。標準ライブラリ
だけで書き(PEP 723 の依存は空)、repo の Python の環境が無くても `uv run --script` で動く。元の実装 = agora-controllers の
`scripts/doeff_linter_snapshot.hy`(#1582)。

何が壊れていたか(#1582): hook が repo の uv の環境の doeff-linter を使うと、その環境は兄弟の `../doeff`(共有の checkout)への path
の依存で、共有の checkout が進むたびに commit する全席が同じ sdist を組み直して同じ lock に並び、fast-forward の途中の file を cargo が
読むと新旧が混ざった。build の入力が「動く checkout」だった。

何をするか:
  * 入力 = doeff の git の object から `git archive <sha>` で取り出した断面(packages/doeff-linter と、その path 依存
    packages/doeff-indexer)。作業木の file を読まないので、fast-forward の途中でも混ざらない。
  * 置き場 = `<store>/<40 桁の sha>/doeff-linter`(store の既定 = $XDG_CACHE_HOME/doeff-linter-snapshots、無ければ ~/.cache の下。
    env DOEFF_LINTER_SNAPSHOT_DIR で替える)。在れば組まずに返す。
  * 錠 = `<store>/<sha>.lock` の flock — path ではなく sha の単位。同じ sha を組む 2 本目は 1 本目を待ち、組まれた物を使う。
  * 組み方 = `cargo build --release --locked`。cargo の target は一時の dir に作り、組んだ後に消す(置き場には binary だけ)。
    build.rs は断面に .git が無いので env DOEFF_LINTER_BUILD_COMMIT に 40 桁の sha を渡す。組んだ binary の `--version` がその sha
    ちょうど(+dirty なし)を名乗る事を確かめてから、一時の名から rename で置く。
  * git に渡す env から GIT_ で始まる名を外す — 別の repo の git の hook の中で呼ばれると、git が渡す GIT_DIR などが `-C` より勝ち、
    その repo の object を読む(agora-redesign #1481・#1517)。

使い方: uv run --script packages/doeff-linter/scripts/linter_snapshot.py <doeff の checkout> <commit>
  組めれば binary の path を 1 行印字して 0、組めなければ理由を stderr に 1 行出して 1(呼び手は自分の環境の linter へ戻る)、
  引数の誤りは 2。
検 = tests/test_linter_snapshot.py(同時の 3 本が 1 度だけ組む・作業木の変更を読まない・sha を名乗らない linter を置かない)。
"""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

# 断面に取り出す dir(doeff-linter と、その Cargo.toml の path 依存)。
INPUT_DIRS = ("packages/doeff-linter", "packages/doeff-indexer")
MANIFEST = "packages/doeff-linter/Cargo.toml"
BIN_NAME = "doeff-linter"
STORE_ENV = "DOEFF_LINTER_SNAPSHOT_DIR"
BUILD_COMMIT_ENV = "DOEFF_LINTER_BUILD_COMMIT"
# release・lto の組み立ては冷えた target で 3〜5 分(2026-09-29 zeus の実測 156〜266 秒)。
BUILD_TIMEOUT_S = 1800
# linter の `--version` の doeff の commit の綴り(build.rs の DOEFF_LINTER_COMMIT)。
COMMIT_PATTERN = re.compile(r"\(doeff ([0-9a-f]{40})(\+dirty)?\)")


@dataclass(frozen=True)
class SnapshotReady:
    """sha の断面から組んだ linter の path と、この呼び出しが組んだか(False = 置き場に在った物)。"""

    path: Path
    built: bool


@dataclass(frozen=True)
class SnapshotUnavailable:
    """置き場の linter を用意できなかった理由(1 行)。"""

    reason: str


def git_environ(environ: dict[str, str]) -> dict[str, str]:
    """doeff を読む git に渡す env — 呼び手の env から GIT_ で始まる名を外す(頭の註)。"""
    return {name: value for name, value in environ.items() if not name.startswith("GIT_")}


def snapshot_store(environ: dict[str, str]) -> Path:
    """置き場の dir を env から決める(DOEFF_LINTER_SNAPSHOT_DIR・XDG_CACHE_HOME・HOME の順)。"""
    given = environ.get(STORE_ENV, "")
    if given:
        return Path(given)
    cache = environ.get("XDG_CACHE_HOME", "")
    base = Path(cache) if cache else Path(environ.get("HOME", "~")).expanduser() / ".cache"
    return base / "doeff-linter-snapshots"


def names_commit(printed: str, sha: str) -> bool:
    """linter の `--version` の印字が sha ちょうど(手元の変更の印 +dirty なし)を名乗るか。"""
    found = COMMIT_PATTERN.search(printed)
    return bool(found and found.group(1) == sha and not found.group(2))


def resolve_commit(checkout: Path, rev: str, env: dict[str, str]) -> str | None:
    """doeff の git で rev を 40 桁の commit の sha へ解く(無ければ None)。"""
    done = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "--verify", "-q", rev + "^{commit}"],
        capture_output=True, text=True, check=False, env=env,
    )
    return done.stdout.strip() if done.returncode == 0 else None


def extract_inputs(checkout: Path, sha: str, dest: Path, env: dict[str, str]) -> str | None:
    """doeff の git の object から sha の断面(INPUT_DIRS)を dest へ取り出す。失敗の理由を返す(None = 取り出せた)。"""
    proc = subprocess.Popen(
        ["git", "-C", str(checkout), "archive", "--format=tar", sha, *INPUT_DIRS],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    assert proc.stdout is not None and proc.stderr is not None
    with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
        archive.extractall(dest, filter="data")
    err = proc.stderr.read()
    if proc.wait() == 0:
        return None
    return "git archive が落ちた: " + err.decode("utf-8", "replace").strip()


def find_cargo(env: dict[str, str]) -> str | None:
    """PATH の cargo、無ければ ~/.cargo/bin/cargo。"""
    on_path = shutil.which("cargo", path=env.get("PATH", os.defpath))
    if on_path:
        return on_path
    home_cargo = Path(env.get("HOME", "~")).expanduser() / ".cargo" / "bin" / "cargo"
    return str(home_cargo) if home_cargo.exists() else None


def build_into(source: Path, sha: str, target: Path, env: dict[str, str]) -> str | None:
    """断面を cargo で組み、組んだ binary を target(一時の名)へ写す。失敗の理由を返す(None = 組めた)。"""
    cargo = find_cargo(env)
    if not cargo:
        return "cargo が無い"
    cargo_target = source / "cargo-target"
    done = subprocess.run(
        [cargo, "build", "--release", "--locked", "--manifest-path", str(source / MANIFEST)],
        capture_output=True, text=True, check=False, timeout=BUILD_TIMEOUT_S,
        env={**env, "CARGO_TARGET_DIR": str(cargo_target), BUILD_COMMIT_ENV: sha},
    )
    if done.returncode != 0:
        last = (done.stderr.strip().splitlines() or [""])[-1]
        return "cargo build が落ちた: " + last
    shutil.copy2(cargo_target / "release" / BIN_NAME, target)
    printed = subprocess.run([str(target), "--version"], capture_output=True, text=True, check=False).stdout or ""
    if names_commit(printed, sha):
        return None
    return "組んだ linter が " + sha + " を名乗らない: " + printed.strip()


def snapshot_linter(checkout: Path, rev: str, environ: dict[str, str]) -> SnapshotReady | SnapshotUnavailable:
    """doeff の rev の断面から組んだ linter を置き場から返す — 無ければ sha の錠を取って 1 度だけ組む(頭の註)。"""
    env = git_environ(environ)
    sha = resolve_commit(checkout, rev, env)
    if not sha:
        return SnapshotUnavailable("doeff の " + str(checkout) + " に commit " + rev + " が無い")
    store = snapshot_store(environ)
    binary = store / sha / BIN_NAME
    try:
        store.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return SnapshotUnavailable("置き場 " + str(store) + " を作れない: " + str(error))
    with open(store / (sha + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if binary.exists():
            return SnapshotReady(binary, built=False)
        work = Path(tempfile.mkdtemp(prefix="doeff-linter-snapshot-" + sha[:12] + "-"))
        try:
            partial = store / (sha + ".partial")
            failure = extract_inputs(checkout, sha, work, env) or build_into(work, sha, partial, env)
            if failure:
                return SnapshotUnavailable(failure)
            (store / sha).mkdir(exist_ok=True)
            os.replace(partial, binary)
            return SnapshotReady(binary, built=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str]) -> int:
    """組めれば path を印字して 0、組めなければ理由を 1 行出して 1、引数の誤りは 2。"""
    if len(argv) != 2:
        print("使い方: uv run --script linter_snapshot.py <doeff の checkout> <commit>", file=sys.stderr)
        return 2
    got = snapshot_linter(Path(argv[0]), argv[1], dict(os.environ))
    if isinstance(got, SnapshotReady):
        print(got.path)
        return 0
    print("doeff-linter-snapshot: " + got.reason, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
