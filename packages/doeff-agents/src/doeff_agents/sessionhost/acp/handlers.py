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

秘密の扱い: 借りた access token・auth.json は値として返すだけで log に出さない。
"""

# pyright: strict
import contextlib
import json
import os
import queue
import socket
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
    AcpCreate,
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
    FsCanonicalPath,
    FsFileSize,
    FsWritePrivateText,
    JSONObject,
    LeaseGrant,
    LeaseOutcome,
    LeaseRefused,
    LogLine,
    MetricLine,
    Pushed,
    PushOutcome,
    Refused,
    SessionCapture,
    SessionGet,
    SessionLaunch,
    SessionOutcome,
    SessionRefused,
    SessionResume,
    SessionSend,
    SessionTranscript,
    SessionView,
    TranscriptChunk,
    WatchAdvance,
    WriteOutcome,
    Written,
)

Dispatcher: TypeAlias = Callable[[EffectBase, K], "Resume | Pass"]

#: ACP の URL(既定 = Mac の bridge が k3s へ透過する loopback)。
ACP_URL_ENV = "ACP_DAEMON_URL"
ACP_URL_DEFAULT = "http://127.0.0.1:8868"
#: 名簿の agentd の札の file。
ACP_TOKEN_FILE_ENV = "ACP_AGENTD_TOKEN_FILE"
#: custody の URL と借り手札(dotfiles agentcli/lease.py と同じ綴り)。
CUSTODY_URL_ENV = "AGORA_CUSTODY_URL"
CUSTODY_URL_DEFAULT = "http://127.0.0.1:8320"
BORROWER_KEY_PATH_DEFAULT = "~/.local/state/agora/borrower-key"
#: agentd の書きが乗る source(ACP の cpSource — 登録の無い source は無制限)。
ACP_WRITE_SOURCE = "agentd"
#: HTTP の期限(秒)。watch は別(SSE の読みは keepalive 15 s より長く待つ)。
HTTP_TIMEOUT_SECONDS = 30.0
WATCH_READ_TIMEOUT_SECONDS = 60.0
WATCH_RECONNECT_SECONDS = 2.0


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
        if isinstance(effect, (AcpGet, AcpGetRow, AcpWatchSse)):
            return Resume(k, self._read(effect))
        if isinstance(effect, (AcpPutStatus, AcpCreate, AcpStreamPush)):
            return Resume(k, self._write(effect))
        return Pass(effect, k)

    def _read(self, effect: AcpGet | AcpGetRow | AcpWatchSse) -> object:
        if isinstance(effect, AcpGet):
            return self._list(effect.kind)
        if isinstance(effect, AcpGetRow):
            return self._row(effect.key)
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
        if isinstance(effect, SessionLaunch):
            return Resume(k, self._incarnate("session.launch", effect.params))
        if isinstance(effect, SessionResume):
            return Resume(k, self._incarnate("session.resume", effect.params))
        if isinstance(effect, SessionSend):
            self._client.request(
                "session.send",
                {
                    "session_id": effect.session_id,
                    "message": effect.text,
                    "literal": True,
                    "enter": True,
                },
            )
            return Resume(k, None)
        if isinstance(effect, SessionGet):
            result: JSON = self._client.request("session.get", {"session_id": effect.session_id})
            return Resume(k, None if result is None else session_view_of(result))
        if isinstance(effect, SessionCapture):
            return Resume(k, self._capture(effect.session_id, effect.lines))
        return Pass(effect, k)

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


class LocalIo:
    """時計・計器(stdout の JSON 行)・log(stderr)・file の読み書き。"""

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, (ClockNowMs, MetricLine, LogLine)):
            return Resume(k, self._observe(effect))
        if isinstance(effect, (FsCanonicalPath, FsFileSize, FsWritePrivateText, SessionTranscript)):
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
        self, effect: FsCanonicalPath | FsFileSize | FsWritePrivateText | SessionTranscript
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
    """``offset`` から完全な行だけを読む(途中の行は次回へ残す)。不在は空。"""
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


# ------------------------------------------------------------------ 札の読み


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
