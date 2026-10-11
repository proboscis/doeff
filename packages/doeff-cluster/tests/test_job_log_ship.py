"""job の log の行を OTLP/HTTP でも送る刻つきの書き手(card ki-ae919abe5b26 — 取り決め job-log/1)の失敗ケース。

worker の環境に送り先の基の URL(DOEFF_JOB_LOG_OTLP)が在る時だけ、shim と待ちの子から分かれた子 A の刻つきの書き手が、file へ書く行を
OTLP/HTTP の logs としても送る。本物の process の検は、検の process の中に OTLP の受け手(HTTP の server)を立て、worker と同じ持ち方で
shim を起こす。外の世界(送り先)の代わりを渡す検は、送り手の溜めの上限と終わりの上限だけを見る。

守る事と、その失敗ケース:
- 送った行は file の行と同じ並び・同じ中身(刻の頭と改行を除く)で、timeUnixNano は file の行の頭の刻と同じ ms
- resource 属性は取り決めの 5 つ(service.name・doeff.worker・doeff.job・doeff.attempt・doeff.instance)と、配備が足す属性(doeff の 5 つが勝つ)
- 送り先が落ちている・答えない時も、file は全部在り、終了コードはそのまま、shim の終わりは送りの終わりの上限を越えて延びない
- 溜めが溢れても書き手は待たず、溢れた行は捨てて数える(届いた行の通し番号の飛びが捨てた行)
- 送り先の値が使えなければ送らず、理由を 1 行 log に書く(job はそのまま走る)
- 送り先の環境変数は、worker の環境から実行環境の job と待ちの子へ継ぐ許可表に在る
"""

import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import hy  # noqa: F401 — 許可表の module(Hy)を読むため
import pytest
from doeff_cluster.worker.core.launch import CHILD_ENV_ALLOWED
from doeff_cluster.worker.entry.line_stamp import (
    JOB_LOG_ATTRIBUTES_VAR,
    JOB_LOG_ENV_NAMES,
    JOB_LOG_OTLP_VAR,
    Attribute,
    CutLines,
    JobLogOff,
    JobLogRefused,
    JobLogShipper,
    JobLogTarget,
    OpenLine,
    QueuedLine,
    ShipLimits,
    cut_lines,
    job_log_plan,
    logs_body,
)

MODULE_TAGS = {"context": "doeff-cluster-test", "role": "program"}

# この module の主題は本物の thread(送りの thread・受け手の server)と本物の process(shim・待ちの子)の時間 — 受けた行と終わりを
# 実時間の上限で待つ。
pytestmark = pytest.mark.realtime

LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="shim の子孫の引き取りと /proc は Linux だけ — 範囲は Linux の worker"
)

SHIM = "doeff_cluster.worker.entry.shim"
PACKAGE = Path(__file__).resolve().parent.parent
# 刻の頭の形: ISO 8601 の日時・ms・時差・空白 1 つ(tests/test_line_stamp.py と同じ形を検が自分で綴る)。
STAMP = re.compile(rb"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}) ")
END_LIMIT_SECONDS = 30.0
RECEIVE_LIMIT_SECONDS = 10.0
T = 1_791_234_567_891_000_000
CONTEXT = {
    "DOEFF_WORKER_NAME": "w1",
    "DOEFF_WORKER_JOB": "task/t-1",
    "DOEFF_WORKER_ATTEMPT": "2",
    "DOEFF_WORKER_INSTANCE": "2-abc",
}


def line(head: bytes, at_ns: int = T, cut: bool = False) -> OpenLine:
    """送る行 1 つを作るため(検の短い綴り)。"""
    return OpenLine(at_ns=at_ns, head=head, cut=cut)


# --- 行の切り出し(純粋)-------------------------------------------------------------------------------------------------------


def test_each_line_of_one_chunk_is_one_record_with_the_read_time() -> None:
    """1 塊の複数行は、空の行も含めて行ごとに 1 つになり、どれも塊を読んだ刻を持つ。最後の改行の後には書きかけの行を残さない。"""
    assert cut_lines(b"a\n\nb\n", None, T, 100) == CutLines(closed=(line(b"a"), line(b""), line(b"b")), open=None)


