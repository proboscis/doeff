"""job の log の 1 行ごとの壁の時計の刻(#3714)を、本物の shim の process で確かめる失敗ケース。

worker は job の子の stdout・stderr を同じ log の file へ書き取る(StartProcess が同じ path を 2 度 "ab" で開く)。shim は --stamp-lines の
時だけ、子の出力を pipe で受けて 1 行ごとに頭へ刻を付けて log へ書く(入口の検めは stdout の行を worker が読んで判じるので、旗なしの
そのままの形)。検は worker と同じ持ち方(stdin を pipe・自分の session・stdout と stderr を同じ file へ 2 度開く)で shim を起こす。

守る事と、その失敗ケース:
- 1 行ごとに刻の頭が付く(1 つの write が複数行・行が複数の write に分かれる・空の行も)
- 行を途中で切らない(1 MiB の行も 1 行のまま・刻は行の頭にだけ)
- 改行の無い末尾も 1 行として刻つきで残る
- UTF-8 でない byte はそのまま運ぶ
- stdout と stderr の両方に付き、同じ file に混ざる今の形のまま行の境を壊さない
- 子が signal で落ちても、os._exit で終わっても、それまでの行が全部 file に在る(終了コードは今のまま)
- 旗の無い shim(入口の検めの形)は、子の出力をそのまま運ぶ
"""

import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from doeff_cluster.worker.entry.line_stamp import Stamped, stamp_of, stamped

MODULE_TAGS = {"context": "doeff-cluster-test", "role": "program"}

LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="shim の子孫の引き取りと /proc は Linux だけ — 範囲は Linux の worker"
)

SHIM = "doeff_cluster.worker.entry.shim"
# 刻の頭の形: ISO 8601 の日時・ms・時差(+09:00 など)・空白 1 つ。検は形を自分で綴る(部品の定義を写さない)。
STAMP = re.compile(rb"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}) ")
END_LIMIT_SECONDS = 30.0
MIB = 1 << 20
S = b"<S> "


def test_the_stamp_is_iso_8601_with_milliseconds_and_the_offset() -> None:
    """刻の頭は ISO 8601 の日時・ms・時差・空白 1 つで、読み戻すと同じ ms の時刻になる。"""
    at = 1_791_234_567.891
    stamp = stamp_of(at)
    found = STAMP.match(stamp)
    assert found is not None and found.end() == len(stamp), stamp
    assert datetime.fromisoformat(found.group(1).decode("ascii")) == datetime.fromtimestamp(at, tz=UTC).replace(microsecond=891000)


def test_each_line_of_one_chunk_gets_one_stamp() -> None:
    """1 塊の複数行は行ごとに刻を 1 つ持ち、空の行にも付く。最後の改行の後には刻を置かない(次の byte が来た時に置く)。"""
    assert stamped(b"a\n\nb\n", True, S) == Stamped(data=S + b"a\n" + S + b"\n" + S + b"b\n", line_start=True)


def test_a_line_split_over_chunks_keeps_one_stamp_at_its_head() -> None:
    """複数の塊に分かれた 1 行は、頭にだけ刻を持ち、途中で切れない。"""
    first = stamped(b"spl", True, S)
    second = stamped(b"it\nnext", first.line_start, S)
    third = stamped(b"-line\n", second.line_start, S)
    assert first.data + second.data + third.data == S + b"split\n" + S + b"next-line\n"
    assert (first.line_start, second.line_start, third.line_start) == (False, False, True)


def test_bytes_that_are_not_utf8_pass_through_and_an_empty_chunk_writes_nothing() -> None:
    """UTF-8 でない byte はそのまま運ぶ。空の塊は何も書かず、行の頭の状態を変えない。"""
    assert stamped(b"\xff\xfe\n", True, S) == Stamped(data=S + b"\xff\xfe\n", line_start=True)
    assert stamped(b"", False, S) == Stamped(data=b"", line_start=False)


@dataclass(frozen=True)
class ShimRan:
    """shim の 1 回: code = shim の終了コード(job と同じ値 — signal なら 128 + 番号)・log = log の file の中身。"""

    code: int
    log: bytes


