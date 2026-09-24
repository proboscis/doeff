"""agentd の要求を実世界で扱う handler — HTTP(ACP・custody)/ RPC(sessionhost)/ file / 時計。

判断を持たない: 要求(effects.py)を受けて I/O を行い、結果を値の型(effects.py)で返す。
どの handler を使うかは runtime.py(composition root)だけが選ぶ。fake は fake.py。

wire の綴り:
- ACP の資源の読み = ``GET /api/resources?kind=`` / ``GET /api/resources/<key>``、書き =
  ``POST /api/events``(control_plane.status_synced / spec_applied・ifGeneration の CAS)、
  watch = ``GET /api/watch/stream?since=``(SSE・`event: watch` の開幕 + `data: {latestSequence}`)、
  中継 = ``POST /api/streams/{owner}/{name}``(``{"frames": [...]}`` → ``{"ok", "seq", "subscribers"?}``)。
  bearer = 名簿の agentd の札(``Authorization: Bearer``)。
- custody = ``POST /lease/{claude|codex}``(``{"account", "purpose"}``・身元 ``X-Borrower-Key``)、
  ``POST /lease/{id}/revoke``。
- 会話の記録の service(段 9f lane 9f-2)= ``POST /v1/conversations/{cid}/streams/{streamId}/events``
  (契約 record-service.json appendEvents・bearer = ACP と同じ名簿の agentd の札)。本文の batch の spool =
  ``<spool dir>/<鍵>.json``(1 batch 1 file・temp + fsync + rename + dir の fsync — 送る前の outbox)。
- 器 = sessionhost の RPC(``doeff_agents.agentd_client.AgentdClient`` の JSON-lines)。
- 所有の検(段 6 lane 6f)= GCE の metadata server ``GET http://metadata.google.internal/
  computeMetadata/v1/project/project-id``(header ``Metadata-Flavor: Google``)。届かない機体
  (Mac・GCE の外)は値 None — 判断(一致・不一致・読めない)は join.ownership-verdict。
- profile の家の在否(段 8e lane 4j)= dotfiles agentcli の登録簿の 1 点を subprocess で読み
  (``PROFILES_COMMAND`` = ``agentcli profiles list --json --kind K`` → ``[{"name", "dir", …}…]``)、
  その ``dir`` の実在をこの機体で検める。家の無い機体(pool の pod)は usage を読まない。
- profile の残量(段 7 lane 7d-3)= dotfiles agentcli の usage の 1 点を subprocess で読む
  (``USAGE_COMMAND`` = ``ai usage --json [--cache-ttl N]`` → ``{"claude": [record…], "codex": […]}``)。
  agentcli は doeff の tool env に無い(doeff は dotfiles の上流)ので import ではなく console script。
  会社境界(会社 profile の API 呼び出しは会社機体だけ・unknown は不許可)は agentcli の葉
  (company_boundary)がその中で判定し、断り・失敗は record の ``error`` に載る — ここは
  ProfileUsageUnavailable に写すだけで第 2 の判定を持たない。

秘密の扱い: 借りた access token・auth.json は値として返すだけで log に出さない。
"""

# pyright: strict
import contextlib
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPException, HTTPResponse, HTTPSConnection
from typing import NamedTuple, TypeAlias, TypeGuard, assert_never, get_args

from doeff import EffectBase, K, Pass, Resume
from doeff_agents.agentd_client import AgentdClient, AgentdClientError, launch_rpc_timeout_seconds
from doeff_agents.sessionhost.acp import host_slots
from doeff_agents.sessionhost.acp.cache_operation import (
    AcpCacheOperations,
    MaintenanceTransportUncertainError,
    SessionCachePing,
    SessionCacheProbe,
)
from doeff_agents.sessionhost.acp.effects import (
    CUSTODY_STAGE_LEASE,
    CUSTODY_STAGE_REDEEM,
    DECLARATION_FINGERPRINT_HEADER,
    FS_READ_TEXT_DEFAULT_MAX_CHARS,
    JSON,
    MEMORY_KIND,
    MEMORY_SPEC_CONVERSATION_KEY,
    MESSAGE_CONVERSATION_FIELDS,
    MESSAGE_KIND,
    OWNERSHIP_PROOF_GCE_PREFIX,
    RECORD_PAGE_MAX_LIMIT,
    RECORD_SPOOL_GIVEN_UP_DIR,
    SESSION_LIVE_STATUSES,
    SESSION_OBSERVED_BUSY,
    SESSION_OBSERVED_IDLE,
    STREAM_SOURCE_HEADER,
    SUMMARY_KIND,
    SUMMARY_SPEC_CONVERSATION_KEY,
    TURN_RECORD_CONVERSATION_FIELD,
    TURN_RECORD_KIND,
    TURN_RECORD_RUNNING_SELECTOR,
    AcpConversationMail,
    AcpConversationMemories,
    AcpConversationSummaries,
    AcpCreate,
    AcpEventWindow,
    AcpGet,
    AcpGetRow,
    AcpPutSpec,
    AcpPutStatus,
    AcpRow,
    AcpRunningTurnRecords,
    AcpStreamPush,
    AcpTurnHeadlines,
    AcpWatchSse,
    CaptureFrame,
    CaptureGone,
    CaptureOutcome,
    ClockNowMs,
    CommandExited,
    CommandGone,
    CommandProbe,
    CommandProbeOutcome,
    CommandRefused,
    CommandRunning,
    CommandStart,
    CommandStarted,
    CommandStartOutcome,
    CommandStop,
    Conflict,
    CustodyHealth,
    CustodyLeaseBorrow,
    CustodyLeaseRevoke,
    DriverResolution,
    DriversUnavailable,
    Escalated,
    EventWindow,
    FsCanonicalPath,
    FsDirectoryExists,
    FsFileExists,
    FsFileSize,
    FsListDirectory,
    FsMakeDirectories,
    FsReadText,
    FsWritePrivateText,
    HostDriversOutcome,
    Interjected,
    JSONObject,
    LeaseGrant,
    LeaseKind,
    LeaseOutcome,
    LeaseRefused,
    ListHostDrivers,
    ListPaneSeats,
    ListProfileHomes,
    LogLine,
    MetricLine,
    MintId,
    OwnershipProbe,
    PaneSeat,
    PaneSeatsOutcome,
    PaneSeatsUnavailable,
    ProbeAnswer,
    ProfileHome,
    ProfileUsage,
    ProfileUsageOutcome,
    ProfileUsageUnavailable,
    PublishWorker,
    Pushed,
    PushOutcome,
    ReadProfileUsage,
    RecordAppend,
    RecordAppended,
    RecordAppendOutcome,
    RecordBatch,
    RecordConflicted,
    RecordEvent,
    RecordPage,
    RecordRead,
    RecordReadOutcome,
    RecordReadSince,
    RecordReadStream,
    RecordSpoolGiveUp,
    RecordSpoolList,
    RecordSpoolListing,
    RecordSpoolPut,
    RecordSpoolRemove,
    RecordStream,
    RecordStreamKind,
    RecordSupersede,
    RecordSupersedeConflicted,
    RecordSuperseded,
    RecordSupersedeOutcome,
    RecordUnread,
    RecordUnsent,
    Refused,
    ResolveLocalExecutable,
    SessionCapture,
    SessionCleanup,
    SessionEscalate,
    SessionEvents,
    SessionGet,
    SessionInterject,
    SessionInterrupt,
    SessionLaunch,
    SessionList,
    SessionOutcome,
    SessionRefused,
    SessionResume,
    SessionSend,
    SessionTranscript,
    SessionView,
    TranscriptChunk,
    UsageWindow,
    UsageWindowName,
    WatchAdvance,
    WatchKind,
    WorkerPublished,
    WriteOutcome,
    Written,
    ownership_proof_file_parts,
)
from doeff_agents.sessionhost.acp.io_types import (
    AcpRows,
    Offsets,
    PaneSeats,
    Paths,
    ProfileHomes,
    ProfileUsageOutcomes,
)
from doeff_agents.sessionhost.attachment import TurnAttachment, attachment_wire
from doeff_agents.sessionhost.cache_host_model import HostCacheRecord, decode_cache_receipt
from doeff_agents.sessionhost.drivers import driver_path_in

Dispatcher: TypeAlias = Callable[[EffectBase, K], "Resume | Pass"]

#: ACP の URL に既定は無い(段 10 lane 10h 便 2 — 宣言 ACP_DAEMON_URL ちょうど・localhost の bridge は段 9p で退役)。
#: custody の URL に既定は無い(段 10 lane 10d 便 2・agora-redesign #85)— 宣言(join の [custody].url / --custody →
#: AGORA_CUSTODY_URL)ちょうど。宣言の無い機体は借りない(custody_declared が偽の node は口座の要る job を起こさない)。
BORROWER_KEY_PATH_DEFAULT = "~/.local/state/agora/borrower-key"
#: agentd の書きが乗る source(ACP の cpSource — 登録の無い source は無制限)。
ACP_WRITE_SOURCE = "agentd"
#: HTTP の期限(秒)。watch は別(SSE の読みは keepalive 15 s より長く待つ)。
HTTP_TIMEOUT_SECONDS = 30.0
WATCH_READ_TIMEOUT_SECONDS = 60.0
WATCH_RECONNECT_SECONDS = 2.0
#: 器(host)の出来事の journal の long-poll(段 12 lane 12b・agora-redesign #207 根 1): 1 回の待ちの上限(host の
#: WAIT-EVENTS-MAX-SECONDS = 60 の内)、socket の読みの余白、届かない / 断られた拍の張り直しの有界の backoff。
SESSION_WAKE_WAIT_SECONDS = 30.0
SESSION_WAKE_READ_MARGIN_SECONDS = 5.0
SESSION_WAKE_RETRY_SECONDS = 1.0
SESSION_WAKE_RETRY_MAX_SECONDS = 30.0
#: profile の残量の読み口(段 7 lane 7d-3)= dotfiles agentcli の console script の 1 点。PATH で解く
#: (launchd の job_env.sh は ~/.local/bin を載せる)。会社境界の判定はこの葉の中。
USAGE_COMMAND: tuple[str, ...] = ("ai", "usage", "--json")
USAGE_CACHE_TTL_FLAG = "--cache-ttl"
#: profile の登録簿の読み口(段 8e lane 4j)= agentcli の console script の 1 点(ADR-DOTFILES-005 R2
#: 「profile 登録簿の問い合わせは単一の正」)。家(dir)の実在はこの機体で検める。
PROFILES_COMMAND: tuple[str, ...] = ("agentcli", "profiles", "list", "--json")
PROFILES_KIND_FLAG = "--kind"
PROFILES_TIMEOUT_SECONDS = 30.0
#: pane の席の読み口(段 12・agora-redesign #577)= dotfiles agentcli の console script の 1 点
#: (`ai pane-sessions --json` — 席の一覧・席の会話 id・席の家の解きはその葉が単一所有する)。
#: 読み口を持たない機体(pool の pod)では起動できず PaneSeatsUnavailable に落ちる(観測の pane の
#: 半分が空 = pane の席 0 と同じ答えで、自分の session の観測は書く)。
PANE_SESSIONS_COMMAND: tuple[str, ...] = ("ai", "pane-sessions", "--json")
#: 席の一覧(下地への 1 往復)と艦隊一括の走査の実測 1.2 秒に対する上限。
PANE_SESSIONS_TIMEOUT_SECONDS = 30.0
#: worker の面と残量行の公開の口(段 12 lane 12j・agora-redesign #445)= dotfiles agentcli の console script の
#: 1 点(`ai route publish-worker` — 欄・簿・組み立てはその葉。旧 headless-worker の常駐の拍が呼んでいた口と同じ)。
PUBLISH_COMMAND: tuple[str, ...] = ("ai", "route", "publish-worker", "--json")
PUBLISH_CADENCE_FLAG = "--cadence"
PUBLISH_RUNNING_TURNS_FLAG = "--running-turns"
PUBLISH_POLL_TICK_FLAG = "--poll-tick-at"
PUBLISH_TIMEOUT_SECONDS = 120.0
#: 35 profile の live の照会(cache が古い時)を含めた上限。
USAGE_TIMEOUT_SECONDS = 180.0
#: agentcli の record の窓の綴り(`<key>_used_percentage` / `<key>_resets_at`)→ 契約の窓の名。
USAGE_RECORD_WINDOWS: tuple[tuple[UsageWindowName, str], ...] = (
    ("5h", "five_hour"),
    ("7d", "seven_day"),
)


# ------------------------------------------------------------------ JSON の境界


def attachment_params(attachments: tuple[TurnAttachment, ...]) -> list[JSON]:
    """段 10 lane 10o(agora-redesign #96): 型つきの添付 → session.send の wire の項。mime と base64 の
    逐語を運ぶだけ — 画像の綴り(block / input の項)は器の Dialogue が組む(法 012 R21)。"""
    result: list[JSONObject] = [dict(attachment_wire(attachment)) for attachment in attachments]
    return list(result)


def attachments_ignored_of(answer: JSON) -> str | None:
    """session.send の答え → 器が添付を落とした理由(落としていなければ None)。呼び手(agentd)が
    条件 AttachmentIgnored に写す — 黙って落とさない。"""
    if not isinstance(answer, dict):
        return None
    reason = answer.get("attachmentsIgnored")
    return reason if isinstance(reason, str) and reason else None


def _json_object(pairs: list[tuple[str, JSON]]) -> JSONObject:
    """json の object_pairs_hook — decoder が組む object を JSON の型のまま受ける。"""
    return dict(pairs)


def _loads(raw: bytes) -> JSON:
    """bytes → JSON(壊れた JSON は {"error": …} — 断りの本文も同じ形で運ぶ)。"""
    if not raw:
        return {}
    try:
        decoded: JSON = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_object)
    except ValueError:
        return {"error": raw.decode("utf-8", errors="replace")[:500]}
    return decoded


