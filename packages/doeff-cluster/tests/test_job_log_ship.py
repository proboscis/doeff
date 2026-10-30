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
- worker の process 自身の行(card ki-35923a5895b2)は job の名 worker/<worker の名>・試み 0・世代 boot で送り、捕まえない例外の
  traceback も file と送り先の両方に届く。送り先が無い・stdout と stderr が別の file の時は出力の向きを替えない
- worker の差し替えは Python の流れだけで、fd 1・2 は元の file のまま — worker より長く生きる子孫の行も file に残る(設計検証
  lt-8PQM3S5DDHCSJ92Q7X259K9HAW の盲検 A)。worker の道で溜めが溢れても worker の write は送りに待たされない(盲検 B)
"""

import json
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator
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
    POD_NAME_VAR,
    POD_NAMESPACE_VAR,
    Attribute,
    CutLines,
    JobLogOff,
    JobLogRefused,
    JobLogShipper,
    JobLogTarget,
    OpenLine,
    QueuedLine,
    ShipLimits,
    coordinator_log_plan,
    cut_lines,
    job_log_plan,
    logs_body,
    worker_log_plan,
)

from tests.warm_child_rig import StartWarmChild, wait_ready_file

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


def test_the_worker_process_names_itself_and_ignores_a_job_context_in_its_environ() -> None:
    """worker の process 自身の行は job の名 worker/<worker の名>・試み 0・世代 = 渡した boot で名乗る。worker の環境に job の文脈の
    名が在っても読まない(送り先の基の URL と配備が足す属性だけを読む)。送り先が無ければ送らない。"""
    environ = {
        **CONTEXT,
        JOB_LOG_OTLP_VAR: "http://collector:4318",
        JOB_LOG_ATTRIBUTES_VAR: "owner.tenant=personal",
    }
    assert worker_log_plan(environ, "zeus-native", "b0") == JobLogTarget(
        endpoint="http://collector:4318/v1/logs",
        resource=(
            Attribute("owner.tenant", "personal"),
            Attribute("service.name", "doeff.job-log"),
            Attribute("doeff.worker", "zeus-native"),
            Attribute("doeff.job", "worker/zeus-native"),
            Attribute("doeff.attempt", "0"),
            Attribute("doeff.instance", "b0"),
        ),
    )
    assert worker_log_plan(CONTEXT, "zeus-native", "b0") == JobLogOff()


def test_the_body_is_otlp_logs_json_with_one_record_per_line() -> None:
    """本文は OTLP/HTTP の logs の JSON: resource 1 つ・scope 1 つ・1 行 = record 1 つ(timeUnixNano は 10 進の文字・body は刻と改行
    を除いた行 — CR LF の CR も除く・UTF-8 でない byte は置き換えの字)。印は通し番号 doeff.line・切った印 doeff.cut・それまでに捨てた
    行の数 doeff.dropped(0 なら置かない)。"""
    target = JobLogTarget(endpoint="http://c/v1/logs", resource=(Attribute("service.name", "doeff.job-log"),))
    batch = (QueuedLine(1, line(b"one\r")), QueuedLine(3, line(b"bad-\xff", T + 1, cut=True)))
    document = json.loads(logs_body(target, batch, 0, 100))
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
    dropped = json.loads(logs_body(target, batch[:1], 7, 100))["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]
    assert {"key": "doeff.dropped", "value": {"intValue": "7"}} in dropped


@dataclass(frozen=True)
class Shipped:
    """送る本文の record 1 つの body と、印 doeff.cut を持つか。"""

    body: str
    cut: bool


def shipped_records(lines: tuple[OpenLine, ...], line_bytes: int) -> list[Shipped]:
    """切った行の並びを、送る本文の record の並びに戻すため(body の上限 = line_bytes)。"""
    target = JobLogTarget(endpoint="http://c/v1/logs", resource=())
    body = logs_body(target, tuple(QueuedLine(n, one) for n, one in enumerate(lines, 1)), 0, line_bytes)
    return [
        Shipped(r["body"]["stringValue"], {"key": "doeff.cut", "value": {"boolValue": True}} in r["attributes"])
        for r in json.loads(body)["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    ]


def test_the_body_keeps_the_line_limit_in_utf8_bytes_after_replacement() -> None:
    """本番の上限で、送る body は UTF-8 で上限以下(取り決め job-log/1 の 1 行の上限 — 読んだ byte でなく送る body で数える)。
    失敗ケース: UTF-8 でない byte だけの 10 KiB の行は、読んだ byte で上限に切っても置き換えの字(3 byte)で 3 倍に伸びる — 伸びた分も
    字の境で落とし、印 doeff.cut を付ける。上限より短くても置き換えで上限を越える行も、同じく落として印を付ける。"""
    limit = ShipLimits().line_bytes
    long_bad = cut_lines(b"\xff" * (10 << 10) + b"\n", None, T, limit).closed
    short_bad = cut_lines(b"\xff" * (limit // 2) + b"\n", None, T, limit).closed
    for record in shipped_records((*long_bad, *short_bad), limit):
        assert len(record.body.encode("utf-8")) <= limit
        assert record.cut


def test_a_character_split_by_the_cut_is_dropped_at_the_character_boundary() -> None:
    """字の途中で切れた行は、切れた字を落として字の境で終わる(置き換えの字を末尾に残して上限を越えない)。上限ちょうどの UTF-8 の行は
    切らず、印も付けない。"""
    split = cut_lines("あ".encode() * 5 + b"\n", None, T, 8).closed
    whole = cut_lines(b"abcdefgh\n", None, T, 8).closed
    assert shipped_records((*split, *whole), 8) == [Shipped("ああ", True), Shipped("abcdefgh", False)]


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
    """OTLP の受け手(検の process の中の HTTP の server): POST /v1/logs の本文を残す。hang の時は放すまで答えない。delay の時は本文を
    残してから delay 秒待って答える(遅い送り先 — 送り手が待ちを切っても、受けた本文は残る)。"""

    def __init__(self, hang: bool, delay: float = 0.0) -> None:
        """127.0.0.1 の空いた port で受け始めるため。"""
        self.hang = hang
        self.delay = delay
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
                if collector.delay:
                    collector.released.wait(collector.delay)
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
def slow() -> Iterator[Collector]:
    """POST ごとに答えるまで 2 秒かかる受け手を立てて、終わりに止めるため(送りの終わりの上限 ShipLimits.end_seconds 0.5 秒より長い)。"""
    running = Collector(hang=False, delay=2.0)
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


WARM_JOB = "tests.fixtures.warm_job"


@LINUX_ONLY
def test_a_job_forked_from_the_warm_child_ships_its_lines(
    tmp_path: Path, collector: Collector, start_warm_child: StartWarmChild
) -> None:
    """待ちの子から分かれた子 A も、頼みの env に送り先が在れば同じ部品で行を送る(job の名は task/<id>)。socket の path が AF_UNIX の
    上限に収まるよう、置き場は /tmp の下の短い dir。"""
    short = Path(tempfile.mkdtemp(prefix="wl-", dir="/tmp"))
    warm = start_warm_child(short / "root", short / "s", short / "r.json", (WARM_JOB,))
    try:
        # 準備は温めた Hy の code の置き場で数秒(冷たい compile は 25 秒以上)— 期限は目安の READY_SECONDS で、越えたら置き場の名で落とす。
        wait_ready_file(warm, short / "r.json")
        deadline = time.monotonic() + END_LIMIT_SECONDS
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


# --- worker の process 自身の行(card ki-35923a5895b2 — 機体の systemd・launchd と同じ持ち方の本物の process)-----------------------

WORKER_PROCESS = """
import os, sys
from doeff_cluster.worker.entry.line_stamp import process_lines, worker_log_plan
process_lines(worker_log_plan(os.environ, "zeus-native", "b0"))
print("worker: 起動します boot=b0", file=sys.stderr, flush=True)
print("worker: heartbeat の gap", flush=True)
raise RuntimeError("worker-crashed-at-the-end")
"""


@dataclass(frozen=True)
class WorkerRan:
    """worker の代わりの process の 1 回: code = 終了コード・out と err = stdout と stderr の file の中身(同じ file なら同じ)。"""

    code: int
    out: bytes
    err: bytes
    seconds: float


def worker_run(tmp: Path, env: dict[str, str], same_file: bool, script: str = WORKER_PROCESS) -> WorkerRan:
    """worker の入口と同じ呼び方で自分の行を差し替える python の 1 本(script)を、systemd の StandardOutput=append: と
    StandardError=append: の持ち方(同じ path を 2 度開く — same_file が偽なら別の 2 つの file)で走らせるため。env = 検の process の
    環境に足す環境変数。seconds = 起こしてから終わるまで。"""
    out_path = tmp / "worker.log"
    err_path = out_path if same_file else tmp / "worker.err.log"
    argv = ["env", *(f"{name}={value}" for name, value in env.items()), sys.executable, "-B", "-c", script]
    started = time.monotonic()
    with open(out_path, "ab") as out, open(err_path, "ab") as err:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
    code = process.wait(timeout=END_LIMIT_SECONDS)
    seconds = time.monotonic() - started
    return WorkerRan(code=code, out=out_path.read_bytes(), err=err_path.read_bytes(), seconds=seconds)


def test_the_worker_process_ships_its_own_lines_and_its_crash_traceback(tmp_path: Path, collector: Collector) -> None:
    """送り先が在り、stdout と stderr が同じ file の worker は、自分の行を刻つきで file へ書き、同じ行を job の名 worker/<名>・試み 0・
    世代 boot で送る。捕まえない例外で落ちても、traceback の行は file と送り先の両方に在る(書き手の終わりは excepthook の後)。"""
    env = {JOB_LOG_OTLP_VAR: collector.base(), JOB_LOG_ATTRIBUTES_VAR: "owner.tenant=personal"}
    ran = worker_run(tmp_path, env, same_file=True)
    assert ran.code == 1, ran.out
    lines = file_lines(ran.out)
    bodies = [x.body for x in lines]
    assert bodies[:3] == ["worker: 起動します boot=b0".encode(), "worker: heartbeat の gap".encode(), b"Traceback (most recent call last):"]
    assert bodies[-1] == b"RuntimeError: worker-crashed-at-the-end", bodies
    records = received_until(collector, len(lines))
    assert [r.body for r in records] == [x.body.decode() for x in lines]
    assert [r.at_ns // 1_000_000 for r in records] == [x.at_ms for x in lines]
    assert {r.resource for r in records} == {
        (
            ("owner.tenant", "personal"),
            ("service.name", "doeff.job-log"),
            ("doeff.worker", "zeus-native"),
            ("doeff.job", "worker/zeus-native"),
            ("doeff.attempt", "0"),
            ("doeff.instance", "b0"),
        )
    }


def test_a_worker_without_the_destination_keeps_its_output_as_it_is(tmp_path: Path, collector: Collector) -> None:
    """送り先が無い worker(Pod の worker の形)は、出力の向きを替えない — 行は刻を付けずにそのまま file に在り、何も送らない。"""
    ran = worker_run(tmp_path, {}, same_file=True)
    assert ran.code == 1, ran.out
    assert ran.out.startswith("worker: 起動します boot=b0\nworker: heartbeat の gap\nTraceback".encode()), ran.out
    assert collector.records() == []


def test_a_worker_whose_stdout_and_stderr_are_two_files_keeps_them_and_names_why(tmp_path: Path, collector: Collector) -> None:
    """送り先が在っても stdout と stderr が別の file なら、向きを替えずに(刻を付けない・2 つの file のまま)送らない訳を 1 行 stderr に
    書き、worker はそのまま走る。"""
    ran = worker_run(tmp_path, {JOB_LOG_OTLP_VAR: collector.base()}, same_file=False)
    assert ran.code == 1, ran.err
    assert ran.out == "worker: heartbeat の gap\n".encode(), ran.out
    assert ran.err.startswith(
        "line_stamp: worker の行を送らない — stdout と stderr が同じ file でない\nworker: 起動します boot=b0\nTraceback".encode()
    ), ran.err
    assert collector.records() == []


OUTLIVING_DESCENDANT = """
import os, subprocess, sys
from doeff_cluster.worker.entry.line_stamp import process_lines, worker_log_plan
process_lines(worker_log_plan(os.environ, "zeus-native", "b0"))
print("worker: 子を起こして終わる", file=sys.stderr, flush=True)
# lifeline の見張りと同じ持ち方の子孫: 自分の session・stdout と stderr を指定しない(worker の fd 1・2 を継ぐ)・worker より長く生きる。
subprocess.Popen(["sh", "-c", "sleep 0.5; echo descendant-after-the-worker >&2"], stdin=subprocess.DEVNULL, start_new_session=True)
"""


def test_a_descendant_that_outlives_the_worker_still_writes_to_the_file(tmp_path: Path, collector: Collector) -> None:
    """worker が差し替えるのは Python の流れだけで、fd 1・2 は元の file のまま。worker より長く生きる子孫(lifeline の見張りと同じ持ち方)が
    worker の終わった後に stderr へ書いた行も、刻なしで file に在る(読み手の居ない pipe へ行かない)。子孫の行は worker の行として送らない。"""
    ran = worker_run(tmp_path, {JOB_LOG_OTLP_VAR: collector.base()}, same_file=True, script=OUTLIVING_DESCENDANT)
    assert ran.code == 0, ran.out
    log = tmp_path / "worker.log"
    deadline = time.monotonic() + RECEIVE_LIMIT_SECONDS
    while b"descendant-after-the-worker" not in log.read_bytes() and time.monotonic() < deadline:
        time.sleep(0.05)
    lines = log.read_bytes().split(b"\n")
    assert b"descendant-after-the-worker" in lines, lines
    assert file_line(lines[0]).body == "worker: 子を起こして終わる".encode(), lines
    assert [r.body for r in collector.records()] == ["worker: 子を起こして終わる"]


FLOODING_WORKER = """
import os, sys
from doeff_cluster.worker.entry.line_stamp import process_lines, worker_log_plan
process_lines(worker_log_plan(os.environ, "zeus-native", "b0"))
for n in range(40_000):
    print(f"worker: burst line {n:06d}")
