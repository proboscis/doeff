"""uv の cache を node ごとに用意し(seed)、古い項目と使われない venv を消す(gc)道具 — worker の起動の準備(deploy/boot.sh)が呼ぶ。

訳: worker の uv の cache(DOEFF_UV_CACHE_DIR・既定 $WORK_DIR/state/uv-cache)は node の disk に在り、新しい node では空から始まる。空の
cache で依存を取り込むと、wheel の無い package(free-threaded の 3.14t の grpcio など)を source から組み、doeff の workspace の全部で
611 秒かかった(2026-11-01 の zeus の測り — 依存を取り込み済みの cache なら秒)。また cache は誰も消さず、zeus で展開済みの wheel の項目が
169,393 個に溜まった。`uv cache prune` は走っている uv の process が 1 つでも在ると錠を待ち続けて終わらない(常駐の job の `uv run` は
生きている間ずっと錠を持つ)ので、uv の外で消す。

動詞(答えはどれも標準出力の JSON 1 行・訳は標準エラーの 1 行):
  upkeep   worker の起動の準備(deploy/boot.sh の doeff_uv_cache_upkeep)が背後で 1 度起こす: 環境変数 DOEFF_UV_CACHE_SEED_URL が在れば
           seed(DOEFF_UV_CACHE_SEED_SECRET = --secret・空きが DOEFF_UV_CACHE_SEED_MIN_FREE_GIB(既定 50)を下回れば入れない)、
           続けて DOEFF_UV_CACHE_GC_DAYS が在れば gc(--idle-days = その値・--venvs = DOEFF_UV_CACHE_GC_VENVS を `:` で分けた dir・
           --links = DOEFF_UV_CACHE_GC_LINK_ROOTS を `:` で分けた dir・--every-seconds = DOEFF_UV_CACHE_GC_EVERY_SECONDS(既定 72000)・
           --size-budget-gib = DOEFF_UV_CACHE_GC_SIZE_BUDGET_GIB(無ければ予算なし)・--floor-days = DOEFF_UV_CACHE_GC_FLOOR_DAYS(既定 1))、
           続けて DOEFF_UV_CACHE_SEED_REFRESH_PROJECTS が在り、環境変数 NODE_NAME が DOEFF_UV_CACHE_SEED_REFRESH_NODE と同じ node でだけ
           refresh(--url = DOEFF_UV_CACHE_SEED_URL・--project = その値を `:` で分けた dir・--every-seconds =
           DOEFF_UV_CACHE_SEED_REFRESH_EVERY_SECONDS(既定 72000)— 置き場の写しを置き直す node を 1 つに限り、同じ時に起きる worker が
           並べて組まない)。どれが落ちても訳を 1 行出して続け、0 で終わる。
  pack    cache を tar.zst 1 file にする。入れない物: 錠(.lock)・作りかけ(.tmp*)・組みの一時の環境(builds-v*)・`uv run --with` の
           環境(environments-v*)・interpreter の問い合わせの控え(interpreter-v*)。
  publish  pack した file を S3 互換の object storage の URL(s3://<bucket>/<key> か path の形 http(s)://<host>/<bucket>/<key> —
           下の「資格と置き場」)へ PUT する。PUT が 5xx・
           時間切れ・接続の切れで落ちれば、30・60・120・240・480 秒を待って 5 回まで試し直す(refresh の PUT も同じ)。
  seed     cache に印(.doeff-uv-seed.json)が無ければ、URL(s3://・S3 の path の形の http(s) か file の path)の tar.zst を取り寄せ、cache の中の一時の dir に
           展開してから、cache に無い物だけを rename で入れる(在る物は替えない — 走っている uv の索引と展開済みの項目を壊さない)。
           展開済みの wheel の項目(archive-v*)を先に入れ、索引(wheels-v*・sdists-v* など)を後に入れる。入れ終えたら印を書く。印が在れば
           何もしない(--force で入れ直す)。
  refresh  置き場の写しを、渡した project の lock(--project の dir の uv.lock)に合わせて作り直して置く。cache の中の一時の dir
           (.doeff-uv-refresh-*)の空の cache に、project ごとに `uv sync --frozen --no-install-workspace --no-install-local --all-groups
           --all-packages` で第三者の依存だけを取り込み(venv と uv の TMPDIR も一時の dir に置く — project の dir に .venv を作らず、
           source から組む gcc が埋まった /tmp で落ちない)、その cache を pack
           して PUT する。lock の中身の hash(並べた uv.lock の sha256)を object の metadata(x-amz-meta-doeff-uv-locks)に載せ、置いてある
           写しの hash が同じ時と、置いた刻(Last-Modified)が --every-seconds より新しい時は組まない(--force で組む)。空きが --min-free-gib
           を下回れば組まない。同じ cache の refresh は錠(.doeff-uv-refresh.lock)で 1 つに限る。
  count    gc と同じ数えだけをする(何も消さない)。
  gc       数えてから消す。消す物:
             venv        --venvs の dir の下(深さ --depth まで)の .venv のうち、最後に使った刻(dir・pyvenv.cfg・bin・site-packages の
                         最後の変更と pyvenv.cfg の最後の読み)が --idle-days 日より前で、どの process も使っていない物(作業 dir が
                         venv を持つ dir の下・命令の path か読み込んだ file が venv の下)。
             環境        cache の environments-v* の項目(`uv run --with` の環境 — 中身は venv)を venv と同じ決まりで。
             一時の物    cache の直下の .tmp* と builds-v* の項目のうち、最後の変更が --idle-days 日より前の物。
             展開済みの wheel  archive-v* の項目のうち、中の file が全部 hardlink の数 1(どの venv も同じ中身を指していない)で、項目の
                         最後の変更が --idle-days 日より前で、残る venv と環境と --links の dir の下の venv のどれも中の symlink で指さず
                         (UV_LINK_MODE=symlink の venv は中身を項目への symlink で持ち、hardlink の数を増やさない — 項目を消すと壊れる)、
                         走っている process が読み込んでいない物。venv と環境を消した後に数え直して判じる。symlink の venv の置き場が
                         --venvs と --links の外に在ると守れないので、置き場を必ずどちらかに渡す。索引が指す項目を消しても、uv は
                         取り寄せ直すだけで壊れない(2026-11-01 に uv 0.12.19 で確かめた — 索引の指す項目を消した後の install は
                         「uncached distribution」として取り寄せ直した)。
             git の checkout  git-v*/checkouts/<repo>/<commit> のうち、作った刻が --idle-days 日より前で、残る venv と環境と --links の
                         dir の下の venv のどれも symlink か .pth の行(editable の install)で指さず、走っている process(組みの作業 dir・
                         引数・読み込んだ file)が使っていない物(proc が読めなければ消さない)。uv がその repo に持つ錠(git-v*/locks/<repo>)
                         を待たずに取れた時だけ、印 <commit>.ok を先に退けてから消す。venv は git の依存を checkout から組んだ wheel で
                         持ち、checkout を指さない。uv は checkout を bare の repo(git-v*/db — 消さない)から作り直す。2026-11-08 の atlas
                         では 09-26 から 1 本も消えずに 211 本・25 GB 溜まっていた(doeff の checkout は 1 本 720〜845 MB)。
             組んだ物    sdists-v*/<種類>/… の、source 1 つの dir(版の索引 revision.* と版の dir — editable・path・pypi)と git の
                         依存の commit 1 つの dir のうち、最後の変更が --idle-days 日より前で、走っている process が使っていない物。
                         消えた worktree の path の editable の dir は誰も使わずに残り、atlas で 26,488 個溜まっていた。uv は索引か版が
                         無ければ組み直し、展開済みの wheel(別の項目)を指す venv は壊れない。
           予算(--size-budget-gib)を渡すと、期限の消しの後に、cache の dir の全部の byte — cache だけが持つ byte(消す単位 — 展開済みの
           wheel・環境・一時の物・git の checkout・組んだ wheel の dir — の file のうち、hardlink の全部が単位の中に在る物と dir の byte)と、
           単位の外の消せない byte(git の bare の repo・索引・interpreter の控え・印と錠)の和を数え、予算を越えていれば、最後に使った刻の古い
           順に、--floor-days 日(既定 1)より前で、cache の外の venv が hardlink で指さず(展開済みの wheel)、上の決まり(残る venv の
           symlink・走っている process)に当たらない項目を、和が予算に収まるまで消す。venv が hardlink で持つ byte は venv の持ち分で、
           消しても disk が戻らないので数えない。
           消す物は先に同じ dir の中の別の名へ rename し(uv からは即座に見えなくなる)、その後で中身を消す。前と後の数(項目の数・file の
           数・disk の byte・file system の空き)を答えに載せる。--every-seconds を渡すと、前の gc の印(.doeff-uv-gc.json)がその秒より
           新しい時は何もしない。同じ cache の gc と seed は錠(.doeff-uv-gc.lock・.doeff-uv-seed.lock)で 1 つに限る。

資格と置き場(publish・refresh と、file の path でない seed): --secret <namespace>/<name> を渡すと、その k8s の Secret の鍵
access-key-id・secret-access-key と endpoint(在れば)を Pod の ServiceAccount の token(標準の path)で API server から読む。渡さなければ
環境変数 AWS_ACCESS_KEY_ID・AWS_SECRET_ACCESS_KEY と AWS_ENDPOINT_URL_S3(無ければ AWS_ENDPOINT_URL — aws cli と同じ名)を使い、資格が
無ければ署名しない。URL が s3://<bucket>/<key> なら、要求は <endpoint>/<bucket>/<key> へ送る(endpoint が無ければ断る)ので、bucket と key
が同じ 2 つの置き場(例 Garage と nemo)は、Secret の名 1 つ(upkeep なら DOEFF_UV_CACHE_SEED_SECRET)で切り替わる。URL が http(s) なら
その URL へ送り、Secret の endpoint は読まない。署名は SigV4(--region・既定 us-east-1・payload は UNSIGNED-PAYLOAD)。

標準の library だけを使う(依存を取り込む前の cache に入れる道具なので、取り込みに頼らない)。Python 3.14 以上(zstd は 3.14 の
compression.zstd・型の註の前方の参照は 3.14 の遅れて読む註で解く)。
"""

