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
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPResponse
from typing import TypeAlias

from doeff import EffectBase, K, Pass, Resume
from doeff_agents.agentd_client import AgentdClient, AgentdClientError, launch_rpc_timeout_seconds
from doeff_agents.sessionhost.acp.effects import (
    JSON,
    OWNERSHIP_PROOF_GCE_PREFIX,
    AcpCreate,
    AcpEventWindow,
    AcpGet,
    AcpGetRow,
    AcpPutStatus,
    AcpRow,
    AcpStreamPush,
    AcpWatchSse,
    CaptureFrame,
    CaptureGone,
    CaptureOutcome,
    ClockNowMs,
    Conflict,
    CustodyLeaseBorrow,
    CustodyLeaseRevoke,
    EventWindow,
    FsCanonicalPath,
    FsFileSize,
    FsWritePrivateText,
    JSONObject,
    LeaseGrant,
    LeaseKind,
    LeaseOutcome,
    LeaseRefused,
    ListProfileHomes,
    LogLine,
    MetricLine,
    MintId,
    OwnershipProbe,
    ProbeAnswer,
    ProfileHome,
    ProfileUsage,
    ProfileUsageOutcome,
    ProfileUsageUnavailable,
    Pushed,
    PushOutcome,
    ReadProfileUsage,
    Refused,
    SessionCapture,
    SessionCleanup,
    SessionEvents,
    SessionGet,
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
    WriteOutcome,
    Written,
)

Dispatcher: TypeAlias = Callable[[EffectBase, K], "Resume | Pass"]

#: ACP の URL の既定(= Mac の bridge が k3s へ透過する loopback)— env の名は effects.py。
ACP_URL_DEFAULT = "http://127.0.0.1:8868"
#: custody の URL の既定(dotfiles agentcli/lease.py と同じ綴り)— env の名は effects.py。
CUSTODY_URL_DEFAULT = "http://127.0.0.1:8320"
BORROWER_KEY_PATH_DEFAULT = "~/.local/state/agora/borrower-key"
#: agentd の書きが乗る source(ACP の cpSource — 登録の無い source は無制限)。
ACP_WRITE_SOURCE = "agentd"
#: HTTP の期限(秒)。watch は別(SSE の読みは keepalive 15 s より長く待つ)。
HTTP_TIMEOUT_SECONDS = 30.0
WATCH_READ_TIMEOUT_SECONDS = 60.0
WATCH_RECONNECT_SECONDS = 2.0
#: profile の残量の読み口(段 7 lane 7d-3)= dotfiles agentcli の console script の 1 点。PATH で解く
#: (launchd の job_env.sh は ~/.local/bin を載せる)。会社境界の判定はこの葉の中。
USAGE_COMMAND: tuple[str, ...] = ("ai", "usage", "--json")
USAGE_CACHE_TTL_FLAG = "--cache-ttl"
#: profile の登録簿の読み口(段 8e lane 4j)= agentcli の console script の 1 点(ADR-DOTFILES-005 R2
#: 「profile 登録簿の問い合わせは単一の正」)。家(dir)の実在はこの機体で検める。
PROFILES_COMMAND: tuple[str, ...] = ("agentcli", "profiles", "list", "--json")
PROFILES_KIND_FLAG = "--kind"
PROFILES_TIMEOUT_SECONDS = 30.0
#: 35 profile の live の照会(cache が古い時)を含めた上限。
USAGE_TIMEOUT_SECONDS = 180.0
#: agentcli の record の窓の綴り(`<key>_used_percentage` / `<key>_resets_at`)→ 契約の窓の名。
USAGE_RECORD_WINDOWS: tuple[tuple[UsageWindowName, str], ...] = (
    ("5h", "five_hour"),
    ("7d", "seven_day"),
)


# ------------------------------------------------------------------ JSON の境界


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
    )


# ------------------------------------------------------------------ HTTP の小さな口


@dataclass(frozen=True)
class HttpReply:
    status: int
    body: JSONObject


def _http_json(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: JSONObject | None,
    timeout: float,
) -> HttpReply:
    """JSON の要求 → JSON の応答(HTTP の断りも HttpReply — 到達不能だけ 0)。"""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    for name, value in headers.items():
        request.add_header(name, value)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return HttpReply(int(response.status), _as_object(_loads(raw)))
    except urllib.error.HTTPError as error:
        raw = error.read()
        return HttpReply(int(error.code), _as_object(_loads(raw)))
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        return HttpReply(0, {"error": f"unreachable: {error}"})


def _error_text(reply: HttpReply) -> str:
    error = reply.body.get("error")
    return error if isinstance(error, str) else f"HTTP {reply.status}"


