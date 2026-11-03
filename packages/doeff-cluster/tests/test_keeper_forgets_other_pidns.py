"""keeper が前の container の世代と準備を忘れて起きる事(deploy/boot.sh の keeper_forget_other_pidns)の検。

訳: 機体の worker の /tmp を container の書き層から tmpfs の emptyDir(Pod の寿命)へ移すと、
keeper の状態の dir(既定 /tmp/doeff-worker-keeper)が container の起こし直しを越えて残る。新しい container は pid を 1 から振り直すので、
残った世代の pid の file が新しい container の別の process(新しい世代そのものも)に当たり、keeper が前の世代と読んで drain して SIGTERM を
送るか、残った準備の pid を準備中と読んで待ち続ける。

確かめ方: boot.sh から keeper の関数を抜き出して sh で走らせる。前の container の名前空間は、$keeper_dir/pidns に今と違う値を書いて作る。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

BOOT = Path(__file__).resolve().parents[1] / "deploy" / "boot.sh"
FUNCTIONS = ("keeper_log", "keeper_forget_other_pidns")
FORGET_WORDS = "は別の pid の名前空間(前の container)の物 — 残った世代と準備を忘れる"


def _functions() -> str:
    """boot.sh の keeper の関数の定義(本物の文のまま)。"""
    text = BOOT.read_text(encoding="utf-8")
    found = {name: re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL) for name in FUNCTIONS}
    missing = [name for name, match in found.items() if match is None]
    assert not missing, f"boot.sh に関数 {missing} が無い"
    return "\n".join(match.group(0) for match in found.values() if match is not None)


def _leftovers(keeper: Path, pidns: str | None) -> None:
    """前の keeper の残した状態: 世代 1 つ(pid = 生きている process の pid)・準備中の版 1 つ・止めの印・pid の名前空間の file。"""
    gen = keeper / "gens" / "pod.1"
    gen.mkdir(parents=True)
    (gen / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    prep = keeper / "prepare" / ("a" * 40)
    prep.mkdir(parents=True)
    (prep / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (keeper / "leaving").write_text("", encoding="utf-8")
    (keeper / "commit").write_text("b" * 40 + "\n", encoding="utf-8")
    if pidns is not None:
        (keeper / "pidns").write_text(pidns + "\n", encoding="utf-8")


def _forget(keeper: Path) -> str:
    """keeper_forget_other_pidns を 1 度呼ぶ。答え = keeper の log。"""
    probe = f"""set -eu
{_functions()}
keeper_dir={keeper}
keeper_forget_other_pidns
"""
    done = subprocess.run(
        ["/bin/sh", "-c", probe], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=30, check=True
    )
    return done.stderr


def _this_pidns() -> str:
    return os.readlink("/proc/self/ns/pid")


def test_state_from_another_pid_namespace_is_forgotten(tmp_path: Path) -> None:
    """前の container の名前空間の世代・準備・止めの印を消し、名前空間の file を今の値に書き直す。版の file(commit)は残す。"""
    keeper = tmp_path / "keeper"
    _leftovers(keeper, pidns="pid:[1]")
    log = _forget(keeper)
    assert FORGET_WORDS in log, log
    assert not (keeper / "gens").exists()
    assert not (keeper / "prepare").exists()
    assert not (keeper / "leaving").exists()
    assert (keeper / "commit").exists()
    assert (keeper / "pidns").read_text(encoding="utf-8").strip() == _this_pidns()


def test_state_written_before_the_namespace_file_is_forgotten(tmp_path: Path) -> None:
    """名前空間の file を書かない前の版の keeper の残した状態は、どの名前空間の物か分からないので忘れる。"""
    keeper = tmp_path / "keeper"
    _leftovers(keeper, pidns=None)
    log = _forget(keeper)
    assert FORGET_WORDS in log, log
    assert not (keeper / "gens").exists()


def test_state_from_the_same_pid_namespace_is_kept(tmp_path: Path) -> None:
    """同じ名前空間の状態は残す(pid は今の container の process を指す)。"""
    keeper = tmp_path / "keeper"
    _leftovers(keeper, pidns=_this_pidns())
    log = _forget(keeper)
    assert FORGET_WORDS not in log, log
    assert (keeper / "gens" / "pod.1" / "pid").exists()
    assert (keeper / "prepare" / ("a" * 40) / "pid").exists()


def test_a_first_start_makes_the_dir_and_names_the_namespace(tmp_path: Path) -> None:
    """dir の無い初めての起動は何も忘れず(log に出さず)、dir を作って名前空間を書く。"""
    keeper = tmp_path / "keeper"
    log = _forget(keeper)
    assert FORGET_WORDS not in log, log
    assert (keeper / "pidns").read_text(encoding="utf-8").strip() == _this_pidns()