import argparse
import array
import base64
import bisect
import contextlib
import dataclasses
import datetime
import email.utils
import fcntl
import hashlib
import hmac
import http.client
import json
import os
import shutil
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Self

SEED_MARK = ".doeff-uv-seed.json"
GC_MARK = ".doeff-uv-gc.json"
SEED_LOCK = ".doeff-uv-seed.lock"
GC_LOCK = ".doeff-uv-gc.lock"
# seed の展開の一時の dir と gc の消しの一時の名の頭(cache の直下 — 同じ file system の rename で入れ・退ける)。
SEED_STAGE = ".doeff-uv-seed-"
GC_TRASH = ".doeff-uv-gc-"
REFRESH_LOCK = ".doeff-uv-refresh.lock"
REFRESH_BUILD = ".doeff-uv-refresh-"
# 置いた写しの lock の中身の hash を載せる object の metadata の header(S3 は x-amz-meta-* を HEAD でそのまま返す)。
LOCKS_META = "x-amz-meta-doeff-uv-locks"
# refresh が project ごとに撃つ uv の動詞と旗(第三者の依存だけを取り込む — workspace と path の package は組まない)。
SYNC_ARGS = (
    "sync",
    "-q",
    "--frozen",
    "--no-install-workspace",
    "--no-install-local",
    "--all-groups",
    "--all-packages",
)
# pack が入れない cache の直下の名(頭の一致)。uv の bucket の版の番号は uv の版で変わるので、頭の名で引く。
# この道具の印・錠・一時の名・log(.doeff-uv-*)も入れない。
PACK_SKIP_PREFIXES = (".lock", ".tmp", "builds-v", "environments-v", "interpreter-v", ".doeff-uv-")
ARCHIVE_PREFIX = "archive-v"
ENVIRONMENTS_PREFIX = "environments-v"
BUILDS_PREFIX = "builds-v"
TMP_PREFIX = ".tmp"
# git の依存の bucket(git-v*/db = 取り寄せた bare の repo・checkouts/<repo>/<commit> = 組みに使う checkout とその印 <commit>.ok・
# locks/<repo> = uv が取り寄せと checkout の間に持つ錠)と、source から組んだ wheel の bucket(sdists-v*/<種類>/… — 組んだ物の
# dir は、版の索引 revision.*(editable・path は revision.rev・pypi は revision.http)と版の dir を持つ source 1 つの dir か、git の
# 依存の commit 1 つの dir(組みの控え metadata.msgpack を持つ))。
GIT_PREFIX = "git-v"
GIT_CHECKOUTS = "checkouts"
GIT_LOCKS = "locks"
CHECKOUT_MARK = ".ok"
SDISTS_PREFIX = "sdists-v"
SDIST_INDEX = "revision."
SDIST_BUILT = "metadata.msgpack"
# 組んだ物の dir を探す深さ(sdists-v*/<種類> の下 — pypi/<package>/<版> が最も深い)。
SDIST_DEPTH = 3
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
CHUNK = 1 << 20
# 並べた Range の GET の 1 片の byte。
PART = 64 << 20
# PUT が落ちた時に試し直すまでの秒(並びの数 = 試し直しの回数)。object の置き場は disk の命令が詰まると数分 503(quorum に届かない)
# を返し、組みから pack までは約 30 分かかるので、落ちた PUT 1 回で組みを捨てない — 5 回・待ちの計 15.5 分。
PUT_RETRY_DELAYS = (30.0, 60.0, 120.0, 240.0, 480.0)


def say(line: str) -> None:
    """訳の 1 行を標準エラーへ出す(boot.sh の log に載る)。"""
    print(f"uv-cache: {line}", file=sys.stderr, flush=True)


def answer(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True), flush=True)


# ── S3 の SigV4 ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Credentials:
    access_key: str
    secret_key: str


def _uri_encode(text: str, *, keep_slash: bool) -> str:
    return urllib.parse.quote(text, safe="/-_.~" if keep_slash else "-_.~")


