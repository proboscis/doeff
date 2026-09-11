"""agentd の要求を memory で受ける fake の handler(test 用・HTTP も socket も無し)。

handlers.py と同じ effect の契約を満たす: 同じ program(agentd.hy)が実 I/O なしで
一周でき、法の反例(購読 0 で capture が呼ばれない・binding を書かない・…)を撃てる。
test が状態を覗き、operator の代わりに行を置く(Bound の job・Node の行・購読者の数)。
"""

# pyright: strict
from dataclasses import dataclass

from doeff import EffectBase, K, Pass, Resume
from doeff_agents.sessionhost.acp.effects import (
    AcpCreate,
    AcpGet,
    AcpGetRow,
    AcpPutStatus,
    AcpRow,
    AcpStreamPush,
    AcpWatchSse,
    ClockNowMs,
    Conflict,
    CustodyLeaseBorrow,
    CustodyLeaseRevoke,
    FsCanonicalPath,
    FsFileSize,
    FsWritePrivateText,
    JSONObject,
    LeaseGrant,
    LeaseKind,
    LeaseRefused,
    LogLine,
    MetricLine,
    Pushed,
    Refused,
    SessionCapture,
    SessionGet,
    SessionLaunch,
    SessionRefused,
    SessionResume,
    SessionSend,
    SessionTranscript,
    SessionView,
    TranscriptChunk,
    WatchAdvance,
    Written,
)


@dataclass(frozen=True)
class Birth:
    """kind の生まれの状態(engine の宣言の stateKey / initial の写し — test が渡す)。"""

    state_key: str
    initial: str


class FakeAcp:
    """ACP の資源の store・watch の sequence・中継(購読者の数は test が置く)。"""

    def __init__(self, births: dict[str, Birth]) -> None:
        self.births: dict[str, Birth] = births
        self.rows: dict[str, AcpRow] = {}
        self.sequence: int = 0
        self.writes: list[tuple[str, JSONObject]] = []
        self.pushes: list[tuple[str, str, tuple[JSONObject, ...]]] = []
        self.subscribers: dict[str, int] = {}
        self.push_seq: int = 0

    def put_row(self, row: AcpRow) -> None:
        """test / operator の代わりに行を置く(書き手の判定は無い)。"""
        self.rows[row.key] = row
        self.sequence += 1

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, (AcpGet, AcpGetRow, AcpWatchSse)):
            return Resume(k, self._read(effect))
        if isinstance(effect, (AcpPutStatus, AcpCreate, AcpStreamPush)):
            return Resume(k, self._write(effect))
        return Pass(effect, k)

    def _read(self, effect: AcpGet | AcpGetRow | AcpWatchSse) -> object:
        if isinstance(effect, AcpGet):
            return tuple(row for row in self.rows.values() if row.kind == effect.kind)
        if isinstance(effect, AcpGetRow):
            return self.rows.get(effect.key)
        if self.sequence > effect.since:
            return WatchAdvance(kind="changed", sequence=self.sequence)
        return WatchAdvance(kind="idle", sequence=effect.since)

    def _write(self, effect: AcpPutStatus | AcpCreate | AcpStreamPush) -> object:
        if isinstance(effect, AcpPutStatus):
            return self._put_status(effect.row, effect.status)
        if isinstance(effect, AcpCreate):
            return self._create(effect)
        self.push_seq += len(effect.frames)
        self.pushes.append((effect.owner, effect.name, effect.frames))
        return Pushed(self.push_seq, self.subscribers.get(effect.name, 0))

    def _put_status(self, row: AcpRow, status: JSONObject) -> Written | Conflict | Refused:
        existing = self.rows.get(row.key)
        if existing is None:
            return Refused(404, "no such row")
        if existing.generation != row.generation:
            return Conflict(existing.generation)
        self.rows[row.key] = AcpRow(
            namespace=existing.namespace,
            key=existing.key,
            kind=existing.kind,
            resource_id=existing.resource_id,
            version=existing.version,
            generation=existing.generation + 1,
            created_at_ms=existing.created_at_ms,
            labels=existing.labels,
            payload=existing.payload,
            spec=existing.spec,
            status=dict(status),
        )
        self.sequence += 1
        self.writes.append((row.key, dict(status)))
        return Written(f"ev-{self.sequence}")

    def _create(self, effect: AcpCreate) -> Written | Conflict | Refused:
        key = f"{effect.namespace}:{effect.kind}:{effect.resource_id}"
        if key in self.rows:
            return Refused(400, f"row {key} already exists")
        birth = self.births.get(effect.kind)
        status: JSONObject | None = None if birth is None else {birth.state_key: birth.initial}
        self.sequence += 1
        self.rows[key] = AcpRow(
            namespace=effect.namespace,
            key=key,
            kind=effect.kind,
            resource_id=effect.resource_id,
            version="v1",
            generation=1,
            created_at_ms=0,
            labels={},
            payload={},
            spec=dict(effect.spec),
            status=status,
        )
        return Written(f"ev-{self.sequence}")


