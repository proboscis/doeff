"""job の log の 1 行ごとに、壁の時計の刻の頭を付ける書き手(#3714 — job が止められた刻を log から刻で数えられなかった)。

shim(worker/entry/shim の --stamp-lines)と、待ちの子から分かれた子 A(worker/entry/warm_child)が、同じ見張り shim_code の中で使う
1 つの部品。各 service の slog は替えない — job の子が stdout・stderr に書いた byte に、書き取る所で刻を付ける。

形: この process の stdout(fd 1)と stderr(fd 2)を 1 本の pipe の書き口に差し替え、job はそれを継ぐ。書き手の thread が pipe を読み、
行の頭に `2026-10-06T05:36:21.123+09:00 `(ISO 8601・ms・時差・空白 1 つ — その行の最初の byte を読んだ時の壁の時計)を差し込んで、
元の log の file へ書く。
- 行を切らない: 読んだ byte は受けた順にそのまま流し、刻は改行の次の byte が来た時にだけ差し込む(1 行を溜めないので、長い行も memory を
  食わない・1 つの write の複数行も、複数の write に分かれた 1 行も同じ)。byte のまま運ぶ(UTF-8 でない byte もそのまま)。
- stdout と stderr は 1 本の pipe に入る(同じ file に混ざる今の形のまま・書いた順のまま)。2 つが別の file を指していれば断る(job の
  log の形でない)。
- 終わり(end — shim_code の片づけの後): この process の書き口を元の file へ戻し、pipe に残った分を読み切ってから thread を止め、改行の
  無い末尾の行を改行で閉じる。job が signal・os._exit で落ちても、書いた byte は pipe に在るので全部残る。job の子孫が全部止まって
  いれば読み口は EOF まで読める。引き取りを使えずに子孫が残った時は、その時に読める分までで止める(残った子孫を待たない — 上限
  DRAIN_LIMIT_SECONDS)。
- 書き手は job の終わりの判定・終了コード・止めの合図に触れない(signal の扱いを置かない・子を回収しない — それは main thread の
  shim_code だけ)。thread は job を起こした後に始める(A は B を fork で起こすので、fork の時の thread を 1 本に保つ)。fork で起こす子は、
  書き手の fd(writer_descriptors)を閉じる。
- log の file へ書けなくなったら(disk が満ちた等)、以後は読み捨てる — 書き手が詰まって job の write を止めない。
残る穴: shim 自身が KILL で止まった時(引き取りを使えない時の期限切れの group への KILL・worker の停止の猶予の後の KILL)は、その瞬間に
pipe に残っていた分と末尾の改行は残らない(shim の残る穴と同じ所)。
"""

import os
import select
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime

# 層 entry の文脈と役の名乗り(DOEFF104)— 隣の shim.py と同じ。
MODULE_TAGS = {"context": "worker", "role": "main"}

# 1 回に読む上限(byte)。
CHUNK_BYTES = 1 << 16
# 終わりの読み切りの上限(秒): 引き取りを使えずに残った子孫が書き続けても、shim の終わりを延ばし続けない。
DRAIN_LIMIT_SECONDS = 1.0


def stamp_of(seconds: float) -> bytes:
    """壁の時計の刻(epoch 秒)を、行の頭に差し込む形(ISO 8601・ms・この process の時差・空白 1 つ)にするため。"""
    return datetime.fromtimestamp(seconds, tz=UTC).astimezone().isoformat(timespec="milliseconds").encode("ascii") + b" "


@dataclass(frozen=True)
class Stamped:
    """読んだ byte 1 塊を書き出す形: data = 刻を差し込んだ byte・line_start = 次の塊が行の頭から始まるか(この塊が改行で終わった)。"""

    data: bytes
    line_start: bool


def stamped(chunk: bytes, line_start: bool, stamp: bytes) -> Stamped:
    """読んだ byte 1 塊の行の頭ごとに刻を差し込むため。line_start = 前の塊が改行で終わった(最初の塊は True)。改行の直後に刻を置かず、
    次の byte が来た時に置く(最後の改行の後に刻だけの行を作らない)。"""
    if not chunk:
        return Stamped(data=b"", line_start=line_start)
    closed = chunk.endswith(b"\n")
    body = chunk[:-1] if closed else chunk
    head = stamp if line_start else b""
    return Stamped(data=head + body.replace(b"\n", b"\n" + stamp) + (b"\n" if closed else b""), line_start=closed)


def written(target: int, data: bytes) -> bool:
    """data を全部 target へ書くため。答え = 書けたか(書けなければ以後は読み捨てる — 頭の註)。"""
    view = memoryview(data)
    try:
        while view:
            view = view[os.write(target, view) :]
    except OSError:
        return False
    return True