"""


def test_a_flooding_worker_does_not_wait_for_a_silent_destination(tmp_path: Path, silent: Collector) -> None:
    """worker の道(process_lines が作る書き手と送り手 — 本番の上限)で、答えない送り先の下で溜めの上限(1 万行)の 4 倍の行を書いても、
    worker の write も終わりも送りに待たされない(溢れは捨てて数える)。file には全部の行が在る。待つ形にすると、塊ごとに待って
    数十秒になる(設計検証 lt-8PQM3S5DDHCSJ92Q7X259K9HAW の盲検 B — 実測 27〜30 秒・待たない形は 1〜2 秒)。"""
    ran = worker_run(tmp_path, {JOB_LOG_OTLP_VAR: silent.base()}, same_file=True, script=FLOODING_WORKER)
    assert ran.code == 0, ran.out[-500:]
    assert ran.out.count(b"worker: burst line ") == 40_000
    assert ran.seconds < 10.0, ran.seconds


# --- coordinator の process 自身の行(card ki-0b0ab40048b9 — Pod の log は Pod の入れ替えで消える)--------------------------------------

POD = {POD_NAME_VAR: "coordinator-5f-abc", POD_NAMESPACE_VAR: "lab-ns"}


def test_the_coordinator_names_itself_after_its_pod() -> None:
    """coordinator の process 自身の行は job の名 coordinator/<Pod の namespace>(Pod が入れ替わっても同じ名で読む)・名乗り = Pod の名・
    試み 0・世代 = 渡した起動の刻で名乗る。送り先が無ければ送らない。送り先が在るのに Pod の名乗りが欠ければ、欠けた名を挙げて送らない。"""
    environ = {**CONTEXT, **POD, JOB_LOG_OTLP_VAR: "http://collector:4318", JOB_LOG_ATTRIBUTES_VAR: "owner.tenant=personal"}
    assert coordinator_log_plan(environ, "1791234567891") == JobLogTarget(
        endpoint="http://collector:4318/v1/logs",
        resource=(
            Attribute("owner.tenant", "personal"),
            Attribute("service.name", "doeff.job-log"),
            Attribute("doeff.worker", "coordinator-5f-abc"),
            Attribute("doeff.job", "coordinator/lab-ns"),
            Attribute("doeff.attempt", "0"),
            Attribute("doeff.instance", "1791234567891"),
        ),
    )
    assert coordinator_log_plan(POD, "1") == JobLogOff()
    assert coordinator_log_plan({}, "1") == JobLogOff()
    assert coordinator_log_plan({JOB_LOG_OTLP_VAR: "http://collector:4318", POD_NAMESPACE_VAR: "lab-ns"}, "1") == JobLogRefused(
        "coordinator の名乗りの環境変数が無い: POD_NAME"
    )


@dataclass(frozen=True)
class CoordinatorRan:
    """本物の coordinator の入口の 1 回: code = 終了コード・out と err = stdout と stderr の file の中身(別の 2 つの file)。"""

    code: int
    out: bytes
    err: bytes


def coordinator_run(tmp: Path, env: dict[str, str], before_stop: Callable[[], object] | None = None) -> CoordinatorRan:
    """本物の coordinator の入口(boot.sh と同じ `hy -m doeff_cluster._coordinator.entry.main`)を、k8s の container と同じく stdout と
    stderr を別の 2 つの先へ向けて起こし、受け始めたら止めの合図(SIGTERM — Pod の止めと同じ)を送って終わらせるため。env = 足す環境変数・
    before_stop = 受け始めた後、止めの合図の前に呼ぶ物(例 受け手が最初の POST を受けるまで待つ)。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    hy_command = str(Path(sys.executable).parent / "hy")
    argv = ["env", *(f"{name}={value}" for name, value in env.items()), hy_command, "-m", "doeff_cluster._coordinator.entry.main",
            "--state-file", str(tmp / "coord" / "state.json"), "--port", str(port), "--notice-broker", "memory"]
    (tmp / "coord").mkdir()
    out_path, err_path = tmp / "coordinator.out", tmp / "coordinator.err"
    with open(out_path, "ab") as out, open(err_path, "ab") as err:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err, cwd=str(tmp))
    try:
        deadline = time.monotonic() + 90.0
        while True:
            assert process.poll() is None, err_path.read_bytes()[-2000:] + out_path.read_bytes()[-2000:]
            assert time.monotonic() < deadline, "coordinator が 90 秒で答えなかった"
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/livez", timeout=1.0):
                    break
            except OSError:
                time.sleep(0.1)
        if before_stop is not None:
            before_stop()
        process.send_signal(signal.SIGTERM)
        code = process.wait(timeout=END_LIMIT_SECONDS)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    return CoordinatorRan(code=code, out=out_path.read_bytes(), err=err_path.read_bytes())