class FakeCustody:
    """預かり所の代わり: 預かっている account と、その貸出の記録。"""

    def __init__(
        self,
        tokens: dict[str, str] | None = None,
        auth_jsons: dict[str, str] | None = None,
        hold_ms: int = 3_600_000,
    ) -> None:
        self.tokens: dict[str, str] = dict(tokens or {})
        self.auth_jsons: dict[str, str] = dict(auth_jsons or {})
        self.hold_ms: int = hold_ms
        self.borrowed: list[tuple[LeaseKind, str, str]] = []
        self.revoked: list[str] = []
        self.refuse_with: LeaseRefused | None = None
        self.counter: int = 0

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, CustodyLeaseBorrow):
            if self.refuse_with is not None:
                return Resume(k, self.refuse_with)
            self.borrowed.append((effect.kind, effect.account, effect.purpose))
            token = self.tokens.get(effect.account)
            auth_json = self.auth_jsons.get(effect.account)
            if token is None and auth_json is None:
                return Resume(k, LeaseRefused(404, "account not in custody", None))
            self.counter += 1
            return Resume(
                k,
                LeaseGrant(
                    lease_id=f"lease-{self.counter}",
                    kind=effect.kind,
                    renewed=False,
                    hold_expires_at_ms=self.hold_ms,
                    access_token=token if effect.kind == "claude" else None,
                    auth_json=auth_json if effect.kind == "codex" else None,
                ),
            )
        if isinstance(effect, CustodyLeaseRevoke):
            self.revoked.append(effect.lease_id)
            return Resume(k, True)
        return Pass(effect, k)


class FakeSessions:
    """器の代わり: launch は行を作り、status は test が動かす。capture の呼びを数える。"""

    def __init__(self, agent_type: str = "claude", work_dir: str = "/work") -> None:
        self.views: dict[str, SessionView] = {}
        self.launches: list[JSONObject] = []
        self.resumes: list[JSONObject] = []
        self.sends: list[tuple[str, str]] = []
        self.captures: list[tuple[str, int]] = []
        self.capture_text: str = "❯ \n"
        self.refuse_launch: SessionRefused | None = None
        self.agent_type: str = agent_type
        self.work_dir: str = work_dir
        self.config_dir: str = "/homes/claude/acct"

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, (SessionLaunch, SessionResume)):
            return Resume(k, self._incarnate(effect))
        if isinstance(effect, SessionSend):
            self.sends.append((effect.session_id, effect.text))
            return Resume(k, None)
        if isinstance(effect, SessionGet):
            return Resume(k, self.views.get(effect.session_id))
        if isinstance(effect, SessionCapture):
            self.captures.append((effect.session_id, effect.lines))
            return Resume(k, self.capture_text)
        return Pass(effect, k)

    def _incarnate(self, effect: SessionLaunch | SessionResume) -> SessionView | SessionRefused:
        params = effect.params
        if isinstance(effect, SessionLaunch):
            self.launches.append(dict(params))
            session_id = params.get("session_id")
        else:
            self.resumes.append(dict(params))
            session_id = params.get("new_session_id")
        if self.refuse_launch is not None:
            return self.refuse_launch
        if not isinstance(session_id, str):
            return SessionRefused("missing session_id", None)
        binding = params.get("binding")
        config_dir = self.config_dir
        if isinstance(binding, dict):
            declared = binding.get("config_dir")
            if isinstance(declared, str):
                config_dir = declared
        view = SessionView(
            session_id=session_id,
            agent_type=self.agent_type,
            status="running",
            work_dir=self.work_dir,
            lifecycle="run_to_completion",
            conversation={"session_id": session_id},
            effective_identity={"CLAUDE_CONFIG_DIR": config_dir},
            result_payload=None,
            terminal_cause=None,
        )
        self.views[session_id] = view
        return view

    def finish(self, session_id: str, status: str, result: JSONObject | None = None) -> None:
        """test が手番の終わりを起こす(policy の turn-end が done へ倒すのと同じ意味)。"""
        view = self.views[session_id]
        self.views[session_id] = SessionView(
            session_id=view.session_id,
            agent_type=view.agent_type,
            status=status,
            work_dir=view.work_dir,
            lifecycle=view.lifecycle,
            conversation=view.conversation,
            effective_identity=view.effective_identity,
            result_payload=result,
            terminal_cause=None
            if status == "done"
            else {"category": "run_failed", "reason": status},
        )


class FakeLocal:
    """時計・計器・log・file の代わり。transcript は path → text の表。"""

    def __init__(self, now_ms: int = 1_000) -> None:
        self.now_ms: int = now_ms
        self.metrics: list[JSONObject] = []
        self.logs: list[str] = []
        self.files: dict[str, str] = {}
        self.transcripts: dict[str, str] = {}

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, (ClockNowMs, MetricLine, LogLine)):
            return Resume(k, self._observe(effect))
        if isinstance(effect, (FsCanonicalPath, FsFileSize, FsWritePrivateText, SessionTranscript)):
            return Resume(k, self._file(effect))
        return Pass(effect, k)

    def _observe(self, effect: ClockNowMs | MetricLine | LogLine) -> object:
        if isinstance(effect, ClockNowMs):
            return self.now_ms
        if isinstance(effect, MetricLine):
            self.metrics.append(dict(effect.fields))
            return None
        self.logs.append(effect.text)
        return None

    def _file(
        self, effect: FsCanonicalPath | FsFileSize | FsWritePrivateText | SessionTranscript
    ) -> object:
        if isinstance(effect, FsCanonicalPath):
            return effect.path
        if isinstance(effect, FsFileSize):
            return len(self.transcripts.get(effect.path, "").encode("utf-8"))
        if isinstance(effect, FsWritePrivateText):
            self.files[effect.path] = effect.text
            return None
        raw = self.transcripts.get(effect.path, "").encode("utf-8")[effect.offset :]
        cut = raw.rfind(b"\n")
        if cut < 0:
            return TranscriptChunk("", effect.offset)
        complete = raw[: cut + 1]
        return TranscriptChunk(complete.decode("utf-8"), effect.offset + len(complete))
