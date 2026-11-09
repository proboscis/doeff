"""準備 1 つを process の木 1 つに束ねる検(deploy/boot.sh の keeper_swap・keeper_group_open・keeper_group_close と、worker の
worker/core/job_cgroup の GROUP-SCRIPT・CLOSE-SCRIPT)。

実例(11-08 の k3s-0・k3s-2): keeper の版の準備を準備の層(memory.max 640M)に直に置いた。uv の build hook の頭が OOM kill で止まり、
残った multiprocessing の forkserver と子(ppid 1)が uv の出力の pipe を持ち続け、uv は EOF を待って返らず、keeper は準備中と読み
続けた(25 時間)。孤児は層の memory も持ち続け、worker の実行環境の準備の空きも奪った。

直した形: 準備の 1 本ずつを準備の層の下の自分の子の cgroup(memory.oom.group=1)で起こす。層の上限での OOM kill はその準備の木を
丸ごと止め、準備の頭が終わったら(成功・失敗とも)起こした側が子の cgroup.kill で残りを止め、空になってから子を消す。

確かめ方: 本物の cgroup v2 の委ねられた層(DOEFF_WORKER_CGROUP_DIR — 子に memory を掛けた層の根)の下に検の組を作り、その下に上限
48M の準備の層を置く。無い機体では skip。keeper は boot.sh から関数を抜き出して sh で走らせ、役 prepare の script は孤児を残す偽物。
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from doeff_cluster._worker.core.job_cgroup import closing_argv, grouped_argv

from doeff import run

BOOT = Path(__file__).resolve().parents[1] / "deploy" / "boot.sh"
FUNCTIONS = (
    "keeper_log",
    "keeper_short",
    "keeper_alive",
    "keeper_mtime",
    "keeper_layer",
    "keeper_room",
    "keeper_swap",
    "keeper_group_open",
    "keeper_group_close",
)
VERSION = "c" * 40
LAYER_MAX = "48M"
# 準備が終わるまで待つ秒の上限。直す前の keeper は孤児が pipe を閉じるまで(偽物では 600 秒)返らないので、この秒で落ちる。
SETTLE_SECONDS = 60


def _delegated() -> Path | None:
    """本物の検を走らせられる書ける委ねられた cgroup(子に memory を掛けた層の根)。無ければ None。"""
    named = os.environ.get("DOEFF_WORKER_CGROUP_DIR")
    if not named or not os.access(os.path.join(named, "cgroup.procs"), os.W_OK):
        return None
    if "memory" not in Path(named, "cgroup.subtree_control").read_text(encoding="utf-8").split():
        return None
    return Path(named)


# 主題は本物の kernel の cgroup(OOM kill・cgroup.kill)と、外の process(keeper の sh・準備の子孫)の退きの実時間(tests/realtime_tests.toml)。
pytestmark = pytest.mark.realtime
DELEGATED = _delegated()
real_cgroup = pytest.mark.skipif(
    DELEGATED is None,
    reason="DOEFF_WORKER_CGROUP_DIR の書ける委ねられた cgroup が無い(層を作った worker の子だけで走る)",
)


def _populated(group: Path) -> bool:
    """cgroup の子孫に process が居るか(cgroup.events の populated)。"""
    return "populated 1" in (group / "cgroup.events").read_text(encoding="utf-8").splitlines()


def _close(group: Path) -> None:
    """検の組を子孫ごと止めて消す(深い子から rmdir)。ポーリングの訳: 子孫の退きの知らせは cgroup.events の変更だけ。"""
    if not group.exists():
        return
    (group / "cgroup.kill").write_text("1", encoding="utf-8")
    emptied = time.monotonic() + 5
    while _populated(group) and time.monotonic() < emptied:
        time.sleep(0.01)
    for child in sorted(
        (p for p in group.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
    ):
        child.rmdir()
    group.rmdir()


@pytest.fixture
def layer() -> Iterator[Path]:
    """委ねられた cgroup の下の検の組(子に memory を渡す)と、その下の上限 LAYER_MAX の準備の層 prepare。答え = 準備の層の dir。"""
    assert DELEGATED is not None
    group = DELEGATED / f"doeff-test-prepare-{os.getpid()}"
    group.mkdir()
    try:
        (group / "cgroup.subtree_control").write_text("+memory", encoding="utf-8")
        prepare = group / "prepare"
        prepare.mkdir()
        (prepare / "memory.max").write_text(LAYER_MAX, encoding="utf-8")
        (prepare / "memory.swap.max").write_text("0", encoding="utf-8")
        yield prepare
    finally:
        _close(group)


def _gone(pid: int) -> bool:
    """pid の process が居ないか(回収を待つ zombie も居ない側に数える)。"""
    try:
        state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return True
    return state in ("Z", "X")


def _settled(pid: int) -> bool:
    """孤児が SIGKILL の後に退くまで(5 秒まで)待ち、居なくなったか。ポーリングの訳: 回収は PID 1 で、検に知らせが無い。"""
    until = time.monotonic() + 5
    while not _gone(pid) and time.monotonic() < until:
        time.sleep(0.01)
    return _gone(pid)


# --- keeper の版の準備 ---------------------------------------------------------------------------------------------


def _functions() -> str:
    """boot.sh の keeper の関数の定義(本物の文のまま)。"""
    text = BOOT.read_text(encoding="utf-8")
    bodies = []
    for name in FUNCTIONS:
        found = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
        assert found, f"boot.sh に関数 {name} が無い"
        bodies.append(found.group(0))
    return "\n".join(bodies)


# 偽の役 prepare の頭: 自分の cgroup と、自分の子の memory.oom.group を書き残す。
HEAD = """#!/bin/sh
cat /proc/self/cgroup >"$OBS/cgroup"
cat "$DOEFF_WORKER_CGROUP_DIR/prepare/keeper-$WORKER_DOEFF_COMMIT/memory.oom.group" >"$OBS/oom-group" 2>/dev/null || :
"""
# 偽の役 prepare(OOM): uv が build hook の出力を読むように、子の出力を $(…) で読む。子の中で、出力の pipe を継いだ孤児(sleep — 11-08 の
# forkserver の代わり)を残し、memory を際限なく増やす子(tail — build hook の頭の代わり)を層の上限で OOM にする。
OOM_PREPARE = (
    HEAD
    + """out=$( (sleep 600 & echo $! >"$OBS/orphan") ; head -c 4G /dev/zero | tail )
