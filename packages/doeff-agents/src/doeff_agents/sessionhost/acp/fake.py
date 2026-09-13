"""agentd の要求を memory で受ける fake の handler(test 用・HTTP も socket も無し)。

handlers.py と同じ effect の契約を満たす: 同じ program(agentd.hy)が実 I/O なしで
一周でき、法の反例(購読 0 で capture が呼ばれない・binding を書かない・…)を撃てる。
test が状態を覗き、operator の代わりに行を置く(Bound の job・Node の行・購読者の数)。
"""

# pyright: strict
from dataclasses import dataclass, replace

from doeff import EffectBase, K, Pass, Resume
from doeff_agents.sessionhost.acp.effects import (
    MESSAGE_KIND,
    TURN_RECORD_KIND,
    AcpConversationHistory,
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
    ConversationHistory,
    CustodyLeaseBorrow,
    CustodyLeaseRevoke,
    EventWindow,
    FsCanonicalPath,
    FsFileSize,
    FsWritePrivateText,
    Interjected,
    JSONObject,
    LeaseGrant,
    LeaseKind,
    LeaseRefused,
    ListProfileHomes,
    LogLine,
    MetricLine,
    MintId,
    OwnershipProbe,
    ProbeAnswer,
    ProfileHome,
    ProfileUsageOutcome,
    Pushed,
    ReadProfileUsage,
    Refused,
    SessionCapture,
    SessionCleanup,
    SessionEvents,
    SessionGet,
    SessionInterject,
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
        #: watch を待った上限(AcpWatchSse.wait_seconds の列 — 拍の周期の検が読む)。
        self.waits: list[float] = []
        self.push_seq: int = 0
        #: kind → list(AcpGet)で投げる例外(実弾 002 の Connection reset の再現)。
        self.list_failures: dict[str, Exception] = {}
        #: 鍵 → 次の status の書き 1 回だけ Conflict で断る時の currentGeneration(agentd が読んだ
        #: 後に他の書き手が行を進めた race の再現・1 回で消える)。
        self.conflict_once: dict[str, int] = {}
        #: 全量 list(AcpGet)を受けた kind の列(差分の読みの検が数える)。
        self.lists: list[str] = []
        #: event の journal: (sequence, 鍵, post-image | None = delete)。event-window の材料。
        self.journal: list[tuple[int, str, AcpRow | None]] = []
        #: event-window を断る(cursor が retention の床の下の再現)。
        self.window_incomplete: bool = False
        #: 会話の記録の材料を読んだ会話の id の順(履歴からの再開の読みは手番を起こし直す時だけ — 段 8q)。
        self.history_reads: list[str] = []

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
        if isinstance(
            effect, (AcpGet, AcpGetRow, AcpEventWindow, AcpWatchSse, AcpConversationHistory)
        ):
            return Resume(k, self._read(effect))
        if isinstance(effect, (AcpPutStatus, AcpCreate, AcpStreamPush)):
            return Resume(k, self._write(effect))
        return Pass(effect, k)

    def _read(
        self,
        effect: AcpGet | AcpGetRow | AcpEventWindow | AcpWatchSse | AcpConversationHistory,
    ) -> object:
        if isinstance(effect, AcpConversationHistory):
            self.history_reads.append(effect.conversation_id)
            return ConversationHistory(
                messages=tuple(row for row in self.rows.values() if row.kind == MESSAGE_KIND),
                records=tuple(row for row in self.rows.values() if row.kind == TURN_RECORD_KIND),
            )
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
        self.waits.append(effect.wait_seconds)
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
        if row.key in self.conflict_once:
            return Conflict(self.conflict_once.pop(row.key))
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
        #: 段 8 lane 4x: 割り込みの本文(session.send の mode = interrupt)— (session_id, 本文)の順。
        self.interjections: list[tuple[str, str]] = []
        #: None = 引き受ける / SessionRefused = 器が断る(走っている手番が無い)。
        self.refuse_interject: SessionRefused | None = None
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
        #: session.resume だけを断る(transcript が見つからない等の typed reject の再現 — 行は作らない)。
        self.refuse_resume: SessionRefused | None = None
        #: 器の時計(started_at の代わり — 起こすたびに 1 進む)。
        self.clock: int = 0
        self.agent_type: str = agent_type
        self.work_dir: str = work_dir
        self.config_dir: str = "/homes/claude/acct"

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(
            effect,
            (SessionLaunch, SessionResume, SessionSend, SessionInterject, SessionInterrupt, SessionCleanup),
        ):
            return Resume(k, self._act(effect))
        if isinstance(effect, (SessionGet, SessionList, SessionCapture)):
            return Resume(k, self._look(effect))
        return Pass(effect, k)

    def _act(
        self,
        effect: SessionLaunch
        | SessionResume
        | SessionSend
        | SessionInterject
        | SessionInterrupt
        | SessionCleanup,
    ) -> object:
        if isinstance(effect, (SessionLaunch, SessionResume)):
            return self._incarnate(effect)
        if isinstance(effect, SessionSend):
            self.sends.append((effect.session_id, effect.text, effect.awaiting))
            return None
        if isinstance(effect, SessionInterject):
            if self.refuse_interject is not None:
                return self.refuse_interject
            self.interjections.append((effect.session_id, effect.text))
            return Interjected()
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
        if isinstance(effect, SessionResume) and self.refuse_resume is not None:
            return self.refuse_resume
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
        if isinstance(effect, SessionResume):
            # host と同じ意味論: 新しい incarnation は蘇生元の行の lifecycle を継ぐ(params の lifecycle は読まない)。
            source = self.views.get(str(params.get("session_id")))
            lifecycle = None if source is None else source.lifecycle
        attribution = params.get("launch_attribution")
        self.clock += 1
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
            launch_attribution=attribution if isinstance(attribution, dict) else None,
            started_at_ms=self.clock,
        )
        self.views[session_id] = view
        return view

    def finish(self, session_id: str, status: str, result: JSONObject | None = None) -> None:
        """test が器の終端を起こす(policy の turn-end が done へ倒す・死亡が exited 等)。"""
        view = self.views[session_id]
        self.views[session_id] = replace(
            view,
            status=status,
            result_payload=result,
            terminal_cause=None
            if status == "done"
            else {"category": "run_failed", "reason": status},
        )

    def finish_turn(self, session_id: str, at_ms: int | None) -> None:
        """test が温かい session の手番の終わりを起こす(host の monitor が turn_ended_at を
        刻むのと同じ意味・None = 次の手番が走り出した)。status は running のまま。"""
        view = self.views[session_id]
        self.views[session_id] = replace(view, turn_ended_at_ms=at_ms)


