"""keeper の版の準備の置き場と待ち(deploy/boot.sh の keeper_swap・keeper_layer・keeper_room)の検。

実例(11-02): k3s-1 の keeper が準備を jobs の層で起こし、worker が追い出しの線から縮めた jobs の上限(61〜139 MiB)で bytecode の焼きが
OOM になり、300 秒ごとの撃ち直しも同じだった。直した keeper は準備を準備の層(DOEFF_WORKER_PREPARE_CGROUP — worker が縮めない)で
起こし、worker が書く準備の空き($keeper_dir/prepare-room の「<空き MiB> <準備が線へ足しうる量 MiB>」)がその量に足りない間は準備を起こさずに
待ち、その訳を log に 1 度名指す。

確かめ方: boot.sh から keeper の関数を抜き出して sh で走らせる。cgroup の dir は tmp の dir(cgroup.procs は素の file — 移した pid が
書かれる)、役 prepare の script は root の path を出すだけの偽物。
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

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
VERSION = "a" * 40
WAIT_WORDS = "の root の準備を待つ — 追い出しの線までの空きが無い"


def _functions() -> str:
    """boot.sh の keeper の関数の定義(本物の文のまま)。"""
    text = BOOT.read_text(encoding="utf-8")
    bodies = []
    for name in FUNCTIONS:
        found = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
        assert found, f"boot.sh に関数 {name} が無い"
        bodies.append(found.group(0))
    return "\n".join(bodies)


def _world(tmp_path: Path, *, prepare_layer: bool) -> Path:
    """keeper の状態の dir・cgroup の層の dir・偽の役 prepare の script を置く。答え = 偽の cgroup の dir。"""
    cgroup = tmp_path / "cgroup"
    for layer in ("jobs", "prepare") if prepare_layer else ("jobs",):
        (cgroup / layer).mkdir(parents=True)
        (cgroup / layer / "cgroup.procs").write_text("", encoding="utf-8")
    (tmp_path / "keeper" / "prepare").mkdir(parents=True)
    script = tmp_path / "fake-boot.sh"
    script.write_text(f'#!/bin/sh\necho "boot: 準備した" >&2\necho "{tmp_path}/root"\n', encoding="utf-8")
    return cgroup


def _swap(tmp_path: Path, cgroup: Path, times: int = 1) -> str:
    """keeper_swap を版 VERSION で times 回呼び、背後の準備が終わるのを待つ(偽の script は即座に終わる)。答え = keeper の log。"""
    calls = "\n".join(f'keeper_swap "{VERSION}"' for _ in range(times))
    probe = f"""set -eu
{_functions()}
keeper_dir={tmp_path}/keeper
keeper_script={tmp_path}/fake-boot.sh
target={"b" * 40}
prepare_waiting=""
{calls}
wait
"""
    env = {
        "PATH": "/usr/bin:/bin",
        "DOEFF_WORKER_CGROUP_DIR": str(cgroup),
        "DOEFF_WORKER_JOB_CGROUP": "jobs",
        "DOEFF_WORKER_PREPARE_CGROUP": "prepare",
    }
    # 背後の準備は keeper の stderr を継ぐので、run は準備が終わって pipe が閉じるまで返らない。
    done = subprocess.run(["/bin/sh", "-c", probe], env=env, capture_output=True, text=True, timeout=30, check=True)
    return done.stderr


def _room(tmp_path: Path, text: str, age_seconds: int = 0) -> None:
    """worker の書く準備の空きの file を置く(age_seconds 秒前に書いた形)。"""
    room = tmp_path / "keeper" / "prepare-room"
    room.write_text(text, encoding="utf-8")
    then = time.time() - age_seconds
    os.utime(room, (then, then))


def test_a_short_room_keeps_the_keeper_waiting_and_names_the_shortage_once(tmp_path: Path) -> None:
    """空き(280 MiB)が準備の足しうる量(640 MiB)に足りない間、keeper は準備を起こさず(OOM で殺される準備を起こさない)、空きの不足を
    数で名指す行を 1 度だけ出す(tick ごとに繰り返さない)。"""
    cgroup = _world(tmp_path, prepare_layer=True)
    _room(tmp_path, "280 640\n")
    log = _swap(tmp_path, cgroup, times=3)
    assert log.count(WAIT_WORDS) == 1, log
    assert "空き − 余裕 = 280 MiB < 準備の層 prepare の足しうる量 640 MiB" in log, log
    assert not (tmp_path / "keeper" / "prepare" / VERSION).exists()
    assert (cgroup / "prepare" / "cgroup.procs").read_text(encoding="utf-8") == ""


def test_enough_room_starts_the_prepare_in_the_prepare_layer_not_the_jobs_layer(tmp_path: Path) -> None:
    """空き(720 MiB — k3s-1 の平常)が足しうる量以上なら準備を起こし、準備の process を準備の層の下のその版の子(memory.oom.group=1)へ移す
    (jobs の層へも、層そのものへも移さない — 本物の cgroup での止めと消しは test_prepare_group.py)。"""
    cgroup = _world(tmp_path, prepare_layer=True)
    _room(tmp_path, "720 640\n")
    log = _swap(tmp_path, cgroup)
    assert WAIT_WORDS not in log, log
    assert (tmp_path / "keeper" / "prepare" / VERSION / "root").read_text(encoding="utf-8").strip() == f"{tmp_path}/root"
    group = cgroup / "prepare" / f"keeper-{VERSION}"
    assert (group / "cgroup.procs").read_text(encoding="utf-8").strip().isdigit()
    assert (group / "memory.oom.group").read_text(encoding="utf-8").strip() == "1"
    assert (group / "cgroup.kill").read_text(encoding="utf-8").strip() == "1"
    assert (cgroup / "prepare" / "cgroup.procs").read_text(encoding="utf-8") == ""
    assert (cgroup / "jobs" / "cgroup.procs").read_text(encoding="utf-8") == ""


def test_the_keeper_compares_the_room_with_what_the_prepare_can_add_not_with_the_whole_cap(tmp_path: Path) -> None:
    """process の居ない名残だけの層(k3s-1 の 11-09 — 上限 640 MiB・名残 288 MiB)では、worker の書く第 2 の数は足しうる量 352 MiB で、
    空き 400 MiB ≥ 352 MiB の keeper は準備を起こす。失敗ケース: 上限 640 を丸ごと求めると、線の使いに数え済みの名残を 2 度数えて待ち続ける。"""
    cgroup = _world(tmp_path, prepare_layer=True)
    _room(tmp_path, "400 352\n")
    log = _swap(tmp_path, cgroup)
    assert WAIT_WORDS not in log, log
    assert (tmp_path / "keeper" / "prepare" / VERSION / "root").exists()


def test_a_stale_or_missing_room_file_does_not_hold_the_prepare(tmp_path: Path) -> None:
    """古い file(線を読めなくなった worker)と、file を書かない世代(準備の層を知らない版)は待たせない — 前と同じく準備を起こす。"""
    cgroup = _world(tmp_path, prepare_layer=True)
    _room(tmp_path, "280 640\n", age_seconds=120)
    log = _swap(tmp_path, cgroup)
    assert WAIT_WORDS not in log, log
    assert (tmp_path / "keeper" / "prepare" / VERSION / "root").exists()


def test_a_node_without_the_prepare_layer_prepares_in_the_jobs_layer_as_before(tmp_path: Path) -> None:
    """準備の層を宣言しない機体では、準備の空きの file を読まず、前と同じく jobs の層で準備する。"""
    cgroup = _world(tmp_path, prepare_layer=False)
    _room(tmp_path, "280 640\n")
    log = _swap(tmp_path, cgroup)
    assert WAIT_WORDS not in log, log
    assert (cgroup / "jobs" / "cgroup.procs").read_text(encoding="utf-8").strip().isdigit()
    assert (tmp_path / "keeper" / "prepare" / VERSION / "root").exists()