def test_the_coordinator_ships_its_own_lines_from_two_pipes_until_it_stops(tmp_path: Path, collector: Collector) -> None:
    """送り先と Pod の名乗りが在る coordinator は、stdout と stderr が別の先(k8s の container の 2 本の pipe)でも 2 つを 1 本にまとめ、
    自分の行(受け始めの行から止まりの行まで)を刻つきで stdout へ書き、同じ行を job の名 coordinator/<namespace>・名乗り Pod の名で
    送る。止まりの行も送ってから終わる(書き手の終わりは process の終わり)。"""
    env = {JOB_LOG_OTLP_VAR: collector.base(), JOB_LOG_ATTRIBUTES_VAR: "owner.tenant=personal", **POD}
    ran = coordinator_run(tmp_path, env)
    assert ran.code == 0, ran.err[-2000:]
    lines = file_lines(ran.out)
    bodies = [x.body for x in lines]
    assert any(b.startswith(b"coordinator: :") and "で受けます".encode() in b for b in bodies), bodies
    assert bodies[-1] == "coordinator: 止まりました".encode(), bodies
    assert "で受けます".encode() not in ran.err, ran.err
    records = received_until(collector, len(lines))
    assert [r.body for r in records] == [x.body.decode() for x in lines]
    assert {dict(r.resource)["doeff.job"] for r in records} == {"coordinator/lab-ns"}
    assert {dict(r.resource)["doeff.worker"] for r in records} == {"coordinator-5f-abc"}
    assert {dict(r.resource)["doeff.attempt"] for r in records} == {"0"}


