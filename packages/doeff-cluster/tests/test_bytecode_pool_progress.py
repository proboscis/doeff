"""bytecode の準備の道具(worker/entry/code_prepare.hy)の進みの印(--progress の file)は、焼きの途中でも 1 つ用意し終えるたびに進む
(card ki-6401ff10af71)。

直す前は、印を進めるのは道具の走査の後・閉包の一回りごと・焼きの前と後だけで、焼き(_foundation/bytecode_pool.hy の pool)の間は
印が止まったままだった。保存先に合う code の無い焼き直し(macro を変えた版の後の初めての準備 — 2026-10-27 の zeus-native で約 2,400 本・
並べる数 2)は 1 file ずつ進んでいても、焼きの全体が準備の見張りの停滞の閾(600 秒)を越え、「最後に進んだ処理: bytecode」で止められた。
作り直しも同じ所で止められ、着地の宣言の round が 20〜30 分延びた。

模擬: 見張りの読み(印の時刻が止まっている間)を、「1 file の焼きの秒より長く、焼きの全体の秒より短い」閾で確かめる。木の Hy の
source 3 本は、compile の時に展開される macro が PAUSE 秒眠り(1 file が遅い焼き)、眠りの終わりに「今 − 印の時刻」(その時までに印が
止まっていた秒)を log に 1 行書く。焼きの全体は並べる数 1 で 3 × PAUSE・2 で 2 × PAUSE 秒かかる。log の最大が STALL 秒より短い事を
確かめる — 焼きの前と後だけの印では、最後の 1 本の終わりで焼きの全体(2 × PAUSE 秒以上)になる。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

#: 準備の道具(本番の準備の処理ステージが起こす script と同じ file)。
TOOL = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster" / "worker" / "entry" / "code_prepare.hy"

#: 1 file の焼きの秒(compile の時の macro の眠り)。
PAUSE = 3.0
#: 模擬の見張りの閾 — 1 file の秒より長く、焼きの全体(2 × PAUSE 以上)より短い。
STALL = 1.6 * PAUSE
SLOW_SOURCE = (
    "(defmacro pause [] (import os time) (time.sleep " + str(PAUSE) + ")\n"
    "  (with [f (open (get os.environ \"STALL_LOG\") \"a\")]\n"
    "    (.write f (.format \"{}\\n\" (- (time.time) (os.path.getmtime (get os.environ \"MARK_FILE\"))))))\n"
    "  None)\n(pause)\n(setv X 1)\n"
)


def _plant(tree: Path) -> None:
    """遅い Hy の source 3 本と速い Python の source 1 本の木を置くため。"""
    package = tree / "slowpkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "fast.py").write_text("Y = 2\n")
    for name in ("slow_a", "slow_b", "slow_c"):
        (package / f"{name}.hy").write_text(f";; {name}\n" + SLOW_SOURCE)


@pytest.mark.parametrize("jobs", [1, 2])
def test_the_progress_mark_never_stands_still_for_a_whole_slow_bake(tmp_path: Path, jobs: int) -> None:
    tree = tmp_path / "tree"
    _plant(tree)
    mark = tmp_path / "progress"
    mark.write_text("bytecode\n")
    os.utime(mark, (0, 0))
    argv = [sys.executable, "-m", "hy", str(TOOL), "--revision", "r", "--jobs", str(jobs), "--progress", str(mark),
            "--tree", str(tree), "--roots", "."]
    log = tmp_path / "stall.log"
    env = {**os.environ, "DOEFF_HY_CODE_STORE": "off", "STALL_LOG": str(log), "MARK_FILE": str(mark)}
    done = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
    assert done.returncode == 0, done.stderr[-3000:]
    for name in ("slow_a", "slow_b", "slow_c", "fast"):
        assert list((tree / "slowpkg" / "__pycache__").glob(f"{name}.*.pyc")), (name, done.stderr[-3000:])
    stalls = tuple(float(line) for line in log.read_text().split())
    assert len(stalls) == 3, stalls
    assert max(stalls) < STALL, stalls
    assert mark.read_text() == "bytecode\n"
