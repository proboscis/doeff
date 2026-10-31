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
pipe と file の書きの溜めに残っていた分と末尾の改行は残らない(shim の残る穴と同じ所)。

file の書き(機体の log の置き場の disk が詰まると、log の file への write が 1〜16 秒返らない事が、1 つの常駐の
job の 8 日分の log で 1 秒越え 1126 回在った。前の形は読みの thread が file へ書き終えるまで pipe を読まなかったので、詰まりが続くと
pipe(64 KiB)が満ち、job の stdout・stderr への write が止まり、job の main thread と event loop が止まる): 読みの thread は pipe を読んで
刻を差し込むだけにし、file への write は別の thread(FileFeed)が溜めから受けた順に書く。溜めは上限つき(FeedLimits.queue_bytes)で、
越えた塊は捨てて数え、その位置に刻つきの 1 行の知らせ(捨てた byte の数)を書く — file の書きが詰まっても job の write を止めない
(送りの溜めと同じ考え方)。終わり(end)は、読みの thread が pipe を読み切った後に、溜めに在る分を file へ書き終えるまで待つ(前の形と
同じく file を欠かない)。

送り(取り決め job-log/1): 子の環境に送り先の基の URL(DOEFF_JOB_LOG_OTLP)が在る時だけ、file へ書く行を
OTLP/HTTP の logs(JSON・送り先 = 基の URL + /v1/logs)としても送る(無ければ今と同じ — 送り手を作らない)。機体の外から job の log を
読むため。
- 1 行 = log の record 1 つ: timeUnixNano = その行の最初の byte を読んだ壁の時計(file の行の頭の刻と同じ読み)・body = 刻の頭と改行を
  除いた行。resource 属性 = service.name=doeff.job-log・doeff.worker・doeff.job(task は task/<id>)・doeff.attempt・doeff.instance
  (worker が子へ置く文脈の環境変数から)と、配備が DOEFF_JOB_LOG_ATTRIBUTES(`名=値` を `,` で並べる)で足す属性(送り先が行を振り分ける
  名乗りなど — この部品は上に載る系の属性の名を持たない。同じ名なら doeff の 5 つが勝つ)。
- 送りは job の write を止めない: 書き手の thread は行を切って上限つきの溜め(行の数と byte)へ入れるだけで待たない。溢れた行は捨てて
  数える。束ねて POST するのは別の送りの thread(送り先が遅い・落ちている時も、file へは今まで通り全部書く — 書けなければ読み捨てるのと
  同じ考え方)。1 行の中身は上限(ShipLimits.line_bytes)で切って送る(file には切らずに書く)。上限は送る body の UTF-8 の byte で
  守る — 読んだ byte で切った後、置き換えの字(3 byte)で伸びた分と字の途中で切れた分も、字の境で落とす。
- 終わり(end): 書き手が pipe を読み切ってから、残りの行を短い上限(ShipLimits.end_seconds)の内で送って止める — 送りが file の書きの
  後なので、送りが遅れて shim が KILL に先を越されても file は欠けない。上限の後に残った行は捨てて数える。
- 送り先の値が使えない(URL の形・属性の並べ方・job の文脈の欠け)時は送らず、理由を 1 行 log に書く(job はそのまま走る)。

worker の process 自身の行(機体の上で直に動く worker(zeus-native・mac-native)の log は systemd・launchd の
出力先の file にしか無く、会話から読めなかった): worker の入口(worker/entry/main)も同じ刻の付け方と送り手で、自分の Python の
出力(sys.stdout と sys.stderr)を刻つきの書き手へ差し替えて送る(process_lines・stamped_streams)。job の文脈は worker が自分で
置く(worker_log_plan — job の名 worker/<worker の名>・試み 0・世代 = worker の boot)。差し替えるのは送り先が使えて、stdout と
stderr が同じ file を指す時だけ(機体の形 — 送り先の無い Pod の worker は今のまま)。
- fd 1・2 は替えない(job の shim と違う所): worker の子孫(lifeline の見張りのように worker より長く生きる子)は fd 2 を継ぐので、
  fd を pipe にすると、worker が終わった後の子孫の書きは読み手の居ない pipe へ行き、行が消えて子孫が止まる。
  替えるのは Python の流れの物だけで、子孫と、fd へ直に書く物(Rust の panic など)は
  元の file へ刻なしで書く(送らない)。
- 書き手は送りに待たされない(job と同じ — 溢れは捨てて数える)。worker の道で溜めが満ちても worker の write が止まらない事は
  tests/test_job_log_ship.py の検が見張る(盲検 B)。
- 書き手の終わりは process の終わり(atexit)で、捕まえない例外の traceback(excepthook が atexit より前に sys.stderr へ書く)も
  file へ書いて送ってから止める。

coordinator の process 自身の行(coordinator の Pod の log は版上げの Pod の入れ替えで消え、全部の worker が
coordinator に届かなかった窓の coordinator の側を後から読めなかった): coordinator の入口(coordinator/entry/main)も worker と同じ
process_lines で、自分の Python の出力を送る。job の文脈は coordinator が置く(coordinator_log_plan — job の名 coordinator/<Pod の
namespace>(Pod が入れ替わっても同じ名で読む)・worker の名の欄 = Pod の名・試み 0・世代 = process の起動の刻)。Pod の名と
namespace は宣言が downward API で env に置く(POD_NAME・POD_NAMESPACE)。
- k8s の container の stdout と stderr は 2 本の別の pipe だが、2 つは同じ container の log に入る。coordinator は 2 つを 1 本に
  まとめて(merged — 同じ file でなくても差し替え、刻つきの行を stdout の pipe へ書く)送る。fd 1・2 は worker と同じく替えない。