def shim_run(tmp: Path, script: str, stamp: bool) -> ShimRan:
    """worker と同じ持ち方で shim の下に python の 1 本を走らせ、終了コードと log の中身を返すため。"""
    log = tmp / "job.1.log"
    flags = ["--stamp-lines"] if stamp else []
    argv = [sys.executable, "-B", "-m", SHIM, "1", *flags, "--", sys.executable, "-B", "-c", script]
    with open(log, "ab") as out, open(log, "ab") as err:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=err, start_new_session=True)
    try:
        code = process.wait(timeout=END_LIMIT_SECONDS)
    finally:
        if process.stdin is not None:
            process.stdin.close()
    return ShimRan(code=code, log=log.read_bytes())


def payload_of(line: bytes) -> bytes:
    """log の 1 行の刻の頭を確かめて(読める日時か)外し、中身を返すため(刻の無い行は AssertionError)。"""
    found = STAMP.match(line)
    assert found is not None, line[:200]
    datetime.fromisoformat(found.group(1).decode("ascii"))
    return line[found.end() :]


def payloads(log: bytes) -> list[bytes]:
    """log の行ごとの中身の並びを返すため(どの行も刻の頭を持ち、file は改行で終わる — 欠けは AssertionError)。"""
    assert log.endswith(b"\n"), log[-200:]
    return [payload_of(line) for line in log[:-1].split(b"\n")]


MIXED = """
import os, time
os.write(1, b"out-1\\nout-2\\n")
os.write(2, b"err-1\\n")
os.write(1, b"split-")
time.sleep(0.05)
os.write(1, b"line")
time.sleep(0.05)
os.write(1, b"\\n\\n")
os.write(1, b"x" * (1 << 20) + b"\\n")
os.write(2, b"bad-\\xff\\xfe-bytes\\n")
os.write(1, b"tail-without-newline")
"""


@LINUX_ONLY
def test_each_line_of_both_streams_gets_a_stamp_and_no_line_is_cut(tmp_path: Path) -> None:
    """複数行の 1 つの write・3 つの write に分かれた 1 行・空の行・1 MiB の行・UTF-8 でない byte・改行の無い末尾が、stdout と stderr の
    書いた順のまま、どの行も刻の頭を 1 つだけ持って log に在る。"""
    ran = shim_run(tmp_path, MIXED, stamp=True)
    assert ran.code == 0, ran.log[-500:]
    assert payloads(ran.log) == [
        b"out-1",
        b"out-2",
        b"err-1",
        b"split-line",
        b"",
        b"x" * MIB,
        b"bad-\xff\xfe-bytes",
        b"tail-without-newline",
    ]


KILLED = """
import os, signal
os.write(1, b"before-1\\nbefore-2\\n")
os.write(2, b"partial-before-kill")
os.kill(os.getpid(), signal.SIGKILL)
"""


@LINUX_ONLY
def test_lines_written_before_the_child_is_killed_by_a_signal_are_all_kept(tmp_path: Path) -> None:
    """子が KILL で落ちても、それまでに書いた行(書きかけの最後の行も)は刻つきで全部 log に在り、終了コードは今のまま 128 + 9。"""
    ran = shim_run(tmp_path, KILLED, stamp=True)
    assert ran.code == 137, ran.log
    assert payloads(ran.log) == [b"before-1", b"before-2", b"partial-before-kill"]


EXITED = """
import os
os.write(1, b"written-1\\nwritten-2")
os._exit(3)
"""


@LINUX_ONLY
def test_lines_written_before_os_exit_are_all_kept(tmp_path: Path) -> None:
    """子が終了処理を経ずに os._exit で終わっても、書いた行は刻つきで全部 log に在り、終了コードはそのまま。"""
    ran = shim_run(tmp_path, EXITED, stamp=True)
    assert ran.code == 3, ran.log
    assert payloads(ran.log) == [b"written-1", b"written-2"]


@LINUX_ONLY
def test_a_shim_without_the_flag_carries_the_output_as_is(tmp_path: Path) -> None:
    """旗の無い shim(入口の検め — worker が stdout の行を読んで判じる形)は、子の出力に何も足さない。"""
    ran = shim_run(tmp_path, 'import os\nos.write(1, b"a\\nb\\n")\nos.write(2, b"c")\n', stamp=False)
    assert ran.code == 0, ran.log
    assert ran.log == b"a\nb\nc"
