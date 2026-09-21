"""本線 7451fa17 の FsLinkArtifact に残る扉を、実 substrate の handler を駆動して測る。

測るのは 3 つ:
  競り    同じ敷設先へ 2 process が同拍で降りると、何が返り・何が raise するか
  器の断り 親が実体 file / 家が r-x / 祖父が r-x の 3 形で、4 値の語彙の外へ出るか
  直し方   「FileExistsError を合図に読み直す」形の試作が、同じ競りで語彙の中に収まるか

⚠ 試作(`fixed_link_artifact`)はこの file の中だけの写しで、出荷される code は 1 byte も変えない
   (計画段は code を触らない — 直すのは実装段)。
"""

import mmap
import os
import platform
import select
import signal
import sys
import tempfile
import time

import hy  # noqa: F401  (Hy の import hook)
from doeff import run
from doeff_agents.sessionhost import effects, substrate

HANDLER = substrate.real_substrate("tmux")
ROUNDS = int(os.environ.get("ROUNDS", "200"))


def ship(source_path, target_path):
    """出荷されている FsLinkArtifact を 1 回撃つ。raise は語彙の外として名乗る。"""
    try:
        return run(HANDLER(effects.fs_link_artifact(source_path, target_path)))
    except OSError as error:
        return f"RAISED:{type(error).__name__}"
    except Exception as error:  # noqa: BLE001 — 何が抜けるかを測るのが目的
        return f"RAISED:{type(error).__name__}"


def fixed_link_artifact(source_path, target_path):
    """試作: 「見てから張る」を判断の座にせず、FileExistsError を合図に読み直す。

    約束は出荷と同じ「据わっている物を絶対に置き換えない」なので、原子の 1 手は rename ではなく
    os.symlink そのもの。既存の 4 値だけで閉じる(語彙を 1 つも足さない)。
    """
    if not (os.path.exists(source_path) or os.path.islink(source_path)):
        return "source-missing"

    def seated_verdict():
        same = False
        try:
            same = os.path.samefile(target_path, source_path)
        except OSError:
            pass
        return "same-entity" if same else "target-conflict"

    parent = os.path.dirname(target_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        os.symlink(source_path, target_path)
    except FileExistsError:
        # 見た後に何かが現れた(相手が張った / 実体が据わった)— 改めて読んで名乗る
        return seated_verdict()
    return "linked"


def fixed(source_path, target_path):
    try:
        return fixed_link_artifact(source_path, target_path)
    except OSError as error:
        return f"RAISED:{type(error).__name__}"
    except Exception as error:  # noqa: BLE001
        return f"RAISED:{type(error).__name__}"


# ---------------------------------------------------------------------------
# 同拍の競り(2 process・lock を 1 つも取らない — 子の中の lock は fork を越えて固まる)
# ---------------------------------------------------------------------------


def spin_barrier(shared, index, total, deadline):
    shared[index] = 1
    while any(shared[i] == 0 for i in range(total)):
        if time.monotonic() > deadline:
            return False
    return True


def child_installs(shared, index, source, target, write_fd, which):
    try:
        spin_barrier(shared, index, 2, time.monotonic() + 30)
        verb = ship if which == "ship" else fixed
        os.write(write_fd, verb(source, target).encode("utf-8"))
    except BaseException as error:  # noqa: BLE001
        try:
            os.write(write_fd, f"RAISED:{type(error).__name__}".encode("utf-8"))
        except OSError:
            pass
    finally:
        os._exit(0)


def read_line(read_fd, deadline):
    chunks = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "timeout"
        ready = select.select([read_fd], [], [], remaining)[0]
        if not ready:
            return "timeout"
        chunk = os.read(read_fd, 256)
        if not chunk:
            return "".join(chunks)
        chunks.append(chunk.decode("utf-8"))


def reap(pids, deadline):
    for pid in pids:
        while True:
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done != 0:
                break
            if time.monotonic() > deadline:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
                os.waitpid(pid, 0)
                break
            time.sleep(0.001)


def race(base, which):
    source = os.path.join(base, f"{which}-src.jsonl")
    with open(source, "w") as handle:
        handle.write('{"type":"summary"}\n')
    tally = {}
    wrong = []
    for round_index in range(ROUNDS):
        target_project = os.path.join(base, f"{which}-proj-{round_index}")
        os.makedirs(target_project)
        target = os.path.join(target_project, "sessions-index.json")
        shared = mmap.mmap(-1, 2)
        shared[0] = 0
        shared[1] = 0
        read_fds = []
        pids = []
        for index in range(2):
            read_fd, write_fd = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(read_fd)
                child_installs(shared, index, source, target, write_fd, which)
            os.close(write_fd)
            read_fds.append(read_fd)
            pids.append(pid)
        deadline = time.monotonic() + 60
        for read_fd in read_fds:
            line = read_line(read_fd, deadline)
            os.close(read_fd)
            tally[line] = tally.get(line, 0) + 1
        reap(pids, deadline)
        shared.close()
        if not (os.path.islink(target) and os.readlink(target) == source):
            wrong.append(round_index)
    return tally, wrong


# ---------------------------------------------------------------------------
# 器の断り(扉 1 と同型の 3 形)
# ---------------------------------------------------------------------------


def refusals(base, label, verb):
    print(f"-- 器の断り({label})--")
    source = os.path.join(base, f"{label}-src.jsonl")
    with open(source, "w") as handle:
        handle.write("x")

    home = os.path.join(base, f"{label}-a-home")
    with open(home, "w") as handle:
        handle.write("operator の物")
    print(f"  親の位置に実体 file  → {verb(source, os.path.join(home, 'sessions-index.json'))}")

    home = os.path.join(base, f"{label}-b-home")
    os.makedirs(home)
    os.chmod(home, 0o500)
    try:
        print(f"  家が書けない(r-x)   → {verb(source, os.path.join(home, 'sessions-index.json'))}")
    finally:
        os.chmod(home, 0o700)

    grand = os.path.join(base, f"{label}-c-grand")
    os.makedirs(grand)
    os.chmod(grand, 0o500)
    try:
        target = os.path.join(grand, "projects", "sessions-index.json")
        print(f"  親 dir を作れない    → {verb(source, target)}")
    finally:
        os.chmod(grand, 0o700)


def main():
    base = os.path.realpath(tempfile.mkdtemp(prefix="link-artifact-doors-"))
    print(f"# 機体 {platform.node()} / {platform.system()} {platform.release()} / python {sys.version.split()[0]}")
    print(f"# 検体 = 本線 7451fa17 の substrate.hy FsLinkArtifact(実 handler を駆動)")
    print(f"# 回数 = {ROUNDS} 回 × 2 process / 作業 dir = {base}")
    print()
    for which, label in (("ship", "出荷"), ("fixed", "試作")):
        tally, wrong = race(base, which)
        print(f"-- 同拍の競り({label}・{ROUNDS} 回 × 2 席 = {ROUNDS * 2} 呼び出し)--")
        for key in sorted(tally):
            print(f"  {key:24s} {tally[key]:5d}  ({100.0 * tally[key] / (ROUNDS * 2):.1f} %)")
        print(f"  根が正しい先を指さない回: {len(wrong)}")
        print()
    refusals(base, "ship", ship)
    print()
    refusals(base, "fixed", fixed)


if __name__ == "__main__":
    main()