- 終わりに残りを送る上限(ShipLimits.end_seconds)は、載っている process の止め方の知識(shim は worker の掃除の余裕・coordinator は
  Pod の止めの猶予)で、この部品は既定(shim の値)だけを持つ。process_lines は上限(limits)を受け、coordinator の入口が自分の値を
  渡す(盲検 A — 既定の 0.5 秒では、POST の答えを待つ間に止まると、止まる直前の行が送られずに消えた)。
"""

import atexit
import os
import select
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from itertools import accumulate, islice
from json import dumps
from typing import Protocol, TextIO
from urllib.parse import urlsplit

# 層 entry の文脈と役の名乗り(DOEFF104)— 隣の shim.py と同じ。
MODULE_TAGS = {"context": "worker", "role": "main"}

# 1 回に読む上限(byte)。
CHUNK_BYTES = 1 << 16
# 終わりの読み切りの上限(秒): 引き取りを使えずに残った子孫が書き続けても、shim の終わりを延ばし続けない。
DRAIN_LIMIT_SECONDS = 1.0

# 送りの環境変数の名。DOEFF_JOB_LOG_* は worker の環境から子へ継ぐ(worker/core/launch の CHILD-ENV-ALLOWED — 名が揃う事は
# tests/test_job_log_ship.py が確かめる)。DOEFF_WORKER_* は worker が子へ置く文脈(名の定義点は shared/core/run_context_rules — shim は
# Hy を読み込まないので、ここは同じ綴りを読むだけ)。
JOB_LOG_OTLP_VAR = "DOEFF_JOB_LOG_OTLP"
JOB_LOG_ATTRIBUTES_VAR = "DOEFF_JOB_LOG_ATTRIBUTES"
WORKER_NAME_VAR = "DOEFF_WORKER_NAME"
WORKER_JOB_VAR = "DOEFF_WORKER_JOB"
WORKER_ATTEMPT_VAR = "DOEFF_WORKER_ATTEMPT"
WORKER_INSTANCE_VAR = "DOEFF_WORKER_INSTANCE"
JOB_LOG_ENV_NAMES = (JOB_LOG_OTLP_VAR, JOB_LOG_ATTRIBUTES_VAR, WORKER_NAME_VAR, WORKER_JOB_VAR, WORKER_ATTEMPT_VAR, WORKER_INSTANCE_VAR)
# 送りの名乗り(resource 属性 service.name と scope の名)と、基の URL に足す path。
JOB_LOG_SERVICE = "doeff.job-log"
LOGS_PATH = "/v1/logs"
# worker の process 自身の行の job の文脈(頭の註の「worker の process 自身の行」): job の名の頭(task の task/<id> と同じく `/` で区切る —
# 宣言の job の名と重ならない)と試みの番号(job の試みは 1 から数える — worker の process は job の試みでない)。
WORKER_PROCESS_JOB_PREFIX = "worker/"
WORKER_PROCESS_ATTEMPT = "0"
# worker_log_plan が worker の環境から読む名(worker の入口はこの組だけを読んで渡す — 名を 2 か所に並べない)。
WORKER_LOG_ENV_NAMES = (JOB_LOG_OTLP_VAR, JOB_LOG_ATTRIBUTES_VAR)
# coordinator の process 自身の行の job の文脈(頭の註の「coordinator の process 自身の行」): job の名の頭(worker/ と同じく `/` で区切る)と、
# 宣言が downward API で置く Pod の名乗りの 2 つの名。coordinator_log_plan が読む名の組(coordinator の入口はこの組だけを読んで渡す)。
COORDINATOR_PROCESS_JOB_PREFIX = "coordinator/"
POD_NAME_VAR = "POD_NAME"
POD_NAMESPACE_VAR = "POD_NAMESPACE"
COORDINATOR_LOG_ENV_NAMES = (JOB_LOG_OTLP_VAR, JOB_LOG_ATTRIBUTES_VAR, POD_NAME_VAR, POD_NAMESPACE_VAR)


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
class ShipLimits:
    """送りの上限(頭の註の「送り」): queue_lines と queue_bytes = 送りを待つ行の溜めの上限(行の数と中身の byte — 越える行は捨てて
    数える)・line_bytes = 1 行の中身(送る body の UTF-8)の上限(越えた分は送らず、行に印 doeff.cut を付ける — 取り決め job-log/1 の
    1 行の上限。定義元は使い手の repo の deploy/claude-telemetry/claude-telemetry.yaml で、値と印の名が合う事は同じ repo の
    job の log の経路の検 test_job_log_route.hy が見張る)・batch_lines と batch_bytes = 1 回の POST に
    束ねる上限・interval_seconds = 束が満ちなくても送るまでの間・post_seconds = 1 回の POST の待ちの上限・end_seconds = 終わり(end)に
    残りを送る上限(shim の掃除の余裕 WorkerPolicy.shim-sweep-margin-ms 1.5 秒の内に、読み切りの上限 DRAIN_LIMIT_SECONDS と並べて
    収まる値 — 既定は shim の値。worker・coordinator の process 自身の行は、止め方を持つ入口が process_lines に自分の上限を渡す)・
    pause_seconds = POST が失敗した後、次の POST までの間(落ちた送り先を叩き続けない)。"""

    queue_lines: int = 10_000
    queue_bytes: int = 4 << 20
    line_bytes: int = 4 << 10
    batch_lines: int = 1_000
    batch_bytes: int = 1 << 20
    interval_seconds: float = 1.0
    post_seconds: float = 5.0
    end_seconds: float = 0.5
    pause_seconds: float = 2.0


# 本番の送りの上限(検は小さい上限を渡す)。
SHIP_LIMITS = ShipLimits()


@dataclass(frozen=True)
class Attribute:
    """OTLP の resource 属性 1 つ(名と文字の値)。"""

    key: str
    value: str


@dataclass(frozen=True)
class JobLogTarget:
    """job の log の送り先: endpoint = POST する URL(基の URL + /v1/logs)・resource = 送る行の全部に付ける resource 属性。"""

    endpoint: str
    resource: tuple[Attribute, ...]


@dataclass(frozen=True)
class JobLogOff:
    """送らない(子の環境に送り先の基の URL が無い — 今までの形)。"""


@dataclass(frozen=True)
class JobLogRefused:
    """送り先の値が使えないので送らない(reason = 何が使えないか — 値そのものは書かない)。"""

    reason: str


JobLogPlan = JobLogTarget | JobLogOff | JobLogRefused


def endpoint_usable(endpoint: str) -> bool:
    """送り先の URL が POST できる形(http か https・host の在る絶対の URL・port が読める)か — 送り手を作る前に、使えない値を断るため。"""
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and (port is None or port > 0)


def extra_attributes(text: str) -> tuple[Attribute, ...] | JobLogRefused:
    """DOEFF_JOB_LOG_ATTRIBUTES(`名=値` を `,` で並べる — OTEL_RESOURCE_ATTRIBUTES と同じ並べ方)を、配備が足す resource 属性の並びに
    するため。空の項は飛ばす。`=` の無い項・名の空の項は断る(何番目の項かを名指す — 値は文に出さない)。"""
    items = tuple(item.strip() for item in text.split(",") if item.strip())
    broken = tuple(index for index, item in enumerate(items, 1) if not item.partition("=")[1] or not item.partition("=")[0].strip())
    if broken:
        return JobLogRefused(f"{JOB_LOG_ATTRIBUTES_VAR} の {broken[0]} 番目の項が `名=値` の形でない")
    return tuple(Attribute(key.strip(), value.strip()) for key, _, value in (item.partition("=") for item in items))


def job_log_plan(environ: Mapping[str, str]) -> JobLogPlan:
    """子の環境変数(shim = この process の環境・待ちの子から分かれた子 A = 頼みの env)から、job の log の送り先を決めるため(頭の註の
    「送り」)。送り先の基の URL が無ければ送らない(JobLogOff)。URL の形・属性の並べ方・job の文脈(worker の名・job の名・試み・世代)の
    欠けは、送らずに理由を返す(JobLogRefused)。"""
    base = environ.get(JOB_LOG_OTLP_VAR, "").strip()
    if not base:
        return JobLogOff()
    endpoint = base.rstrip("/") + LOGS_PATH
    if not endpoint_usable(endpoint):
        return JobLogRefused(f"{JOB_LOG_OTLP_VAR} が http か https の絶対の URL でない")
    context = (WORKER_NAME_VAR, WORKER_JOB_VAR, WORKER_ATTEMPT_VAR, WORKER_INSTANCE_VAR)
    missing = tuple(name for name in context if not environ.get(name, ""))
    if missing:
        return JobLogRefused(f"job の文脈の環境変数が無い: {','.join(missing)}")
    extra = extra_attributes(environ.get(JOB_LOG_ATTRIBUTES_VAR, ""))
    if isinstance(extra, JobLogRefused):
        return extra
    own = (
        Attribute("service.name", JOB_LOG_SERVICE),
        Attribute("doeff.worker", environ[WORKER_NAME_VAR]),
        Attribute("doeff.job", environ[WORKER_JOB_VAR]),
        Attribute("doeff.attempt", environ[WORKER_ATTEMPT_VAR]),
        Attribute("doeff.instance", environ[WORKER_INSTANCE_VAR]),
    )
    owned = frozenset(attribute.key for attribute in own)
    return JobLogTarget(endpoint=endpoint, resource=tuple(a for a in extra if a.key not in owned) + own)


def worker_log_plan(environ: Mapping[str, str], worker: str, instance: str) -> JobLogPlan:
    """worker の process 自身の行の送り先を決めるため(頭の註の「worker の process 自身の行」)。environ = worker の環境(読むのは
    WORKER_LOG_ENV_NAMES — 送り先の基の URL と配備が足す属性だけ・job の文脈の名は読まない)・worker = 名乗る worker の名・instance =
    この process の世代(boot)。job の文脈は worker が置く: job の名 worker/<worker の名>・試み 0・世代 instance。決め方は job の子と
    同じ job_log_plan。"""
    return process_log_plan(environ, worker, WORKER_PROCESS_JOB_PREFIX + worker, instance)


def coordinator_log_plan(environ: Mapping[str, str], instance: str) -> JobLogPlan:
    """coordinator の process 自身の行の送り先を決めるため(頭の註の「coordinator の process 自身の行」)。environ = coordinator の環境
    (読むのは COORDINATOR_LOG_ENV_NAMES)・instance = この process の世代(起動の刻)。job の名 coordinator/<POD_NAMESPACE>・名乗り
    POD_NAME・試み 0。送り先が在るのに Pod の名乗りが欠けていれば、欠けた名を挙げて送らない。"""
    missing = tuple(name for name in (POD_NAME_VAR, POD_NAMESPACE_VAR) if not environ.get(name, ""))
    if missing and environ.get(JOB_LOG_OTLP_VAR, "").strip():
        return JobLogRefused(f"coordinator の名乗りの環境変数が無い: {','.join(missing)}")
    namespace = environ.get(POD_NAMESPACE_VAR, "")
    return process_log_plan(environ, environ.get(POD_NAME_VAR, ""), COORDINATOR_PROCESS_JOB_PREFIX + namespace, instance)


def process_log_plan(environ: Mapping[str, str], worker: str, job: str, instance: str) -> JobLogPlan:
    """worker・coordinator の process 自身の行の送り先を、process が置く job の文脈(名乗り worker・job の名 job・試み 0・世代
    instance)で決めるため。environ から読むのは送り先の基の URL と配備が足す属性だけ(job の文脈の名は読まない)。決め方は job の子と
    同じ job_log_plan。"""
    shipping = {name: environ[name] for name in WORKER_LOG_ENV_NAMES if name in environ}
    context = {
        WORKER_NAME_VAR: worker,
        WORKER_JOB_VAR: job,
        WORKER_ATTEMPT_VAR: WORKER_PROCESS_ATTEMPT,
        WORKER_INSTANCE_VAR: instance,
    }
    return job_log_plan({**shipping, **context})


@dataclass(frozen=True)
class OpenLine:
    """送る行 1 つ(閉じた行か、まだ改行が来ていない書きかけの行): at_ns = 行の最初の byte を読んだ壁の時計(epoch ns — file の行の頭の
    刻と同じ読み)・head = 行の頭から line_bytes までの byte(改行は含まない)・cut = 行がそれより長いか。"""

    at_ns: int
    head: bytes
    cut: bool


@dataclass(frozen=True)
class CutLines:
    """読んだ byte 1 塊を送る行に切った答え: closed = 改行で閉じた行(順のまま)・open = 塊の末尾の書きかけの行(無ければ None)。"""

    closed: tuple[OpenLine, ...]
    open: OpenLine | None


def extended(line: OpenLine, piece: bytes, line_bytes: int) -> OpenLine:
    """行の続きの byte を、1 行の上限まで行の中身へ足すため(上限を越えた分は捨てて印 cut を立てる — 長い行も memory を食わない)。"""
    room = max(0, line_bytes - len(line.head))
    return OpenLine(at_ns=line.at_ns, head=line.head + piece[:room], cut=line.cut or len(piece) > room)


def cut_lines(chunk: bytes, open_line: OpenLine | None, at_ns: int, line_bytes: int) -> CutLines:
    """読んだ byte 1 塊を送る行に切るため。open_line = 前の塊の末尾の書きかけの行・at_ns = この塊を読んだ壁の時計。この塊で始まる行は
    at_ns を、前の塊から続く行はその行の最初の byte の刻を持つ(file の刻の頭と同じ — stamped は改行の次の byte が来た時に刻を置く)。
    改行で終わる塊は書きかけの行を残さない(次の行は次の塊の刻で始まる)。"""
    *inner, tail = chunk.split(b"\n")
    fresh = OpenLine(at_ns=at_ns, head=b"", cut=False)
    if not inner:
        return CutLines(closed=(), open=extended(open_line or fresh, tail, line_bytes) if tail else open_line)
    first = extended(open_line or fresh, inner[0], line_bytes)
    rest = tuple(extended(fresh, piece, line_bytes) for piece in inner[1:])
    return CutLines(closed=(first, *rest), open=extended(fresh, tail, line_bytes) if tail else None)


@dataclass(frozen=True)
class QueuedLine:
    """送りを待つ行 1 つ: number = この書き手が読んだ行の通し番号(1 から — 捨てた行も数えるので、届いた行の番号の飛びが捨てた行。
    同じ刻の行の順も読み手が戻せる)・line = 切った行。"""

    number: int
    line: OpenLine


@dataclass(frozen=True)
class ShipCounts:
    """送り手の数え: seen = 読んだ行の数(通し番号の最後)・dropped = 送らずに捨てた行の数(溜めの溢れ・失敗した POST・終わりの上限の
    後に残った行)・queued_bytes = 溜めの中の行の中身の byte の和。"""

    seen: int = 0
    dropped: int = 0
    queued_bytes: int = 0


@dataclass(frozen=True)
class Batch:
    """1 回の POST に束ねた行(溜めの順のまま)・その POST の待ちの上限(秒 — 終わりの後は終わりの上限までの残り)・ending = 終わり
    (end)の後に取った束か(その POST が失敗したら、残りを送らずに止める)。"""

    lines: tuple[QueuedLine, ...]
    timeout: float
    ending: bool


def admitted(lines: tuple[OpenLine, ...], counts: ShipCounts, queued_lines: int, limits: ShipLimits) -> int:
    """新しい行の並びのうち、溜めの上限(行の数と byte)に収まる頭からの行の数 — 溢れた所から後ろは捨てる(書き手を待たせない)。"""
    totals = accumulate(len(line.head) for line in lines)
    return sum(
        1
        for index, total in enumerate(totals, 1)
        if queued_lines + index <= limits.queue_lines and counts.queued_bytes + total <= limits.queue_bytes
    )


def batch_size(queued: deque[QueuedLine], limits: ShipLimits) -> int:
    """溜めの頭から 1 回の POST に束ねる行の数(行の数と byte の上限の内 — 少なくとも 1 行)。"""
    totals = accumulate(len(item.line.head) for item in islice(queued, limits.batch_lines))
    return max(1, sum(1 for total in totals if total <= limits.batch_bytes)) if queued else 0


def any_value(value: str | int | bool) -> dict[str, object]:
    """OTLP の JSON の値 1 つ(AnyValue — 整数は 10 進の文字で書く)にするため。"""
    match value:
        case bool():
            return {"boolValue": value}
        case int():
            return {"intValue": str(value)}
        case str():
            return {"stringValue": value}


def record_json(item: QueuedLine, dropped: int, line_bytes: int) -> dict[str, object]:
    """送る行 1 つを OTLP の log の record 1 つ(JSON)にするため。body = 刻の頭と改行(CR LF の CR も)を除いた行 — UTF-8 でない byte は
    置き換えの字にする(file には元の byte のまま在る)。置き換えた後の UTF-8 が line_bytes を越えれば、越えた分を字の境で落とす(読んだ
    byte で切っても、置き換えの字と字の途中の切れ目で伸びる)。印は 3 つ: 通し番号の doeff.line・中身を切った印の doeff.cut(読んだ
    byte で切った行と、ここで落とした行)・それまでに捨てた行の数の doeff.dropped(0 なら置かない)。"""
    head = item.line.head
    text = (head[:-1] if head.endswith(b"\r") and not item.line.cut else head).decode("utf-8", errors="replace")
    encoded = text.encode("utf-8")
    trimmed = len(encoded) > line_bytes
    body = encoded[:line_bytes].decode("utf-8", errors="ignore") if trimmed else text
    cut = item.line.cut or trimmed
    marks = (("doeff.line", item.number), *((("doeff.cut", True),) if cut else ()), *((("doeff.dropped", dropped),) if dropped else ()))
    return {
        "timeUnixNano": str(item.line.at_ns),
        "body": {"stringValue": body},
        "attributes": [{"key": key, "value": any_value(value)} for key, value in marks],
    }


def logs_body(target: JobLogTarget, batch: tuple[QueuedLine, ...], dropped: int, line_bytes: int) -> bytes:
    """送る行の束 1 つを、OTLP/HTTP の logs の JSON の本文(resource 1 つ・scope 1 つ — 1 行の body は line_bytes まで)にするため。"""
    resource = [{"key": attribute.key, "value": any_value(attribute.value)} for attribute in target.resource]
    document = {
        "resourceLogs": [
            {
                "resource": {"attributes": resource},
                "scopeLogs": [{"scope": {"name": JOB_LOG_SERVICE}, "logRecords": [record_json(item, dropped, line_bytes) for item in batch]}],
            }
        ]
    }
    return dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Poster(Protocol):
    """送り先 1 つへ本文を POST する口(本物は foundation/otlp_post の OtlpPoster — 検は外の世界の代わりを渡す)。"""

    def posted(self, body: bytes, timeout: float) -> bool:
        """本文 1 つを POST する。答え = 送り先が受けたか。"""
        ...

    def close(self) -> None:
        """接続を閉じる。"""
        ...


def real_poster(endpoint: str) -> Poster:
    """本物の送り(foundation の OtlpPoster)を作るため。読み込みを送りの thread の始めまで遅らせる(http.client の読み込み 約 30 ms を、
    送らない shim と job の起動の道に載せない)。"""
    from doeff_cluster._foundation.otlp_post import OtlpPoster

    return OtlpPoster(endpoint)


class NoShipping:
    """送らない(送り先が無いか使えない時): 書き手の行を受けて何もしない。"""

    def take(self, chunk: bytes, at_ns: int) -> None:
        """何もしない(送らない)。"""

    def finish(self) -> None:
        """何もしない(送らない)。"""

    def begin(self) -> None:
        """何もしない(送りの thread が居ない)。"""

    def end(self) -> None:
        """何もしない(送りの thread が居ない)。"""


class JobLogShipper:
    """job の log の行を OTLP/HTTP で送る送り手(頭の註の「送り」)。書き手の thread が take と finish で行を溜めに入れ(待たない)、
    送りの thread が束ねて送る。作るのは stamped_lines(送り先が在る時)。thread は begin で起こす(job を起こした後 — fork の時の
    thread を 1 本に保つ)。"""

    def __init__(self, target: JobLogTarget, limits: ShipLimits = SHIP_LIMITS, poster_of: Callable[[str], Poster] = real_poster) -> None:
        """送り先・上限・送りの口の作り方を持ち、溜めを空・thread をまだ起こしていない状態で始めるため。"""
        self.target = target
        self.limits = limits
        self.poster_of = poster_of
        self.ready = threading.Condition()
        self.queue: deque[QueuedLine] = deque()
        self._mut_open: OpenLine | None = None
        self._mut_counts = ShipCounts()
        self._mut_end_at: float | None = None
        self._mut_thread: threading.Thread | None = None

    def counts(self) -> ShipCounts:
        """今の数え(読んだ行・捨てた行・溜めの byte)を読むため。"""
        with self.ready:
            return self._mut_counts

    def take(self, chunk: bytes, at_ns: int) -> None:
        """書き手の thread が読んだ byte 1 塊を行に切り、閉じた行を溜めに入れるため(書きかけの行は次の塊まで持つ)。"""
        cut = cut_lines(chunk, self._mut_open, at_ns, self.limits.line_bytes)
        self._mut_open = cut.open
        self.queued(cut.closed)

    def finish(self) -> None:
        """書き手の終わりに、改行の無い末尾の行を 1 行として溜めに入れるため(file の側で改行で閉じるのと同じ)。"""
        last = self._mut_open
        self._mut_open = None
        if last is not None:
            self.queued((last,))

    def queued(self, lines: tuple[OpenLine, ...]) -> None:
        """閉じた行に通し番号を付けて、溜めの上限に収まる分だけ入れ、溢れた分を捨てて数えるため(待たない)。"""
        if not lines:
            return
        with self.ready:
            counts = self._mut_counts
            fits = admitted(lines, counts, len(self.queue), self.limits)
            kept = tuple(QueuedLine(counts.seen + index, line) for index, line in enumerate(lines[:fits], 1))
            self.queue.extend(kept)
            self._mut_counts = replace(
                counts,
                seen=counts.seen + len(lines),
                dropped=counts.dropped + len(lines) - fits,
                queued_bytes=counts.queued_bytes + sum(len(item.line.head) for item in kept),
            )
            self.ready.notify_all()

    def begin(self) -> None:
        """送りの thread を起こすため(1 度 — 2 度目は何もしない)。"""
        if self._mut_thread is None:
            self._mut_thread = threading.Thread(target=self.shipped, name="job-log-ship", daemon=True)
            self._mut_thread.start()

    def end(self) -> None:
        """書き手が読み切った後に、残りの行を終わりの上限の内で送らせてから止めるため(上限を越えて待たない — 送りの thread は daemon
        なので、POST の途中でも process の終わりを止めない)。"""
        with self.ready:
            self._mut_end_at = time.monotonic() + self.limits.end_seconds
            self.ready.notify_all()
        if self._mut_thread is not None:
            self._mut_thread.join(self.limits.end_seconds)

    def batch_due(self) -> bool:
        """束を今 送るか: 溜めが 1 回の POST の上限まで満ちたか、終わりが来たか。"""
        return (
            self._mut_end_at is not None
            or len(self.queue) >= self.limits.batch_lines
            or self._mut_counts.queued_bytes >= self.limits.batch_bytes
        )

    def next_batch(self) -> Batch | None:
        """次に送る束を取るため。行が来るまで待ち、束が満ちるか interval_seconds が過ぎるまで束ねる。None = もう送らない(終わりが来て
        溜めが空か、終わりの上限を過ぎた)。"""
        with self.ready:
            self.ready.wait_for(lambda: bool(self.queue) or self._mut_end_at is not None)
            self.ready.wait_for(self.batch_due, timeout=self.limits.interval_seconds)
            end_at = self._mut_end_at
            left = self.limits.post_seconds if end_at is None else min(self.limits.post_seconds, end_at - time.monotonic())
            if not self.queue or left <= 0:
                return None
            lines = tuple(self.queue.popleft() for _ in range(batch_size(self.queue, self.limits)))
            self._mut_counts = replace(
                self._mut_counts, queued_bytes=self._mut_counts.queued_bytes - sum(len(item.line.head) for item in lines)
            )
            return Batch(lines=lines, timeout=left, ending=end_at is not None)

    def sent(self, poster: Poster, batch: Batch) -> bool:
        """束 1 つを POST するため。答え = 送り先が受けたか。受けなければ束を捨てて数え、終わりの前なら pause_seconds だけ次の POST を
        控える(終わりが来たら控えをやめる)。"""
        body = logs_body(self.target, batch.lines, self.counts().dropped, self.limits.line_bytes)
        if poster.posted(body, batch.timeout):
            return True
        with self.ready:
            self._mut_counts = replace(self._mut_counts, dropped=self._mut_counts.dropped + len(batch.lines))
            self.ready.wait_for(lambda: self._mut_end_at is not None, timeout=self.limits.pause_seconds)
        return False

    def dropped_rest(self) -> None:
        """送らずに止める時、溜めに残った行を捨てて数えるため。"""
        with self.ready:
            left = len(self.queue)
            self.queue.clear()
            self._mut_counts = replace(self._mut_counts, dropped=self._mut_counts.dropped + left, queued_bytes=0)

    def shipped(self) -> None:
        """送りの thread の本体: 束を取って送ることを、もう送らなくなるまで続ける。終わりの後に取った束の POST が失敗したら、残りを
        送らずに止める(落ちた送り先を待って終わりを延ばさない)。"""
        poster = self.poster_of(self.target.endpoint)
        try:
            while (batch := self.next_batch()) is not None:
                if not self.sent(poster, batch) and batch.ending:
                    break
        finally:
            poster.close()
            self.dropped_rest()


Shipping = NoShipping | JobLogShipper


@dataclass(frozen=True)
class WriterEnds:
    """書き手だけが持つ fd: source = pipe の読み口・target = 元の log の file・stop_read と stop_write = 終わりの合図の pipe。"""

    source: int
    target: int
    stop_read: int
    stop_write: int


@dataclass(frozen=True)
class FeedLimits:
    """file の書きの溜めの上限(頭の註の「file の書き」): queue_bytes = file へ書くのを待つ byte の上限(越える塊は捨てて数える)。"""

    queue_bytes: int = 8 << 20


# 本番の file の書きの上限(検は小さい上限を渡す)。
FEED_LIMITS = FeedLimits()


@dataclass(frozen=True)
class Dropped:
    """溜めの中の印: 溜めが満ちて捨てた byte の数(file にはこの位置に 1 行の知らせを書く)。"""

    count: int


class FileFeed:
    """読んだ byte を log の file へ書く書き手(頭の註の「file の書き」)。読みの thread が put で塊を溜めに入れ(待たない)、file の書きの
    thread が受けた順に file へ書く。作るのは pumped(読みの thread の中 — 読みの thread と同じく job を起こした後に起こす)。"""

    def __init__(self, target: int, limits: FeedLimits = FEED_LIMITS) -> None:
        """書き先の fd と上限を持ち、溜めを空・thread をまだ起こしていない状態で始めるため。"""
        self.target = target
        self.limits = limits
        self.ready = threading.Condition()
        self.queue: deque[bytes | Dropped] = deque()
        self._mut_queued_bytes = 0
        self._mut_closing = False
        self._mut_thread: threading.Thread | None = None

    def put(self, data: bytes) -> None:
        """塊 1 つを溜めに入れるため(待たない)。上限を越える塊は捨て、捨てた byte を溜めの今の位置の印に数える。"""
        if not data:
            return
        with self.ready:
            if self._mut_queued_bytes + len(data) > self.limits.queue_bytes:
                last = self.queue[-1] if self.queue else None
                if isinstance(last, Dropped):
                    self.queue[-1] = Dropped(last.count + len(data))
                else:
                    self.queue.append(Dropped(len(data)))
            else:
                self.queue.append(data)
                self._mut_queued_bytes += len(data)
            self.ready.notify_all()

    def begin(self) -> None:
        """file の書きの thread を起こすため(1 度 — 2 度目は何もしない)。"""
        if self._mut_thread is None:
            self._mut_thread = threading.Thread(target=self.drained, name="line-stamp-file", daemon=True)
            self._mut_thread.start()

    def close(self) -> None:
        """溜めに在る分を全部 file へ書かせてから thread を止めるため(file を欠かない — 書きが止まっている間は待つ。前の形で読みの thread が
        file の書きを待ったのと同じ)。"""
        with self.ready:
            self._mut_closing = True
            self.ready.notify_all()
        if self._mut_thread is not None:
            self._mut_thread.join()

    def next_items(self) -> tuple[bytes | Dropped, ...] | None:
        """次に書く物を溜めから全部取るため(来るまで待つ)。取った塊の byte は、書き終えるまで溜めの byte に数えたまま(書いている間も
        memory の上限を守る)。None = 閉じた後で溜めが空。"""
        with self.ready:
            self.ready.wait_for(lambda: bool(self.queue) or self._mut_closing)
            items = tuple(self.queue)
            self.queue.clear()
            return items or None

    def done_with(self, size: int) -> None:
        """書き終えた(か読み捨てた)塊の byte を、溜めの byte から外すため。"""
        with self.ready:
            self._mut_queued_bytes -= size

    def drained(self) -> None:
        """file の書きの thread の本体: 溜めの物を受けた順に file へ書く。捨てた印は、その位置に刻つきの 1 行の知らせにする(書きかけの行の
        途中なら改行で閉じてから)。file へ書けなくなったら、以後は読み捨てる(前の形と同じ)。"""
        writable = True
        line_start = True
        while (items := self.next_items()) is not None:
            for item in items:
                match item:
                    case Dropped(count=count):
                        notice = f"line_stamp: log の file への書きが詰まり、溜めの上限を越えた {count} byte を捨てた\n".encode()
                        data = (b"" if line_start else b"\n") + stamp_of(time.time()) + notice
                    case bytes():
                        data = item
                writable = writable and written(self.target, data)
                line_start = data.endswith(b"\n")
                if isinstance(item, bytes):
                    self.done_with(len(item))


def pumped(ends: WriterEnds, shipping: Shipping, limits: FeedLimits = FEED_LIMITS) -> None:
    """読みの thread の本体: pipe を読み、行の頭に刻を差し込んで file の書き手(FileFeed — 別の thread)へ渡し、同じ刻で行を送り手へ渡す
    (どちらも待たせない — 頭の註の「file の書き」「送り」)。終わりの合図の後は、読める分を上限の内で読み切って止め、改行の無い末尾の行を
    改行で閉じ(送り手へも 1 行として渡す)、溜めに在る分を file へ書き終えるまで待つ。limits = file の書きの溜めの上限。"""
    feed = FileFeed(ends.target, limits)
    feed.begin()
    line_start = True
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
        at_ns = time.time_ns()
        piece = stamped(chunk, line_start, stamp_of(at_ns / 1e9))
        feed.put(piece.data)
        line_start = piece.line_start
        shipping.take(chunk, at_ns)
    if not line_start:
        feed.put(b"\n")
    feed.close()
    shipping.finish()


class RawLines:
    """子の出力をそのまま運ぶ(入口の検め — worker が stdout の行を読んで判じる): fd 1 と fd 2 は起こした側の向け替えのまま。"""

    writer_descriptors: tuple[int, ...] = ()

    def begin(self) -> None:
        """何もしない(書き手が居ない)。"""

    def end(self) -> None:
        """何もしない(書き手が居ない)。"""


class StampedLines:
    """job の log の 1 行ごとに刻を付ける書き手(頭の註)。作るのは stamped_lines(fd 1 と fd 2 を pipe へ差し替えた後)。"""

    def __init__(self, ends: WriterEnds, shipping: Shipping) -> None:
        """書き手の fd と行の送り手(送らないなら NoShipping)を持ち、thread をまだ起こしていない状態で始めるため。"""
        self.ends = ends
        self.shipping = shipping
        self.writer_descriptors = (ends.source, ends.target, ends.stop_read, ends.stop_write)
        self._mut_thread: threading.Thread | None = None

    def begin(self) -> None:
        """書き手の thread と送りの thread を起こすため(job を起こした後に 1 度 — 2 度目は何もしない)。"""
        if self._mut_thread is None:
            self.shipping.begin()
            self._mut_thread = threading.Thread(target=pumped, args=(self.ends, self.shipping), name="line-stamp", daemon=True)
            self._mut_thread.start()

    def end(self) -> None:
        """書き口を元の file へ戻し(以後のこの process の出力は file へ直に行く)、pipe に残った分を読み切らせてから thread を止め、
        残りの行を送りの上限の内で送らせてから、書き手の fd を閉じるため(file の書きが先 — 送りの遅れで file を欠かない)。job を
        起こせずに終わる時も通る(thread がまだ無ければ、ここで起こしてから読み切る)。"""
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(self.ends.target, 1)
        os.dup2(self.ends.target, 2)
        self.begin()
        os.write(self.ends.stop_write, b"\0")
        if self._mut_thread is not None:
            self._mut_thread.join()
        self.shipping.end()
        for descriptor in self.writer_descriptors:
            os.close(descriptor)


OutputLines = RawLines | StampedLines


def same_file(first: int, second: int) -> bool:
    """2 つの fd が同じ file を指すか(worker は同じ path を 2 度開くので、開き方でなく file の身元で比べる)。"""
    one, other = os.fstat(first), os.fstat(second)
    return (one.st_dev, one.st_ino) == (other.st_dev, other.st_ino)


def shipping_of(plan: JobLogPlan, limits: ShipLimits = SHIP_LIMITS) -> Shipping:
    """送り先の決め(job_log_plan の答え)から行の送り手(上限 limits)を作るため。使えない値なら理由を 1 行 stderr(= この後 job の
    log)に書いて送らない(job はそのまま走る)。"""
    match plan:
        case JobLogTarget():
            return JobLogShipper(plan, limits)
        case JobLogOff():
            return NoShipping()
        case JobLogRefused(reason=reason):
            print(f"line_stamp: job の log の行を送らない — {reason}(file には全部書く)", file=sys.stderr, flush=True)
            return NoShipping()


def stamped_lines(plan: JobLogPlan) -> StampedLines:
    """この process の stdout と stderr(同じ log の file)を 1 本の pipe の書き口へ差し替え、その pipe を読んで刻を付ける書き手を作るため
    (thread は begin で起こす)。plan = 行を OTLP でも送るかの決め(job_log_plan の答え — 頭の註の「送り」)。stdout と stderr が別の
    file なら断る。"""
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
    shipping = shipping_of(plan)
    return StampedLines(WriterEnds(source=source, target=target, stop_read=stop_read, stop_write=stop_write), shipping)


@dataclass(frozen=True)
class Streams:
    """この process の Python の出力の流れの組(差し替える前の物 — 終わりに戻す)。"""

    stdout: TextIO
    stderr: TextIO


class StampedStreams:
    """worker の process 自身の Python の出力(sys.stdout と sys.stderr)に刻を付ける書き手(頭の註の「worker の process 自身の行」)。
    作るのは stamped_streams(2 つの流れを pipe の書き口の流れへ差し替えた後)。fd 1・2 は元の file のまま — 子孫は元の file を継ぐ。"""

    def __init__(self, ends: WriterEnds, sink: int, shipping: Shipping, originals: Streams) -> None:
        """書き手の fd・pipe の書き口(sink — 差し替えの流れが書く fd)・行の送り手・元の 2 つの流れを持ち、thread をまだ起こしていない
        状態で始めるため。"""
        self.ends = ends
        self.sink = sink
        self.shipping = shipping
        self.originals = originals
        self._mut_thread: threading.Thread | None = None

    def begin(self) -> None:
        """書き手の thread と送りの thread を起こすため(1 度 — 2 度目は何もしない)。"""
        if self._mut_thread is None:
            self.shipping.begin()
            self._mut_thread = threading.Thread(target=pumped, args=(self.ends, self.shipping), name="line-stamp", daemon=True)
            self._mut_thread.start()

    def end(self) -> None:
        """2 つの流れを元へ戻し(以後の Python の出力は file へ直に行く)、pipe の書き口の fd を元の file へ付け替え(差し替えの流れを
        持ち続ける書き手の書きも file へ行く — pipe は書き口が無くなり EOF になる)、pipe に残った分を読み切らせてから thread を止め、
        残りの行を送りの上限の内で送らせてから、書き手の fd を閉じるため(file の書きが先 — 送りの遅れで file を欠かない)。"""
        sys.stdout.flush()
        sys.stderr.flush()
        sys.stdout = self.originals.stdout
        sys.stderr = self.originals.stderr
        os.dup2(self.ends.target, self.sink)
        self.begin()
        os.write(self.ends.stop_write, b"\0")
        if self._mut_thread is not None:
            self._mut_thread.join()
        self.shipping.end()
        for descriptor in (self.ends.source, self.ends.target, self.ends.stop_read, self.ends.stop_write):
            os.close(descriptor)


ProcessLines = RawLines | StampedStreams


def stamped_streams(plan: JobLogPlan, merged: bool = False, limits: ShipLimits = SHIP_LIMITS) -> StampedStreams:
    """この process の sys.stdout と sys.stderr(fd 1・2 が同じ log の file)を、1 本の pipe の書き口へ書く 1 つの流れへ差し替え、その
    pipe を読んで刻を付ける書き手を作るため(thread は begin で起こす)。fd 1・2 は替えない(頭の註 — 子孫は元の file を継ぐ)。pipe の
    fd は継がれない(os.pipe の既定)。流れは行ごとに書き出し、書けない字は元の stderr と同じく backslashreplace にする。stdout と
    stderr が別の file なら断る — merged が真(k8s の container — 2 本の pipe が同じ container の log に入る)なら断らず、刻つきの行を
    stdout の file へ書く。limits = 送りの上限(終わりの上限は呼び手の止め方が決める)。"""
    if not merged and not same_file(1, 2):
        raise ValueError("刻を付ける log は stdout と stderr が同じ file の時だけ(機体の worker の log の形)")
    sys.stdout.flush()
    sys.stderr.flush()
    target = os.dup(1)
    source, sink = os.pipe()
    stop_read, stop_write = os.pipe()
    originals = Streams(stdout=sys.stdout, stderr=sys.stderr)
    # 流れは process の終わりまで閉じない(end の後も、流れを持ち続けた書き手の書きは付け替えた sink の先 = 元の file へ行く)。
    stream = os.fdopen(sink, "w", buffering=1, encoding=sys.stderr.encoding or "utf-8", errors="backslashreplace", closefd=False)
    sys.stdout = stream
    sys.stderr = stream
    ends = WriterEnds(source=source, target=target, stop_read=stop_read, stop_write=stop_write)
    return StampedStreams(ends, sink, shipping_of(plan, limits), originals)


def process_lines(plan: JobLogPlan, merged: bool = False, limits: ShipLimits = SHIP_LIMITS) -> ProcessLines:
    """worker・coordinator の process 自身の Python の出力を、送り先が使えて stdout と stderr が同じ file を指す時(merged が真なら
    別の file でも — coordinator の Pod の形)だけ刻つきの書き手(送り手つき)へ差し替えて動かし始め、終わり(end)を process の終わり
    (atexit)に置くため(頭の註の「worker の process 自身の行」「coordinator の process 自身の行」)。plan = worker_log_plan か
    coordinator_log_plan の答え。それ以外は何も替えない(RawLines)— 送り先の値が使えない時と、送り先は在るが stdout と stderr が
    別の file の時は、理由を 1 行 stderr に書く(process はそのまま走る)。limits = 送りの上限(終わりの上限 end_seconds は、process
    の止め方を持つ入口が選ぶ — 頭の註の「coordinator の process 自身の行」)。"""
    match plan:
        case JobLogTarget() if merged or same_file(1, 2):
            lines = stamped_streams(plan, merged, limits)
            lines.begin()
            atexit.register(lines.end)
            return lines
        case JobLogTarget():
            print("line_stamp: worker の行を送らない — stdout と stderr が同じ file でない", file=sys.stderr, flush=True)
            return RawLines()
        case JobLogOff():
            return RawLines()
        case JobLogRefused(reason=reason):
            print(f"line_stamp: 自分の行を送らない — {reason}", file=sys.stderr, flush=True)
            return RawLines()