def _as_object(value: JSON) -> JSONObject:
    return value if isinstance(value, dict) else {}


def _str_field(obj: Mapping[str, JSON], key: str) -> str | None:
    value = obj.get(key)
    return value if isinstance(value, str) else None


def _int_field(obj: Mapping[str, JSON], key: str) -> int | None:
    value = obj.get(key)
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _epoch_ms_of_iso(text: str) -> int:
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def decode_row(value: JSON) -> AcpRow | None:
    """GET /api/resources の 1 行 → AcpRow(欠けた欄の行は None — 発明しない)。"""
    if not isinstance(value, dict):
        return None
    namespace = _str_field(value, "resourceNamespace")
    key = _str_field(value, "resourceKey")
    kind = _str_field(value, "resourceKind")
    resource_id = _str_field(value, "resourceId")
    version = _str_field(value, "resourceVersion")
    created = _str_field(value, "resourceCreatedAt") or _str_field(value, "observedAt")
    if namespace is None or key is None or kind is None or resource_id is None or created is None:
        return None
    generation = _int_field(value, "resourceGeneration")
    spec = value.get("resourceSpecJson")
    status = value.get("resourceStatusJson")
    landed = _str_field(value, "resourceLandedAt")
    return AcpRow(
        namespace=namespace,
        key=key,
        kind=kind,
        resource_id=resource_id,
        version=version or "v1",
        generation=generation if generation is not None else 0,
        created_at_ms=_epoch_ms_of_iso(created),
        labels=_as_object(value.get("resourceLabels")),
        payload=_as_object(value.get("resourcePayload")),
        spec=spec if isinstance(spec, dict) else {},
        status=status if isinstance(status, dict) else None,
        landed_at_ms=None if landed is None else _epoch_ms_of_iso(landed),
    )


def decode_event_window(after: int, body: JSONObject) -> EventWindow:
    """``GET /api/event-window`` の応答 → EventWindow(post-image は鍵ごとの最後・delete は retired)。"""
    through = _int_field(body, "through")
    latest = _int_field(body, "latestSequence")
    epoch = body.get("storeEpoch")
    if "storeEpoch" in body and (not isinstance(epoch, str) or not epoch):
        raise RuntimeError(f"agentd: ACP event-window storeEpoch is not a non-empty string: {epoch!r}")
    events = body.get("events")
    rows: dict[str, AcpRow] = {}
    retired: dict[str, None] = {}
    births: dict[str, int] = {}
    for event in events if isinstance(events, list) else []:
        deltas = event.get("postDeltas") if isinstance(event, dict) else None
        for delta in deltas if isinstance(deltas, list) else []:
            if not isinstance(delta, dict):
                continue
            key = _str_field(delta, "key")
            if delta.get("op") == "delete":
                if key is not None:
                    rows.pop(key, None)
                    retired[key] = None
                continue
            row = decode_row(delta.get("image"))
            if row is not None:
                rows[row.key] = row
                retired.pop(row.key, None)
                if row.generation == 1 and row.landed_at_ms is not None:
                    births.setdefault(row.resource_id, row.landed_at_ms)
    return EventWindow(
        complete=True,
        through=through if through is not None else after,
        latest=latest if latest is not None else (through if through is not None else after),
        rows=tuple(rows.values()),
        retired=tuple(retired),
        births=tuple(sorted(births.items())),
        store_epoch=epoch if isinstance(epoch, str) else None,
    )


# ------------------------------------------------------------------ HTTP の小さな口


@dataclass(frozen=True)
class HttpReply:
    status: int
    body: JSONObject


@dataclass(frozen=True)
class HttpRaw:
    """生の応答。status 0 = 届かなかった(error に理由・body は空)。"""

    status: int
    body: bytes
    error: str


#: 接続の宛先(scheme, host, port)— 保つ単位はこれ 1 つ。
HttpHost: TypeAlias = "tuple[str, str, int]"


class HttpAddress(NamedTuple):
    host: HttpHost
    target: str


class CheckedOutConnection(NamedTuple):
    connection: HTTPConnection
    reused: bool


def _split_url(url: str) -> HttpAddress:
    """URL を宛先(scheme, host, port)と要求行の target に割る。"""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    return HttpAddress((parts.scheme, host, port), target)


def _new_connection(host: HttpHost, timeout: float) -> HTTPConnection:
    scheme, name, port = host
    if scheme == "https":
        return HTTPSConnection(name, port, timeout=timeout)
    return HTTPConnection(name, port, timeout=timeout)