echo "$OBS/root"
"""
)
# 偽の役 prepare(成功): 出力を継がない孤児(sleep)を残して root の path を出す。
DONE_PREPARE = (
    HEAD
    + """sleep 600 </dev/null >/dev/null 2>&1 &
echo $! >"$OBS/orphan"
echo "$OBS/root"
"""
)


def _keeper_prepares(
    tmp_path: Path, prepare: Path, script: str
) -> subprocess.CompletedProcess[str]:
    """keeper_swap を版 VERSION で 2 回呼び(起こす拍と、失敗の印の後の拍)、背後の準備が終わるのを待つ。答え = keeper の走り。"""
    obs = tmp_path / "obs"
    obs.mkdir()
    fake = tmp_path / "fake-boot.sh"
    fake.write_text(script, encoding="utf-8")
    (tmp_path / "keeper" / "prepare").mkdir(parents=True)
    probe = f"""set -eu
{_functions()}
keeper_dir={tmp_path}/keeper
keeper_script={fake}
target={"b" * 40}
prepare_waiting=""
keeper_swap "{VERSION}"
while keeper_alive "$(cat "$keeper_dir/prepare/{VERSION}/pid")"; do sleep 0.05; done
keeper_swap "{VERSION}"
"""
    # 背後の準備は keeper の子から外れる(wait で待てない)。ポーリングの訳: keeper も準備の pid の生死を tick ごとに読む(keeper_swap)。
    env = {
        "PATH": "/usr/bin:/bin",
        "OBS": str(obs),
        "DOEFF_WORKER_CGROUP_DIR": str(prepare.parent),
        "DOEFF_WORKER_JOB_CGROUP": "jobs",
        "DOEFF_WORKER_PREPARE_CGROUP": "prepare",
    }
    # stdout と stderr は file に受ける — 孤児が pipe を継いで run を止めないように。
    with (
        open(tmp_path / "keeper.out", "w+", encoding="utf-8") as out,
        open(tmp_path / "keeper.err", "w+", encoding="utf-8") as err,
    ):
        done = subprocess.run(
            ["/bin/sh", "-c", probe],
            env=env,
            stdout=out,
            stderr=err,
            timeout=SETTLE_SECONDS,
            check=False,
        )
        err.seek(0)
        return subprocess.CompletedProcess(done.args, done.returncode, "", err.read())


@real_cgroup
def test_an_oom_in_the_keeper_prepare_stops_the_whole_tree_and_the_keeper_reads_a_failure(
    tmp_path: Path, layer: Path
) -> None:
    """準備の木の 1 本が層の上限で OOM kill になり、出力の pipe を継いだ孤児が残る形(11-08 の k3s-0・k3s-2)。keeper の準備は版の子
    (memory.oom.group=1)で走り、kernel は木を丸ごと止め、keeper は失敗と読んで撃ち直しの待ちに入る(次の拍でも準備を起こし直さない)。
    子は消え、層に process は残らない。直す前は孤児が pipe を持ち続け、準備も keeper も返らない(SETTLE_SECONDS で落ちる)。"""
    ran = _keeper_prepares(tmp_path, layer, OOM_PREPARE)
    obs = tmp_path / "obs"
    prep = tmp_path / "keeper" / "prepare" / VERSION
    assert ran.returncode == 0, ran.stderr
    assert (prep / "failed").exists(), ran.stderr
    assert not (prep / "root").exists()
    assert "の root を準備できない — 300 秒後に撃ち直す" in ran.stderr, ran.stderr
    assert ran.stderr.count("その root を準備する") == 1, ran.stderr
    assert (
        (obs / "cgroup").read_text(encoding="utf-8").strip().endswith(f"/prepare/keeper-{VERSION}")
    )
    assert (obs / "oom-group").read_text(encoding="utf-8").strip() == "1"
    assert _settled(int((obs / "orphan").read_text(encoding="utf-8")))
    assert not (layer / f"keeper-{VERSION}").exists()
    assert not _populated(layer)
    assert (
        int(
            dict(line.split() for line in (layer / "memory.events").read_text().splitlines())[
                "oom_kill"
            ]
        )
        >= 1
    )


@real_cgroup
def test_after_the_keeper_prepare_succeeds_its_group_is_emptied_and_removed(
    tmp_path: Path, layer: Path
) -> None:
    """準備の頭が root の path を出して終わった後、頭の外で生き残った子孫(孤児)を止めて版の子を消す。root は読める。"""
    ran = _keeper_prepares(tmp_path, layer, DONE_PREPARE)
    obs = tmp_path / "obs"
    prep = tmp_path / "keeper" / "prepare" / VERSION
    assert ran.returncode == 0, ran.stderr
    assert (prep / "root").read_text(encoding="utf-8").strip() == f"{obs}/root", ran.stderr
    assert (
        (obs / "cgroup").read_text(encoding="utf-8").strip().endswith(f"/prepare/keeper-{VERSION}")
    )
    assert _settled(int((obs / "orphan").read_text(encoding="utf-8")))
    assert not (layer / f"keeper-{VERSION}").exists()
    assert (layer / "cgroup.procs").read_text(encoding="utf-8") == ""
    assert not _populated(layer)


# --- worker の実行環境の準備 ---------------------------------------------------------------------------------------


def _wrapped(group: Path, command: str) -> list[str]:
    """worker と同じ包み(grouped-argv)で sh の命令を包んだ argv。"""
    return list(run(grouped_argv(str(group), ("/bin/sh", "-c", command))))


def _closed(group: Path) -> subprocess.CompletedProcess[str]:
    """worker と同じ止め(closing-argv)を撃つ。"""
    argv = list(run(closing_argv(str(group))))
    return subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)


@real_cgroup
def test_a_worker_prepare_runs_in_its_own_oom_group_and_its_leftovers_go_with_the_group(
    layer: Path,
) -> None:
    """worker の準備の頭は層の下の自分の子(memory.oom.group=1)で走る。頭が終わった後も子孫が子に残り、止め(CLOSE-SCRIPT)が残りを
    止めて子を消す — 層の cgroup.procs は空のまま。"""
    group = layer / "env-test-done"
    with tempfile.TemporaryDirectory() as obs:
        head = _wrapped(
            group,
            f'cat /proc/self/cgroup >{obs}/cgroup; cat "$(dirname $0)/memory.oom.group" 2>/dev/null; '
            f"sleep 600 </dev/null >/dev/null 2>&1 & echo $! >{obs}/orphan",
        )
        done = subprocess.run(head, capture_output=True, text=True, timeout=30, check=False)
        assert done.returncode == 0, done.stderr
        assert (
            Path(obs, "cgroup")
            .read_text(encoding="utf-8")
            .strip()
            .endswith("/prepare/env-test-done")
        )
        assert (group / "memory.oom.group").read_text(encoding="utf-8").strip() == "1"
        orphan = int(Path(obs, "orphan").read_text(encoding="utf-8"))
        assert (group / "cgroup.procs").read_text(encoding="utf-8").split() == [str(orphan)]
        assert (layer / "cgroup.procs").read_text(encoding="utf-8") == ""
        closed = _closed(group)
        assert closed.returncode == 0, closed.stderr
        assert _settled(orphan)
        assert not group.exists()
        assert not _populated(layer)


@real_cgroup
def test_an_oom_in_a_worker_prepare_stops_the_whole_tree_and_releases_the_output_pipe(
    layer: Path,
) -> None:
    """worker の準備の木の 1 本が層の上限で OOM kill になっても、kernel は子ごと全部止め、出力の pipe を継いだ孤児も残らない — 頭を
    pipe で読む側(ここでは run の capture)が返る。止めの後、子は消える。"""
    group = layer / "env-test-oom"
    with tempfile.TemporaryDirectory() as obs:
        head = _wrapped(group, f"(sleep 600 & echo $! >{obs}/orphan); head -c 4G /dev/zero | tail")
        done = subprocess.run(
            head, capture_output=True, text=True, timeout=SETTLE_SECONDS, check=False
        )
        assert done.returncode != 0, done
        assert _settled(int(Path(obs, "orphan").read_text(encoding="utf-8")))
        closed = _closed(group)
        assert closed.returncode == 0, closed.stderr
        assert not group.exists()
        assert not _populated(layer)


@real_cgroup
def test_a_leftover_group_of_the_same_prepare_is_stopped_before_the_next_prepare(
    layer: Path,
) -> None:
    """worker が止まって残した同じ名の子(前の準備の孤児が居る)は、次の準備の包みが止めて消してから作り直す。"""
    group = layer / "env-test-again"
    with tempfile.TemporaryDirectory() as obs:
        first = _wrapped(group, f"sleep 600 </dev/null >/dev/null 2>&1 & echo $! >{obs}/orphan")
        assert (
            subprocess.run(
                first, capture_output=True, text=True, timeout=30, check=False
            ).returncode
            == 0
        )
        orphan = int(Path(obs, "orphan").read_text(encoding="utf-8"))
        second = subprocess.run(
            _wrapped(group, "cat /proc/self/cgroup"),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert second.returncode == 0, second.stderr
        assert second.stdout.strip().endswith("/prepare/env-test-again")
        assert _settled(orphan)
        assert _closed(group).returncode == 0