# ------------------------------------------------------------------ watch(SSE)の読み手


@dataclass(frozen=True)
class _Frame:
    kind: str
    sequence: int


class WatchReader:
    """``GET /api/watch/stream`` を張り続ける thread。切れたら since から張り直す。

    frame は queue に積み、``take(wait)`` が 1 回の待ちの答え(WatchAdvance)を返す。
    """

    def __init__(self, base_url: str, headers: Mapping[str, str], since: int) -> None:
        self._base_url = base_url
        self._headers = dict(headers)
        self._since = since
        self._frames: queue.Queue[_Frame] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="agentd-watch", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def take(self, since: int, wait_seconds: float) -> WatchAdvance:
        self._since = max(self._since, since)
        latest: int | None = None
        gap: int | None = None
        closed = False
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
            # 溜まっている frame は空になるまで読む(待たずに)
            deadline = min(deadline, time.monotonic())
        if gap is not None:
            return WatchAdvance(kind="gap", sequence=max(since, gap))
        if latest is not None:
            return WatchAdvance(kind="changed", sequence=max(since, latest))
        if closed:
            return WatchAdvance(kind="closed", sequence=since)
        return WatchAdvance(kind="idle", sequence=since)

    def _loop(self) -> None:
        while not self._stop.is_set():
            url = f"{self._base_url}/api/watch/stream?since={self._since}"
            request = urllib.request.Request(url, method="GET")
            for name, value in self._headers.items():
                request.add_header(name, value)
            request.add_header("Accept", "text/event-stream")
            try:
                with urllib.request.urlopen(
                    request, timeout=WATCH_READ_TIMEOUT_SECONDS
                ) as response:
                    self._read_stream(response)
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
            if self._stop.is_set():
                return
            self._frames.put(_Frame("closed", self._since))
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
            self._frames.put(_Frame("gap", resume_at if resume_at is not None else self._since))
            return
        latest = _int_field(payload, "latestSequence")
        if latest is None:
            return
        changed = payload.get("changed")
        if event == "watch" and changed is False:
            self._since = max(self._since, latest)
            return
        self._since = max(self._since, latest)
        self._frames.put(_Frame("changed", latest))


# ------------------------------------------------------------------ ACP の handler