class HttpConnections:
    """外向きの HTTP の口 — **host ごとに 1 本の接続を保つ**(ADR-DOE-AGENTS-012 R22 の追補・card
    acp:kanban-issue:ki-6eb745f6d528)。

    呼び毎に TCP を張り直すと、**1 発ごとに名前を引き直す**(getaddrinfo)。この宿は resolv.conf が
    ``options ndots:5`` + search 4 つで、点で終わらない綴り(`…svc.cluster.local` は 4 dots < 5)は
    探索の列を歩く —— 依頼者の実射(2026-09-19・同じ宿・同じ港・同じ路・n=9・中央値): 点なしは
    名引き 23.61 ms / 全体 25.20 ms(**全体の 94 % が名引き**・server 側の処理は 0.17 ms)、点ありは
    3.90 / 5.64 ms、ClusterIP の数字なら 0.02 / 1.46 ms。保った接続は名引きを**接続 1 本につき 1 度**に
    畳むので、綴りに関わらず 1 発 0.46 ms(n=40・p50)—— 1 拍 約 35 往復で 0.882 秒 → 0.016 秒。
    保った接続は相手の idle timeout で先に閉じていることがあるので、**使い回した接続が落ちた時だけ**
    1 度張り直す(新しく張った接続の失敗は本物の失敗 — POST を二度撃たない)。

    ⚠ 保つのは **host ごとに 1 本**で、同じ宛先を 2 つの thread が同時に使った拍は後から来た方が
    その場で接続を張る = **名引き + 握手を払い直す**(実射の「初回」: 点なし 46.26 ms / 点あり
    15.28 ms / ClusterIP 31.81 ms)。今日その値段を払う口は 1 つも無い —— この process の 4 つの thread は
    ``sessionhost-agentd``(拍。``runtime.real_dispatchers`` の口を独りで使う)/
    ``sessionhost-agentd-heartbeat``(``runtime.heartbeat_dispatchers`` の**別の** AcpHttp)/
    ``agentd-watch``(長く開いたままの SSE 1 本で、この口には載せない — WatchReader._loop の註)/
    ``agentd-session-wake``(器の unix socket の ``session.wait_events`` — HTTP を 1 発も撃たない)で、
    1 つの HttpConnections を 2 つの thread が共有しない。``_keep`` の押し出しはその日の備えで、
    上の値段がその拍の代価。
    ⚠ card acp:kanban-issue:ki-e786e72e2ae7 から、受け付けの係(claim から送信まで — ``intake.spawned-intake``)が
    拍と同じ口を同じ VM の別の task から使う(async-dispatch が要求ごとに thread へ出す)。受け付けと拍が同じ宛先を
    同時に撃った拍は、後から来た方が上の値段(名引き + 握手)を払う — 受け付けの数秒に比べて小さい。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: dict[HttpHost, HTTPConnection] = {}

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpRaw:
        host, target = _split_url(url)
        sent = dict(headers)
        if body is not None:
            sent["Content-Type"] = "application/json"
        failure = ""
        for _attempt in (0, 1):
            connection, reused = self._checkout(host, timeout)
            try:
                connection.request(method, target, body=body, headers=sent)
                response = connection.getresponse()
                raw = response.read()
                status = int(response.status)
                will_close = response.will_close
            except (OSError, TimeoutError, HTTPException) as error:
                connection.close()
                failure = str(error)
                if reused:
                    continue
                return HttpRaw(0, b"", failure)
            if will_close:
                connection.close()
            else:
                self._keep(host, connection)
            return HttpRaw(status, raw, "")
        return HttpRaw(0, b"", failure)

    def close(self) -> None:
        with self._lock:
            held = tuple(self._held.values())
            self._held.clear()
        for connection in held:
            connection.close()

    def _checkout(self, host: HttpHost, timeout: float) -> CheckedOutConnection:
        with self._lock:
            connection = self._held.pop(host, None)
        if connection is not None:
            connection.timeout = timeout
            return CheckedOutConnection(connection, True)
        return CheckedOutConnection(_new_connection(host, timeout), False)

    def _keep(self, host: HttpHost, connection: HTTPConnection) -> None:
        with self._lock:
            displaced = self._held.get(host)
            self._held[host] = connection
        if displaced is not None and displaced is not connection:
            # 同じ宛先を 2 つの thread が同時に使った拍 — 後から戻った 1 本だけを保つ。
            displaced.close()


def _http_once(method: str, url: str, headers: Mapping[str, str], timeout: float) -> HttpRaw:
    """1 度きりの要求(起動の拍の 1 回・接続は保たない)。"""
    host, target = _split_url(url)
    connection = _new_connection(host, timeout)
    try:
        connection.request(method, target, headers=dict(headers))
        response = connection.getresponse()
        return HttpRaw(int(response.status), response.read(), "")
    except (OSError, TimeoutError, HTTPException) as error:
        return HttpRaw(0, b"", str(error))
    finally:
        connection.close()


def _http_json(
    connections: HttpConnections,
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: JSONObject | None,
    timeout: float,
) -> HttpReply:
    """JSON の要求 → JSON の応答(HTTP の断りも HttpReply — 到達不能だけ 0)。"""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    raw = connections.request(method, url, headers, data, timeout)
    if raw.status == 0:
        return HttpReply(0, {"error": f"unreachable: {raw.error}"})
    return HttpReply(raw.status, _as_object(_loads(raw.body)))


def _error_text(reply: HttpReply) -> str:
    error = reply.body.get("error")
    return error if isinstance(error, str) else f"HTTP {reply.status}"


# ------------------------------------------------------------------ watch(SSE)の読み手


@dataclass(frozen=True)
class _Frame:
    kind: WatchKind
    sequence: int


class WakeQueue:
    """拍を起こす合図の 1 本の列(段 12 lane 12b・agora-redesign #207 根 1)。

    ACP の watch(SSE)の frame と、器(host)の出来事の journal の進み(SessionEventWaker)が同じ列に載り、
    ``WatchReader.take`` がそれを 1 回の待ちの答え(WatchAdvance)にする — 拍の待ちの定義点は AcpWatchSse
    の 1 つのまま、起こす源だけが 2 つになる。
    """

    def __init__(self) -> None:
        self.frames: queue.Queue[_Frame] = queue.Queue()

    def wake(self, kind: WatchKind, sequence: int) -> None:
        self.frames.put(_Frame(kind, sequence))


class WatchReader:
    """``GET /api/watch/stream`` を張り続ける thread。切れたら since から張り直す。

    frame は queue に積み、``take(wait)`` が 1 回の待ちの答え(WatchAdvance)を返す。queue は
    composition root が WakeQueue で渡す(器の出来事の合図と共有)か、無ければ自前。
    """

    def __init__(
        self,
        base_url: str,
        headers: Mapping[str, str],
        since: int,
        frames: "queue.Queue[_Frame] | None" = None,
    ) -> None:
        self._base_url = base_url
        self._headers = dict(headers)
        self._mut_since = since
        self._frames: queue.Queue[_Frame] = frames if frames is not None else queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="agentd-watch", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def take(self, since: int, wait_seconds: float) -> WatchAdvance:
        self._mut_since = max(self._mut_since, since)
        latest: int | None = None
        gap: int | None = None
        closed = False
        session = False
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            remaining = deadline - time.monotonic()
            try:
                frame = self._frames.get(timeout=max(0.0, remaining))
            except queue.Empty:
                break
            if frame.kind == "changed":
                latest = frame.sequence if latest is None else max(latest, frame.sequence)
            elif frame.kind == "gap":
                gap = frame.sequence
            elif frame.kind == "closed":
                closed = True
            elif frame.kind == "session":
                session = True
            # 溜まっている frame は空になるまで読む(待たずに)
            deadline = min(deadline, time.monotonic())
        # 答えは 1 語: ACP の側(gap > changed > closed)が器の合図(session)に勝つ — どの語でも拍は走っている job を
        # 観測するので、器の合図は「観測を今する」以上を求めない。session は ACP の sequence を進めない。
        if gap is not None:
            return WatchAdvance(kind="gap", sequence=max(since, gap))
        if latest is not None:
            return WatchAdvance(kind="changed", sequence=max(since, latest))
        if closed:
            return WatchAdvance(kind="closed", sequence=since)
        if session:
            return WatchAdvance(kind="session", sequence=since)
        return WatchAdvance(kind="idle", sequence=since)

    def _loop(self) -> None:
        # watch は長く開いたままの 1 本(拍ごとの往復ではない)なので、保つ口(HttpConnections)には
        # 載せない — この読み手が自分の接続を持ち、閉じたら張り直す。
        while not self._stop.is_set():
            host, target = _split_url(
                f"{self._base_url}/api/watch/stream?since={self._mut_since}"
            )
            connection = _new_connection(host, WATCH_READ_TIMEOUT_SECONDS)
            try:
                connection.request(
                    "GET", target, headers={**self._headers, "Accept": "text/event-stream"}
                )
                self._read_stream(connection.getresponse())
            except (OSError, TimeoutError, HTTPException):
                pass
            finally:
                connection.close()
            if self._stop.is_set():
                return
            self._frames.put(_Frame("closed", self._mut_since))
            self._stop.wait(WATCH_RECONNECT_SECONDS)

    def _read_stream(self, response: HTTPResponse) -> None:
        event = "message"
        data_lines: list[str] = []
        while not self._stop.is_set():
            raw = response.readline()
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == "":
                self._dispatch(event, "\n".join(data_lines))
                event = "message"
                data_lines = []
            elif line.startswith(":"):
                continue
            elif line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())

    def _dispatch(self, event: str, data: str) -> None:
        if not data:
            return
        payload = _as_object(_loads(data.encode("utf-8")))
        if event == "gap":
            resume_at = _int_field(payload, "resumeAt")
            self._frames.put(_Frame("gap", resume_at if resume_at is not None else self._mut_since))
            return
        latest = _int_field(payload, "latestSequence")
        if latest is None:
            return
        changed = payload.get("changed")
        if event == "watch" and changed is False:
            self._mut_since = max(self._mut_since, latest)
            return
        self._mut_since = max(self._mut_since, latest)
        self._frames.put(_Frame("changed", latest))


# ------------------------------------------------------------------ 器の出来事の合図(host の journal の long-poll)

#: 器の出来事の journal を 1 回待つ口: (after, wait_seconds) → 先端の seq(RPC session.wait_events の 1 往復)。
SessionJournalPoll = Callable[[int, float], int]


def session_journal_poll(socket_path: str) -> SessionJournalPoll:
    """sessionhost の socket(公開の境界)で ``session.wait_events`` を 1 往復する口。"""
    client = AgentdClient(socket_path)

    def poll(after: int, wait_seconds: float) -> int:
        answer: JSON = client.request(
            "session.wait_events",
            {"after": after, "wait_seconds": wait_seconds},
            read_timeout=wait_seconds + SESSION_WAKE_READ_MARGIN_SECONDS,
        )
        seq = _int_field(_as_object(answer), "seq")
        if seq is None:
            raise AgentdClientError(f"session.wait_events answered without an integer seq: {answer!r}")
        return seq

    return poll


class SessionEventWaker:
    """器(host)の出来事の journal(agent_session_events)の進みを long-poll で待ち、進むたびに拍を起こす合図
    (kind session)を WakeQueue に積む thread(段 12 lane 12b・agora-redesign #207 根 1)。

    手番の終わりを host の monitor が刻んだ(session_turn_ended)拍に agentd が即座に観測し、同じ拍で
    turn-record を ended・job を Ended にする — 拍の周期(transcript_poll_seconds)は保険に退く。届かない・
    断られた拍は log して有界の backoff で張り直す(合図が無い間も拍の周期が観測を運ぶ)。
    """

    def __init__(
        self,
        poll: SessionJournalPoll,
        wakes: WakeQueue,
        log: Callable[[str], None],
        wait_seconds: float = SESSION_WAKE_WAIT_SECONDS,
        retry_seconds: float = SESSION_WAKE_RETRY_SECONDS,
    ) -> None:
        self._poll = poll
        self._wakes = wakes
        self._log = log
        self._wait_seconds = wait_seconds
        self._retry_seconds = retry_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="agentd-session-wake", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        after: int | None = None
        backoff = self._retry_seconds
        while not self._stop.is_set():
            try:
                if after is None:
                    # 最初は今の先端を知るだけ(過去の出来事で起こさない)。
                    after = self._poll(0, 0.0)
                    continue
                seq = self._poll(after, self._wait_seconds)
            except Exception as error:  # loop の縁: 落とさず log して有界の backoff で張り直す
                self._log(f"agentd: session wake poll failed: {type(error).__name__}: {error}")
                self._stop.wait(backoff)
                backoff = min(SESSION_WAKE_RETRY_MAX_SECONDS, backoff * 2)
                continue
            backoff = self._retry_seconds
            if seq > after:
                after = seq
                self._wakes.wake("session", seq)


# ------------------------------------------------------------------ ACP の handler


class AcpHttp:
    """ACP の資源の読み書き・watch・中継の push。bearer は名簿の agentd の札。``wakes`` が在れば watch の frame を
    その列に載せる(器の出来事の合図と共有 — 段 12 lane 12b)。"""

    def __init__(
        self, base_url: str, token: str | None, wakes: WakeQueue | None = None, node: str = ""
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = {}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._mut_watch: WatchReader | None = None
        self._wakes = wakes
        #: この機体の名 — 実況の push だけがこれを header で名乗る(綴りは effects.STREAM_SOURCE_HEADER の
        #: 1 点)。空 = 名乗らない(名を知らない口で撃つ検体・借りの口)。読み書きの札とは別の欄で、
        #: 身元の証明ではない(engine 側は label の値として丸めるだけ — cardinality の門は engine が持つ)。
        self._node = node.strip()
        #: 拍ごとの読み書きは host ごとに保った 1 本で撃つ(R22 の追補)。
        self._http = HttpConnections()

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, AcpCacheOperations):
            return Resume(k, self._list("cache-operation", f"spec.nodeRow={effect.node_row}"))
        if isinstance(effect, AcpRunningTurnRecords):
            # 段 12(agora-redesign #537 便 1): 走っている記録だけ(engine の status 軸の field selector —
            # 綴りは effects の 1 点)。kind の全量は読まない。
            return Resume(k, self._list(TURN_RECORD_KIND, TURN_RECORD_RUNNING_SELECTOR))
        if isinstance(
            effect,
            (AcpGet, AcpGetRow, AcpEventWindow, AcpWatchSse, AcpConversationMail, AcpTurnHeadlines,
             AcpConversationSummaries, AcpConversationMemories),
        ):
            return Resume(k, self._read(effect))
        if isinstance(effect, (AcpPutStatus, AcpPutSpec, AcpCreate, AcpStreamPush)):
            return Resume(k, self._write(effect))
        return Pass(effect, k)

    def _read(
        self,
        effect: AcpGet
        | AcpGetRow
        | AcpEventWindow
        | AcpWatchSse
        | AcpConversationMail
        | AcpTurnHeadlines
        | AcpConversationSummaries
        | AcpConversationMemories,
    ) -> object:
        if isinstance(effect, AcpGet):
            return self._list(effect.kind)
        if isinstance(effect, AcpConversationMemories | AcpConversationSummaries | AcpConversationMail | AcpTurnHeadlines):
            return self._read_conversation(effect)
        if isinstance(effect, AcpGetRow):
            return self._row(effect.key)
        if isinstance(effect, AcpEventWindow):
            return self._event_window(effect.after, effect.limit)
        return self._watch_take(effect.since, effect.wait_seconds)

    def _read_conversation(self, effect: AcpConversationMemories | AcpConversationSummaries | AcpConversationMail | AcpTurnHeadlines) -> object:
        if isinstance(effect, AcpConversationMemories):
            # card acp:kanban-issue:ki-9fc7d4bca4dc: この会話の kind agent-memory の行だけ(宣言の indexes が引く)。
            return self._list(MEMORY_KIND, f"spec.{MEMORY_SPEC_CONVERSATION_KEY}={effect.conversation_id}")
        if isinstance(effect, AcpConversationSummaries):
            # 段 12 lane 12j(agora-redesign #233): この会話の kind summary の行だけ(field selector・宣言の indexes が引く)。
            return self._list(SUMMARY_KIND, f"spec.{SUMMARY_SPEC_CONVERSATION_KEY}={effect.conversation_id}")
        if isinstance(effect, AcpConversationMail):
            # 段 10 lane 10ba(#115): この会話を名指す郵便だけ(欄ごとの field selector・履歴に入れる判断は judgment)。
            return self._conversation_mail(effect.conversation_id)
        match effect:
            case AcpTurnHeadlines():
                # 薄い再開の拍だけ(段 9q・#77)— 段 10 lane 10ba: この会話の行だけ(旧来の kind の全量は 172 MB / 59 秒)。
                return self._list(
                    TURN_RECORD_KIND, f"spec.{TURN_RECORD_CONVERSATION_FIELD}={effect.conversation_id}"
                )
        assert_never(effect)

    def _write(self, effect: AcpPutStatus | AcpPutSpec | AcpCreate | AcpStreamPush) -> object:
        if isinstance(effect, AcpPutStatus):
            return self._put_status(effect.row, effect.status)
        if isinstance(effect, AcpPutSpec):
            return self._put_spec(effect.row, effect.spec, effect.declaration_sha256)
        if isinstance(effect, AcpCreate):
            return self._create(
                effect.namespace, effect.kind, effect.resource_id, effect.spec, effect.declaration_sha256
            )
        return self._push(effect.owner, effect.name, effect.frames)

    def close(self) -> None:
        if self._mut_watch is not None:
            self._mut_watch.stop()
        self._http.close()

    def _conversation_mail(self, conversation_id: str) -> AcpRows:
        """会話の郵便(段 10 lane 10ba): spec の MESSAGE_CONVERSATION_FIELDS の欄ごとに ACP の field selector で 1 回ずつ
        読み(ACP の field selector は 1 回の読みに条件 1 つ)、同じ行(自分宛の自分の郵便)は鍵で 1 つにする。順は読んだ順。"""
        merged: dict[str, AcpRow] = {}
        for field in MESSAGE_CONVERSATION_FIELDS:
            for row in self._list(MESSAGE_KIND, f"spec.{field}={conversation_id}"):
                merged.setdefault(row.key, row)
        return tuple(merged.values())

    def _list(self, kind: str, field_selector: str | None = None) -> AcpRows:
        """kind の行の一覧。field_selector(``spec.<欄>=<値>`` — 段 10 lane 10ba)が在れば engine がその行だけを返す。"""
        query = f"kind={urllib.parse.quote(kind, safe='')}"
        if field_selector is not None:
            query = f"{query}&fieldSelector={urllib.parse.quote(field_selector, safe='')}"
        url = f"{self._base_url}/api/resources?{query}"
        raw = self._http.request("GET", url, self._headers, None, HTTP_TIMEOUT_SECONDS)
        if raw.status != 200:
            narrowed = "" if field_selector is None else f" ({field_selector})"
            detail = raw.error if raw.status == 0 else f"HTTP {raw.status}"
            raise RuntimeError(f"agentd: ACP list of {kind}{narrowed} failed: {detail}")
        listed = _loads(raw.body)
        if not isinstance(listed, list):
            return ()
        decoded = [decode_row(item) for item in listed]
        return tuple(row for row in decoded if row is not None)

    def _row(self, key: str) -> AcpRow | None:
        reply = _http_json(
            self._http,
            "GET",
            f"{self._base_url}/api/resources/{urllib.parse.quote(key, safe='')}",
            self._headers,
            None,
            HTTP_TIMEOUT_SECONDS,
        )
        if reply.status == 404:
            return None
        if reply.status != 200:
            raise RuntimeError(
                f"agentd: ACP read of {key} failed ({reply.status}): {_error_text(reply)}"
            )
        return decode_row(reply.body)

    def _event_window(self, after: int, limit: int) -> EventWindow:
        """変わった行だけ(cursor-only の窓)。409(cursor が retention の床の下)と到達不能は
        complete = False(呼び手は全量 list に落ちる)、それ以外の断りは RuntimeError(tick の縁)。"""
        reply = _http_json(
            self._http,
            "GET",
            f"{self._base_url}/api/event-window?after={after}&limit={limit}",
            self._headers,
            None,
            HTTP_TIMEOUT_SECONDS,
        )
        if reply.status in (0, 409):
            return EventWindow(complete=False, through=after, latest=after, rows=(), retired=())
        if reply.status != 200:
            raise RuntimeError(
                f"agentd: ACP event-window after {after} failed ({reply.status}): {_error_text(reply)}"
            )
        return decode_event_window(after, reply.body)

    def _post_event(self, body: JSONObject, declaration_sha256: str | None = None) -> WriteOutcome:
        # 段 10 lane 10y: 宣言 file の指紋は書きごとの header(kind node の declaredByFile の欄を変える書きだけが運ぶ)
        headers = (
            self._headers
            if declaration_sha256 is None
            else {**self._headers, DECLARATION_FINGERPRINT_HEADER: declaration_sha256}
        )
        reply = _http_json(
            self._http, "POST", f"{self._base_url}/api/events", headers, body, HTTP_TIMEOUT_SECONDS
        )
        if reply.status == 200:
            event_id = _str_field(reply.body, "eventId")
            return Written(event_id or "")
        if reply.status == 409:
            return Conflict(_int_field(reply.body, "currentGeneration"))
        return Refused(reply.status, _error_text(reply))

    def _post_image(
        self, row: AcpRow, spec: JSONObject | None, status: JSONObject | None
    ) -> JSONObject:
        return {
            "resourceNamespace": row.namespace,
            "resourceKey": row.key,
            "resourceKind": row.kind,
            "resourceId": row.resource_id,
            "resourceVersion": row.version,
            "resourceLabels": dict(row.labels),
            "resourcePayload": dict(row.payload),
            "observedAt": _iso_now(),
            "resourceSpecJson": spec,
            "resourceStatusJson": status,
        }

    def _put_status(self, row: AcpRow, status: JSONObject) -> WriteOutcome:
        return self._post_event(
            {
                "eventType": "control_plane.status_synced",
                "ifGeneration": row.generation,
                "payload": {
                    "cpNamespace": row.namespace,
                    "cpKind": row.kind,
                    "cpResourceKey": row.key,
                    "cpStatus": self._post_image(row, None, status),
                    "cpSource": ACP_WRITE_SOURCE,
                    "cpAt": _iso_now(),
                },
            }
        )

    def _put_spec(self, row: AcpRow, spec: JSONObject, declaration_sha256: str | None = None) -> WriteOutcome:
        """段 10 lane 10d: 行の spec の書き(spec_applied・ifGeneration = 行の generation・status は運ばない —
        engine の SpecApplied は status の軸を触らない)。段 10 lane 10y: 指紋が在れば header で運ぶ。"""
        return self._post_event(
            {
                "eventType": "control_plane.spec_applied",
                "ifGeneration": row.generation,
                "payload": {
                    "cpNamespace": row.namespace,
                    "cpKind": row.kind,
                    "cpResourceKey": row.key,
                    "cpSpec": self._post_image(row, spec, None),
                    "cpSource": ACP_WRITE_SOURCE,
                    "cpAt": _iso_now(),
                },
            },
            declaration_sha256,
        )

    def _create(
        self,
        namespace: str,
        kind: str,
        resource_id: str,
        spec: JSONObject,
        declaration_sha256: str | None = None,
    ) -> WriteOutcome:
        key = f"{namespace}:{kind}:{resource_id}"
        row = AcpRow(
            namespace=namespace,
            key=key,
            kind=kind,
            resource_id=resource_id,
            version="v1",
            generation=0,
            created_at_ms=0,
            labels={},
            payload={},
            spec=spec,
            status=None,
        )
        return self._post_event(
            {
                "eventType": "control_plane.spec_applied",
                "ifGeneration": 0,
                "payload": {
                    "cpNamespace": namespace,
                    "cpKind": kind,
                    "cpResourceKey": key,
                    "cpSpec": self._post_image(row, spec, None),
                    "cpSource": ACP_WRITE_SOURCE,
                    "cpAt": _iso_now(),
                },
            },
            declaration_sha256,
        )

    def _watch_take(self, since: int, wait_seconds: float) -> WatchAdvance:
        if self._mut_watch is None:
            frames = self._wakes.frames if self._wakes is not None else None
            self._mut_watch = WatchReader(self._base_url, self._headers, since, frames)
            self._mut_watch.start()
        return self._mut_watch.take(since, wait_seconds)

    def _push(self, owner: str, name: str, frames: tuple[JSONObject, ...]) -> PushOutcome:
        url = (
            f"{self._base_url}/api/streams/"
            f"{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"
        )
        # card acp:kanban-issue:ki-6eb745f6d528: 押す拍だけが「どの機体が押したか」を名乗る(読み書きには
        # 付けない — 要る問いは「実況の粒がどの機体で粗いか」1 つ)。
        headers = dict(self._headers)
        if self._node:
            headers[STREAM_SOURCE_HEADER] = self._node
        reply = _http_json(
            self._http, "POST", url, headers, {"frames": list(frames)}, HTTP_TIMEOUT_SECONDS
        )
        if reply.status != 200:
            return Refused(reply.status, _error_text(reply))
        seq = _int_field(reply.body, "seq")
        return Pushed(seq if seq is not None else 0, _int_field(reply.body, "subscribers"))


# ------------------------------------------------------------------ custody の handler


class CustodyHttp:
    """預かり所の貸出と返却。身元は借り手札(``X-Borrower-Key`` — Mac の agentd)と、宣言が在れば pod の
    ServiceAccount の token(``Authorization: Bearer`` — 預かり所の k3s の backend が TokenReview で解く・段 10 lane 10y)。"""

    def __init__(
        self, base_url: str, borrower_key: str | None, service_account_token_file: str | None = None
    ) -> None:
        #: 宣言の無い機体は空(借りの要求はここで断る — 既定の宿を発明しない・段 10 lane 10d 便 2)。
        self._base_url = base_url.rstrip("/")
        self._borrower_key = borrower_key
        #: token の file は要求ごとに読む(kubelet が projected の token を回すので、起動時の値を持ち続けない)。
        self._service_account_token_file = service_account_token_file
        #: 預かり所も host ごとに 1 本の接続を保つ(R22 の追補 — 札の借り直しは拍の中の往復)。
        self._http = HttpConnections()

    def _identity_headers(self) -> dict[str, str] | LeaseRefused:
        """要求に載せる身元の header。SA token の file を宣言したのに読めない拍は断り(名乗らずに撃って
        預かり所の 401 で知るのではなく、宣言の置き場を名指して止める)。"""
        headers: dict[str, str] = {}
        if self._borrower_key:
            headers["X-Borrower-Key"] = self._borrower_key
        if self._service_account_token_file is not None:
            token = read_secret_file(self._service_account_token_file)
            if token is None:
                return LeaseRefused(
                    503,
                    f"service account token file {self._service_account_token_file} is absent or empty "
                    "(join の [custody].service_account_token_file → AGORA_CUSTODY_SA_TOKEN_PATH)",
                    None,
                )
            headers["Authorization"] = f"Bearer {token}"
        return headers

    #: 宣言が無い時の断り(呼び手には LeaseRefused として返る — 到達不能と同じ扱いで job は終端へ)。
    _UNDECLARED = LeaseRefused(
        503,
        "custody URL is not declared (join の [custody].url / --custody → AGORA_CUSTODY_URL) — 既定の宿は無い",
        None,
    )

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, CustodyHealth):
            # 版の読み — 札は要らない(契約 custody-api.json の health は auth = none)。
            # 届かない / 200 でない拍は None(判断は持たない — 判じるのは judgment の 1 点)。
            if not self._base_url:
                return Resume(k, None)
            reply = _http_json(self._http, "GET", f"{self._base_url}/health", {}, None, HTTP_TIMEOUT_SECONDS)
            return Resume(k, reply.body if reply.status == 200 else None)
        if isinstance(effect, CustodyLeaseBorrow):
            return Resume(k, self._borrow(effect))
        if isinstance(effect, CustodyLeaseRevoke):
            return Resume(k, self._revoke(effect.lease_id))
        return Pass(effect, k)

    def _revoke(self, lease_id: str) -> bool:
        """借りた札を返す。宣言が無い・身元が組めない拍は返せない(False)。"""
        if not self._base_url:
            return False
        headers = self._identity_headers()
        if isinstance(headers, LeaseRefused):
            return False
        reply = _http_json(
            self._http,
            "POST",
            f"{self._base_url}/lease/{lease_id}/revoke",
            headers,
            {},
            HTTP_TIMEOUT_SECONDS,
        )
        return reply.status == 200

    def _borrow(self, effect: CustodyLeaseBorrow) -> LeaseOutcome:
        """借りは 2 段(預かり所の契約 v2・段 10 lane 10d): master へ貸与を頼んで**引換券**と口座の worker の基点を受け、
        その worker で引換券を札に換える。札は master を通らない(引換券は一回限り・期限は貸与の hold ちょうど)。
        どちらの段の断りもそのまま LeaseRefused(409 の hold も master の答えから運ぶ)。

        card acp:kanban-issue:ki-fd0f3b234a38(設計 v2 §10.1): 断りは**どの処理ステージが断ったか**(lease / redeem)と
        本文の機械の語(code・why)を運ぶ — 分類(judgment.custody-refusal-verdict-of)は散文ではなくこの語で決める
        (以前は error の文だけを写し、処理ステージと code を捨てていた)。引換に失敗した拍は、その直前に master が
        出した貸与をここで返す(接続が答えない間の 15 秒ごとのやり直しで貸与の行を積まない)。返せなくても断りの
        語は変えない(返せなかった貸与の id を断りに載せ、呼び手が log に名乗る)。
        """
        if not self._base_url:
            return self._UNDECLARED
        headers = self._identity_headers()
        if isinstance(headers, LeaseRefused):
            return headers
        reply = _http_json(
            self._http,
            "POST",
            f"{self._base_url}/lease/{effect.kind}",
            headers,
            {"account": effect.account, "purpose": effect.purpose},
            HTTP_TIMEOUT_SECONDS,
        )
        hold = _str_field(reply.body, "holdExpiresAt")
        if reply.status != 200:
            return LeaseRefused(
                reply.status,
                _error_text(reply),
                _epoch_ms_of_iso(hold) if hold else None,
                CUSTODY_STAGE_LEASE,
                _str_field(reply.body, "code"),
                _str_field(reply.body, "why"),
            )
        lease_id = _str_field(reply.body, "leaseId")
        voucher = _str_field(reply.body, "voucher")
        worker_url = _str_field(reply.body, "workerUrl")
        if lease_id is None or hold is None or voucher is None or worker_url is None:
            return LeaseRefused(
                reply.status,
                "malformed grant (no leaseId / holdExpiresAt / voucher / workerUrl)",
                None,
            )
        redeemed = _http_json(
            self._http,
            "POST",
            f"{worker_url.rstrip('/')}/redeem",
            headers,
            {"voucher": voucher},
            HTTP_TIMEOUT_SECONDS,
        )
        if redeemed.status != 200:
            # 引換券を札に換えられなかった拍(worker 不達・期限切れ・別の借り手)— 貸与の hold は master の答えから運ぶ。
            # card ki-fd0f3b234a38: master が出した貸与はここで返す(返せたかは分類を変えない — 返せなかった id だけ運ぶ)。
            returned = self._revoke(lease_id)
            return LeaseRefused(
                redeemed.status,
                _error_text(redeemed),
                _epoch_ms_of_iso(hold),
                CUSTODY_STAGE_REDEEM,
                _str_field(redeemed.body, "code"),
                _str_field(redeemed.body, "why"),
                None if returned else lease_id,
            )
        auth_json_value = redeemed.body.get("authJson")
        return LeaseGrant(
            lease_id=lease_id,
            kind=effect.kind,
            renewed=reply.body.get("renewed") is True,
            hold_expires_at_ms=_epoch_ms_of_iso(hold),
            access_token=_str_field(redeemed.body, "accessToken"),
            auth_json=None
            if auth_json_value is None
            else json.dumps(auth_json_value, ensure_ascii=False),
        )


# ------------------------------------------------------------------ 器(sessionhost の RPC)の handler


def session_view_of(result: JSON) -> SessionView | None:
    """sessionhost の wire snapshot → SessionView(欠けた欄は None — 発明しない)。"""
    snapshot = _as_object(result)
    session_id = _str_field(snapshot, "session_id")
    agent_type = _str_field(snapshot, "agent_type")
    status = _str_field(snapshot, "status")
    if session_id is None or agent_type is None or status is None:
        return None
    conversation = snapshot.get("conversation")
    identity = snapshot.get("effective_identity")
    cause = snapshot.get("terminal_cause")
    turn_ended = _str_field(snapshot, "turn_ended_at")
    started = _str_field(snapshot, "started_at")
    backend_ref = snapshot.get("backend_ref")
    attribution = snapshot.get("launch_attribution")
    alive = snapshot.get("backend_alive")
    return SessionView(
        session_id=session_id,
        agent_type=agent_type,
        status=status,
        work_dir=_str_field(snapshot, "work_dir") or "",
        lifecycle=_str_field(snapshot, "lifecycle") or "",
        conversation=_str_map(conversation),
        effective_identity=_str_map(identity),
        result_payload=snapshot.get("result_payload"),
        terminal_cause=cause if isinstance(cause, dict) else None,
        turn_ended_at_ms=None if turn_ended is None else _epoch_ms_of_iso(turn_ended),
        backend_kind=_str_field(snapshot, "backend_kind") or "tmux",
        backend_ref=backend_ref if isinstance(backend_ref, dict) else None,
        launch_attribution=attribution if isinstance(attribution, dict) else None,
        started_at_ms=None if started is None else _epoch_ms_of_iso(started),
        backend_alive=alive if isinstance(alive, bool) else None,
        turn_error=_str_field(snapshot, "turn_error"),
        cache_last_success_at_ms=_int_field(snapshot, "cache_last_success_at_ms"),
    )


def _str_map(value: JSON) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    return {key: item for key, item in value.items() if isinstance(item, str)}


def _driver_resolution_of(item: JSON) -> DriverResolution | None:
    """``drivers.list`` の答えの 1 項 → 在否(形が違えば None — 項を推し量らない)。"""
    entry = _as_object(item)
    agent_type = _str_field(entry, "agent_type")
    executable = _str_field(entry, "executable")
    path = entry.get("path")
    if agent_type is None or executable is None or not (path is None or isinstance(path, str)):
        return None
    return DriverResolution(agent_type=agent_type, executable=executable, path=path)


def decode_driver_listing(result: JSON) -> HostDriversOutcome:
    """host の ``drivers.list`` の答え ``{"drivers": [{"agent_type", "executable", "path"}…]}`` → 在否の列
    (ADR-DOE-AGENTS-012 R61)。形の違う答えは読めなかった(host には届いた)— 項を推し量らない。"""
    drivers = _as_object(result).get("drivers")
    if not isinstance(drivers, list):
        return DriversUnavailable(reason="drivers.list answered without a drivers list", reachable=True)
    decoded = [(item, _driver_resolution_of(item)) for item in drivers]
    malformed = [item for item, resolution in decoded if resolution is None]
    if malformed:
        return DriversUnavailable(reason=f"drivers.list answered a malformed item: {malformed[0]!r}", reachable=True)
    return tuple(resolution for _, resolution in decoded if resolution is not None)


class SessionRpc:
    """sessionhost の socket を話す(公開の境界 — host の内側には触らない)。"""

    def __init__(self, socket_path: str) -> None:
        self._client = AgentdClient(socket_path)

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if not handles_session_effect(effect):
            return Pass(effect, k)
        return Resume(k, self.answer(effect))

    def answer(self, effect: EffectBase) -> object:
        """この器 1 つへ要求を撃った答え(dispatch の中身 — 経路 SessionRoutes も同じ口を通る)。"""
        if isinstance(effect, (SessionCachePing, SessionCacheProbe)):
            return self._cache_operation(effect)
        if isinstance(
            effect,
            (
                SessionLaunch,
                SessionResume,
                SessionSend,
                SessionInterject,
                SessionEscalate,
                SessionInterrupt,
                SessionCleanup,
            ),
        ):
            return self._act(effect)
        if isinstance(effect, (SessionGet, SessionList, SessionCapture)):
            return self._look(effect)
        if isinstance(effect, ListHostDrivers):
            return self._drivers(effect)
        raise TypeError(f"SessionRpc does not answer {type(effect).__name__}")

    def _drivers(self, effect: ListHostDrivers) -> HostDriversOutcome:
        """``drivers.list``(ADR-DOE-AGENTS-012 R61)→ 種類ごとの在否の列。host の断り(RPC の error 封筒 —
        読み口を持たない旧い host の unknown method を含む)は届いた読めなさ、socket の失敗(OSError — host が
        降りている)は届かない読めなさとして**値**で返す(例外にしない: 参加の腕はどちらでも申告を空にする)。"""
        try:
            answer: JSON = self._client.request("drivers.list", {"env": dict(effect.env)})
        except AgentdClientError as error:
            return DriversUnavailable(reason=f"the host refused drivers.list: {error}", reachable=True)
        except OSError as error:
            return DriversUnavailable(
                reason=f"the host is unreachable: {type(error).__name__}: {error}", reachable=False
            )
        return decode_driver_listing(answer)

    def _cache_operation(self, effect: SessionCachePing | SessionCacheProbe) -> HostCacheRecord | None:
        operation = effect.operation
        params: dict[str, object] = {
            "session_id": operation.target.session, "operation_id": operation.key,
        }
        method = "session.cache-ping-status"
        if isinstance(effect, SessionCachePing):
            method = "session.cache-ping"
            params.update(expires_at=operation.expires_at, session_env=effect.session_env)
        try:
            result = self._client.request(method, params)
        except OSError as error:
            raise MaintenanceTransportUncertainError("sessionhostの専用操作応答を確認できません") from error
        return None if result is None else decode_cache_receipt(result)

    def _act(
        self,
        effect: SessionLaunch
        | SessionResume
        | SessionSend
        | SessionInterject
        | SessionEscalate
        | SessionInterrupt
        | SessionCleanup,
    ) -> object:
        """器を動かす要求(起こす・送る・割り込む・停止の合図・止める・片付ける)。"""
        if isinstance(effect, SessionLaunch):
            return self._incarnate("session.launch", effect.params)
        if isinstance(effect, SessionResume):
            return self._incarnate("session.resume", effect.params)
        if isinstance(effect, SessionInterrupt):
            self._client.request("session.interrupt", {"session_id": effect.session_id})
            return None
        if isinstance(effect, (SessionInterject, SessionEscalate)):
            return self._interrupt_arm(effect)
        if isinstance(effect, SessionSend):
            return self._send(effect)
        return self._cleanup(effect.session_id)

    def _send(self, effect: SessionSend) -> str | None | SessionRefused:
        """``session.send`` の mode = turn(手番の本文を器へ)→ 器が落とした添付の理由 | None。
        host の断り(RPC の error 封筒 — 走っている手番が無い・行が無い・同じ名の process が既に在る)は
        _interject と同じ形で SessionRefused に写す(本文は届いていない)。裸で投げると
        receive-bound-jobs の外まで抜け、計器 agent-job-to-send も turn-record も走らない
        (失敗が最も見えない形 — card acp:kanban-issue:ki-3149aebbf675 C)。
        socket の失敗(OSError)は素通し(tick の縁が持ち越す)。"""
        params: JSONObject = {
            "session_id": effect.session_id,
            "message": effect.text,
            "literal": True,
            "enter": True,
            "awaiting": effect.awaiting,
        }
        # 追補 2(実弾 #92): この手番の env は空でない時だけ載せる(器が起こし直す時に重ねる)。
        # 値は秘密 — ここでも log に出さない。
        if effect.session_env:
            params["session_env"] = dict(effect.session_env)
        # card acp:kanban-issue:ki-a40292ed30d9(4 つ目の腕): この手番の荷(記憶の置き場と冊)は
        # 空でない時だけ載せる — 記憶を使わない会話の送りは 1 byte も変わらない。
        if effect.turn_charter:
            params["turn_charter"] = dict(effect.turn_charter)
        # 段 10 lane 10o(agora-redesign #96): 添付は型つきのまま wire の項にする(綴りは器の Dialogue)。
        if effect.attachments:
            params["attachments"] = attachment_params(effect.attachments)
        try:
            answer = self._client.request("session.send", params)
        except AgentdClientError as error:
            code = error.error_code
            return SessionRefused(str(error), str(code) if code is not None else None)
        return attachments_ignored_of(answer)

    def _look(self, effect: SessionGet | SessionList | SessionCapture) -> object:
        """器を眺める要求(1 つ・一覧・pane の断面)。"""
        if isinstance(effect, SessionGet):
            result: JSON = self._client.request("session.get", {"session_id": effect.session_id})
            return None if result is None else session_view_of(result)
        if isinstance(effect, SessionList):
            params: JSONObject = {"lifecycle": effect.lifecycle}
            if effect.statuses is not None:
                params["status"] = list(effect.statuses)
            if effect.limit is not None:
                params["limit"] = effect.limit
                params["offset"] = effect.offset
            listed: JSON = self._client.request("session.list", params)
            items: list[JSON] = listed if isinstance(listed, list) else []
            views = [session_view_of(item) for item in items]
            return tuple(view for view in views if view is not None)
        return self._capture(effect.session_id, effect.lines)

    def _interrupt_arm(
        self, effect: SessionInterject | SessionEscalate
    ) -> Interjected | Escalated | SessionRefused:
        """割り込みの 2 腕(注入 / 停止の合図 — 段 8 lane 4x・段 10 lane 10n)。"""
        if isinstance(effect, SessionInterject):
            return self._interject(
                effect.session_id, effect.text, effect.ref, effect.attachments
            )
        return self._escalate(effect.session_id)

    def _interject(
        self,
        session_id: str,
        text: str,
        ref: str,
        attachments: tuple[TurnAttachment, ...] = (),
    ) -> Interjected | SessionRefused:
        """``session.send`` の mode = interrupt(段 8 lane 4x)→ 引き受けたか。host の断り(RPC の
        error 封筒 — 走っている手番が無い・行が無い)は SessionRefused(本文は届いていない)。
        socket の失敗(OSError)は素通し(tick の縁が持ち越す)。ref = 注入の行の名(段 10 lane 10n)。"""
        try:
            self._client.request(
                "session.send",
                {
                    "session_id": session_id,
                    "message": text,
                    "literal": True,
                    "enter": True,
                    "awaiting": False,
                    "mode": "interrupt",
                    "ref": ref,
                    # 段 10 lane 10o: 割り込みの郵便の添付(型つき — 綴りは器の Dialogue)。
                    "attachments": attachment_params(attachments),
                },
            )
        except AgentdClientError as error:
            code = error.error_code
            return SessionRefused(str(error), str(code) if code is not None else None)
        return Interjected()

    def _escalate(self, session_id: str) -> Escalated | SessionRefused:
        """``session.escalate``(段 10 lane 10n)→ 合図を出したか。host の断り(出す物が無い・行が無い・
        注入の段の無い器)は SessionRefused。socket の失敗(OSError)は素通し。"""
        try:
            self._client.request("session.escalate", {"session_id": session_id})
        except AgentdClientError as error:
            code = error.error_code
            return SessionRefused(str(error), str(code) if code is not None else None)
        return Escalated()

    def _cleanup(self, session_id: str) -> bool:
        """``session.cleanup`` → 受けたか。host の断り(RPC の error 封筒 — 行が無い等)は
        False(片付ける物が無い)。socket の失敗(OSError)は素通し(tick の縁が持ち越す)。"""
        try:
            self._client.request("session.cleanup", {"session_id": session_id})
        except AgentdClientError:
            return False
        return True

    def _capture(self, session_id: str, lines: int) -> CaptureOutcome:
        """``session.capture`` → 断面 ``{"text"}``。host が断った(pane も server も無い・行が
        無い — RPC の error 封筒)なら CaptureGone = 実況の終わりの合図(ADR-DOE-AGENTS-012 R8)。
        socket の失敗(OSError)は host の答えではないので素通し(tick の縁が持ち越す)。"""
        try:
            captured: JSON = self._client.request(
                "session.capture", {"session_id": session_id, "lines": lines}
            )
        except AgentdClientError as error:
            return CaptureGone(str(error))
        text = _str_field(_as_object(captured), "text")
        if text is None:
            raise RuntimeError(f"agentd: session.capture of {session_id} returned no text")
        return CaptureFrame(text)

    def _incarnate(self, method: str, params: JSONObject) -> SessionOutcome:
        try:
            result: JSON = self._client.request(
                method, params, read_timeout=launch_rpc_timeout_seconds()
            )
        except AgentdClientError as error:
            code = error.error_code
            return SessionRefused(str(error), str(code) if code is not None else None)
        view = session_view_of(result)
        if view is None:
            return SessionRefused(f"{method} returned a malformed snapshot", None)
        return view


_SESSION_EFFECTS = (
    SessionCachePing,
    SessionCacheProbe,
    SessionLaunch,
    SessionResume,
    SessionSend,
    SessionInterject,
    SessionEscalate,
    SessionInterrupt,
    SessionCleanup,
    SessionGet,
    SessionList,
    SessionCapture,
    ListHostDrivers,
)


def handles_session_effect(effect: EffectBase) -> bool:
    """器への要求か(SessionRpc / SessionRoutes が答える要求の閉じた集合)。"""
    return isinstance(effect, _SESSION_EFFECTS)


#: 区画の観測(指し札の読み・socket の応答の確かめ)を持ち回る秒数。器の入れ替えは分の単位の出来事で、
#: 要求ごとに connect を撃たないための天井ちょうど(待ちの新設ではない)。
HOST_LAYOUT_TTL_SECONDS = 5.0


def _read_text_or_none(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


class SessionRoutes:
    """器が 2 つ並ぶ時(器の入れ替えの blue/green — ``host_slots``)の腕の経路。

    新しい手番(launch / resume)は指し札の器へ。session id の要求は、その session が生きている器へ
    (札の器で生きていなければ、降りる途中の器で生きているか確かめる — 古い器が抱えている手番を最後まで
    見張り、割り込み、片付ける)。一覧は札の器 + 降りる途中の器の生きている行(頁で読む終端の一覧は札の器
    だけ — 終端の行は ``host-slot seed`` が札の器へ写してある)。降りる途中の器から返した眺めには
    ``draining=True`` を付ける(judgment.next-arm-for-job が send の腕を採らない — 古い器に新しい手番を
    積まない)。降りる途中の器が無い間(ふつうの時)は札の器 1 つへそのまま渡す(要求を 1 つも増やさない)。
    ⚠ 降りる途中の器が**答えない**拍は例外をそのまま上げる(tick の縁が持ち越す)— 「そこでは生きていない」と
    読まない。札の器の写しは古い器で生きている session を終端と記しているので、それを答えにすると腕が手番を閉じ、
    片付けの要求で古い器の手番を切る(実弾 2026-09-23 15:45 会社 Mac: 古い器の session.get が時間切れ → 写しの
    『exited / vanished』で 2 本の手番を閉じた)。器が**降りた**(socket が応答しない)拍は区画の観測
    (HOST_LAYOUT_TTL_SECONDS ごと)から外れ、その後は札の器の答えだけになる。"""

    def __init__(
        self,
        socket_path: str,
        *,
        rpc_of: Callable[[str], SessionRpc] = SessionRpc,
        listening: Callable[[str], bool] | None = None,
        listdir: Callable[[str], list[str]] = os.listdir,
        read_pointer: Callable[[str], str | None] = _read_text_or_none,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._configured = socket_path
        self._state_dir = host_slots.state_dir_of_socket(socket_path)
        self._rpc_of = rpc_of
        self._listening = listening if listening is not None else socket_may_have_host
        self._listdir = listdir
        self._read_pointer = read_pointer
        self._clock = clock
        self._rpcs: dict[str, SessionRpc] = {}
        self._mut_layout: host_slots.HostLayout | None = None
        self._mut_layout_at = 0.0
        self._lock = threading.Lock()

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if not handles_session_effect(effect):
            return Pass(effect, k)
        return Resume(k, self.answer(effect))

    def layout(self) -> host_slots.HostLayout:
        """いまの区画の役割(持ち回りは HOST_LAYOUT_TTL_SECONDS)。"""
        now = self._clock()
        with self._lock:
            if self._mut_layout is not None and now - self._mut_layout_at < HOST_LAYOUT_TTL_SECONDS:
                return self._mut_layout
        pointer = self._read_pointer(host_slots.pointer_path(self._state_dir))
        active = host_slots.active_socket_of(self._state_dir, pointer, self._configured)
        observed = [
            host_slots.HostSlot(slot, path, path != active and self._listening(path))
            for slot in host_slots.slots_in(self._state_dir, self._listdir)
            for path in (host_slots.slot_socket(self._state_dir, slot),)
        ]
        layout = host_slots.layout_of(active, observed)
        with self._lock:
            self._mut_layout = layout
            self._mut_layout_at = now
        return layout

    def active_socket(self) -> str:
        return self.layout().active

    def any_host(self) -> bool:
        """器が 1 つでも居るかもしれないか(card acp:kanban-issue:ki-18d6c4851b21 — 停止の排水が待ちをやめる読み)。
        偽は「どの区画にも居ない」の証拠が在る拍ちょうど: 指し札の器が socket_may_have_host で偽 ∧ 降りる途中の器も無い。
        混んだ器の遅い connect は居る側に倒れる(socket_may_have_host の 1 点)。"""
        layout = self.layout()
        return bool(layout.draining) or self._listening(layout.active)

    def _rpc(self, path: str) -> SessionRpc:
        with self._lock:
            found = self._rpcs.get(path)
            if found is None:
                found = self._rpc_of(path)
                self._rpcs[path] = found
            return found

    def answer(self, effect: EffectBase) -> object:
        layout = self.layout()
        active = self._rpc(layout.active)
        # 新しい手番と器の種類の在否(ADR-DOE-AGENTS-012 R61 — 新しい手番を起こす器の答え)は指し札の器へ。
        if not layout.draining or isinstance(effect, (SessionLaunch, SessionResume, ListHostDrivers)):
            return active.answer(effect)
        if isinstance(effect, SessionList):
            return self._list(effect, active, layout.draining)
        session_id = _session_id_of(effect)
        own = active.answer(SessionGet(session_id=session_id))
        own_view = own if isinstance(own, SessionView) else None
        others = [(path, self._draining_view(path, session_id)) for path in layout.draining]
        owner = host_slots.owner_of(own_view, others)
        if isinstance(effect, SessionGet):
            if owner is None:
                return own_view
            found = dict(others)[owner]
            return None if found is None else replace(found, draining=True)
        if owner is None:
            return active.answer(effect)
        return self._rpc(owner).answer(effect)

    def _draining_view(self, path: str, session_id: str) -> SessionView | None:
        """降りる途中の器の眺め。⚠ 答えない(時間切れ・断り)拍は例外をそのまま上げる(tick の縁が持ち越す)—
        「そこには無い」と読んで指し札の器の写しを答えにしない: 写しは古い器で生きている session を終端と記して
        いるので、腕はそれを読んで手番を閉じ、片付けの要求で古い器の手番を切る(実弾 2026-09-23 15:45・会社 Mac)。"""
        found = self._rpc(path).answer(SessionGet(session_id=session_id))
        return found if isinstance(found, SessionView) else None

    def _list(
        self, effect: SessionList, active: SessionRpc, draining: tuple[str, ...]
    ) -> Sequence[SessionView]:
        """一覧の答え(SessionList の結果の契約どおり tuple で返す)。"""
        listed = active.answer(effect)
        views: list[SessionView] = [view for view in listed if isinstance(view, SessionView)] if isinstance(
            listed, tuple
        ) else []
        if effect.limit is not None:
            return tuple(views)
        wanted = SESSION_LIVE_STATUSES if effect.statuses is None else frozenset(effect.statuses) & SESSION_LIVE_STATUSES
        if not wanted:
            return tuple(views)
        ask = replace(effect, statuses=tuple(sorted(wanted)))
        index = {view.session_id: position for position, view in enumerate(views)}
        for path in draining:
            # ⚠ 答えない拍は例外をそのまま上げる(_draining_view と同じ — 生きている行を黙って落とさない)。
            theirs = self._rpc(path).answer(ask)
            for view in theirs if isinstance(theirs, tuple) else ():
                if not isinstance(view, SessionView) or not host_slots.session_live(view):
                    continue
                marked = replace(view, draining=True)
                position = index.get(view.session_id)
                if position is None:
                    index[view.session_id] = len(views)
                    views.append(marked)
                elif not host_slots.session_live(views[position]):
                    views[position] = marked
        return tuple(views)


def _session_id_of(effect: EffectBase) -> str:
    if isinstance(effect, (SessionCachePing, SessionCacheProbe)):
        return effect.operation.target.session
    session_id = getattr(effect, "session_id", None)
    if not isinstance(session_id, str):
        raise TypeError(f"{type(effect).__name__} carries no session id")
    return session_id


# ------------------------------------------------------------------ 時計・計器・file の handler


#: ULID の Crockford base32(I / L / O / U を除く 32 字)。
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def mint_ulid(now_ms: int, entropy: bytes) -> str:
    """ULID(26 字): 48 bit の時刻(ms)+ 80 bit の乱数を Crockford base32 で。純関数 —
    時刻と乱数は呼び手(handler)が渡す。"""
    if len(entropy) != 10:
        raise ValueError(f"ULID entropy must be 10 bytes, got {len(entropy)}")
    value = ((now_ms & ((1 << 48) - 1)) << 80) | int.from_bytes(entropy, "big")
    out: list[str] = []
    for _ in range(26):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


class LocalIo:
    """時計・計器(stdout の JSON 行)・log(stderr)・file の読み書き・id の鋳造・この機体の
    資格の残量(agentcli の usage の subprocess)・verify の命令の process(段 12 lane 12a)。"""

    def __init__(self) -> None:
        #: 自分が起こした verify の命令の process(pid → Popen)— probe で poll()(reap)する。再起動で消えた分は
        #: pid の file から読んだ pid を kill -0 で問う(zombie にはならない — 親は init)。
        self._commands: dict[int, subprocess.Popen[bytes]] = {}

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, MintId):
            return Resume(k, mint_ulid(int(time.time() * 1000), os.urandom(10)))
        if isinstance(effect, CommandStart | CommandProbe | CommandStop):
            return self._command_dispatch(effect, k)
        if isinstance(effect, FsFileExists | FsReadText | FsCanonicalPath | FsFileSize | FsWritePrivateText | SessionTranscript | SessionEvents | FsDirectoryExists | FsListDirectory | FsMakeDirectories):
            return self._filesystem_dispatch(effect, k)
        if isinstance(effect, OwnershipProbe | ListProfileHomes | ListPaneSeats | ReadProfileUsage | PublishWorker):
            return self._profile_dispatch(effect, k)
        if isinstance(effect, (ClockNowMs, MetricLine, LogLine)):
            return Resume(k, self._observe(effect))
        if isinstance(effect, ResolveLocalExecutable):
            # ADR-DOE-AGENTS-012 R61: agentd 自身が起こす process(要約の job — _start_command)の env の PATH は
            # この process の os.environ そのもの(足す env は札と家だけ)なので、同じ env で host と同じ関数で探す。
            return Resume(k, driver_path_in(effect.word, dict(os.environ)))
        return Pass(effect, k)

    def _filesystem_dispatch(self, effect: FsFileExists | FsReadText | FsCanonicalPath | FsFileSize | FsWritePrivateText | SessionTranscript | SessionEvents | FsDirectoryExists | FsListDirectory | FsMakeDirectories, k: K) -> Resume | Pass:
        if isinstance(effect, FsFileExists):
            return Resume(k, os.path.isfile(effect.path))
        if isinstance(effect, FsReadText):
            return Resume(k, read_small_text(effect.path, effect.max_chars))
        if isinstance(
            effect,
            (FsCanonicalPath, FsFileSize, FsWritePrivateText, SessionTranscript, SessionEvents),
        ):
            return Resume(k, self._file(effect))
        if isinstance(effect, FsDirectoryExists):
            return Resume(k, os.path.isdir(effect.path))
        if isinstance(effect, FsListDirectory):
            return Resume(k, list_directory(effect.path))
        match effect:
            case FsMakeDirectories():
                return Resume(k, make_directories(effect.path))
        assert_never(effect)

    def _profile_dispatch(self, effect: OwnershipProbe | ListProfileHomes | ListPaneSeats | ReadProfileUsage | PublishWorker, k: K) -> Resume | Pass:
        if isinstance(effect, OwnershipProbe):
            return Resume(k, probe_ownership(effect.proof))
        if isinstance(effect, ListProfileHomes):
            return Resume(k, list_profile_homes(effect.kind))
        if isinstance(effect, ListPaneSeats):
            return Resume(k, list_pane_seats())
        if isinstance(effect, ReadProfileUsage):
            return Resume(k, read_profile_usage(effect.kind, effect.cache_ttl_seconds))
        match effect:
            case PublishWorker():
                return Resume(k, publish_worker(effect))
        assert_never(effect)

    def _command_dispatch(self, effect: CommandStart | CommandProbe | CommandStop, k: K) -> Resume | Pass:
        if isinstance(effect, CommandStart):
            return Resume(k, self._start_command(effect))
        if isinstance(effect, CommandProbe):
            return Resume(k, self._probe_command(effect))
        match effect:
            case CommandStop():
                return Resume(k, stop_command(effect.pid))
        assert_never(effect)

    def _observe(self, effect: ClockNowMs | MetricLine | LogLine) -> object:
        if isinstance(effect, ClockNowMs):
            return int(time.time() * 1000)
        if isinstance(effect, MetricLine):
            sys.stdout.write(
                json.dumps(effect.fields, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            sys.stdout.flush()
            return None
        sys.stderr.write(effect.text + "\n")
        sys.stderr.flush()
        return None

    def _file(
        self,
        effect: FsCanonicalPath
        | FsFileSize
        | FsWritePrivateText
        | SessionTranscript
        | SessionEvents,
    ) -> object:
        if isinstance(effect, FsCanonicalPath):
            return os.path.realpath(effect.path)
        if isinstance(effect, FsFileSize):
            return _file_size(effect.path)
        if isinstance(effect, FsWritePrivateText):
            _write_private(effect.path, effect.text)
            return None
        return read_transcript(effect.path, effect.offset)


    def _start_command(self, effect: CommandStart) -> CommandStartOutcome:
        """verify の命令を自分の session で起こし、待たずに戻る(段 12 lane 12a)。stdin は閉じる・stdout / stderr は
        argv の中の sh が log の file へ向ける(ここは何も繋がない — agentd の stdout を汚さない)。"""
        # 段 12 lane 12j: 足す env(借りた札・家)は親の env に重ねる — 秘密なので log にも argv にも出さない。空 = 親のまま。
        env: dict[str, str] | None = None
        if effect.env:
            env = dict(os.environ)
            env.update(dict(effect.env))
        try:
            proc = subprocess.Popen(
                list(effect.argv),
                cwd=effect.cwd or None,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
                env=env,
            )
        except (OSError, ValueError) as exc:
            return CommandRefused(error=f"{type(exc).__name__}: {exc}")
        self._commands[proc.pid] = proc
        return CommandStarted(pid=proc.pid)

    def _probe_command(self, effect: CommandProbe) -> CommandProbeOutcome:
        """rc の file が在れば Exited、無ければ pid の生死(自分の子は poll() で reap・拾い直した pid は kill -0)。
        pid が読めない(None)間は pid の file を読み直し、それでも無ければ Running とも Gone とも言えないので
        Running(pid 0)を返す — 結末は rc の file が決める(期限は agentd の側が数える)。"""
        rc = rc_of_file(effect.rc_path)
        if rc is not None:
            if effect.pid is not None:
                proc = self._commands.pop(effect.pid, None)
                if proc is not None:
                    with contextlib.suppress(OSError):
                        proc.wait(timeout=0.5)
            return CommandExited(rc=rc)
        pid = effect.pid
        if pid is None:
            pid = pid_of_file(effect.pid_path)
        if pid is None:
            return CommandRunning(pid=0)
        proc = self._commands.get(pid)
        if proc is not None:
            if proc.poll() is None:
                return CommandRunning(pid=pid)
            # 子は終わったが rc の file が無い(sh が書く前に死んだ)— 結末を残さずに消えた
            self._commands.pop(pid, None)
            return CommandGone()
        return CommandRunning(pid=pid) if pid_alive(pid) else CommandGone()


def pid_alive(pid: int) -> bool:
    """pid が生きているか(kill -0・EPERM = 生きている・ESRCH = 居ない)。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def stop_command(pid: int) -> bool:
    """verify の命令の process group へ SIGTERM(段 12 lane 12a — 期限超過・取り下げ)。合図を送れたか。"""
    import signal

    if pid <= 0:
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return False
    return True


def read_small_text(path: str, max_chars: int = FS_READ_TEXT_DEFAULT_MAX_CHARS) -> str | None:
    """text の file を先頭から max_chars 字まで読む(rc / pid は既定 256・summarize の答えは SUMMARY_ANSWER_MAX_CHARS)。
    不在・読めない = None。"""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(max_chars)
    except OSError:
        return None


def _int_of_text(text: str | None) -> int | None:
    if text is None:
        return None
    stripped = text.strip()
    return int(stripped) if stripped.isdigit() else None


def rc_of_file(path: str) -> int | None:
    return _int_of_text(read_small_text(path))


def pid_of_file(path: str) -> int | None:
    return _int_of_text(read_small_text(path))


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def read_transcript(path: str, offset: int) -> TranscriptChunk:
    """``offset`` から完全な行だけを読む(途中の行は次回へ残す)。不在は空。transcript も
    headless の events file も同じ読み(1 行 1 JSON の追記 file)。"""
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            raw = handle.read()
    except OSError:
        return TranscriptChunk("", offset)
    cut = raw.rfind(b"\n")
    if cut < 0:
        return TranscriptChunk("", offset)
    complete = raw[: cut + 1]
    return TranscriptChunk(complete.decode("utf-8", errors="replace"), offset + len(complete))


def make_directories(path: str) -> bool:
    """dir を親ごと作る(段 10 lane 10y — scratch の work_dir)。作れた / 既に在る = True・作れない(権限・file が居る)= False。"""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return False
    return os.path.isdir(path)


def list_directory(path: str) -> Paths:
    """dir の直下の **file** の名(card acp:kanban-issue:ki-9fc7d4bca4dc — 記憶の置き場の冊)。名の順・dir は数えない。
    dir が無い・読めない = 空(置き場は手番の頭に作られるので、無い = まだ 1 冊も書いていない)。"""
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return ()
    return tuple(name for name in names if os.path.isfile(os.path.join(path, name)))


def _write_private(path: str, text: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".agentd-", dir=directory or None)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# ------------------------------------------------------------------ profile の残量の読み(段 7 lane 7d-3)


def _number_field(obj: Mapping[str, JSON], key: str) -> float | None:
    found = obj.get(key)
    if isinstance(found, bool) or not isinstance(found, (int, float)):
        return None
    return float(found)


def _epoch_ms_of(value: JSON) -> int | None:
    """agentcli の record の時刻(ISO 8601 の文字列・epoch 秒の数)→ epoch ms。読めなければ None。"""
    if isinstance(value, str):
        try:
            return _epoch_ms_of_iso(value)
        except ValueError:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value * 1000)


def decode_profile_usage(doc: JSON, kind: str) -> ProfileUsageOutcomes:
    """``ai usage --json`` の答え → 資格の種類(kind = 答えの鍵 claude | codex)の record ごとの答え。
    ``error`` を持つ record(会社境界の断り・provider の失敗 — 判定は agentcli)は
    ProfileUsageUnavailable、断面の時刻(captured_at_epoch)が無い record も同じ。窓は
    ``<key>_used_percentage`` が数の窓だけ(欠けた窓は発明しない)。"""
    records = _as_object(doc).get(kind)
    out: list[ProfileUsageOutcome] = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        profile = _str_field(record, "profile")
        if profile is None:
            continue
        error = _str_field(record, "error")
        if error is not None:
            out.append(ProfileUsageUnavailable(profile, error))
            continue
        captured = _number_field(record, "captured_at_epoch")
        if captured is None:
            out.append(ProfileUsageUnavailable(profile, "usage record has no captured_at_epoch"))
            continue
        windows: list[UsageWindow] = []
        for name, key in USAGE_RECORD_WINDOWS:
            used = _number_field(record, f"{key}_used_percentage")
            if used is None:
                continue
            windows.append(UsageWindow(name, used, _epoch_ms_of(record.get(f"{key}_resets_at"))))
        out.append(ProfileUsage(profile, int(captured * 1000), tuple(windows)))
    return tuple(out)


def decode_profile_homes(doc: JSON, present: Callable[[str], bool]) -> ProfileHomes:
    """``agentcli profiles list --json`` の答え(登録簿の record の列)→ profile ごとの家の在否。
    ``name`` か ``dir`` の無い record は読まない(発明しない)。``present`` = 家の実在の検
    (実 = os.path.isdir・検では表)。``aliases`` = 登録簿の別名の列(文字列だけ・無ければ空 — #479:
    ACP の行の名が別名の口座を家に結ぶ材料)。"""
    out: list[ProfileHome] = []
    for record in doc if isinstance(doc, list) else []:
        if not isinstance(record, dict):
            continue
        name = _str_field(record, "name")
        home = _str_field(record, "dir")
        if name is None or home is None:
            continue
        raw_aliases = record.get("aliases")
        aliases = tuple(a for a in raw_aliases if isinstance(a, str) and a) if isinstance(raw_aliases, list) else ()
        out.append(ProfileHome(name, home, present(home), aliases))
    return tuple(out)


def decode_pane_seats(doc: JSON) -> PaneSeats:
    """``ai pane-sessions --json`` の答え(席の列)→ PaneSeat の列(純関数)。

    必須の 3 欄(conversationId / sessionId / state)を持たない要素と、state が契約の 2 語でない要素は
    読まない(発明しない)。``profile`` は無ければ ""(実測できなかった = 口座を解かない材料)。"""
    out: list[PaneSeat] = []
    for record in doc if isinstance(doc, list) else []:
        if not isinstance(record, dict):
            continue
        conversation = _str_field(record, "conversationId")
        session = _str_field(record, "sessionId")
        state = _str_field(record, "state")
        if not conversation or not session or state not in (SESSION_OBSERVED_IDLE, SESSION_OBSERVED_BUSY):
            continue
        out.append(PaneSeat(conversation, session, state, _str_field(record, "profile") or ""))
    return tuple(out)


def list_pane_seats() -> PaneSeatsOutcome:
    """dotfiles の 1 点を subprocess で撃つ。起動できない・期限・非 0 の終了・JSON でない答えは
    **値**(PaneSeatsUnavailable)で返る — 例外にしない(参加の腕は自分の session の観測を必ず書く)。"""
    argv = list(PANE_SESSIONS_COMMAND)
    try:
        completed = subprocess.run(
            argv, capture_output=True, timeout=PANE_SESSIONS_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return PaneSeatsUnavailable(f"`{' '.join(argv)}` failed: {error}")
    if completed.returncode != 0:
        tail = completed.stderr.decode("utf-8", errors="replace").strip()[-500:]
        return PaneSeatsUnavailable(f"`{' '.join(argv)}` exited {completed.returncode}: {tail}")
    try:
        doc = _loads(completed.stdout)
    except (ValueError, UnicodeDecodeError) as error:
        return PaneSeatsUnavailable(f"`{' '.join(argv)}` did not answer JSON: {error}")
    if not isinstance(doc, list):
        return PaneSeatsUnavailable(f"`{' '.join(argv)}` did not answer a list")
    return decode_pane_seats(doc)


def list_profile_homes(kind: LeaseKind) -> ProfileHomes:
    """agentcli の登録簿の 1 点を subprocess で撃ち、家の dir の実在を検める。起動できない・期限・
    非 0 の終了・JSON でない答えは RuntimeError(tick の縁が log して次の周期へ)。"""
    argv = [*PROFILES_COMMAND, PROFILES_KIND_FLAG, kind]
    try:
        completed = subprocess.run(
            argv, capture_output=True, timeout=PROFILES_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(
            f"agentd: profile registry read `{' '.join(argv)}` failed: {error}"
        ) from error
    if completed.returncode != 0:
        tail = completed.stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise RuntimeError(
            f"agentd: profile registry read `{' '.join(argv)}` exited {completed.returncode}: {tail}"
        )
    doc = _loads(completed.stdout)
    if not isinstance(doc, list):
        raise RuntimeError(
            f"agentd: profile registry read `{' '.join(argv)}` did not answer a list"
        )
    return decode_profile_homes(doc, os.path.isdir)


def read_profile_usage(kind: LeaseKind, cache_ttl_seconds: int) -> ProfileUsageOutcomes:
    """agentcli の usage の 1 点を subprocess で撃つ。起動できない・期限・非 0 の終了・JSON でない
    答えは RuntimeError(tick の縁が log して次の周期へ)。答えの中の profile ごとの断り・失敗は
    値(ProfileUsageUnavailable)で返る。"""
    argv = [*USAGE_COMMAND, USAGE_CACHE_TTL_FLAG, str(cache_ttl_seconds)]
    try:
        completed = subprocess.run(
            argv, capture_output=True, timeout=USAGE_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"agentd: usage read `{' '.join(argv)}` failed: {error}") from error
    if completed.returncode != 0:
        tail = completed.stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise RuntimeError(
            f"agentd: usage read `{' '.join(argv)}` exited {completed.returncode}: {tail}"
        )
    doc = _loads(completed.stdout)
    if not isinstance(doc, dict) or kind not in doc:
        raise RuntimeError(f"agentd: usage read `{' '.join(argv)}` answered no {kind!r} records")
    return decode_profile_usage(doc, kind)


def publish_worker(effect: PublishWorker) -> WorkerPublished:
    """agentcli の公開の 1 点(`ai route publish-worker --json`)を subprocess で撃つ(#445)。起動できない・期限・
    非 0 の終了・JSON でない答えは**値**(ok False・理由)で返す — 公開の失敗で観測の腕(tick の縁)を落とさない。
    旗は測った時だけ立てる作法(headless_worker.publish_worker と同じ): running_turns は常に測った値(空 = 0 本)。"""
    argv = [
        *PUBLISH_COMMAND,
        PUBLISH_CADENCE_FLAG, str(int(effect.cadence_seconds)),
        PUBLISH_RUNNING_TURNS_FLAG, ",".join(effect.running_turns),
        PUBLISH_POLL_TICK_FLAG, repr(effect.poll_tick_at_ms / 1000.0),
    ]
    try:
        completed = subprocess.run(
            argv, capture_output=True, timeout=PUBLISH_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return WorkerPublished(ok=False, worker="", detail=f"`{' '.join(argv)}` failed: {error}")
    if completed.returncode != 0:
        tail = completed.stderr.decode("utf-8", errors="replace").strip()[-500:]
        return WorkerPublished(ok=False, worker="", detail=f"`{' '.join(argv)}` exited {completed.returncode}: {tail}")
    lines = completed.stdout.decode("utf-8", errors="replace").strip().splitlines()
    try:
        doc: JSON = _loads(lines[-1].encode("utf-8")) if lines else {}
    except ValueError:
        doc = {}
    worker = str(doc.get("worker") or "") if isinstance(doc, dict) else ""
    return WorkerPublished(ok=True, worker=worker, detail="")


# ------------------------------------------------------------------ 札の読み


#: GCE の metadata server(機体の外の権威 — VM の project-id を名乗る)。
GCE_METADATA_PROJECT_URL = "http://metadata.google.internal/computeMetadata/v1/project/project-id"
GCE_METADATA_HEADER = ("Metadata-Flavor", "Google")
GCE_METADATA_TIMEOUT_SECONDS = 3.0


def _read_text_stripped(path: str) -> str | None:
    """file の中身を strip して読む葉(不在・読めない・空 = None — 値を発明しない)。札の読みと
    所有の証拠の読み(probe_ownership の file:)が借りる 1 点。"""
    expanded = os.path.expanduser(path)
    try:
        with open(expanded, encoding="utf-8") as handle:
            text = handle.read().strip()
    except OSError:
        return None
    return text or None


def probe_ownership(proof: str) -> ProbeAnswer:
    """検の方法に従って所有の証拠を読む。gce-project = metadata server の project-id(届かない・
    2xx でない・空 = None)。file:<path>=<値> = その path の file の中身(不在・読めない・空 = None
    — 読むのは描かれた path ちょうどで、期待する値との突合は join.ownership-verdict の側)。それ以外
    の綴りは読むものが無い(None — declared は判断の側で通す)。"""
    parts = ownership_proof_file_parts(proof)
    if parts is not None:
        return ProbeAnswer(value=_read_text_stripped(parts[0]))
    if not proof.startswith(OWNERSHIP_PROOF_GCE_PREFIX):
        return ProbeAnswer(value=None)
    answer = _http_once(
        "GET",
        GCE_METADATA_PROJECT_URL,
        dict([GCE_METADATA_HEADER]),
        GCE_METADATA_TIMEOUT_SECONDS,
    )
    # 届かない答えも 2xx でない答えも「読めない」(値を発明しない)。
    if answer.status < 200 or answer.status >= 300:
        return ProbeAnswer(value=None)
    text = answer.body.decode("utf-8", errors="replace").strip()
    return ProbeAnswer(value=text or None)


def read_secret_file(path: str) -> str | None:
    """札の file(bearer / 借り手札)を読む。不在・空は None。"""
    return _read_text_stripped(path)


def socket_is_listening(path: str) -> bool:
    """sessionhost の socket が応答するか(agentd は host の起動を待ってから参加する)。"""
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(1.0)
        probe.connect(path)
        return True
    except OSError:
        return False
    finally:
        probe.close()


def socket_may_have_host(path: str) -> bool:
    """その socket の先に器が**居るかもしれない**か(器の区画の観測 — host_slots)。偽は「居ない」の証拠が在る拍ちょうど:
    socket の file が無い(FileNotFoundError)か、file はあるが誰も listen していない(ConnectionRefusedError)。
    connect の時間切れ・その他の失敗は**居る側**に倒す — 混んだ器の遅い connect を「降りた」と読むと、腕は新しい器の写し
    (古い器で生きている session を終端と記す)を答えにして手番を閉じ、道具は手番を持った器を降ろす
    (実弾 2026-09-23 15:5x 会社 Mac: 1 秒の connect の時間切れで古い器が区画の観測から外れ、手番 3 本を閉じた)。"""
    if not os.path.exists(path):
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(1.0)
        probe.connect(path)
        return True
    except (FileNotFoundError, ConnectionRefusedError):
        return False
    except OSError:
        return True
    finally:
        probe.close()


# ------------------------------------------------------------------ 会話の記録の service(段 9f lane 9f-2)

#: service の HTTP の期限(秒)。ACP(30 s)より短く — 届かない service で agentd の loop を長く塞がない(送れなければ spool に
#: 残して record_retry_seconds の後に再送)。
RECORD_HTTP_TIMEOUT_SECONDS = 5.0
#: spool の file の拡張子(temp は `.` で始まり一覧に出ない)と本文の schema。
RECORD_SPOOL_SUFFIX = ".json"
#: 隔離した batch の隣に置く理由の file の接尾(段 9f lane 9f-8)。
RECORD_SPOOL_REASON_SUFFIX = ".reason.txt"
RECORD_SPOOL_SCHEMA = "doeff.agentd-record-spool.v1"


def record_stream_wire(stream: RecordStream) -> JSONObject:
    """契約 $defs.streamRef の綴り(node / profile は空なら名乗らない)。"""
    wire: JSONObject = {
        "kind": stream.kind,
        "id": stream.stream_id,
        "startedAt": stream.started_at_ms,
        "attempt": stream.attempt,
    }
    if stream.node:
        wire["node"] = stream.node
    if stream.profile:
        wire["profile"] = stream.profile
    return wire


def record_append_body(batch: RecordBatch) -> JSONObject:
    """契約 $defs.appendRequest(stream + events — events は judgment が組んだ eventIn をそのまま)。"""
    events: list[JSON] = list(batch.events)
    return {"stream": record_stream_wire(batch.stream), "events": events}


def _int_items(value: JSON) -> Offsets:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, int) and not isinstance(item, bool))


def _record_error_text(reply: HttpReply) -> str:
    reason = reply.body.get("reason")
    error = _error_text(reply)
    return f"{error}: {reason}" if isinstance(reason, str) else error


def decode_record_reply(reply: HttpReply) -> RecordAppendOutcome:
    """appendEvents の応答 → 結末(2xx = appendAnswer・409 = conflictAnswer・他 = 送れなかった)。"""
    if 200 <= reply.status < 300:
        highest = _int_field(reply.body, "highestProducerSeq")
        if highest is None:
            return RecordUnsent(reply.status, "appendAnswer without highestProducerSeq")
        return RecordAppended(
            highest_producer_seq=highest,
            appended=_int_items(reply.body.get("appended")),
            ignored=_int_items(reply.body.get("ignored")),
        )
    if reply.status == 409:
        raw = reply.body.get("conflicts")
        conflicts = (
            tuple(item for item in raw if isinstance(item, dict)) if isinstance(raw, list) else ()
        )
        return RecordConflicted(conflicts=conflicts)
    return RecordUnsent(reply.status, _record_error_text(reply))


def decode_record_event(doc: JSON) -> RecordEvent | None:
    """readEvents の 1 項(契約 $defs.storedEvent)→ RecordEvent(required の欄が欠けた項は None — 発明しない)。"""
    if not isinstance(doc, dict):
        return None
    record_seq = _int_field(doc, "recordSeq")
    stream_id = _str_field(doc, "streamId")
    stream_kind = _stream_kind_of(doc.get("streamKind"))
    producer_seq = _int_field(doc, "producerSeq")
    at = _int_field(doc, "at")
    kind = _str_field(doc, "kind")
    size = _int_field(doc, "bytes")
    digest = _str_field(doc, "sha256")
    if (
        record_seq is None
        or stream_id is None
        or stream_kind is None
        or producer_seq is None
        or at is None
        or kind is None
        or size is None
        or digest is None
    ):
        return None
    return RecordEvent(
        record_seq=record_seq,
        stream_id=stream_id,
        stream_kind=stream_kind,
        producer_seq=producer_seq,
        at=at,
        kind=kind,
        bytes=size,
        sha256=digest,
        text=_str_field(doc, "text"),
        summary=_str_field(doc, "summary"),
        input=doc.get("input"),
        output=doc.get("output"),
        tool_name=_str_field(doc, "toolName"),
        tool_use_id=_str_field(doc, "toolUseId"),
        model=_str_field(doc, "model"),
        is_error=doc.get("isError") is True,
        truncated=doc.get("truncated") is True,
        mime=_str_field(doc, "mime"),
        name=_str_field(doc, "name"),
        data=_str_field(doc, "data"),
        # 段 12 lane 12l(agora-redesign #383 粒 2): 本文が消された刻(無ければ None・bool は刻ではない)。
        tombstoned_at=_int_field(doc, "tombstonedAt"),
        # card acp:kanban-issue:ki-9fc7d4bca4dc: 版(契約 storedEvent.version・required なので必ず在るが、
        # 古い service の答えには無いので既定 1 に倒す)。記憶の畳み戻しが supersede の相手を読み直す材料。
        version=_int_field(doc, "version") or 1,
    )


def decode_record_page(reply: HttpReply) -> RecordReadOutcome:
    """readEvents の応答 → 1 頁(2xx = eventsAnswer・他 = 読めなかった)。形が契約と違う項は落とす(発明しない)。"""
    if not (200 <= reply.status < 300):
        return RecordUnread(reply.status, _record_error_text(reply))
    raw = reply.body.get("events")
    cursor = reply.body.get("cursor")
    if not isinstance(raw, list) or not isinstance(cursor, dict):
        return RecordUnread(reply.status, "eventsAnswer without events / cursor")
    events = tuple(
        event for event in (decode_record_event(item) for item in raw) if event is not None
    )
    return RecordPage(events=events, next=_int_field(cursor, "next"))


class RecordHttp:
    """会話の記録の service への追記(契約 appendEvents)と履歴の読み(readEvents・before=latest から後向き)。
    bearer = 名簿の agentd の札(ACP と同じ札 — 書き手と読み手の両方の名簿に agentd が在る)。"""

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = {"Authorization": f"Bearer {token}"}
        #: 記録の service も host ごとに 1 本の接続を保つ(R22 の追補 — 拍の終わりの flush は連続の往復)。
        self._http = HttpConnections()

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, RecordAppend):
            return Resume(k, self._append(effect.batch))
        if isinstance(effect, RecordRead):
            return Resume(k, self._read(effect.conversation_id, effect.before, effect.limit))
        if isinstance(effect, RecordReadSince):
            return Resume(k, self._read_since(effect.conversation_id, effect.since, effect.limit, effect.kinds))
        if isinstance(effect, RecordReadStream):
            return Resume(k, self._read_stream(effect.conversation_id, effect.stream_id))
        if isinstance(effect, RecordSupersede):
            return Resume(k, self._supersede(effect))
        return Pass(effect, k)

    def _append(self, batch: RecordBatch) -> RecordAppendOutcome:
        cid = urllib.parse.quote(batch.conversation_id, safe="")
        stream_id = urllib.parse.quote(batch.stream.stream_id, safe="")
        url = f"{self._base_url}/v1/conversations/{cid}/streams/{stream_id}/events"
        reply = _http_json(
            self._http, "POST", url, self._headers, record_append_body(batch), RECORD_HTTP_TIMEOUT_SECONDS
        )
        return decode_record_reply(reply)

    def _supersede(self, effect: RecordSupersede) -> RecordSupersedeOutcome:
        """契約 supersede: 既に在る出来事を新しい版で置き換える(上書きではない — 前の版は鎖として残る)。
        2xx = versionAnswer / 409 = 名指した版はもう置き換えられている(= 頭が動いた・立ち直れる)/
        それ以外は送れなさを値で返す。"""
        cid = urllib.parse.quote(effect.conversation_id, safe="")
        url = f"{self._base_url}/v1/conversations/{cid}/events/{effect.record_seq}/supersede"
        reply = _http_json(
            self._http,
            "POST",
            url,
            self._headers,
            {"reason": effect.reason, "event": effect.event},
            RECORD_HTTP_TIMEOUT_SECONDS,
        )
        if reply.status == 409:
            # 本番の逐語: {"error":"sha256-conflict","reason":"event <seq> is already-superseded"}。
            # 送れなさに畳むと、呼び手は「頭を読み直せば書ける」拍と「届かなかった」拍を割れない。
            return RecordSupersedeConflicted(_record_error_text(reply))
        if not (200 <= reply.status < 300):
            return RecordUnsent(reply.status, _record_error_text(reply))
        record_seq = _int_field(reply.body, "recordSeq")
        version = _int_field(reply.body, "version")
        if record_seq is None or version is None:
            return RecordUnsent(reply.status, "versionAnswer without recordSeq / version")
        return RecordSuperseded(record_seq=record_seq, version=version)

    def _read(self, conversation_id: str, before: int | None, limit: int) -> RecordReadOutcome:
        cid = urllib.parse.quote(conversation_id, safe="")
        query = urllib.parse.urlencode(
            {"before": "latest" if before is None else str(before), "limit": str(limit)}
        )
        url = f"{self._base_url}/v1/conversations/{cid}/events?{query}"
        reply = _http_json(self._http, "GET", url, self._headers, None, RECORD_HTTP_TIMEOUT_SECONDS)
        return decode_record_page(reply)

    def _read_since(self, conversation_id: str, since: int, limit: int, kinds: tuple[str, ...]) -> RecordReadOutcome:
        """段 12 lane 12j(agora-redesign #233): 会話の出来事を前向きに 1 頁(readEvents?since=&limit=&kinds= — 要約の区間の原文)。"""
        cid = urllib.parse.quote(conversation_id, safe="")
        fields: dict[str, str] = {"since": str(since), "limit": str(limit)}
        if kinds:
            fields["kinds"] = ",".join(kinds)
        query = urllib.parse.urlencode(fields)
        url = f"{self._base_url}/v1/conversations/{cid}/events?{query}"
        reply = _http_json(self._http, "GET", url, self._headers, None, RECORD_HTTP_TIMEOUT_SECONDS)
        return decode_record_page(reply)

    def _read_stream(self, conversation_id: str, stream_id: str) -> RecordReadOutcome:
        """段 10f 便 1b: 郵便 1 通の stream を前向きに 1 頁(1 郵便 = 1 出来事なので 1 頁で足りる)。"""
        cid = urllib.parse.quote(conversation_id, safe="")
        stream = urllib.parse.quote(stream_id, safe="")
        query = urllib.parse.urlencode({"since": "0", "limit": str(RECORD_PAGE_MAX_LIMIT)})
        url = f"{self._base_url}/v1/conversations/{cid}/streams/{stream}/events?{query}"
        reply = _http_json(self._http, "GET", url, self._headers, None, RECORD_HTTP_TIMEOUT_SECONDS)
        return decode_record_page(reply)


def encode_spooled_batch(batch: RecordBatch) -> JSONObject:
    """spool の file の本文(schema・鍵・会話・appendRequest そのもの)。"""
    return {
        "schema": RECORD_SPOOL_SCHEMA,
        "spoolKey": batch.spool_key,
        "conversationId": batch.conversation_id,
        "request": record_append_body(batch),
    }


#: 語彙の写し — 定義点は effects.RecordStreamKind の 1 つで、ここはそこから導く。
#: card acp:kanban-issue:ki-9fc7d4bca4dc: 以前は語を手で並べた arm だったので、
#: 記憶の語(memory)を型と fake に足した便が decoder を見落とし、append の直後の
#: 読み戻しが本番で 100% 空振りした(行が 1 本も立たない・fake は自分で語を組むので検は緑)。
_RECORD_STREAM_KINDS: frozenset[str] = frozenset(get_args(RecordStreamKind))


def _is_stream_kind(value: JSON) -> TypeGuard[RecordStreamKind]:
    return isinstance(value, str) and value in _RECORD_STREAM_KINDS


def _stream_kind_of(value: JSON) -> RecordStreamKind | None:
    """契約の語彙(RecordStreamKind)の語だけを通す — 外の値は None(発明しない)。"""
    if _is_stream_kind(value):
        return value
    return None


def decode_spooled_batch(doc: JSON) -> RecordBatch | None:
    """spool の file の本文 → batch(形が合わなければ None — 読めない file として名乗る)。"""
    if not isinstance(doc, dict) or doc.get("schema") != RECORD_SPOOL_SCHEMA:
        return None
    key = _str_field(doc, "spoolKey")
    cid = _str_field(doc, "conversationId")
    request = doc.get("request")
    if key is None or cid is None or not isinstance(request, dict):
        return None
    stream = request.get("stream")
    events = request.get("events")
    if not isinstance(stream, dict) or not isinstance(events, list):
        return None
    kind = _stream_kind_of(stream.get("kind"))
    stream_id = _str_field(stream, "id")
    started = _int_field(stream, "startedAt")
    attempt = _int_field(stream, "attempt")
    if kind is None or stream_id is None or started is None or attempt is None:
        return None
    return RecordBatch(
        spool_key=key,
        conversation_id=cid,
        stream=RecordStream(
            kind=kind,
            stream_id=stream_id,
            started_at_ms=started,
            node=_str_field(stream, "node") or "",
            profile=_str_field(stream, "profile") or "",
            attempt=attempt,
        ),
        events=tuple(event for event in events if isinstance(event, dict)),
    )


def _fsync_directory(directory: str) -> None:
    """rename / unlink を耐久化する(dir の fsync)。"""
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace_durably(directory: str, path: str, data: bytes) -> None:
    """directory の中の path を data で置き換える(temp + fsync + rename + dir の fsync — 途中で落ちても半端な file を残さない)。"""
    fd, tmp = tempfile.mkstemp(prefix=".spool-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _fsync_directory(directory)


class RecordSpool:
    """本文の batch の spool(1 batch 1 file・temp + fsync + rename + dir の fsync)— 送る前の outbox。"""

    def __init__(self, directory: str) -> None:
        self._directory = directory

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, RecordSpoolPut):
            self.put(effect.batch)
            return Resume(k, None)
        if isinstance(effect, RecordSpoolList):
            return Resume(k, self.listing())
        if isinstance(effect, RecordSpoolRemove):
            self.delete_record(effect.spool_key)
            return Resume(k, None)
        if isinstance(effect, RecordSpoolGiveUp):
            self.give_up(effect.spool_key, effect.reason)
            return Resume(k, None)
        return Pass(effect, k)

    def _path(self, spool_key: str) -> str:
        return os.path.join(self._directory, spool_key + RECORD_SPOOL_SUFFIX)

    def put(self, batch: RecordBatch) -> None:
        os.makedirs(self._directory, mode=0o700, exist_ok=True)
        data = json.dumps(
            encode_spooled_batch(batch), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        _replace_durably(self._directory, self._path(batch.spool_key), data)

    def give_up(self, spool_key: str, reason: str) -> None:
        """決まった断りの batch を送る順から外す(段 9f lane 9f-8): file を given-up の置き場へ移し(本文は消さない)、
        理由を隣に書く。既に無い file は移さず理由だけ残す(同じ鍵の二度目も壊れない)。"""
        given_up = os.path.join(self._directory, RECORD_SPOOL_GIVEN_UP_DIR)
        os.makedirs(given_up, mode=0o700, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            os.replace(
                self._path(spool_key), os.path.join(given_up, spool_key + RECORD_SPOOL_SUFFIX)
            )
        _replace_durably(
            given_up,
            os.path.join(given_up, spool_key + RECORD_SPOOL_REASON_SUFFIX),
            reason.encode("utf-8"),
        )
        _fsync_directory(self._directory)

    def listing(self) -> RecordSpoolListing:
        try:
            names = sorted(os.listdir(self._directory))
        except FileNotFoundError:
            return RecordSpoolListing(batches=(), unreadable=())
        batches: list[RecordBatch] = []
        unreadable: list[str] = []
        for name in names:
            if name.startswith(".") or not name.endswith(RECORD_SPOOL_SUFFIX):
                continue
            try:
                with open(os.path.join(self._directory, name), "rb") as handle:
                    doc = _loads(handle.read())
            except OSError:
                unreadable.append(name)
                continue
            batch = decode_spooled_batch(doc)
            if batch is None:
                unreadable.append(name)
            else:
                batches.append(batch)
        return RecordSpoolListing(batches=tuple(batches), unreadable=tuple(unreadable))

    def remove(self, spool_key: str) -> None:
        """既存呼び手の互換入口。"""
        self.delete_record(spool_key)

    def delete_record(self, spool_key: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self._path(spool_key))
        _fsync_directory(self._directory)