def sigv4_headers(
    method: str,
    url: str,
    region: str,
    credentials: Credentials,
    now: datetime.datetime,
    payload_hash: str = "UNSIGNED-PAYLOAD",
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """S3 の要求に付ける header(host・x-amz-date・x-amz-content-sha256・Authorization と、渡した header)を返す。

    url の path は decode した形で受け、segment ごとに 1 度だけ encode する(S3 は 2 度 encode しない)。渡した header も署名に入れる。
    """
    parts = urllib.parse.urlsplit(url)
    stamp = now.astimezone(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    day = stamp[:8]
    signed = {k.lower(): " ".join(v.strip().split()) for k, v in (headers or {}).items()}
    signed["host"] = parts.netloc
    signed["x-amz-date"] = stamp
    signed["x-amz-content-sha256"] = payload_hash
    names = sorted(signed)
    query = "&".join(
        f"{_uri_encode(k, keep_slash=False)}={_uri_encode(v, keep_slash=False)}"
        for k, v in sorted(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    )
    canonical = "\n".join(
        [
            method,
            _uri_encode(urllib.parse.unquote(parts.path) or "/", keep_slash=True),
            query,
            "".join(f"{name}:{signed[name]}\n" for name in names),
            ";".join(names),
            payload_hash,
        ]
    )
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()]
    )
    key = f"AWS4{credentials.secret_key}".encode()
    for piece in (day, region, "s3", "aws4_request"):
        key = hmac.new(key, piece.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    out = dict(headers or {})
    out["x-amz-date"] = stamp
    out["x-amz-content-sha256"] = payload_hash
    out["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={credentials.access_key}/{scope}, "
        f"SignedHeaders={';'.join(names)}, Signature={signature}"
    )
    return out


def read_secret(ref: str, keys: tuple[str, ...], sa_dir: Path = SA_DIR) -> dict[str, str]:
    """k8s の Secret <namespace>/<name> の鍵のうち keys に在る物の値(decode した文字列)を ServiceAccount の token で読む。"""
    namespace, _, name = ref.partition("/")
    if not namespace or not name:
        raise SystemExit(f"uv-cache: --secret は <namespace>/<name>: {ref!r}")
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if not host:
        raise SystemExit("uv-cache: KUBERNETES_SERVICE_HOST が無い — Secret を読めない")
    token = (sa_dir / "token").read_text().strip()
    context = ssl.create_default_context(cafile=str(sa_dir / "ca.crt"))
    conn = http.client.HTTPSConnection(host, int(port), context=context, timeout=30)
    try:
        conn.request(
            "GET",
            f"/api/v1/namespaces/{namespace}/secrets/{name}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        response = conn.getresponse()
        body = response.read()
    finally:
        conn.close()
    if response.status != 200:
        raise SystemExit(f"uv-cache: Secret {ref} を読めない(HTTP {response.status})")
    data = json.loads(body).get("data") or {}
    return {key: base64.b64decode(data[key]).decode() for key in keys if key in data}


@dataclass(frozen=True)
class Target:
    """要求を送る先 1 つ: url = http(s) の URL か file の path・credentials = 署名の資格(None なら署名しない)。"""

    url: str
    credentials: Credentials | None


def resolve_store(url: str, secret: str | None) -> Target:
    """--url と --secret を、要求を送る http(s) の URL と資格に解く(頭の註の「資格と置き場」)。file の path は資格なしでそのまま。"""
    if "://" not in url:
        return Target(url, None)
    if secret:
        data = read_secret(secret, ("access-key-id", "secret-access-key", "endpoint"))
        try:
            credentials: Credentials | None = Credentials(
                data["access-key-id"], data["secret-access-key"]
            )
        except KeyError as missing:
            raise SystemExit(f"uv-cache: Secret {secret} に鍵 {missing} が無い") from None
        endpoint = data.get("endpoint", "")
    else:
        key, value = os.environ.get("AWS_ACCESS_KEY_ID"), os.environ.get("AWS_SECRET_ACCESS_KEY")
        credentials = Credentials(key, value) if key and value else None
        endpoint = os.environ.get("AWS_ENDPOINT_URL_S3") or os.environ.get("AWS_ENDPOINT_URL", "")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "s3":
        return Target(url, credentials)
    if not parts.netloc or parts.path in ("", "/"):
        raise SystemExit(f"uv-cache: s3 の URL は s3://<bucket>/<key>: {url!r}")
    if not endpoint:
        raise SystemExit(
            f"uv-cache: {url} の置き場の入口が無い — --secret の Secret の鍵 endpoint か、"
            "--secret が無ければ環境変数 AWS_ENDPOINT_URL_S3(か AWS_ENDPOINT_URL)に書く"
        )
    return Target(f"{endpoint.rstrip('/')}/{parts.netloc}{parts.path}", credentials)


def _connection(url: str, timeout: float) -> tuple[http.client.HTTPConnection, str]:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise SystemExit(f"uv-cache: URL は http か https: {url!r}")
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    return cls(parts.netloc, timeout=timeout), target


class PutError(Exception):
    """PUT の 1 回の失敗(retryable = 試し直せば通りうる — 5xx・時間切れ・接続の切れ)。"""

    def __init__(self, reason: str, *, retryable: bool) -> None:
        super().__init__(reason)
        self.retryable = retryable


def _put_once(
    url: str,
    path: Path,
    credentials: Credentials | None,
    region: str,
    timeout: float,
    *,
    headers: dict[str, str],
) -> dict[str, str]:
    if credentials:
        headers = sigv4_headers(
            "PUT", url, region, credentials, datetime.datetime.now(datetime.UTC), headers=headers
        )
    conn, target = _connection(url, timeout)
    try:
        with path.open("rb") as body:
            conn.request("PUT", target, body=body, headers=headers)
            response = conn.getresponse()
            text = response.read()
    except (OSError, http.client.HTTPException) as error:
        raise PutError(f"{type(error).__name__}: {error}", retryable=True) from error
    finally:
        conn.close()
    if response.status not in (200, 201):
        raise PutError(f"HTTP {response.status}: {text[:300]!r}", retryable=response.status >= 500)
    return {"etag": response.getheader("ETag", "").strip('"')}


def http_put_file(
    url: str,
    path: Path,
    credentials: Credentials | None,
    region: str,
    timeout: float,
    meta: dict[str, str] | None = None,
) -> dict[str, object]:
    """path を url へ PUT する(meta = object に載せる x-amz-meta-* の header — 署名に入れる)。5xx・時間切れ・接続の切れ
    (Broken pipe など)は PUT_RETRY_DELAYS の秒を待って試し直す(署名は試しごとに今の刻で作り直す)。4xx は試し直さない。"""
    size = path.stat().st_size
    headers = {"Content-Length": str(size), "Content-Type": "application/zstd", **(meta or {})}
    delays = PUT_RETRY_DELAYS
    attempt = 0
    while True:
        attempt += 1
        try:
            return {
                **_put_once(url, path, credentials, region, timeout, headers=headers),
                "attempts": attempt,
            }
        except PutError as failed:
            if not failed.retryable or attempt > len(delays):
                raise SystemExit(
                    f"uv-cache: PUT {url} が {failed}({attempt} 回目で止めた)"
                ) from failed
            wait = delays[attempt - 1]
            say(f"PUT {url} が {failed} — {wait:g} 秒の後に試し直す({attempt}/{len(delays)})")
            time.sleep(wait)


def _signed(
    method: str, url: str, credentials: Credentials | None, region: str, headers: dict[str, str]
) -> dict[str, str]:
    if credentials is None:
        return headers
    return sigv4_headers(
        method,
        url,
        region,
        credentials,
        datetime.datetime.now(datetime.UTC),
        payload_hash=EMPTY_SHA256,
        headers=headers,
    )


def _request(
    method: str,
    url: str,
    credentials: Credentials | None,
    region: str,
    timeout: float,
    headers: dict[str, str],
    sink: BinaryIO | None = None,
) -> tuple[http.client.HTTPResponse, int]:
    """要求を 1 つ送り、答えと本体の byte の数を返す(sink が在れば本体を書き、無ければ読み捨てる)。"""
    conn, target = _connection(url, timeout)
    try:
        conn.request(method, target, headers=_signed(method, url, credentials, region, headers))
        response = conn.getresponse()
        count = 0
        while chunk := response.read(CHUNK):
            count += len(chunk)
            if sink is not None:
                sink.write(chunk)
        return response, count
    finally:
        conn.close()


def http_download(
    url: str,
    credentials: Credentials | None,
    region: str,
    timeout: float,
    dest: Path,
    workers: int,
    part: int = PART,
) -> dict[str, str]:
    """url の object を dest へ、part byte ごとの Range の GET を workers 本並べて取り寄せる(object の置き場は 1 本の GET では
    disk 1 本の読みの速さしか出ない — 2026-11-01 の測りで 1 本 11.6 MB/s・8 本 39.3 MB/s)。片ごとに ETag が HEAD と同じかを確かめる
    (取り寄せの間に置き直された object を混ぜない)。"""
    head, _ = _request("HEAD", url, credentials, region, timeout, {})
    if head.status != 200:
        raise SystemExit(f"uv-cache: HEAD {url} が HTTP {head.status}")
    size = int(head.getheader("Content-Length", "0"))
    etag = head.getheader("ETag", "")
    with dest.open("wb") as out:
        out.truncate(size)

    def fetch(start: int) -> int:
        end = min(start + part, size) - 1
        with dest.open("r+b") as out:
            out.seek(start)
            response, count = _request(
                "GET",
                url,
                credentials,
                region,
                timeout,
                {"Range": f"bytes={start}-{end}"},
                sink=out,
            )
        if (
            response.status != 206
            or response.getheader("ETag", "") != etag
            or count != end - start + 1
        ):
            raise SystemExit(
                f"uv-cache: GET {url} の bytes={start}-{end} が HTTP {response.status}・{count} byte・ETag "
                f"{response.getheader('ETag', '')!r}(HEAD は {etag!r})"
            )
        return count

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        fetched = sum(pool.map(fetch, range(0, size, part)))
    if fetched != size:
        raise SystemExit(f"uv-cache: {url} の {size} byte のうち {fetched} byte しか届かない")
    return {"etag": etag.strip('"'), "last-modified": head.getheader("Last-Modified", "")}


# ── pack と seed ────────────────────────────────────────────────────────────


def _tar_mode(name: str, write: bool) -> str:
    kind = "w" if write else "r"
    if name.endswith(".tar.zst"):
        return f"{kind}|zst"
    if name.endswith(".tar.gz"):
        return f"{kind}|gz"
    raise SystemExit(f"uv-cache: 名は .tar.zst か .tar.gz で終わる: {name!r}")


def pack_members(cache: Path) -> list[str]:
    """pack が入れる cache の直下の名(並びは名の順 — 入れる時と同じ archive-v* が先)。"""
    names = sorted(p.name for p in cache.iterdir() if not p.name.startswith(PACK_SKIP_PREFIXES))
    return sorted(names, key=lambda n: (not n.startswith(ARCHIVE_PREFIX), n))


def pack(cache: Path, out: Path) -> dict[str, object]:
    started = time.monotonic()
    members = pack_members(cache)
    partial = out.with_name(out.name + ".partial")
    with (
        partial.open("wb") as raw,
        tarfile.open(fileobj=raw, mode=_tar_mode(out.name, write=True)) as tar,
    ):
        for name in members:
            tar.add(cache / name, arcname=name)
    partial.replace(out)
    return {
        "verb": "pack",
        "out": str(out),
        "members": members,
        "bytes": out.stat().st_size,
        "seconds": round(time.monotonic() - started, 1),
    }


@contextlib.contextmanager
def held(lock: Path, *, wait: bool) -> Iterator[bool]:
    """lock の file の排他の錠を持つ間 True を渡す(wait=False で取れなければ False)。"""
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@dataclass(frozen=True)
class Merged:
    """入れた数(rename した file か dir の木)と、同じ名が在って替えなかった数。"""

    moved: int = 0
    skipped: int = 0

    def __add__(self, other: Self) -> Self:
        return type(self)(self.moved + other.moved, self.skipped + other.skipped)


def _is_real_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def merge_one(child: Path, dest: Path) -> Merged:
    """child を dest に同じ名が無ければ rename で入れる。在れば、両方が dir なら中へ降り、file と link は替えない。"""
    if not os.path.lexists(dest):
        child.rename(dest)
        return Merged(moved=1)
    if _is_real_dir(child) and _is_real_dir(dest):
        return merge_tree(child, dest)
    return Merged(skipped=1)


def merge_tree(source: Path, target: Path, order=lambda p: p.name) -> Merged:
    """source の中の物を order の順に target へ入れる(merge_one)。"""
    return sum(
        (merge_one(child, target / child.name) for child in sorted(source.iterdir(), key=order)),
        Merged(),
    )


def seed(
    cache: Path,
    source: str,
    credentials: Credentials | None,
    region: str,
    timeout: float,
    force: bool,
    workers: int = 8,
    part: int = PART,
) -> dict[str, object]:
    """新しい node の空の cache に、置いた cache の写しを入れるため(頭の註の seed)。"""
    mark = cache / SEED_MARK
    if mark.exists() and not force:
        say(f"seed: 印 {mark} が在る — 入れない")
        return {"verb": "seed", "seeded": False, "reason": "marked"}
    cache.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with held(cache / SEED_LOCK, wait=True):
        if mark.exists() and not force:
            return {"verb": "seed", "seeded": False, "reason": "marked"}
        for stale in cache.glob(SEED_STAGE + "*"):
            shutil.rmtree(stale, ignore_errors=True)
        stage = Path(tempfile.mkdtemp(prefix=SEED_STAGE, dir=cache))
        try:
            remote = "://" in source
            name = urllib.parse.urlsplit(source).path if remote else source
            packed = stage / Path(name).name if remote else Path(source)
            meta = (
                http_download(source, credentials, region, timeout, packed, workers, part)
                if remote
                else {}
            )
            fetched = time.monotonic()
            tree = stage / "tree"
            with (
                packed.open("rb") as raw,
                tarfile.open(fileobj=raw, mode=_tar_mode(name, write=False)) as tar,
            ):
                tar.extractall(tree, filter="data")
            extracted = time.monotonic()
            # 展開済みの wheel(archive-v*)を先に入れ、それを指す索引を後に入れる(索引が無い項目を指す間を作らない)。
            merged = merge_tree(
                tree, cache, order=lambda p: (not p.name.startswith(ARCHIVE_PREFIX), p.name)
            )
            size = packed.stat().st_size
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        result: dict[str, object] = {
            "verb": "seed",
            "seeded": True,
            "source": source,
            "bytes": size,
            "moved": merged.moved,
            "skipped": merged.skipped,
            "fetch-seconds": round(fetched - started, 1),
            "extract-seconds": round(extracted - fetched, 1),
            "seconds": round(time.monotonic() - started, 1),
            "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            **meta,
        }
        partial = mark.with_name(mark.name + ".partial")
        partial.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
        partial.replace(mark)
    say(
        f"seed: {source} の {size} byte を入れた({result['seconds']} 秒 — 取り寄せ {result['fetch-seconds']} 秒・展開 "
        f"{result['extract-seconds']} 秒・入れた {merged.moved}・在った {merged.skipped})"
    )
    return result


# ── refresh(置き場の写しを lock に合わせて作り直す)──────────────────────────


def locks_digest(projects: Iterable[Path]) -> str:
    """渡した順の project の uv.lock の中身の sha256(project の dir の名と中身の長さで区切る — 並びと境目の違いも別の hash)。"""
    digest = hashlib.sha256()
    for project in projects:
        body = (project / "uv.lock").read_bytes()
        digest.update(f"{project.name}\0{len(body)}\0".encode())
        digest.update(body)
    return digest.hexdigest()


@dataclass(frozen=True)
class Published:
    """置き場に在る写しの、載せた lock の hash(無ければ空)と置いた刻(epoch 秒)。"""

    digest: str
    at: float


def published(
    url: str, credentials: Credentials | None, region: str, timeout: float
) -> Published | None:
    """置き場の写しを HEAD で読む(無ければ None)。"""
    head, _ = _request("HEAD", url, credentials, region, timeout, {})
    if head.status == 404:
        return None
    if head.status != 200:
        raise SystemExit(f"uv-cache: HEAD {url} が HTTP {head.status}")
    modified = head.getheader("Last-Modified", "")
    at = email.utils.parsedate_to_datetime(modified).timestamp() if modified else 0.0
    return Published(digest=head.getheader(LOCKS_META, "").strip(), at=at)


def sync_into(uv: str, project: Path, cache: Path, venv: Path, tmp: Path) -> dict[str, object]:
    """project の第三者の依存を cache へ取り込む(venv は渡した dir — project の dir に .venv を作らない。source から組む gcc の
    一時 file は渡した dir tmp に書く — 外の /tmp は埋まっていることがある)。落ちれば CalledProcessError。"""
    began = time.monotonic()
    env = {
        **{k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"},
        "UV_PROJECT_ENVIRONMENT": str(venv),
        "TMPDIR": str(tmp),
    }
    subprocess.run(
        [uv, *SYNC_ARGS, "--project", str(project), "--cache-dir", str(cache)],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=sys.stderr,
        check=True,
    )
    return {"project": str(project), "seconds": round(time.monotonic() - began, 1)}


def refresh(
    cache: Path,
    projects: list[Path],
    url: str,
    credentials: Credentials | None,
    region: str,
    timeout: float,
    *,
    every_seconds: float,
    min_free: int,
    force: bool,
    uv: str,
) -> dict[str, object]:
    """置き場の写しが lock より古くならないよう、空の cache に project の依存を取り込み直して置くため(頭の註の refresh)。"""
    cache.mkdir(parents=True, exist_ok=True)
    with held(cache / REFRESH_LOCK, wait=False) as got:
        if not got:
            say("refresh: 同じ cache の別の refresh が走っている — 組まない")
            return {"verb": "refresh", "refreshed": False, "reason": "locked"}
        digest = locks_digest(projects)
        remote = published(url, credentials, region, timeout)
        if remote is not None and not force:
            if remote.digest == digest:
                say(f"refresh: 置いてある写しの lock の hash が今と同じ({digest[:12]})— 組まない")
                return {"verb": "refresh", "refreshed": False, "reason": "current", "locks": digest}
            age = time.time() - remote.at
            if age < every_seconds:
                say(
                    f"refresh: 置いてから {round(age)} 秒で {round(every_seconds)} 秒が経っていない — 組まない"
                )
                return {"verb": "refresh", "refreshed": False, "reason": "recent", "locks": digest}
        if disk_free(cache) < min_free:
            say(f"refresh: 空き {disk_free(cache)} byte が下限 {min_free} byte を下回る — 組まない")
            return {"verb": "refresh", "refreshed": False, "reason": "low-free"}
        for stale in cache.glob(REFRESH_BUILD + "*"):
            shutil.rmtree(stale, ignore_errors=True)
        started = time.monotonic()
        build = Path(tempfile.mkdtemp(prefix=REFRESH_BUILD, dir=cache))
        try:
            tmp = build / "tmp"
            tmp.mkdir()
            synced = [
                sync_into(uv, project, build / "cache", build / f"venv-{index}", tmp)
                for index, project in enumerate(projects)
            ]
            packed = pack(build / "cache", build / Path(urllib.parse.urlsplit(url).path).name)
            put = http_put_file(
                url, Path(str(packed["out"])), credentials, region, timeout, {LOCKS_META: digest}
            )
        finally:
            shutil.rmtree(build, ignore_errors=True)
    result: dict[str, object] = {
        "verb": "refresh",
        "refreshed": True,
        "url": url,
        "locks": digest,
        "bytes": packed["bytes"],
        "synced": synced,
        "pack-seconds": packed["seconds"],
        "seconds": round(time.monotonic() - started, 1),
        "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        **put,
    }
    say(
        f"refresh: {url} へ {packed['bytes']} byte を置いた({result['seconds']} 秒 — lock の hash {digest[:12]})"
    )
    return result


# ── count と gc ────────────────────────────────────────────────────────────


@dataclass
class Tally:
    """数え 1 つ: items = 項目の数・files = file の数・bytes = disk の byte(st_blocks)・freeable = hardlink の数 1 の file の byte。"""

    items: int = 0
    files: int = 0
    bytes: int = 0
    freeable: int = 0

    @classmethod
    def total(cls, tallies: Iterable[Self]) -> Self:
        """数えの和(種類ごとの全部と消す物の数えを、答えの 1 欄にまとめるため)。"""
        parts = list(tallies)
        return cls(
            sum(t.items for t in parts),
            sum(t.files for t in parts),
            sum(t.bytes for t in parts),
            sum(t.freeable for t in parts),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "items": self.items,
            "files": self.files,
            "bytes": self.bytes,
            "freeable": self.freeable,
        }


@dataclass
class Unit:
    """消すかを判じる単位 1 つ(展開済みの wheel・環境・一時の物・venv・git の checkout・組んだ wheel の dir)。"""

    path: Path
    kind: str
    tally: Tally
    shared: bool  # 中の file のどれかが hardlink の数 2 以上
    last_used: float
    # 中の symlink と .pth の path が指す cache の項目(展開済みの wheel archive-v*/<id> と git の checkout git-v*/checkouts/<repo>/<commit>
    # の path)。UV_LINK_MODE=symlink の venv は中身を cache の項目への symlink で持ち、項目の hardlink の数を増やさない — 項目を消すと venv
    # が壊れるので、残す venv の指す項目は消さない。
    links: frozenset[str] = frozenset()


class Ledger:
    """予算の数え: cache の項目ごとの持ち分を、hardlink の inode で数える物。

    hardlink の数 1 の file と dir の byte はその項目だけの物(own)。数 2 以上の file は inode で数え、見た link の数(seen — cache の
    項目の中の link)が hardlink の数より少ない inode は cache の外(venv)も指す物(pinned)で、予算に数えない。pinned でない inode は
    cache の項目の中だけで指される — 指す項目を全部消せば disk が戻るので、1 度だけ数える(展開済みの wheel と環境が同じ中身を指す時)。
    """

    def __init__(self) -> None:
        self.own: dict[Path, int] = {}
        self.inodes: dict[Path, array.array] = {}
        self.size: dict[int, int] = {}
        self.nlink: dict[int, int] = {}
        self.seen: dict[int, int] = {}
        self.pinned: set[int] = set()

    def add(self, path: Path, dirs: list[os.stat_result], files: list[os.stat_result]) -> None:
        own = sum(st.st_blocks * 512 for st in dirs)
        inodes = array.array("q")
        for st in files:
            if st.st_nlink <= 1:
                own += st.st_blocks * 512
                continue
            inodes.append(st.st_ino)
            self.size[st.st_ino] = st.st_blocks * 512
            self.nlink[st.st_ino] = st.st_nlink
            self.seen[st.st_ino] = self.seen.get(st.st_ino, 0) + 1
        self.own[path] = own
        if inodes:
            self.inodes[path] = inodes

    def close(self, kept: Iterable[Path]) -> None:
        """数え終えた後に 1 度: kept(残る項目)の外の持ち分を落とし、cache の外も指す inode を決める。kept の外の項目(期限の消しで
        消した物)は hardlink の数 1 の file だけを持つので、inode の数えは変わらない。"""
        keep = set(kept)
        for path in [p for p in self.own if p not in keep]:
            self.release(path)
        self.pinned = {ino for ino, seen in self.seen.items() if self.nlink[ino] > seen}
        self.nlink.clear()

    def holds_outside(self, path: Path) -> bool:
        """path の項目の file のどれかを cache の外(venv)も hardlink で指すか。"""
        return any(ino in self.pinned for ino in self.inodes.get(path, ()))

    def usage(self) -> int:
        """cache だけが持つ byte の和。"""
        shared = sum(
            self.size[ino] for ino, seen in self.seen.items() if seen and ino not in self.pinned
        )
        return sum(self.own.values()) + shared

    def release(self, path: Path) -> int:
        """path の項目を数えから外し、それで戻る byte(own と、cache の中の最後の link だった pinned でない inode)を返す。"""
        freed = self.own.pop(path, 0)
        for ino in self.inodes.pop(path, ()):
            self.seen[ino] -= 1
            if self.seen[ino] == 0 and ino not in self.pinned:
                freed += self.size[ino]
        return freed


def measure(
    path: Path, archive_roots: tuple[str, ...] = (), ledger: Ledger | None = None
) -> tuple[Tally, bool, float, frozenset[str]]:
    """path の下の file を数え、hardlink を持つ file が在るか・最後の変更の刻・中の symlink と .pth の path が指す cache の項目を返す
    (link は辿らない)。archive_roots = 項目を並べる cache の dir の path(末尾の / つき — entry_roots_of)。ledger を渡すと、予算の
    数えに path の持ち分を載せる。"""
    walked = list(os.walk(path))
    dirs = list(_lstats([path, *(Path(top, name) for top, names, _ in walked for name in names)]))
    files = list(_lstats(Path(top, name) for top, _, names in walked for name in names))
    if ledger is not None:
        ledger.add(path, dirs, files)
    linked = [st for st in files if st.st_nlink > 1 and not stat.S_ISLNK(st.st_mode)]
    tally = Tally(
        items=1,
        files=len(files),
        bytes=sum(st.st_blocks * 512 for st in [*dirs, *files]),
        freeable=sum(st.st_blocks * 512 for st in files) - sum(st.st_blocks * 512 for st in linked),
    )
    entries = [Path(top, name) for top, names, others in walked for name in [*names, *others]]
    pths = [Path(top, name) for top, _, names in walked for name in names if name.endswith(".pth")]
    links = frozenset(
        entry
        for target in [*(_link_target(link) for link in entries), *_pth_paths(pths)]
        if archive_roots and target
        for entry in [_archive_entry(target, archive_roots)]
        if entry
    )
    return tally, bool(linked), (dirs[0].st_mtime if dirs else 0.0), links


def _link_target(link: Path) -> str | None:
    """link が symlink なら、指す先の正規の絶対 path。"""
    try:
        target = os.readlink(link)
    except OSError:  # symlink でない・消えた
        return None
    return os.path.normpath(os.path.join(link.parent, target))


def _pth_paths(pths: Iterable[Path]) -> Iterator[str]:
    """.pth の file の行のうち絶対 path の物(editable の install は source の dir を .pth の行で指す)。"""
    for pth in pths:
        with contextlib.suppress(OSError):
            yield from (
                os.path.normpath(line.strip())
                for line in pth.read_text(errors="replace").splitlines()
                if line.startswith("/")
            )


def _archive_entry(resolved: str, archive_roots: tuple[str, ...]) -> str | None:
    """resolved(正規の絶対 path)が cache の項目の中なら、その項目の path(archive-v*/<id> か git-v*/checkouts/<repo>/<commit>)。"""
    for root in archive_roots:
        if resolved.startswith(root):
            return root + resolved[len(root) :].split("/", 1)[0]
    return None


def _lstats(paths: Iterable[Path]) -> Iterator[os.stat_result]:
    """paths の lstat(消えた物は飛ばす — 走っている uv と並ぶ)。"""
    for path in paths:
        with contextlib.suppress(FileNotFoundError):
            yield path.lstat()


def venv_last_used(venv: Path) -> float:
    """venv を最後に使った刻: dir・pyvenv.cfg・bin・site-packages の最後の変更と、pyvenv.cfg の最後の読み(Python は起動ごとに読む —
    atime を書かない file system では変更の刻と同じ)の遅い方。"""
    probes = [venv, venv / "bin", *venv.glob("lib/python*/site-packages")]
    stamps = [st.st_mtime for st in _lstats(probes)] + [
        t for st in _lstats([venv / "pyvenv.cfg"]) for t in (st.st_mtime, st.st_atime)
    ]
    return max(stamps, default=0.0)


def process_paths(proc: Path = Path("/proc")) -> list[str] | None:
    """走っている process の作業 dir・実行 file・引数の絶対 path・読み込んだ file の path を、並べた list で返す(proc が読めなければ None)。"""
    try:
        pids = [p for p in proc.iterdir() if p.name.isdigit()]
    except OSError:
        return None
    return sorted({path for pid in pids for path in _process_paths_of(pid)})


def _process_paths_of(pid: Path) -> Iterator[str]:
    """process 1 つの作業 dir・実行 file・引数の絶対 path・読み込んだ file の path(読めない物は飛ばす — 終わった・別の uid)。"""
    for link in ("cwd", "exe"):
        with contextlib.suppress(OSError):
            yield os.readlink(pid / link)
    with contextlib.suppress(OSError):
        yield from (
            arg.decode(errors="replace")
            for arg in (pid / "cmdline").read_bytes().split(b"\0")
            if arg.startswith(b"/")
        )
    with contextlib.suppress(OSError):
        lines = (pid / "maps").read_text(errors="replace").splitlines()
        yield from (
            line[line.find(" /") + 1 :].removesuffix(" (deleted)") for line in lines if " /" in line
        )


def used_under(paths: list[str], root: Path) -> bool:
    """並べた path のどれかが root か root の下か。"""
    prefix = str(root)
    at = bisect.bisect_left(paths, prefix)
    while at < len(paths) and paths[at].startswith(prefix):
        rest = paths[at][len(prefix) :]
        if rest == "" or rest.startswith("/"):
            return True
        at += 1
    return False


def find_venvs(root: Path, depth: int) -> Iterator[Path]:
    """root の下の深さ depth までの .venv(pyvenv.cfg を持つ dir)。.venv の中と link の先へは降りない。"""
    if depth <= 0:
        return
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False) or entry.name.startswith(GC_TRASH):
            continue
        path = Path(entry.path)
        if entry.name == ".venv":
            if (path / "pyvenv.cfg").exists():
                yield path
        else:
            yield from find_venvs(path, depth - 1)


def children(cache: Path, prefix: str) -> Iterable[Path]:
    for bucket in sorted(cache.glob(prefix + "*")):
        if bucket.is_dir() and not bucket.is_symlink():
            yield from sorted(bucket.iterdir())


@dataclass
class Survey:
    """cache と venv の置き場を 1 度読んだ物(消す候補の種類ごとの単位と、消さずに symlink だけを読む置き場の venv)。"""

    archives: list[Unit] = field(default_factory=list)
    environments: list[Unit] = field(default_factory=list)
    temporaries: list[Unit] = field(default_factory=list)
    venvs: list[Unit] = field(default_factory=list)
    checkouts: list[Unit] = field(default_factory=list)
    sdists: list[Unit] = field(default_factory=list)
    holders: list[Unit] = field(default_factory=list)

    def groups(self) -> tuple[tuple[str, list[Unit]], ...]:
        return (
            ("archives", self.archives),
            ("environments", self.environments),
            ("temporaries", self.temporaries),
            ("venvs", self.venvs),
            ("checkouts", self.checkouts),
            ("sdists", self.sdists),
        )

    def tallies(self, cutoff: float, used: list[str] | None) -> dict[str, object]:
        kept = self.kept_links(cutoff, used)
        return {
            kind: {
                "all": Tally.total(u.tally for u in units).as_dict(),
                "doomed": Tally.total(
                    u.tally for u in units if is_doomed(u, cutoff, used, kept)
                ).as_dict(),
            }
            for kind, units in self.groups()
        }

    def kept_links(self, cutoff: float, used: list[str] | None) -> frozenset[str]:
        """残る venv・環境と、symlink だけを読む置き場の venv が指す展開済みの wheel の項目(消さない)。"""
        survivors = [u for u in [*self.venvs, *self.environments] if not is_doomed(u, cutoff, used)]
        return frozenset(entry for unit in [*survivors, *self.holders] for entry in unit.links)


def _unit(
    path: Path,
    kind: str,
    last_used: float | None = None,
    archive_roots: tuple[str, ...] = (),
    ledger: Ledger | None = None,
) -> Unit:
    tally, shared, newest, links = measure(path, archive_roots, ledger)
    return Unit(path, kind, tally, shared, newest if last_used is None else last_used, links)


def _real_dirs(path: Path) -> list[Path]:
    """path の直下の dir(link でない・名が . で始まらない — gc の消しの一時の名と uv の作りかけ .tmp* を除く)を名の順に(path が dir
    でなければ空)。"""
    try:
        return sorted(p for p in path.iterdir() if _is_real_dir(p) and not p.name.startswith("."))
    except (FileNotFoundError, NotADirectoryError):
        return []


def checkout_repos(cache: Path) -> list[Path]:
    """git の checkout を並べる dir(git-v*/checkouts/<repo>)。"""
    return [
        repo
        for bucket in sorted(cache.glob(GIT_PREFIX + "*"))
        if _is_real_dir(bucket)
        for repo in _real_dirs(bucket / GIT_CHECKOUTS)
    ]


def is_sdist_unit(path: Path) -> bool:
    """path が組んだ物の dir か(版の索引 revision.* か組みの控え metadata.msgpack を直下に持つ)。"""
    try:
        return any(p.name.startswith(SDIST_INDEX) or p.name == SDIST_BUILT for p in path.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return False


def sdist_walk(path: Path, depth: int) -> Iterator[tuple[Path, bool]]:
    """path(sdists-v*/<種類> か、その下の組んだ物を並べる dir)の下を、組んだ物の dir に当たるまで深さ depth まで降り、
    (dir, 組んだ物の dir か) を渡す(組んだ物の dir の中へは降りない)。"""
    for child in _real_dirs(path):
        unit = is_sdist_unit(child)
        yield child, unit
        if not unit and depth > 1:
            yield from sdist_walk(child, depth - 1)


def sdist_paths(cache: Path) -> list[Path]:
    """組んだ物の dir の全部(消す単位)。"""
    return [
        path
        for bucket in sorted(cache.glob(SDISTS_PREFIX + "*"))
        if _is_real_dir(bucket)
        for kind in _real_dirs(bucket)
        for path, unit in sdist_walk(kind, SDIST_DEPTH)
        if unit
    ]


def entry_roots_of(cache: Path) -> tuple[str, ...]:
    """venv の symlink と .pth が指しうる項目を並べる cache の dir の path(archive-v* と git-v*/checkouts/<repo> — 末尾の / つき。
    指す先と頭で比べる)。"""
    archives = (bucket for bucket in sorted(cache.glob(ARCHIVE_PREFIX + "*")) if bucket.is_dir())
    return tuple(f"{root}/" for root in [*archives, *checkout_repos(cache)])


def survey(
    cache: Path, venv_roots: list[Path], depth: int, link_roots: list[Path] | None = None
) -> Survey:
    temporaries = [*sorted(cache.glob(TMP_PREFIX + "*")), *children(cache, BUILDS_PREFIX)]
    archive_roots = entry_roots_of(cache)
    return Survey(
        archives=[_unit(entry, "archive") for entry in children(cache, ARCHIVE_PREFIX)],
        environments=[
            _unit(entry, "environment", venv_last_used(entry), archive_roots)
            for entry in children(cache, ENVIRONMENTS_PREFIX)
        ],
        temporaries=[_unit(entry, "temporary") for entry in temporaries],
        venvs=[
            _unit(venv, "venv", venv_last_used(venv), archive_roots)
            for root in venv_roots
            for venv in find_venvs(root, depth)
        ],
        checkouts=[
            _unit(entry, "checkout") for repo in checkout_repos(cache) for entry in _real_dirs(repo)
        ],
        sdists=[_unit(path, "sdist") for path in sdist_paths(cache)],
        holders=[
            _unit(venv, "holder", venv_last_used(venv), archive_roots)
            for root in link_roots or []
            for venv in find_venvs(root, depth)
        ],
    )


def _rest_of(child: Path) -> Iterator[tuple[Path, bool]]:
    """cache の直下の child のうち、消す単位の外の物を (path, 中まで数えるか) で渡す(中まで数えない物は dir それ自身の byte だけ —
    単位を並べる dir)。この道具の印・錠・log と作りかけ(.doeff-uv-*)は数えない — gc の回ごとに印を書き直し、和が回ごとに揺れる。"""
    units = (ARCHIVE_PREFIX, ENVIRONMENTS_PREFIX, BUILDS_PREFIX, TMP_PREFIX, ".doeff-uv-")
    if child.name.startswith(units):
        return
    if child.name.startswith(GIT_PREFIX) and _is_real_dir(child):
        yield child, False
        yield from ((p, p.name != GIT_CHECKOUTS) for p in child.iterdir())
        for repo in _real_dirs(child / GIT_CHECKOUTS):
            yield repo, False
            yield from ((p, True) for p in repo.iterdir() if not _is_real_dir(p))
    elif child.name.startswith(SDISTS_PREFIX) and _is_real_dir(child):
        yield child, False
        yield from ((p, True) for p in child.iterdir() if not _is_real_dir(p))
        for kind in _real_dirs(child):
            yield kind, False
            yield from ((p, True) for p in kind.iterdir() if not _is_real_dir(p))
            for path, unit in sdist_walk(kind, SDIST_DEPTH):
                if not unit:
                    yield path, False
                    yield from ((p, True) for p in path.iterdir() if not _is_real_dir(p))
    else:
        yield child, True


def rest_bytes(cache: Path) -> int:
    """cache の中で、消す単位(展開済みの wheel・環境・一時の物・git の checkout・組んだ wheel の dir)の外の disk の byte(git の bare の
    repo・索引・interpreter の控え・uv の印と錠と、単位を並べる dir — 予算の数えの消せない分)。"""
    rest = [item for child in sorted(cache.iterdir()) for item in _rest_of(child)]
    return sum(st.st_blocks * 512 for st in _lstats(p for p, deep in rest if not deep)) + sum(
        measure(p)[0].bytes for p, deep in rest if deep
    )


def is_doomed(
    unit: Unit, cutoff: float, used: list[str] | None, kept: frozenset[str] = frozenset()
) -> bool:
    """unit を消すか: 最後に使った刻が cutoff より前で、種類ごとの決まりに合う物。kept = 残る venv が symlink か .pth で指す cache の
    項目(展開済みの wheel と git の checkout だけが読む)。"""
    if (
        unit.last_used >= cutoff or unit.kind == "holder"
    ):  # symlink だけを読む置き場の venv は消さない
        return False
    # venv と環境は使っている process が無い事を確かめる(proc が読めなければ消さない)。venv は venv を持つ dir(作業木)を作業 dir に
    # する process も使っていると見る。展開済みの wheel は、どの venv も hardlink で指さず、残る venv が symlink で指さず、走っている
    # process が読み込んでいない物(symlink の venv の拡張 module は、読み込んだ process の maps に項目の中の path で出る)。git の
    # checkout は、残る venv が指さず、走っている process(組みの作業 dir・引数・読み込んだ file)が使っていない物(proc が読めなければ
    # 消さない — venv は git の依存を checkout から組んだ wheel で持ち、checkout を指さない。指すのは editable の install だけ)。組んだ
    # 物は、走っている process が使っていない物(展開済みの wheel は別の項目で、組んだ物を消しても venv は壊れない — uv は索引か
    # 版が無ければ組み直す)。
    match unit.kind:
        case "archive":
            return (
                not unit.shared
                and str(unit.path) not in kept
                and not (used is not None and used_under(used, unit.path))
            )
        case "temporary":
            return True
        case "venv":
            return used is not None and not used_under(used, unit.path.parent)
        case "environment" | "checkout":  # kept は環境の path を持たない
            return (
                str(unit.path) not in kept and used is not None and not used_under(used, unit.path)
            )
        case "sdist":
            return not (used is not None and used_under(used, unit.path))
        case other:
            raise ValueError(f"知らない単位の種類 {other!r}")


def remove(unit: Unit) -> bool:
    """unit を同じ dir の中の別の名へ rename してから消す(rename できなければ消さない)。git の checkout は remove_checkout。"""
    if unit.kind == "checkout":
        return remove_checkout(unit)
    trash = unit.path.with_name(f"{GC_TRASH}{os.getpid()}-{unit.path.name}")
    try:
        unit.path.rename(trash)
    except OSError as error:
        say(f"gc: {unit.path} を退けられない({error})— 残す")
        return False
    shutil.rmtree(trash, ignore_errors=True)
    return True


def remove_checkout(unit: Unit) -> bool:
    """git の checkout(git-v*/checkouts/<repo>/<commit>)を消す: uv がその repo の取り寄せと checkout の間に持つ錠(git-v*/locks/<repo>
    — flock)を待たずに取れた時だけ、先に印 <commit>.ok を退け(uv は印の無い checkout を作り直す — 印だけが残って中身の無い
    checkout を uv が使う事が無い)、続けて dir を退けてから消す。"""
    repo = unit.path.parent
    lock = repo.parent.parent / GIT_LOCKS / repo.name
    mark = unit.path.with_name(unit.path.name + CHECKOUT_MARK)
    with held(lock, wait=False) as got:
        if not got:
            say(f"gc: {unit.path} の repo の錠を uv が持っている — 残す")
            return False
        trashed_mark = mark.with_name(f"{GC_TRASH}{os.getpid()}-{mark.name}")
        try:
            mark.rename(trashed_mark)
        except FileNotFoundError:  # 印の無い checkout(作りかけか前の消しの残り)— uv は使わない
            pass
        except OSError as error:
            say(f"gc: {mark} を退けられない({error})— 残す")
            return False
        trash = unit.path.with_name(f"{GC_TRASH}{os.getpid()}-{unit.path.name}")
        try:
            unit.path.rename(trash)
        except OSError as error:
            say(f"gc: {unit.path} を退けられない({error})— 印を外したまま残す(uv は作り直す)")
            return False
    trashed_mark.unlink(missing_ok=True)
    shutil.rmtree(trash, ignore_errors=True)
    return True


def trim(
    units: list[Unit],
    ledger: Ledger,
    size_budget: int,
    floor_cutoff: float,
    used: list[str] | None,
    kept: frozenset[str],
    fixed: int,
) -> dict[str, object]:
    """cache だけが持つ byte の和が size_budget を越えていれば、最後に使った刻の古い項目から、和が size_budget に収まるまで消す(頭の註の予算)。
    和は消す単位の持ち分(ledger)と、単位の外の消せない byte(fixed — rest_bytes)を足した物。消せる項目は is_doomed の決まりを
    floor_cutoff で当て、展開済みの wheel は cache の外の venv が hardlink で指さない物に限る(cache の中の環境だけが指す物は消せる —
    戻る byte は指す項目を全部消した時に数える)。"""
    start = ledger.usage() + fixed
    usage = start
    plural = {
        "archive": "archives",
        "environment": "environments",
        "temporary": "temporaries",
        "checkout": "checkouts",
        "sdist": "sdists",
    }
    removed = dict.fromkeys(plural.values(), 0)
    for unit in sorted(units, key=lambda u: u.last_used):
        if usage <= size_budget:
            break
        candidate = dataclasses.replace(unit, shared=ledger.holds_outside(unit.path))
        if is_doomed(candidate, floor_cutoff, used, kept) and remove(unit):
            usage -= ledger.release(unit.path)
            removed[plural[unit.kind]] += 1
    return {
        "bytes": size_budget,
        "fixed": fixed,
        "usage-before": start,
        "usage-after": usage,
        "freed": start - usage,
        "removed": removed,
        "within": usage <= size_budget,
    }


def disk_free(path: Path) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def gc(
    cache: Path,
    venv_roots: list[Path],
    idle_days: float,
    depth: int,
    dry_run: bool,
    every_seconds: float | None,
    proc: Path = Path("/proc"),
    link_roots: list[Path] | None = None,
    size_budget: int | None = None,
    floor_days: float = 1.0,
) -> dict[str, object]:
    """古い cache の項目と使われない venv を、数えてから消すため(頭の註の gc)。link_roots = 消さずに、中の venv の symlink が指す
    展開済みの wheel の項目だけを守る置き場(UV_LINK_MODE=symlink の venv を持つ dir — --venvs の外の置き場)。size_budget = cache だけが
    持つ byte の上限(None なら期限だけで消す)・floor_days = 予算で消す項目の、最後に使ってからの最短の日数。"""
    verb = "count" if dry_run else "gc"
    mark = cache / GC_MARK
    if (
        not dry_run
        and every_seconds is not None
        and mark.exists()
        and time.time() - mark.stat().st_mtime < every_seconds
    ):
        say(f"gc: 前の gc から {every_seconds:.0f} 秒が経っていない — 消さない")
        return {"verb": verb, "ran": False, "reason": "recent"}
    started = time.monotonic()
    with held(cache / GC_LOCK, wait=False) as got:
        if not got:
            say("gc: 同じ cache の別の gc が走っている — 消さない")
            return {"verb": verb, "ran": False, "reason": "locked"}
        for leftover in [
            *cache.glob(GC_TRASH + "*"),
            *(p for root in venv_roots for p in root.glob(f"*/{GC_TRASH}*")),
        ]:
            shutil.rmtree(leftover, ignore_errors=True)
        cutoff = time.time() - idle_days * 86400
        free_before = disk_free(cache)
        used = process_paths(proc)
        before = survey(cache, venv_roots, depth, link_roots)
        counted = before.tallies(cutoff, used)
        result: dict[str, object] = {
            "verb": verb,
            "ran": True,
            "cache": str(cache),
            "idle-days": idle_days,
            "venv-roots": [str(r) for r in venv_roots],
            "link-roots": [str(r) for r in link_roots or []],
            "proc-readable": used is not None,
            "before": counted,
            "free-before": free_before,
        }
        say(f"gc: 数えた — {json.dumps(counted, ensure_ascii=False, sort_keys=True)}")
        if dry_run:
            result["seconds"] = round(time.monotonic() - started, 1)
            return result
        gone = {
            kind: [unit for unit in units if is_doomed(unit, cutoff, used) and remove(unit)]
            for kind, units in before.groups()
            if kind in ("environments", "temporaries", "venvs")
        }
        # venv と環境を消すと、同じ中身を指していた展開済みの wheel の hardlink の数が減る — 数え直してから判じる。残る(消さなかった・
        # 消せなかった)venv と環境と、symlink だけを読む置き場の venv が symlink か .pth で指す項目(展開済みの wheel と git の
        # checkout)は消さない。
        first = {unit.path for units in gone.values() for unit in units}
        kept = frozenset(
            entry
            for unit in [*before.venvs, *before.environments, *before.holders]
            if unit.path not in first
            for entry in unit.links
        )
        gone |= {
            kind: [unit for unit in units if is_doomed(unit, cutoff, used, kept) and remove(unit)]
            for kind, units in before.groups()
            if kind in ("checkouts", "sdists")
        }
        removed_paths = {unit.path for units in gone.values() for unit in units}
        # 予算を数える時は、venv を消して変わった hardlink の数で、残る cache の項目の持ち分を数え直す。
        ledger = Ledger() if size_budget is not None else None
        archives = [
            _unit(entry, "archive", ledger=ledger) for entry in children(cache, ARCHIVE_PREFIX)
        ]
        expired = [
            unit for unit in archives if is_doomed(unit, cutoff, used, kept) and remove(unit)
        ]
        removed = {**{kind: len(units) for kind, units in gone.items()}, "archives": len(expired)}
        if ledger is not None and size_budget is not None:
            expired_paths = {unit.path for unit in expired}
            survivors = [
                *(unit for unit in archives if unit.path not in expired_paths),
                *(
                    _unit(unit.path, unit.kind, unit.last_used, ledger=ledger)
                    for unit in [*before.environments, *before.temporaries, *before.checkouts]
                    if unit.path not in removed_paths
                ),
                *(
                    _unit(unit.path, unit.kind, ledger=ledger)
                    for unit in before.sdists
                    if unit.path not in removed_paths
                ),
            ]
            ledger.close(unit.path for unit in survivors)
            trimmed = trim(
                survivors,
                ledger,
                size_budget,
                time.time() - floor_days * 86400,
                used,
                kept,
                rest_bytes(cache),
            )
            trimmed["floor-days"] = floor_days
            result["size-budget"] = trimmed
            say(f"gc: 予算 — {json.dumps(trimmed, ensure_ascii=False, sort_keys=True)}")
        after = survey(cache, venv_roots, depth, link_roots)
        result.update(
            {
                "removed": removed,
                "after": after.tallies(cutoff, used),
                "free-after": disk_free(cache),
                "seconds": round(time.monotonic() - started, 1),
            }
        )
        partial = mark.with_name(mark.name + ".partial")
        partial.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
        partial.replace(mark)
    say(
        f"gc: 消した {removed}・空き {free_before} → {result['free-after']} byte({result['seconds']} 秒)"
    )
    return result


# ── upkeep(起動の準備が背後で 1 度起こす)──────────────────────────────────


@dataclass(frozen=True)
class UpkeepSettings:
    """起動の準備の環境変数から読む値(頭の註の upkeep)。seed_url が空なら seed しない・gc_days が None なら gc しない・
    refresh_projects が空か node が refresh_node でなければ refresh しない。"""

    seed_url: str = ""
    seed_secret: str = ""
    seed_min_free: int = 50 << 30
    gc_days: float | None = None
    gc_venvs: tuple[Path, ...] = ()
    gc_every_seconds: float = 72000.0
    gc_link_roots: tuple[Path, ...] = ()
    node: str = ""
    refresh_node: str = ""
    refresh_projects: tuple[Path, ...] = ()
    refresh_every_seconds: float = 72000.0
    # refresh が撃つ uv(`uv run` が子に渡す UV — 無ければ PATH の uv)。
    uv: str = "uv"
    # gc の予算(cache だけが持つ byte の上限 — None なら期限だけ)と、予算で消す項目の最短の日数。
    gc_size_budget: int | None = None
    gc_floor_days: float = 1.0

    @property
    def refreshes(self) -> bool:
        return bool(
            self.seed_url and self.refresh_projects and self.refresh_node == self.node != ""
        )

    @classmethod
    def from_env(cls, env: dict[str, str]) -> Self:
        days = env.get("DOEFF_UV_CACHE_GC_DAYS", "").strip()
        venvs = tuple(Path(p) for p in env.get("DOEFF_UV_CACHE_GC_VENVS", "").split(":") if p)
        links = tuple(Path(p) for p in env.get("DOEFF_UV_CACHE_GC_LINK_ROOTS", "").split(":") if p)
        projects = env.get("DOEFF_UV_CACHE_SEED_REFRESH_PROJECTS", "")
        size_budget = env.get("DOEFF_UV_CACHE_GC_SIZE_BUDGET_GIB", "").strip()
        return cls(
            node=env.get("NODE_NAME", "").strip(),
            refresh_node=env.get("DOEFF_UV_CACHE_SEED_REFRESH_NODE", "").strip(),
            refresh_projects=tuple(Path(p) for p in projects.split(":") if p),
            refresh_every_seconds=float(
                env.get("DOEFF_UV_CACHE_SEED_REFRESH_EVERY_SECONDS", "72000")
            ),
            uv=env.get("UV", "").strip() or "uv",
            seed_url=env.get("DOEFF_UV_CACHE_SEED_URL", "").strip(),
            seed_secret=env.get("DOEFF_UV_CACHE_SEED_SECRET", "").strip(),
            seed_min_free=int(float(env.get("DOEFF_UV_CACHE_SEED_MIN_FREE_GIB", "50")) * (1 << 30)),
            gc_days=float(days) if days else None,
            gc_venvs=venvs,
            gc_every_seconds=float(env.get("DOEFF_UV_CACHE_GC_EVERY_SECONDS", "72000")),
            gc_link_roots=links,
            gc_size_budget=int(float(size_budget) * (1 << 30)) if size_budget else None,
            gc_floor_days=float(env.get("DOEFF_UV_CACHE_GC_FLOOR_DAYS", "1")),
        )


def upkeep(cache: Path, settings: UpkeepSettings, part: int = PART) -> dict[str, object]:
    """worker の起動ごとに、新しい node の cache を置き場の写しで用意し(印が無く、空きが seed_min_free 以上の時だけ)、続けて古い物を
    片付け(前の gc から gc_every_seconds が経った時だけ)、決めた 1 つの node では置き場の写しを lock に合わせて置き直す(refresh)ため。
    どれが落ちても他と起動を止めない — 落ちた訳を 1 行出して続ける。"""
    cache.mkdir(parents=True, exist_ok=True)
    seeded: dict[str, object] = {"seeded": False, "reason": "unset"}
    if settings.seed_url and (cache / SEED_MARK).exists():
        seeded = {"seeded": False, "reason": "marked"}
    elif settings.seed_url and disk_free(cache) < settings.seed_min_free:
        say(
            f"seed: 空き {disk_free(cache)} byte が下限 {settings.seed_min_free} byte を下回る — 入れない"
        )
        seeded = {"seeded": False, "reason": "low-free"}
    elif settings.seed_url:
        try:
            source = resolve_store(settings.seed_url, settings.seed_secret or None)
            seeded = seed(
                cache, source.url, source.credentials, "us-east-1", 300.0, force=False, part=part
            )
        except (SystemExit, OSError, tarfile.TarError, http.client.HTTPException) as error:
            say(f"seed: 入れられない — {error}(片付けと起動は続ける)")
            seeded = {"seeded": False, "reason": "failed", "error": str(error)}
    collected: dict[str, object] = {"ran": False, "reason": "unset"}
    if settings.gc_days is not None:
        roots = [r.resolve() for r in settings.gc_venvs if r.is_dir()]
        try:
            collected = gc(
                cache,
                roots,
                settings.gc_days,
                3,
                dry_run=False,
                every_seconds=settings.gc_every_seconds,
                link_roots=[r.resolve() for r in settings.gc_link_roots if r.is_dir()],
                size_budget=settings.gc_size_budget,
                floor_days=settings.gc_floor_days,
            )
        except OSError as error:
            say(f"gc: 片付けられない — {error}")
            collected = {"ran": False, "reason": "failed", "error": str(error)}
    refreshed: dict[str, object] = {"refreshed": False, "reason": "unset"}
    if settings.refreshes:
        try:
            target = resolve_store(settings.seed_url, settings.seed_secret or None)
            refreshed = refresh(
                cache,
                list(settings.refresh_projects),
                target.url,
                target.credentials,
                "us-east-1",
                300.0,
                every_seconds=settings.refresh_every_seconds,
                min_free=settings.seed_min_free,
                force=False,
                uv=settings.uv,
            )
        except (
            SystemExit,
            OSError,
            subprocess.CalledProcessError,
            http.client.HTTPException,
        ) as error:
            say(f"refresh: 置き直せない — {error}")
            refreshed = {"refreshed": False, "reason": "failed", "error": str(error)}
    return {"verb": "upkeep", "seed": seeded, "gc": collected, "refresh": refreshed}


# ── 入口 ────────────────────────────────────────────────────────────────────


def store_verb(args: argparse.Namespace, cache: Path, target: Target) -> dict[str, object]:
    """置き場を使う動詞(publish・seed・refresh)を、解いた置き場 target で撃つ。"""
    match args.verb:
        case "publish":
            packed = pack(cache, args.out)
            put = http_put_file(target.url, args.out, target.credentials, args.region, args.timeout)
            say(
                f"publish: {target.url} へ {packed['bytes']} byte を置いた(pack {packed['seconds']} 秒)"
            )
            return {**packed, "verb": "publish", "url": target.url, **put}
        case "seed":
            return seed(
                cache,
                target.url,
                target.credentials,
                args.region,
                args.timeout,
                args.force,
                args.workers,
            )
        case _:  # refresh(main が渡すのは publish・seed・refresh だけ)
            return refresh(
                cache,
                [p.resolve() for p in args.project],
                target.url,
                target.credentials,
                args.region,
                args.timeout,
                every_seconds=args.every_seconds,
                min_free=int(args.min_free_gib * (1 << 30)),
                force=args.force,
                uv=os.environ.get("UV", "").strip() or "uv",
            )


def main(argv: list[str] | None = None) -> int:
    """命令の口(動詞と旗は頭の註)。答えの JSON を標準出力へ 1 行出すため。"""
    parser = argparse.ArgumentParser(prog="uv_cache.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    sub.add_parser("upkeep").add_argument(
        "--cache", required=True, type=Path, help="uv の cache の dir(UV_CACHE_DIR)"
    )
    for name in ("pack", "publish", "seed", "refresh", "count", "gc"):
        p = sub.add_parser(name)
        p.add_argument("--cache", required=True, type=Path, help="uv の cache の dir(UV_CACHE_DIR)")
        if name in ("pack", "publish"):
            p.add_argument(
                "--out", required=True, type=Path, help="pack の file(.tar.zst か .tar.gz)"
            )
        if name in ("publish", "seed", "refresh"):
            p.add_argument(
                "--url",
                required=True,
                help="s3://<bucket>/<key> か S3 の path の形の http(s) の URL(seed は file の path も)",
            )
            p.add_argument(
                "--secret", help="資格と置き場の入口(endpoint)の k8s の Secret <namespace>/<name>"
            )
            p.add_argument("--region", default="us-east-1")
            p.add_argument(
                "--timeout", type=float, default=300.0, help="HTTP の 1 回の読み書きの上限の秒"
            )
        if name == "seed":
            p.add_argument("--force", action="store_true", help="印が在っても入れ直す")
            p.add_argument("--workers", type=int, default=8, help="並べる Range の GET の数")
        if name == "refresh":
            p.add_argument(
                "--project",
                action="append",
                required=True,
                type=Path,
                help="依存を取り込む project の dir(uv.lock が在る — 繰り返せる)",
            )
            p.add_argument(
                "--every-seconds",
                type=float,
                default=72000.0,
                help="置いてからこの秒が経っていなければ組まない",
            )
            p.add_argument(
                "--min-free-gib", type=float, default=50.0, help="空きがこれを下回れば組まない"
            )
            p.add_argument("--force", action="store_true", help="hash と刻に関わらず組む")
        if name in ("count", "gc"):
            p.add_argument(
                "--venvs",
                action="append",
                default=[],
                type=Path,
                help="下の .venv を消す候補にする dir(繰り返せる)",
            )
            p.add_argument(
                "--links",
                action="append",
                default=[],
                type=Path,
                help="消さずに、下の .venv の symlink が指す展開済みの wheel の項目だけを守る dir(繰り返せる)",
            )
            p.add_argument(
                "--depth",
                type=int,
                default=3,
                help="--venvs と --links の dir から .venv を探す深さ",
            )
            p.add_argument(
                "--idle-days",
                type=float,
                default=7.0,
                help="最後に使ってからこの日数を過ぎた物だけを消す",
            )
        if name == "gc":
            p.add_argument(
                "--every-seconds", type=float, help="前の gc からこの秒が経っていなければ何もしない"
            )
            p.add_argument(
                "--size-budget-gib",
                type=float,
                help="期限の消しの後、cache だけが持つ byte がこれを越えれば古い順に消す",
            )
            p.add_argument(
                "--floor-days",
                type=float,
                default=1.0,
                help="予算で消す項目の、最後に使ってからの最短の日数",
            )
    args = parser.parse_args(argv)
    cache: Path = args.cache.resolve()
    match args.verb:
        case "pack":
            answer(pack(cache, args.out))
        case "publish" | "seed" | "refresh":
            answer(store_verb(args, cache, resolve_store(args.url, args.secret)))
        case "upkeep":
            answer(upkeep(cache, UpkeepSettings.from_env(dict(os.environ))))
        case "count" | "gc":
            roots = [r.resolve() for r in args.venvs if r.is_dir()]
            answer(
                gc(
                    cache,
                    roots,
                    args.idle_days,
                    args.depth,
                    dry_run=args.verb == "count",
                    every_seconds=getattr(args, "every_seconds", None),
                    link_roots=[r.resolve() for r in args.links if r.is_dir()],
                    size_budget=(
                        int(args.size_budget_gib * (1 << 30))
                        if getattr(args, "size_budget_gib", None) is not None
                        else None
                    ),
                    floor_days=getattr(args, "floor_days", 1.0),
                )
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