class AcpHttp:
    """ACP の資源の読み書き・watch・中継の push。bearer は名簿の agentd の札。"""

    def __init__(self, base_url: str, token: str | None) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = {}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._watch: WatchReader | None = None

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, (AcpGet, AcpGetRow, AcpEventWindow, AcpWatchSse)):
            return Resume(k, self._read(effect))
        if isinstance(effect, (AcpPutStatus, AcpCreate, AcpStreamPush)):
            return Resume(k, self._write(effect))
        return Pass(effect, k)

    def _read(self, effect: AcpGet | AcpGetRow | AcpEventWindow | AcpWatchSse) -> object:
        if isinstance(effect, AcpGet):
            return self._list(effect.kind)
        if isinstance(effect, AcpGetRow):
            return self._row(effect.key)
        if isinstance(effect, AcpEventWindow):
            return self._event_window(effect.after, effect.limit)
        return self._watch_take(effect.since, effect.wait_seconds)

    def _write(self, effect: AcpPutStatus | AcpCreate | AcpStreamPush) -> object:
        if isinstance(effect, AcpPutStatus):
            return self._put_status(effect.row, effect.status)
        if isinstance(effect, AcpCreate):
            return self._create(effect.namespace, effect.kind, effect.resource_id, effect.spec)
        return self._push(effect.owner, effect.name, effect.frames)

    def close(self) -> None:
        if self._watch is not None:
            self._watch.stop()

    def _list(self, kind: str) -> tuple[AcpRow, ...]:
        url = f"{self._base_url}/api/resources?kind={urllib.parse.quote(kind, safe='')}"
        request = urllib.request.Request(url, method="GET")
        for name, value in self._headers.items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                listed = _loads(response.read())
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise RuntimeError(f"agentd: ACP list of {kind} failed: {error}") from error
        if not isinstance(listed, list):
            return ()
        decoded = [decode_row(item) for item in listed]
        return tuple(row for row in decoded if row is not None)

    def _row(self, key: str) -> AcpRow | None:
        reply = _http_json(
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

    def _post_event(self, body: JSONObject) -> WriteOutcome:
        reply = _http_json(
            "POST", f"{self._base_url}/api/events", self._headers, body, HTTP_TIMEOUT_SECONDS
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

    def _create(
        self, namespace: str, kind: str, resource_id: str, spec: JSONObject
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
            }
        )

    def _watch_take(self, since: int, wait_seconds: float) -> WatchAdvance:
        if self._watch is None:
            self._watch = WatchReader(self._base_url, self._headers, since)
            self._watch.start()
        return self._watch.take(since, wait_seconds)

    def _push(self, owner: str, name: str, frames: tuple[JSONObject, ...]) -> PushOutcome:
        url = (
            f"{self._base_url}/api/streams/"
            f"{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"
        )
        reply = _http_json(
            "POST", url, self._headers, {"frames": list(frames)}, HTTP_TIMEOUT_SECONDS
        )
        if reply.status != 200:
            return Refused(reply.status, _error_text(reply))
        seq = _int_field(reply.body, "seq")
        return Pushed(seq if seq is not None else 0, _int_field(reply.body, "subscribers"))


# ------------------------------------------------------------------ custody の handler


class CustodyHttp:
    """預かり所の貸出と返却。身元は借り手札(``X-Borrower-Key``)。"""

    def __init__(self, base_url: str, borrower_key: str | None) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = {}
        if borrower_key:
            self._headers["X-Borrower-Key"] = borrower_key

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, CustodyLeaseBorrow):
            return Resume(k, self._borrow(effect))
        if isinstance(effect, CustodyLeaseRevoke):
            reply = _http_json(
                "POST",
                f"{self._base_url}/lease/{effect.lease_id}/revoke",
                self._headers,
                {},
                HTTP_TIMEOUT_SECONDS,
            )
            return Resume(k, reply.status == 200)
        return Pass(effect, k)

    def _borrow(self, effect: CustodyLeaseBorrow) -> LeaseOutcome:
        reply = _http_json(
            "POST",
            f"{self._base_url}/lease/{effect.kind}",
            self._headers,
            {"account": effect.account, "purpose": effect.purpose},
            HTTP_TIMEOUT_SECONDS,
        )
        hold = _str_field(reply.body, "holdExpiresAt")
        if reply.status != 200:
            return LeaseRefused(
                reply.status, _error_text(reply), _epoch_ms_of_iso(hold) if hold else None
            )
        lease_id = _str_field(reply.body, "leaseId")
        if lease_id is None or hold is None:
            return LeaseRefused(reply.status, "malformed grant (no leaseId / holdExpiresAt)", None)
        auth_json_value = reply.body.get("authJson")
        return LeaseGrant(
            lease_id=lease_id,
            kind=effect.kind,
            renewed=reply.body.get("renewed") is True,
            hold_expires_at_ms=_epoch_ms_of_iso(hold),
            access_token=_str_field(reply.body, "accessToken"),
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
    backend_ref = snapshot.get("backend_ref")
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
    )


def _str_map(value: JSON) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    return {key: item for key, item in value.items() if isinstance(item, str)}