def test_a_line_split_over_chunks_keeps_the_time_of_its_first_byte() -> None:
    """複数の塊に分かれた 1 行は、最初の byte を読んだ刻を持つ 1 行になる(file の刻の頭と同じ)。次の行は後の塊の刻を持つ。"""
    first = cut_lines(b"spl", None, T, 100)
    second = cut_lines(b"it\nnext", first.open, T + 5, 100)
    third = cut_lines(b"-line\n", second.open, T + 9, 100)
    assert first == CutLines(closed=(), open=line(b"spl"))
    assert second.closed == (line(b"split"),)
    assert third == CutLines(closed=(line(b"next-line", T + 5),), open=None)


def test_a_long_line_is_cut_at_the_line_limit_and_marked() -> None:
    """1 行の上限を越えた行は、頭から上限までを送り、印 cut を持つ(塊をまたいでも上限を越えて溜めない)。空の塊は何も変えない。"""
    first = cut_lines(b"x" * 6, None, T, 4)
    second = cut_lines(b"yy\n", first.open, T + 1, 4)
    assert first.open == line(b"xxxx", cut=True)
    assert second == CutLines(closed=(line(b"xxxx", cut=True),), open=None)
    assert cut_lines(b"", first.open, T + 2, 4) == CutLines(closed=(), open=first.open)


# --- 送り先の決め(純粋)--------------------------------------------------------------------------------------------------------


def test_without_the_endpoint_nothing_is_shipped() -> None:
    """送り先の基の URL が無ければ(空も)送らない — 今までの形。"""
    assert job_log_plan(CONTEXT) == JobLogOff()
    assert job_log_plan({**CONTEXT, JOB_LOG_OTLP_VAR: " "}) == JobLogOff()


def test_the_target_names_the_contract_attributes_and_the_deployment_ones() -> None:
    """送り先 = 基の URL + /v1/logs。resource 属性は配備が足す属性(並べた順)の後に doeff の 5 つ。doeff の名と同じ名を配備が
    足しても doeff の値が勝つ(service.name を書き換えられない)。"""
    environ = {
        **CONTEXT,
        JOB_LOG_OTLP_VAR: "http://collector:4318/",
        JOB_LOG_ATTRIBUTES_VAR: " owner.tenant=personal , ,service.name=other,zone=a=b",
    }
    assert job_log_plan(environ) == JobLogTarget(
        endpoint="http://collector:4318/v1/logs",
        resource=(
            Attribute("owner.tenant", "personal"),
            Attribute("zone", "a=b"),
            Attribute("service.name", "doeff.job-log"),
            Attribute("doeff.worker", "w1"),
            Attribute("doeff.job", "task/t-1"),
            Attribute("doeff.attempt", "2"),
            Attribute("doeff.instance", "2-abc"),
        ),
    )


@pytest.mark.parametrize(
    ("changed", "named"),
    [
        ({JOB_LOG_OTLP_VAR: "collector:4318"}, JOB_LOG_OTLP_VAR),
        ({JOB_LOG_OTLP_VAR: "ftp://collector:4318"}, JOB_LOG_OTLP_VAR),
        ({JOB_LOG_OTLP_VAR: "http://collector:99999"}, JOB_LOG_OTLP_VAR),
        ({JOB_LOG_ATTRIBUTES_VAR: "owner.tenant=personal,secret-without-equals"}, "2 番目"),
        ({JOB_LOG_ATTRIBUTES_VAR: "=personal"}, "1 番目"),
        ({"DOEFF_WORKER_JOB": ""}, "DOEFF_WORKER_JOB"),
    ],
)
def test_an_unusable_value_refuses_shipping_by_name_without_the_value(changed: dict[str, str], named: str) -> None:
    """URL の形でない送り先・`名=値` でない属性の項・job の文脈の欠けは送らず、どれが使えないかを名指す(値そのものは書かない)。"""
    environ = {**CONTEXT, JOB_LOG_OTLP_VAR: "http://collector:4318", **changed}
    plan = job_log_plan(environ)
    assert isinstance(plan, JobLogRefused), plan
    assert named in plan.reason, plan.reason
    assert "secret" not in plan.reason, plan.reason
    assert "collector" not in plan.reason, plan.reason