def test_a_coordinator_without_the_destination_keeps_its_two_streams(tmp_path: Path, collector: Collector) -> None:
    """送り先が無い coordinator(手元の process・送り先を置かない宣言)は、出力の向きを替えない — 行は刻を付けずに stderr のまま在り、
    何も送らない。"""
    ran = coordinator_run(tmp_path, dict(POD))
    assert ran.code == 0, ran.err[-2000:]
    assert ran.err.endswith("coordinator: 止まりました\n".encode()), ran.err[-500:]
    assert STAMP.match(ran.err) is None, ran.err[:200]
    assert collector.records() == []


def test_a_slow_destination_still_gets_the_stop_line_of_the_coordinator(tmp_path: Path, slow: Collector) -> None:
    """送り先が遅くても(POST ごとに 2 秒 — 送りの既定の終わりの上限 0.5 秒より長い)、coordinator は止まりの行まで送ってから終わる
    (盲検 A — 止まる直前の行が、Pod の入れ替えの後に一番要る行。coordinator の止めの猶予は Pod の 30 秒で、0.5 秒に縛られない)。"""
    env = {JOB_LOG_OTLP_VAR: slow.base(), JOB_LOG_ATTRIBUTES_VAR: "owner.tenant=personal", **POD}
    # 止めの合図は、受け手が最初の POST を受けた後(その POST の答えを待っている 2 秒の最中)に送る — 止まりの行はその後ろに並ぶ。
    ran = coordinator_run(tmp_path, env, before_stop=lambda: received_until(slow, 1))
    assert ran.code == 0, ran.err[-2000:]
    lines = file_lines(ran.out)
    records = received_until(slow, len(lines))
    assert [r.body for r in records] == [x.body.decode() for x in lines]
    assert records[-1].body == "coordinator: 止まりました"


