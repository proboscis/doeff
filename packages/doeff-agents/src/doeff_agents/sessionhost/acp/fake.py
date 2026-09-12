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
    AcpEventWindow,
    AcpGet,
    AcpGetRow,
    AcpPutStatus,
    AcpRow,
    AcpStreamPush,
    AcpWatchSse,
    CaptureFrame,
    CaptureGone,
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
    LeaseRefused,
    LogLine,
    MetricLine,
    MintId,
    OwnershipProbe,
    ProbeAnswer,
    Pushed,
    Refused,
    SessionCapture,
    SessionCleanup,
    SessionEvents,
    SessionGet,
    SessionInterrupt,
    SessionLaunch,
    SessionList,
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
        #: kind → list(AcpGet)で投げる例外(実弾 002 の Connection reset の再現)。
        self.list_failures: dict[str, Exception] = {}
        #: 全量 list(AcpGet)を受けた kind の列(差分の読みの検が数える)。
        self.lists: list[str] = []
        #: event の journal: (sequence, 鍵, post-image | None = delete)。event-window の材料。
        self.journal: list[tuple[int, str, AcpRow | None]] = []
        #: event-window を断る(cursor が retention の床の下の再現)。
        self.window_incomplete: bool = False

    def _land(self, key: str, row: AcpRow | None) -> None:
        self.sequence += 1
        self.journal.append((self.sequence, key, row))

    def put_row(self, row: AcpRow) -> None:
        """test / operator の代わりに行を置く(書き手の判定は無い)。"""
        self.rows[row.key] = row
        self._land(row.key, row)

    def delete_row(self, key: str) -> None:
        """test の代わりに行を消す(GC の retire の再現)。"""
        self.rows.pop(key, None)
        self._land(key, None)

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, (AcpGet, AcpGetRow, AcpEventWindow, AcpWatchSse)):
            return Resume(k, self._read(effect))
        if isinstance(effect, (AcpPutStatus, AcpCreate, AcpStreamPush)):
            return Resume(k, self._write(effect))
        return Pass(effect, k)

    def _read(self, effect: AcpGet | AcpGetRow | AcpEventWindow | AcpWatchSse) -> object:
        if isinstance(effect, AcpGet):
            failure = self.list_failures.get(effect.kind)
            if failure is not None:
                raise failure
            self.lists.append(effect.kind)
            return tuple(row for row in self.rows.values() if row.kind == effect.kind)
        if isinstance(effect, AcpGetRow):
            return self.rows.get(effect.key)
        if isinstance(effect, AcpEventWindow):
            return self._window(effect.after, effect.limit)
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

    def _window(self, after: int, limit: int) -> EventWindow:
        if self.window_incomplete:
            return EventWindow(complete=False, through=after, latest=after, rows=(), retired=())
        entries = [entry for entry in self.journal if entry[0] > after][:limit]
        rows: dict[str, AcpRow] = {}
        retired: dict[str, None] = {}
        births: dict[str, int] = {}
        for _sequence, key, image in entries:
            if image is None:
                rows.pop(key, None)
                retired[key] = None
            else:
                rows[key] = image
                retired.pop(key, None)
                if image.generation == 1 and image.landed_at_ms is not None:
                    births.setdefault(image.resource_id, image.landed_at_ms)
        through = entries[-1][0] if entries else after
        return EventWindow(
            complete=True,
            through=through,
            latest=self.sequence,
            rows=tuple(rows.values()),
            retired=tuple(retired),
            births=tuple(sorted(births.items())),
        )

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
        self._land(row.key, self.rows[row.key])
        self.writes.append((row.key, dict(status)))
        return Written(f"ev-{self.sequence}")

    def _create(self, effect: AcpCreate) -> Written | Conflict | Refused:
        key = f"{effect.namespace}:{effect.kind}:{effect.resource_id}"
        if key in self.rows:
            return Refused(400, f"row {key} already exists")
        birth = self.births.get(effect.kind)
        status: JSONObject | None = None if birth is None else {birth.state_key: birth.initial}
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
        self._land(key, self.rows[key])
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

    def __init__(
        self,
        agent_type: str = "claude",
        work_dir: str = "/work",
        backend_kind: str = "tmux",
        events_root: str = "/events",
    ) -> None:
        self.views: dict[str, SessionView] = {}
        self.launches: list[JSONObject] = []
        self.resumes: list[JSONObject] = []
        #: (session_id, 本文, awaiting)
        self.sends: list[tuple[str, str, bool]] = []
        #: session.cleanup を受けた session の順。
        self.cleanups: list[str] = []
        #: session.interrupt を受けた session の順(headless = SIGINT / turn/interrupt・tmux = Escape)。
        self.interrupts: list[str] = []
        #: この器の backend(tmux | herdr | headless)と headless の events file の置き場。
        self.backend_kind: str = backend_kind
        self.events_root: str = events_root
        self.captures: list[tuple[str, int]] = []
        self.capture_text: str = "❯ \n"
        #: None = 断面を返す / str = pane も server も無い(理由)— host が capture を断った形
        #: (実 handler は RPC の error 封筒を CaptureGone に写す・fake は同じ値を直に返す)。
        self.capture_gone: str | None = None
        #: gone の拍で器を終端へ倒す(session.get の後に pane が消える race の再現)。
        self.finish_on_capture: tuple[str, JSONObject | None] | None = None
        #: session_id → session.get で投げる例外(器の RPC が落ちた job の再現)。
        self.failures: dict[str, Exception] = {}
        self.refuse_launch: SessionRefused | None = None
        self.agent_type: str = agent_type
        self.work_dir: str = work_dir
        self.config_dir: str = "/homes/claude/acct"

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
        if isinstance(effect, (SessionLaunch, SessionResume)):
            return self._incarnate(effect)
        if isinstance(effect, SessionSend):
            self.sends.append((effect.session_id, effect.text, effect.awaiting))
            return None
        if isinstance(effect, SessionInterrupt):
            self.interrupts.append(effect.session_id)
            return None
        self.cleanups.append(effect.session_id)
        if effect.session_id not in self.views:
            return False
        view = self.views[effect.session_id]
        if view.status not in {"done", "failed", "exited", "stopped", "cancelled"}:
            self.finish(effect.session_id, "stopped")
        return True

    def _look(self, effect: SessionGet | SessionList | SessionCapture) -> object:
        if isinstance(effect, SessionGet):
            failure = self.failures.get(effect.session_id)
            if failure is not None:
                raise failure
            return self.views.get(effect.session_id)
        if isinstance(effect, SessionList):
            return tuple(view for view in self.views.values() if view.lifecycle == effect.lifecycle)
        self.captures.append((effect.session_id, effect.lines))
        if self.capture_gone is not None:
            if self.finish_on_capture is not None:
                self.finish(effect.session_id, *self.finish_on_capture)
            return CaptureGone(self.capture_gone)
        return CaptureFrame(self.capture_text)

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
        if session_id in self.views:
            # host と同じ意味論: 片付いた(stopped / cleaned)行も登記のまま残る — 同じ id の
            # launch は断られる(実弾 2026-09-12 `session is already registered`)。
            return SessionRefused(f"session is already registered: {session_id}", None)
        binding = params.get("binding")
        config_dir = self.config_dir
        if isinstance(binding, dict):
            declared = binding.get("config_dir")
            if isinstance(declared, str):
                config_dir = declared
        lifecycle = params.get("lifecycle")
        view = SessionView(
            session_id=session_id,
            agent_type=self.agent_type,
            status="running",
            work_dir=self.work_dir,
            lifecycle=lifecycle if isinstance(lifecycle, str) else "run_to_completion",
            conversation={"session_id": session_id},
            effective_identity={"CLAUDE_CONFIG_DIR": config_dir},
            result_payload=None,
            terminal_cause=None,
            turn_ended_at_ms=None,
            backend_kind=self.backend_kind,
            backend_ref=(
                {
                    "session_name": session_id,
                    "events_path": f"{self.events_root}/{session_id}.events.jsonl",
                }
                if self.backend_kind == "headless"
                else None
            ),
        )
        self.views[session_id] = view
        return view

    def finish(self, session_id: str, status: str, result: JSONObject | None = None) -> None:
        """test が器の終端を起こす(policy の turn-end が done へ倒す・死亡が exited 等)。"""
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
            turn_ended_at_ms=view.turn_ended_at_ms,
            backend_kind=view.backend_kind,
            backend_ref=view.backend_ref,
        )

    def finish_turn(self, session_id: str, at_ms: int | None) -> None:
        """test が温かい session の手番の終わりを起こす(host の monitor が turn_ended_at を
        刻むのと同じ意味・None = 次の手番が走り出した)。status は running のまま。"""
        view = self.views[session_id]
        self.views[session_id] = SessionView(
            session_id=view.session_id,
            agent_type=view.agent_type,
            status=view.status,
            work_dir=view.work_dir,
            lifecycle=view.lifecycle,
            conversation=view.conversation,
            effective_identity=view.effective_identity,
            result_payload=view.result_payload,
            terminal_cause=view.terminal_cause,
            turn_ended_at_ms=at_ms,
            backend_kind=view.backend_kind,
            backend_ref=view.backend_ref,
        )


class FakeLocal:
    """時計・計器・log・file の代わり。transcript / events は path → text の表(transcripts)。"""

    def __init__(self, now_ms: int = 1_000) -> None:
        self.now_ms: int = now_ms
        self.metrics: list[JSONObject] = []
        self.logs: list[str] = []
        self.files: dict[str, str] = {}
        self.transcripts: dict[str, str] = {}
        #: 鋳造した session の id の数(id = sid-<n> — charter の id とは別の綴り)。
        self.minted: int = 0
        #: 所有の検の答え(proof → 材料の値・無い proof は None = 読めない)と撃った proof の列。
        self.probe_answers: dict[str, str | None] = {}
        self.probes: list[str] = []

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, MintId):
            self.minted += 1
            return Resume(k, f"sid-{self.minted}")
        if isinstance(effect, OwnershipProbe):
            self.probes.append(effect.proof)
            return Resume(k, ProbeAnswer(value=self.probe_answers.get(effect.proof)))
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
            return self.now_ms
        if isinstance(effect, MetricLine):
            self.metrics.append(dict(effect.fields))
            return None
        self.logs.append(effect.text)
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