def read_some(source: int) -> bytes:
    """pipe から読める分を読むため。答え = 読めた byte。空 = もう読む物が無い(書き口が全部閉じた EOF か、終わりの読み切りで読み口を
    待たない形にした後に今は読める分が無い — 引き取りを使えずに残った子孫が書き口を持つ。どちらも書き手の終わり)。"""
    try:
        return os.read(source, CHUNK_BYTES)
    except BlockingIOError:
        return b""


@dataclass(frozen=True)
class WriterEnds:
    """書き手だけが持つ fd: source = pipe の読み口・target = 元の log の file・stop_read と stop_write = 終わりの合図の pipe。"""

    source: int
    target: int
    stop_read: int
    stop_write: int


def pumped(ends: WriterEnds) -> None:
    """書き手の thread の本体: pipe を読み、行の頭に刻を差し込んで log へ書く。終わりの合図の後は、読める分を上限の内で読み切って止め、
    改行の無い末尾の行を改行で閉じる。"""
    line_start = True
    writable = True
    deadline: float | None = None
    while deadline is None or time.monotonic() <= deadline:
        if deadline is None:
            readable, _, _ = select.select([ends.source, ends.stop_read], [], [])
            if ends.source not in readable:
                os.set_blocking(ends.source, False)
                deadline = time.monotonic() + DRAIN_LIMIT_SECONDS
                continue
        chunk = read_some(ends.source)
        if not chunk:
            break
        piece = stamped(chunk, line_start, stamp_of(time.time()))
        writable = writable and written(ends.target, piece.data)
        line_start = piece.line_start
    if writable and not line_start:
        written(ends.target, b"\n")


class RawLines:
    """子の出力をそのまま運ぶ(入口の検め — worker が stdout の行を読んで判じる): fd 1 と fd 2 は起こした側の向け替えのまま。"""

    writer_descriptors: tuple[int, ...] = ()

    def begin(self) -> None:
        """何もしない(書き手が居ない)。"""

    def end(self) -> None:
        """何もしない(書き手が居ない)。"""


class StampedLines:
    """job の log の 1 行ごとに刻を付ける書き手(頭の註)。作るのは stamped_lines(fd 1 と fd 2 を pipe へ差し替えた後)。"""

    def __init__(self, ends: WriterEnds) -> None:
        """書き手の fd を持ち、thread をまだ起こしていない状態で始めるため。"""
        self.ends = ends
        self.writer_descriptors = (ends.source, ends.target, ends.stop_read, ends.stop_write)
        self._mut_thread: threading.Thread | None = None

    def begin(self) -> None:
        """書き手の thread を起こすため(job を起こした後に 1 度 — 2 度目は何もしない)。"""
        if self._mut_thread is None:
            self._mut_thread = threading.Thread(target=pumped, args=(self.ends,), name="line-stamp", daemon=True)
            self._mut_thread.start()

    def end(self) -> None:
        """書き口を元の file へ戻し(以後のこの process の出力は file へ直に行く)、pipe に残った分を読み切らせてから thread を止め、
        書き手の fd を閉じるため。job を起こせずに終わる時も通る(thread がまだ無ければ、ここで起こしてから読み切る)。"""
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(self.ends.target, 1)
        os.dup2(self.ends.target, 2)
        self.begin()
        os.write(self.ends.stop_write, b"\0")
        if self._mut_thread is not None:
            self._mut_thread.join()
        for descriptor in self.writer_descriptors:
            os.close(descriptor)


OutputLines = RawLines | StampedLines


def same_file(first: int, second: int) -> bool:
    """2 つの fd が同じ file を指すか(worker は同じ path を 2 度開くので、開き方でなく file の身元で比べる)。"""
    one, other = os.fstat(first), os.fstat(second)
    return (one.st_dev, one.st_ino) == (other.st_dev, other.st_ino)


def stamped_lines() -> StampedLines:
    """この process の stdout と stderr(同じ log の file)を 1 本の pipe の書き口へ差し替え、その pipe を読んで刻を付ける書き手を作るため
    (thread は begin で起こす)。stdout と stderr が別の file なら断る。"""
    if not same_file(1, 2):
        raise ValueError("刻を付ける log は stdout と stderr が同じ file の時だけ(job の log の形)")
    sys.stdout.flush()
    sys.stderr.flush()
    target = os.dup(1)
    source, sink = os.pipe()
    stop_read, stop_write = os.pipe()
    os.dup2(sink, 1)
    os.dup2(sink, 2)
    os.close(sink)
    return StampedLines(WriterEnds(source=source, target=target, stop_read=stop_read, stop_write=stop_write))