class FakeLocal:
    """時計・計器・log・file・この機体の資格の残量の代わり。transcript / events は path → text の表
    (transcripts)、残量は kind → 答えの列(usage・既定は空 = 持たない)。"""

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
        #: この機体の資格の残量(kind → 答えの列)と、読んだ (kind, cache_ttl_seconds) の列。
        self.usage: dict[str, tuple[ProfileUsageOutcome, ...]] = {}
        self.usage_reads: list[tuple[str, int]] = []
        #: この機体の profile の家の在否(kind → 登録簿の列)と、読んだ kind の列。据えていない kind は
        #: usage に答えのある profile の家が在る(usage を据えた検が家も据える手間を省く既定)。
        self.homes: dict[str, tuple[ProfileHome, ...]] = {}
        self.home_reads: list[str] = []

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, MintId):
            self.minted += 1
            return Resume(k, f"sid-{self.minted}")
        if isinstance(effect, OwnershipProbe):
            self.probes.append(effect.proof)
            return Resume(k, ProbeAnswer(value=self.probe_answers.get(effect.proof)))
        if isinstance(effect, ListProfileHomes):
            self.home_reads.append(effect.kind)
            return Resume(k, self._homes_of(effect.kind))
        if isinstance(effect, ReadProfileUsage):
            self.usage_reads.append((effect.kind, effect.cache_ttl_seconds))
            return Resume(k, self.usage.get(effect.kind, ()))
        if isinstance(effect, (ClockNowMs, MetricLine, LogLine)):
            return Resume(k, self._observe(effect))
        if isinstance(
            effect,
            (FsCanonicalPath, FsFileSize, FsWritePrivateText, SessionTranscript, SessionEvents),
        ):
            return Resume(k, self._file(effect))
        return Pass(effect, k)

    def _homes_of(self, kind: str) -> tuple[ProfileHome, ...]:
        declared = self.homes.get(kind)
        if declared is not None:
            return declared
        return tuple(
            ProfileHome(outcome.profile, f"/homes/{outcome.profile}", True)
            for outcome in self.usage.get(kind, ())
        )

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