def test_the_body_is_otlp_logs_json_with_one_record_per_line() -> None:
    """本文は OTLP/HTTP の logs の JSON: resource 1 つ・scope 1 つ・1 行 = record 1 つ(timeUnixNano は 10 進の文字・body は刻と改行
    を除いた行 — CR LF の CR も除く・UTF-8 でない byte は置き換えの字)。印は通し番号 doeff.line・切った印 doeff.cut・それまでに捨てた
    行の数 doeff.dropped(0 なら置かない)。"""
    target = JobLogTarget(endpoint="http://c/v1/logs", resource=(Attribute("service.name", "doeff.job-log"),))
    batch = (QueuedLine(1, line(b"one\r")), QueuedLine(3, line(b"bad-\xff", T + 1, cut=True)))
    document = json.loads(logs_body(target, batch, 0))
    (resource_logs,) = document["resourceLogs"]
    assert resource_logs["resource"]["attributes"] == [{"key": "service.name", "value": {"stringValue": "doeff.job-log"}}]
    (scope_logs,) = resource_logs["scopeLogs"]
    assert scope_logs["scope"] == {"name": "doeff.job-log"}
    assert scope_logs["logRecords"] == [
        {
            "timeUnixNano": str(T),
            "body": {"stringValue": "one"},
            "attributes": [{"key": "doeff.line", "value": {"intValue": "1"}}],
        },
        {
            "timeUnixNano": str(T + 1),
            "body": {"stringValue": "bad-�"},
            "attributes": [{"key": "doeff.line", "value": {"intValue": "3"}}, {"key": "doeff.cut", "value": {"boolValue": True}}],
        },
    ]
    dropped = json.loads(logs_body(target, batch[:1], 7))["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]
    assert {"key": "doeff.dropped", "value": {"intValue": "7"}} in dropped


# --- 送り手(外の世界の代わりを渡す)-----------------------------------------------------------------------------------------


TARGET = JobLogTarget(endpoint="http://c/v1/logs", resource=(Attribute("service.name", "doeff.job-log"),))


class HeldPoster:
    """送り先の代わり: 放すまで POST を答えない(遅い・答えない送り先)。放した後は受けて、本文を残す。"""

    def __init__(self) -> None:
        """放す前の状態で始めるため。"""
        self.released = threading.Event()
        self.lock = threading.Lock()
        self._mut_bodies: tuple[bytes, ...] = ()

    def posted(self, body: bytes, timeout: float) -> bool:
        """放すまで(か待ちの上限まで)待ち、放されていれば本文を残して受けるため。"""
        if not self.released.wait(timeout):
            return False
        with self.lock:
            self._mut_bodies = (*self._mut_bodies, body)
        return True

    def close(self) -> None:
        """何もしない(接続が無い)。"""

    def numbers(self) -> list[int]:
        """受けた行の通し番号の並びを読むため。"""
        with self.lock:
            bodies = self._mut_bodies
        records = [r for body in bodies for r in json.loads(body)["resourceLogs"][0]["scopeLogs"][0]["logRecords"]]
        return [int(a["value"]["intValue"]) for r in records for a in r["attributes"] if a["key"] == "doeff.line"]


def test_a_full_queue_drops_lines_without_making_the_writer_wait() -> None:
    """送り先が答えない間に溜めの上限を越えて書いても、書き手(take)は待たずに戻り、溢れた行は捨てて数える。放した後に届くのは
    溜めに入った行で、通し番号の飛びが捨てた行。"""
    poster = HeldPoster()
    limits = ShipLimits(queue_lines=5, queue_bytes=1 << 20, batch_lines=2, interval_seconds=0.01, end_seconds=5.0)
    shipper = JobLogShipper(TARGET, limits, lambda _endpoint: poster)
    shipper.begin()
    started = time.monotonic()
    shipper.take(b"".join(b"line-%d\n" % n for n in range(1, 21)), T)
    assert time.monotonic() - started < 0.5
    taken = shipper.counts()
    assert taken.seen == 20, taken
    assert taken.dropped == 15, taken
    poster.released.set()
    shipper.end()
    assert poster.numbers() == [1, 2, 3, 4, 5]
    assert shipper.counts().dropped == 15


def test_the_end_does_not_wait_past_its_limit_for_a_silent_destination() -> None:
    """送り先が答えないまま(POST の待ちの上限 30 秒の途中で)終わり(end)が来ても、end は終わりの上限の内に戻る。"""
    poster = HeldPoster()
    limits = ShipLimits(interval_seconds=0.01, post_seconds=30.0, end_seconds=0.3)
    shipper = JobLogShipper(TARGET, limits, lambda _endpoint: poster)
    shipper.begin()
    shipper.take(b"a\nb\n", T)
    time.sleep(0.1)
    started = time.monotonic()
    shipper.end()
    assert time.monotonic() - started < 1.0
    assert poster.numbers() == []
    poster.released.set()


def counted(shipper: JobLogShipper, dropped: int) -> int:
    """送り手の捨てた行の数が dropped に届くまで(上限の秒まで)待って読むため。"""
    deadline = time.monotonic() + RECEIVE_LIMIT_SECONDS
    while shipper.counts().dropped < dropped and time.monotonic() < deadline:
        time.sleep(0.01)
    return shipper.counts().dropped


def test_lines_of_a_failed_post_are_dropped_counted_and_marked_on_later_lines() -> None:
    """受けられなかった POST の行は捨てて数え(job は止めない)、後で届いた行は通し番号の続きと、それまでに捨てた行の数の印を持つ。"""
    poster = HeldPoster()
    limits = ShipLimits(interval_seconds=0.01, post_seconds=0.05, pause_seconds=0.05, end_seconds=5.0)
    shipper = JobLogShipper(TARGET, limits, lambda _endpoint: poster)
    shipper.begin()
    shipper.take(b"a\nb\n", T)
    assert counted(shipper, 2) == 2
    poster.released.set()
    shipper.take(b"c\n", T + 1)
    shipper.end()
    assert poster.numbers() == [3]
    with poster.lock:
        (body,) = poster._mut_bodies
    (record,) = json.loads(body)["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    assert {"key": "doeff.dropped", "value": {"intValue": "2"}} in record["attributes"], record


# --- 本物の shim と待ちの子(送り先 = 検の process の中の HTTP の server)--------------------------------------------------------


@dataclass(frozen=True)
class Received:
    """受け手が受けた record 1 つ: resource = resource 属性(名と値の組の並び)・at_ns = timeUnixNano・body = 行・number = 通し番号。"""

    resource: tuple[tuple[str, str], ...]
    at_ns: int
    body: str
    number: int


@dataclass(frozen=True)
class Post:
    """受け手が受けた POST 1 つ(path と本文)。"""

    path: str
    body: bytes


def records_of(body: bytes) -> list[Received]:
    """POST の本文 1 つ(OTLP の logs の JSON)を、受けた record の並びに読むため。"""
    (resource_logs,) = json.loads(body)["resourceLogs"]
    resource = tuple((a["key"], a["value"]["stringValue"]) for a in resource_logs["resource"]["attributes"])
    return [
        Received(
            resource,
            int(r["timeUnixNano"]),
            r["body"]["stringValue"],
            next(int(a["value"]["intValue"]) for a in r["attributes"] if a["key"] == "doeff.line"),
        )
        for r in resource_logs["scopeLogs"][0]["logRecords"]
    ]


class Collector:
    """OTLP の受け手(検の process の中の HTTP の server): POST /v1/logs の本文を残す。hang の時は放すまで答えない。"""

    def __init__(self, hang: bool) -> None:
        """127.0.0.1 の空いた port で受け始めるため。"""
        self.hang = hang
        self.released = threading.Event()
        self.lock = threading.Lock()
        self._mut_posts: tuple[Post, ...] = ()
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                """本文を残して 200 を返すため(hang なら放すまで答えない)。"""
                body = self.rfile.read(int(self.headers["Content-Length"]))
                if collector.hang:
                    collector.released.wait(END_LIMIT_SECONDS)
                    return
                collector.kept(Post(path=self.path, body=body))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — 基の class の引数の名
                """受けた行を stderr に書かないため。"""

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def kept(self, post: Post) -> None:
        """受けた POST 1 つを残すため(handler の thread から)。"""
        with self.lock:
            self._mut_posts = (*self._mut_posts, post)

    def base(self) -> str:
        """送り先の基の URL(/v1/logs を除く)。"""
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def records(self) -> list[Received]:
        """受けた record を受けた順に読むため(path が /v1/logs でない POST は AssertionError)。"""
        with self.lock:
            posts = self._mut_posts
        assert all(p.path == "/v1/logs" for p in posts), [p.path for p in posts]
        return [record for p in posts for record in records_of(p.body)]

    def stop(self) -> None:
        """受けを止めるため(答えずに待つ handler も放す)。"""
        self.released.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def collector() -> Iterator[Collector]:
    """答える受け手を立てて、終わりに止めるため。"""
    running = Collector(hang=False)
    yield running
    running.stop()


@pytest.fixture
def silent() -> Iterator[Collector]:
    """POST を受けても答えない受け手を立てて、終わりに止めるため。"""
    running = Collector(hang=True)
    yield running
    running.stop()


@dataclass(frozen=True)
class ShimRan:
    """shim の 1 回: code = 終了コード・log = log の file の中身・seconds = 起こしてから終わるまで。"""

    code: int
    log: bytes
    seconds: float


def shim_run(tmp: Path, script: str, env: dict[str, str]) -> ShimRan:
    """worker と同じ持ち方(stdin を pipe・自分の session・stdout と stderr を同じ file へ 2 度開く)で、--stamp-lines の shim の下に
    python の 1 本を走らせるため。env = 検の process の環境に足す環境変数(worker が子へ置く物 — 命令 env が足してから shim に
    置き換わるので、shim は同じ pid のまま自分の session の先頭になる)。"""
    log = tmp / "job.2.log"
    added = [f"{name}={value}" for name, value in env.items()]
    argv = ["env", *added, sys.executable, "-B", "-m", SHIM, "1", "--stamp-lines", "--", sys.executable, "-B", "-c", script]
    started = time.monotonic()
    with open(log, "ab") as out, open(log, "ab") as err:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=err, start_new_session=True)
    try:
        code = process.wait(timeout=END_LIMIT_SECONDS)
    finally:
        if process.stdin is not None:
            process.stdin.close()
    return ShimRan(code=code, log=log.read_bytes(), seconds=time.monotonic() - started)


@dataclass(frozen=True)
class FileLine:
    """log の file の 1 行: at_ms = 刻の頭(epoch ms)・body = 刻の頭を除いた中身。"""

    at_ms: int
    body: bytes


def file_line(raw: bytes) -> FileLine:
    """log の 1 行を刻と中身に分けるため(刻の頭の無い行は AssertionError)。"""
    stamp = STAMP.match(raw)
    assert stamp is not None, raw[:200]
    at = datetime.fromisoformat(stamp.group(1).decode("ascii"))
    return FileLine(at_ms=round(at.timestamp() * 1000), body=raw[stamp.end() :])


def file_lines(log: bytes) -> list[FileLine]:
    """log の行を刻と中身に分けるため(どの行も刻の頭を持ち、file は改行で終わる — 欠けは AssertionError)。"""
    assert log.endswith(b"\n"), log[-200:]
    return [file_line(raw) for raw in log[:-1].split(b"\n")]


def received_until(collector: Collector, count: int) -> list[Received]:
    """受け手が count 個の record を受けるまで(上限の秒まで)待って読むため。"""
    deadline = time.monotonic() + RECEIVE_LIMIT_SECONDS
    while len(collector.records()) < count and time.monotonic() < deadline:
        time.sleep(0.02)
    return collector.records()


MIXED = """
import os, time
os.write(1, b"out-1\\nout-2\\n")
os.write(2, b"err-1\\n")
os.write(1, b"split-")
time.sleep(0.05)
os.write(1, b"line")
time.sleep(0.05)
os.write(1, b"\\n\\n")
os.write(2, b"bad-\\xff-bytes\\n")
time.sleep(1.3)
os.write(1, b"after-a-pause\\n")
os.write(1, b"tail-without-newline")
os._exit(3)
"""


@LINUX_ONLY
def test_the_shim_ships_each_file_line_with_its_stamp_and_the_contract_attributes(tmp_path: Path, collector: Collector) -> None:
    """送り先が在る shim は、file の行と同じ並び・同じ中身の行を送り(改行の無い末尾も・UTF-8 でない byte は置き換えの字)、
    timeUnixNano は file の行の頭の刻と同じ ms。束の間(1 秒)をまたいだ行も届く。終了コードと file の形は今のまま。"""
    env = {**CONTEXT, JOB_LOG_OTLP_VAR: collector.base(), JOB_LOG_ATTRIBUTES_VAR: "owner.tenant=personal"}
    ran = shim_run(tmp_path, MIXED, env)
    assert ran.code == 3, ran.log
    lines = file_lines(ran.log)
    assert [x.body for x in lines] == [
        b"out-1", b"out-2", b"err-1", b"split-line", b"", b"bad-\xff-bytes", b"after-a-pause", b"tail-without-newline"
    ]
    records = received_until(collector, len(lines))
    assert [r.body for r in records] == [x.body.decode("utf-8", errors="replace") for x in lines]
    assert [r.at_ns // 1_000_000 for r in records] == [x.at_ms for x in lines]
    assert [r.number for r in records] == list(range(1, len(lines) + 1))
    assert {r.resource for r in records} == {
        (
            ("owner.tenant", "personal"),
            ("service.name", "doeff.job-log"),
            ("doeff.worker", "w1"),
            ("doeff.job", "task/t-1"),
            ("doeff.attempt", "2"),
            ("doeff.instance", "2-abc"),
        )
    }


QUICK = """
import os
os.write(1, b"one\\ntwo\\n")
"""


def closed_port() -> int:
    """誰も受けていない 127.0.0.1 の port を 1 つ選ぶため(開いて閉じる)。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@LINUX_ONLY
def test_a_down_destination_keeps_the_file_whole_and_the_exit_code(tmp_path: Path) -> None:
    """送り先が落ちていても(つながらない)、file は全部在り、終了コードはそのまま、job の log に送りの理由の行は足さない。"""
    ran = shim_run(tmp_path, QUICK, {**CONTEXT, JOB_LOG_OTLP_VAR: f"http://127.0.0.1:{closed_port()}"})
    assert ran.code == 0, ran.log
    assert [x.body for x in file_lines(ran.log)] == [b"one", b"two"]


@LINUX_ONLY
def test_a_silent_destination_does_not_hold_the_shim_end(tmp_path: Path, silent: Collector) -> None:
    """送り先が POST を受けて答えない時も、shim は送りの終わりの上限(0.5 秒)の後に終わる(1 回の POST の待ちの上限 5 秒を待たない)。
    file は全部在る。"""
    ran = shim_run(tmp_path, QUICK, {**CONTEXT, JOB_LOG_OTLP_VAR: silent.base()})
    assert ran.code == 0, ran.log
    assert [x.body for x in file_lines(ran.log)] == [b"one", b"two"]
    assert ran.seconds < 4.0, ran.seconds


@LINUX_ONLY
def test_an_unusable_destination_is_named_in_the_log_and_the_job_runs(tmp_path: Path) -> None:
    """送り先の値が使えなければ送らず、理由を 1 行 log に書き(刻つき)、job はそのまま走る。"""
    ran = shim_run(tmp_path, QUICK, {**CONTEXT, JOB_LOG_OTLP_VAR: "collector:4318"})
    assert ran.code == 0, ran.log
    bodies = [x.body for x in file_lines(ran.log)]
    assert bodies[1:] == [b"one", b"two"], bodies
    assert bodies[0].startswith("line_stamp: job の log の行を送らない".encode()), bodies[0]
    assert JOB_LOG_OTLP_VAR.encode() in bodies[0]


def test_the_endpoint_names_pass_from_the_worker_to_every_child() -> None:
    """送り先と属性の環境変数は、worker の環境から子へ継ぐ許可表に在る(実行環境の job の shim と、待ちの子から分かれた子 A の頼みの
    env に届く — 木の job は worker の環境を継ぐ)。job の文脈の名は worker が子へ置く。"""
    assert {JOB_LOG_OTLP_VAR, JOB_LOG_ATTRIBUTES_VAR} <= CHILD_ENV_ALLOWED
    assert set(JOB_LOG_ENV_NAMES) == {JOB_LOG_OTLP_VAR, JOB_LOG_ATTRIBUTES_VAR, *CONTEXT}


WARM = "doeff_cluster.worker.entry.warm_child"
WARM_JOB = "tests.fixtures.warm_job"


@LINUX_ONLY
def test_a_job_forked_from_the_warm_child_ships_its_lines(tmp_path: Path, collector: Collector) -> None:
    """待ちの子から分かれた子 A も、頼みの env に送り先が在れば同じ部品で行を送る(job の名は task/<id>)。socket の path が AF_UNIX の
    上限に収まるよう、置き場は /tmp の下の短い dir。"""
    short = Path(tempfile.mkdtemp(prefix="wl-", dir="/tmp"))
    root = short / "root"
    root.mkdir(exist_ok=True)
    argv = [sys.executable, "-B", "-m", WARM, "--root", str(root), "--socket", str(short / "s"), "--ready", str(short / "r.json")]
    search = ":".join([str(PACKAGE), *(entry for entry in sys.path if entry)])
    warm = subprocess.Popen(
        [*argv, "--preload", WARM_JOB], stdin=subprocess.PIPE, stderr=subprocess.PIPE, cwd=PACKAGE, env={"PYTHONPATH": search},
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + END_LIMIT_SECONDS
        while not (short / "r.json").exists():
            assert warm.poll() is None, "待ちの子が準備の前に終わった"
            assert time.monotonic() < deadline, "待ちの子が準備できない"
            time.sleep(0.05)
        env = {**CONTEXT, JOB_LOG_OTLP_VAR: collector.base()}
        request = {
            "entry": WARM_JOB,
            "args": ["exit", "5"],
            "cwd": str(PACKAGE),
            "env": [{"name": k, "value": v} for k, v in sorted(env.items())],
            "logPath": str(short / "job.log"),
            "exitPath": str(short / "job.exit"),
            "graceSeconds": 1.0,
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10.0)
            client.connect(str(short / "s"))
            client.sendall((json.dumps(request) + "\n").encode())
            assert b"pid" in client.recv(65536)
        while not (short / "job.exit").exists():
            assert time.monotonic() < deadline, "分かれた子が終わらない"
            time.sleep(0.05)
        lines = file_lines((short / "job.log").read_bytes())
        records = received_until(collector, len(lines))
        assert [r.body for r in records] == [x.body.decode() for x in lines]
        assert {dict(r.resource)["doeff.job"] for r in records} == {"task/t-1"}
    finally:
        if warm.poll() is None:
            warm.kill()
        warm.wait(timeout=10)
        shutil.rmtree(short, ignore_errors=True)