class SessionRpc:
    """sessionhost の socket を話す(公開の境界 — host の内側には触らない)。"""

    def __init__(self, socket_path: str) -> None:
        self._client = AgentdClient(socket_path)

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(
            effect, (SessionLaunch, SessionResume, SessionSend, SessionInterrupt, SessionCleanup)
        ):
            return Resume(k, self._act(effect))
        if isinstance(effect, (SessionGet, SessionList, SessionCapture)):
            return Resume(k, self._look(effect))
        return Pass(effect, k)

    def _act(
        self,
        effect: SessionLaunch | SessionResume | SessionSend | SessionInterrupt | SessionCleanup,
    ) -> object:
        """器を動かす要求(起こす・送る・止める・片付ける)。"""
        if isinstance(effect, SessionLaunch):
            return self._incarnate("session.launch", effect.params)
        if isinstance(effect, SessionResume):
            return self._incarnate("session.resume", effect.params)
        if isinstance(effect, SessionInterrupt):
            self._client.request("session.interrupt", {"session_id": effect.session_id})
            return None
        if isinstance(effect, SessionSend):
            self._client.request(
                "session.send",
                {
                    "session_id": effect.session_id,
                    "message": effect.text,
                    "literal": True,
                    "enter": True,
                    "awaiting": effect.awaiting,
                },
            )
            return None
        return self._cleanup(effect.session_id)

    def _look(self, effect: SessionGet | SessionList | SessionCapture) -> object:
        """器を眺める要求(1 つ・一覧・pane の断面)。"""
        if isinstance(effect, SessionGet):
            result: JSON = self._client.request("session.get", {"session_id": effect.session_id})
            return None if result is None else session_view_of(result)
        if isinstance(effect, SessionList):
            listed: JSON = self._client.request("session.list", {"lifecycle": effect.lifecycle})
            items: list[JSON] = listed if isinstance(listed, list) else []
            views = [session_view_of(item) for item in items]
            return tuple(view for view in views if view is not None)
        return self._capture(effect.session_id, effect.lines)

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
    資格の残量(agentcli の usage の subprocess)。"""

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, MintId):
            return Resume(k, mint_ulid(int(time.time() * 1000), os.urandom(10)))
        if isinstance(effect, OwnershipProbe):
            return Resume(k, probe_ownership(effect.proof))
        if isinstance(effect, ListProfileHomes):
            return Resume(k, list_profile_homes(effect.kind))
        if isinstance(effect, ReadProfileUsage):
            return Resume(k, read_profile_usage(effect.kind, effect.cache_ttl_seconds))
        if isinstance(effect, (ClockNowMs, MetricLine, LogLine)):
            return Resume(k, self._observe(effect))
        if isinstance(
            effect,
            (FsCanonicalPath, FsFileSize, FsWritePrivateText, SessionTranscript, SessionEvents),
        ):
            return Resume(k, self._file(effect))
        return Pass(effect, k)

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


def decode_profile_usage(doc: JSON, kind: str) -> tuple[ProfileUsageOutcome, ...]:
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


def decode_profile_homes(doc: JSON, present: Callable[[str], bool]) -> tuple[ProfileHome, ...]:
    """``agentcli profiles list --json`` の答え(登録簿の record の列)→ profile ごとの家の在否。
    ``name`` か ``dir`` の無い record は読まない(発明しない)。``present`` = 家の実在の検
    (実 = os.path.isdir・検では表)。"""
    out: list[ProfileHome] = []
    for record in doc if isinstance(doc, list) else []:
        if not isinstance(record, dict):
            continue
        name = _str_field(record, "name")
        home = _str_field(record, "dir")
        if name is None or home is None:
            continue
        out.append(ProfileHome(name, home, present(home)))
    return tuple(out)


def list_profile_homes(kind: LeaseKind) -> tuple[ProfileHome, ...]:
    """agentcli の登録簿の 1 点を subprocess で撃ち、家の dir の実在を検める。起動できない・期限・
    非 0 の終了・JSON でない答えは RuntimeError(tick の縁が log して次の周期へ)。"""
    argv = [*PROFILES_COMMAND, PROFILES_KIND_FLAG, kind]
    try:
        completed = subprocess.run(
            argv, capture_output=True, timeout=PROFILES_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"agentd: profile registry read `{' '.join(argv)}` failed: {error}") from error
    if completed.returncode != 0:
        tail = completed.stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise RuntimeError(
            f"agentd: profile registry read `{' '.join(argv)}` exited {completed.returncode}: {tail}"
        )
    doc = _loads(completed.stdout)
    if not isinstance(doc, list):
        raise RuntimeError(f"agentd: profile registry read `{' '.join(argv)}` did not answer a list")
    return decode_profile_homes(doc, os.path.isdir)


def read_profile_usage(kind: LeaseKind, cache_ttl_seconds: int) -> tuple[ProfileUsageOutcome, ...]:
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


# ------------------------------------------------------------------ 札の読み


#: GCE の metadata server(機体の外の権威 — VM の project-id を名乗る)。
GCE_METADATA_PROJECT_URL = "http://metadata.google.internal/computeMetadata/v1/project/project-id"
GCE_METADATA_HEADER = ("Metadata-Flavor", "Google")
GCE_METADATA_TIMEOUT_SECONDS = 3.0


def probe_ownership(proof: str) -> ProbeAnswer:
    """検の方法に従って所有の証拠を読む。gce-project = metadata server の project-id(届かない・
    2xx でない・空 = None)。それ以外の綴りは読むものが無い(None — declared は判断の側で通す)。"""
    if not proof.startswith(OWNERSHIP_PROOF_GCE_PREFIX):
        return ProbeAnswer(value=None)
    request = urllib.request.Request(GCE_METADATA_PROJECT_URL, method="GET")
    request.add_header(*GCE_METADATA_HEADER)
    try:
        with urllib.request.urlopen(request, timeout=GCE_METADATA_TIMEOUT_SECONDS) as response:
            raw = response.read()
            status = int(response.status)
    except (urllib.error.URLError, OSError, TimeoutError):
        # HTTPError は URLError の子 — 2xx でない答えも「読めない」(値を発明しない)。
        return ProbeAnswer(value=None)
    if status < 200 or status >= 300:
        return ProbeAnswer(value=None)
    text = raw.decode("utf-8", errors="replace").strip()
    return ProbeAnswer(value=text or None)


def read_secret_file(path: str) -> str | None:
    """札の file(bearer / 借り手札)を読む。不在・空は None。"""
    expanded = os.path.expanduser(path)
    try:
        with open(expanded, encoding="utf-8") as handle:
            text = handle.read().strip()
    except OSError:
        return None
    return text or None


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