@pytest.mark.parametrize("names", [{POD_NAME_VAR: "coordinator-5f-abc"}, {"HOSTNAME": "coordinator-5f-abc"}])
def test_a_coordinator_without_its_pod_names_ships_nothing_and_names_why(
    tmp_path: Path, collector: Collector, names: dict[str, str]
) -> None:
    """送り先は在るが宣言が Pod の名乗り(POD_NAME・POD_NAMESPACE)を置き忘れた coordinator は、入口で名を補わずに(HOSTNAME・既定の
    namespace を読まない)送らない訳を 1 行出し、何も送らない(盲検 B — 入口が名乗りを補うと、送るかの判断が coordinator_log_plan から
    入口へ移り、別の namespace の coordinator が共有の名で行を混ぜる)。"""
    env = {JOB_LOG_OTLP_VAR: collector.base(), JOB_LOG_ATTRIBUTES_VAR: "owner.tenant=personal", **names}
    ran = coordinator_run(tmp_path, env)
    assert ran.code == 0, ran.err[-2000:]
    assert "line_stamp: 自分の行を送らない — coordinator の名乗りの環境変数が無い: ".encode() in ran.err, ran.err[:500]
    assert ran.err.endswith("coordinator: 止まりました\n".encode()), ran.err[-500:]
    assert collector.records() == []
