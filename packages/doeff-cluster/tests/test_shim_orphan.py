"""worker の消失を退きとして扱う入れ物 shim(worker/entry/shim の --orphan-seconds・card ki-f69f2a6869aa)を本物の process で確かめる。

退きの寿命 retired-ms を宣言した handoff の service は、worker の世代の入れ替えでは退いて寿命の上限まで回る。worker が落ちた時(shim の
標準入力の EOF)も同じ寿命で回すため、shim は止めの合図を送らず、job へ退く知らせの行を中継し、job が自分で終わるのを待つ。
検は worker の代わりに shim を起こし(stdin を pipe で持つ = worker と同じ持ち方)、pipe を閉じて worker の kill -9 と同じ EOF を作る。
job は知らせの pipe から 1 行を読むまで止まり、読んだ行を file に書いて 0 で終わる小さな script。

守る事と、その失敗ケース:
- 旗の在る shim は、EOF の後も job を止めず、job は退く知らせを受けて自分で終わり、shim は job の終了コードで終わる
  (失敗ケース = 旗の無い shim — 同じ job が知らせを受けずに止めの合図で止まり、file を書かない)
- 待つ秒を越えても終わらない job は、止めの合図で止まる(寿命の上限が効く — 走り続ける孤児を残さない)
- 中継する行は worker の retirement-line が Retired に書く行と同じ綴り
- 旗は正の秒だけを受ける
"""

import subprocess
import sys
from pathlib import Path

import pytest

from doeff import run
from doeff_cluster._worker.entry.shim import RETIRED_LINE, positive_seconds, shim_flags
from doeff_cluster._worker.intent.worker_model import Retired
from doeff_cluster._worker.protocol.process_host import retirement_line

MODULE_TAGS = {"context": "doeff-cluster-test", "role": "program"}

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="子孫の引き取りと /proc は Linux だけ")

SHIM = "doeff_cluster._worker.entry.shim"
NOTICE_ENV = "SHIM_ORPHAN_TEST_NOTICE"
# job の終わりを待つ上限(秒)。
END_LIMIT_SECONDS = 20.0
# 知らせの pipe の 1 行を読み、file に書いて 0 で終わる job(読むまで止まる — 止めの合図を受ければ書かずに終わる)。
READS_NOTICE = (
    "import os, sys\n"
    f"fd = int(os.environ[{NOTICE_ENV!r}].split(':')[0])\n"
    "os.set_blocking(fd, True)\n"
    "line = os.read(fd, 64)\n"
    "open(sys.argv[1], 'wb').write(line)\n"
)
# 知らせを読まずに長く眠る job(寿命の上限で止められる)。
SLEEPS = "import time\ntime.sleep(60)\n"


def shim(tmp: Path, flags: list[str], script: str) -> subprocess.Popen[bytes]:
    """worker と同じ持ち方(stdin の pipe・新しい session)で shim を起こし、job に script を走らせるため。"""
    return subprocess.Popen(
        [sys.executable, "-B", "-m", SHIM, "0.5", "--notice-env", NOTICE_ENV, *flags, "--",
         sys.executable, "-c", script, str(tmp / "notice")],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def worker_vanished(process: subprocess.Popen[bytes]) -> tuple[int, str]:
    """worker の kill -9 と同じ EOF を shim の標準入力に作り、shim の終わりを待って (終了コード, 標準エラー) を返すため。"""
    assert process.stdin is not None
    process.stdin.close()
    _, stderr = process.communicate(timeout=END_LIMIT_SECONDS)
    return process.returncode, stderr.decode("utf-8", "replace")


def test_an_orphaned_job_gets_the_retirement_and_ends_by_itself(tmp_path: Path) -> None:
    """旗の在る shim: EOF の後も job は止められず、退く知らせを受けて自分で終わり、shim は job の終了コード 0 で終わる。"""
    code, stderr = worker_vanished(shim(tmp_path, ["--orphan-seconds", "30"], READS_NOTICE))
    assert code == 0, stderr
    assert (tmp_path / "notice").read_bytes() == RETIRED_LINE
    assert "退いた job として" in stderr


def test_without_the_flag_the_job_is_stopped_before_it_hears_anything(tmp_path: Path) -> None:
    """失敗ケース: 旗の無い shim は EOF で止めの合図を送り、job は知らせを受けずに止まる(file を書かない)。"""
    code, stderr = worker_vanished(shim(tmp_path, [], READS_NOTICE))
    assert code == 128 + 15, stderr
    assert not (tmp_path / "notice").exists()
    assert "worker が消えたので job を止めます" in stderr


def test_a_job_past_the_orphan_lifetime_is_stopped(tmp_path: Path) -> None:
    """寿命の上限: 待つ秒を越えても終わらない job は止めの合図で止まり、shim はその終了コードで終わる。"""
    code, stderr = worker_vanished(shim(tmp_path, ["--orphan-seconds", "0.5"], SLEEPS))
    assert code == 128 + 15, stderr
    assert "秒を越えた — job を止めます" in stderr


def test_the_relayed_line_is_the_workers_retirement_line() -> None:
    """中継する行は worker が Retired に書く行と同じ綴り(shim は Hy の層を読み込まないので写しを持つ)。"""
    assert run(retirement_line(Retired())).encode("utf-8") == RETIRED_LINE


def test_the_flag_takes_only_positive_seconds() -> None:
    """旗の値は正の有限の秒だけ — 0・負・数でない値は名を挙げて断る。"""
    assert shim_flags(["--orphan-seconds", "18000"]).orphan_seconds == 18000.0
    assert shim_flags([]).orphan_seconds is None
    assert positive_seconds("inf") is None
    for word in ("0", "-1", "soon"):
        with pytest.raises(SystemExit):
            shim_flags(["--orphan-seconds", word])
