"""agentd の要求を memory で受ける fake の handler(test 用・HTTP も socket も無し)。

handlers.py と同じ effect の契約を満たす: 同じ program(agentd.hy)が実 I/O なしで
一周でき、法の反例(購読 0 で capture が呼ばれない・binding を書かない・…)を撃てる。
test が状態を覗き、operator の代わりに行を置く(Bound の job・Node の行・購読者の数)。
"""

# pyright: strict
import hashlib
import json
from dataclasses import dataclass, replace

from doeff import EffectBase, K, Pass, Resume
from doeff_agents.sessionhost.attachment import TurnAttachment
from doeff_agents.sessionhost.acp.effects import (
    JSON,
    MESSAGE_CONVERSATION_FIELDS,
    MESSAGE_KIND,
    TURN_RECORD_CONVERSATION_FIELD,
    TURN_RECORD_KIND,
    AcpConversationMail,
    AcpCreate,
    AcpEventWindow,
    AcpGet,
    AcpGetRow,
    AcpPutSpec,
    AcpPutStatus,
    AcpRow,
    AcpStreamPush,
    AcpTurnHeadlines,
    AcpWatchSse,
    CaptureFrame,
    CaptureGone,
    ClockNowMs,
    Conflict,
    CUSTODY_CONTRACT_VERSION,
    CustodyHealth,
    CustodyLeaseBorrow,
    CustodyLeaseRevoke,
    EventWindow,
    FsCanonicalPath,
    CommandExited,
    CommandGone,
    CommandProbe,
    CommandRefused,
    CommandRunning,
    CommandStart,
    CommandStarted,
    CommandStop,
    FsDirectoryExists,
    FsFileExists,
    FsMakeDirectories,
    FsReadText,
    FsFileSize,
    FsWritePrivateText,
    Escalated,
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
    RecordAppend,
    RecordAppended,
    RecordAppendOutcome,
    RecordBatch,
    RecordConflicted,
    RecordEvent,
    RecordPage,
    RecordRead,
    RecordReadStream,
    RecordReadOutcome,
    RecordSpoolGiveUp,
    RecordSpoolList,
    RecordSpoolListing,
    RecordSpoolPut,
    RecordSpoolRemove,
    RecordUnread,
    RecordUnsent,
    Refused,
    SessionCapture,
    SessionCleanup,
    SessionEvents,
    SessionGet,
    SessionEscalate,
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
        #: 書きと押しの**順**(段 10 lane 10s 追補 3): ("create", 鍵) / ("push", stream の名)— 手番の最初の frame が
        #: turn-record の作成より先に中継へ出ることを検が読む。
        self.trace: list[tuple[str, str]] = []
        self.pushes: list[tuple[str, str, tuple[JSONObject, ...]]] = []
        self.subscribers: dict[str, int] = {}
        #: watch を待った上限(AcpWatchSse.wait_seconds の列 — 拍の周期の検が読む)。
        self.waits: list[float] = []
        #: 器(host)の出来事の合図(段 12 lane 12b): test が積んだ WatchAdvance(kind session)を、ACP の sequence が
        #: 進んでいない拍に先頭から 1 つ返す(実の WakeQueue に SessionEventWaker が積む形の代わり)。
        self.wakes: list[WatchAdvance] = []
        self.push_seq: int = 0
        #: kind → list(AcpGet)で投げる例外(実弾 002 の Connection reset の再現)。
        self.list_failures: dict[str, Exception] = {}
        #: 鍵 → 次の status の書き 1 回だけ Conflict で断る時の currentGeneration(agentd が読んだ
        #: 後に他の書き手が行を進めた race の再現・1 回で消える)。
        self.conflict_once: dict[str, int] = {}
        #: 段 9p: create を鍵ごとに断る列(先頭から消費 — 頭が答えない拍 Refused(0, …) を N 回・決定論的な断り 400 等)。
        #: 尽きたら普通に作る。作った回数は creates に鍵ごと数える。
        self.create_refusals: dict[str, list[Refused]] = {}
        self.creates: dict[str, int] = {}
        #: 段 10 lane 10d: spec の書き(鍵・書いた spec)と、鍵ごとに spec の書きを断る列(先頭から消費)。
        self.spec_writes: list[tuple[str, JSONObject]] = []
        #: 段 10 lane 10y: 誕生と spec の書きが運んだ宣言 file の指紋(鍵・指紋 | None)— 書きの順。
        self.fingerprints: list[tuple[str, str | None]] = []
        self.spec_refusals: dict[str, list[Refused]] = {}
        #: 全量 list(AcpGet)を受けた kind の列(差分の読みの検が数える)。
        self.lists: list[str] = []
        #: event の journal: (sequence, 鍵, post-image | None = delete)。event-window の材料。
        self.journal: list[tuple[int, str, AcpRow | None]] = []
        #: event-window を断る(cursor が retention の床の下の再現)。
        self.window_incomplete: bool = False
        #: read-freshness.json: 窓の答えに載せる store の版(None = 欄を載せない = この契約より前の engine)。
        self.store_epoch: str | None = None
        #: 会話の郵便(AcpConversationMail)を読んだ会話の id の順(履歴からの再開の読みは手番を起こし直す時だけ — 段 8q)。
        self.history_reads: list[str] = []
        #: 手番の見出し(AcpTurnHeadlines = kind turn-record の全量)を読んだ会話の id の順 — 薄い再開の拍だけ(段 9q・#77)。
        self.headline_reads: list[str] = []

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
            effect,
            (AcpGet, AcpGetRow, AcpEventWindow, AcpWatchSse, AcpConversationMail, AcpTurnHeadlines),
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
        | AcpTurnHeadlines,
    ) -> object:
        if isinstance(effect, (AcpConversationMail, AcpTurnHeadlines)):
            return self._history(effect)
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
        if self.wakes:
            return self.wakes.pop(0)
        return WatchAdvance(kind="idle", sequence=effect.since)

    def _history(self, effect: AcpConversationMail | AcpTurnHeadlines) -> tuple[AcpRow, ...]:
        """履歴からの再開の材料(郵便 / 見出し)— 読んだ会話の id を種類ごとに数える(段 9q の検が読む:
        見出しは薄い再開の拍にだけ)。返すのは engine の field selector と同じ絞り(段 10 lane 10ba): 郵便は
        MESSAGE_CONVERSATION_FIELDS のどれかがこの会話の行、見出しは spec.conversationId がこの会話の行。"""
        wanted = effect.conversation_id
        if isinstance(effect, AcpConversationMail):
            self.history_reads.append(wanted)
            return tuple(
                row
                for row in self.rows.values()
                if row.kind == MESSAGE_KIND and any(row.spec.get(field) == wanted for field in MESSAGE_CONVERSATION_FIELDS)
            )
        self.headline_reads.append(wanted)
        return tuple(
            row
            for row in self.rows.values()
            if row.kind == TURN_RECORD_KIND and row.spec.get(TURN_RECORD_CONVERSATION_FIELD) == wanted
        )

    def _write(self, effect: AcpPutStatus | AcpPutSpec | AcpCreate | AcpStreamPush) -> object:
        if isinstance(effect, AcpPutStatus):
            return self._put_status(effect.row, effect.status)
        if isinstance(effect, AcpPutSpec):
            self.fingerprints.append((effect.row.key, effect.declaration_sha256))
            return self._put_spec(effect.row, effect.spec)
        if isinstance(effect, AcpCreate):
            self.fingerprints.append((f"{effect.namespace}:{effect.kind}:{effect.resource_id}", effect.declaration_sha256))
            self.trace.append(("create", f"{effect.namespace}:{effect.kind}:{effect.resource_id}"))
            return self._create(effect)
        self.push_seq += len(effect.frames)
        self.pushes.append((effect.owner, effect.name, effect.frames))
        self.trace.append(("push", effect.name))
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
            store_epoch=self.store_epoch,
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

    def _put_spec(self, row: AcpRow, spec: JSONObject) -> Written | Conflict | Refused:
        """段 10 lane 10d: 行の spec の書き(status は保つ — engine の SpecApplied は status の軸を触らない)。"""
        existing = self.rows.get(row.key)
        if existing is None:
            return Refused(404, "no such row")
        queued = self.spec_refusals.get(row.key)
        if queued:
            return queued.pop(0)
        if existing.generation != row.generation:
            return Conflict(existing.generation)
        self.rows[row.key] = replace(existing, generation=existing.generation + 1, spec=dict(spec))
        self._land(row.key, self.rows[row.key])
        self.spec_writes.append((row.key, dict(spec)))
        return Written(f"ev-{self.sequence}")

    def _create(self, effect: AcpCreate) -> Written | Conflict | Refused:
        key = f"{effect.namespace}:{effect.kind}:{effect.resource_id}"
        self.creates[key] = self.creates.get(key, 0) + 1
        queued = self.create_refusals.get(key)
        if queued:
            return queued.pop(0)
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
        #: 段 10 lane 10d 便 4: /health が名乗る答え(None = 読めない・欄なし = 版 1 の預かり所)。
        self.health: JSONObject | None = {"ok": True, "role": "master", "contract": CUSTODY_CONTRACT_VERSION}

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, CustodyHealth):
            return Resume(k, self.health)
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
        #: 段 10 lane 10d 便 2 追補 2: 送りが運んだ手番ごとの env — (session_id, env)の順。
        self.send_envs: list[tuple[str, JSONObject]] = []
        #: 段 10 lane 10o(agora-redesign #96): 器へ渡した型つきの添付(session_id と並び)。
        self.sent_attachments: list[tuple[str, tuple[TurnAttachment, ...]]] = []
        #: 器が添付を落とす時の理由(空 = 受ける — 検で tui の器を真似る)。
        self.attachments_ignored: str = ""
        #: session.cleanup を受けた session の順。
        self.cleanups: list[str] = []
        #: session.interrupt を受けた session の順(headless = SIGINT / turn/interrupt・tmux = Escape)。
        self.interrupts: list[str] = []
        #: 段 8 lane 4x: 割り込みの本文(session.send の mode = interrupt)— (session_id, 本文)の順。
        self.interjections: list[tuple[str, str]] = []
        #: None = 引き受ける / SessionRefused = 器が断る(走っている手番が無い)。
        self.refuse_interject: SessionRefused | None = None
        #: 段 10 lane 10n: 注入の行の名(session_id, ref)の順 — agentd は Message の id を渡す。
        self.interjection_refs: list[tuple[str, str]] = []
        #: 段 10 lane 10n: 停止の合図(session.escalate)を受けた session の順と、断り(None = 出す)。
        self.escalations: list[str] = []
        self.refuse_escalate: SessionRefused | None = None
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
        | SessionEscalate
        | SessionInterrupt
        | SessionCleanup,
    ) -> object:
        if isinstance(effect, (SessionLaunch, SessionResume)):
            return self._incarnate(effect)
        if isinstance(effect, SessionSend):
            self.sends.append((effect.session_id, effect.text, effect.awaiting))
            # 追補 2(実弾 #92): 手番ごとの env は「その送りが運ぶ値」— 検はこの列で
            # 「起こし直しがこの手番の札で起きる」ことを読む。
            self.send_envs.append((effect.session_id, dict(effect.session_env)))
            # 段 10 lane 10o(agora-redesign #96): 型つきの添付を器へ渡した記録(綴りは器の Dialogue)。
            self.sent_attachments.append((effect.session_id, effect.attachments))
            # host と同じ意味論(headless.hy headless-send-program): 降りた process への次の手番は
            # --resume で起こし直す = 送った session の backend は生きる。
            if effect.session_id in self.views:
                self.views[effect.session_id] = replace(
                    self.views[effect.session_id], backend_alive=True
                )
            return self.attachments_ignored or None
        if isinstance(effect, SessionInterject):
            if self.refuse_interject is not None:
                return self.refuse_interject
            self.interjections.append((effect.session_id, effect.text))
            self.sent_attachments.append((effect.session_id, effect.attachments))
            self.interjection_refs.append((effect.session_id, effect.ref))
            return Interjected()
        if isinstance(effect, SessionEscalate):
            if self.refuse_escalate is not None:
                return self.refuse_escalate
            self.escalations.append(effect.session_id)
            return Escalated()
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
        # 段 10 lane 10o(実弾 2026-09-15 03:08): 起こす params は **RPC へ出る object** — 本物の handler は
        # これを JSON にする。偽の器も同じ約束で受ける(JSON にできない値が混じったら、本番と同じ拍で落ちる)。
        json.dumps(params)
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
            # host と同じ意味論: 起こした直後の眺めの backend は生きている(観測)。
            backend_alive=True,
        )
        self.views[session_id] = view
        return view

    def finish(
        self,
        session_id: str,
        status: str,
        result: JSONObject | None = None,
        cause: JSONObject | None = None,
    ) -> None:
        """test が器の終端を起こす(policy の turn-end が done へ倒す・死亡が exited 等)。終端の行の
        backend は host と同じく観測せず false。

        cause = 器が書いた終端の cause(段 11 lane 11n 便 C: provider の限度の断りは
        ``{"category": "rate_limited", "reason": <CLI の文>}``)。省略 = 今日どおり
        ``{"category": "run_failed", "reason": status}``(done は cause なし)。"""
        view = self.views[session_id]
        self.views[session_id] = replace(
            view,
            status=status,
            result_payload=result,
            terminal_cause=None
            if status == "done"
            else (cause if cause is not None else {"category": "run_failed", "reason": status}),
            backend_alive=False,
        )

    def kill_backend(self, session_id: str) -> None:
        """test が backend(子 process)の死だけを起こす(段 10 lane 10h・実弾 2026-09-14: agentd の
        kickstart -k で子 process が道連れ — 行の status は running のまま、host の観測 backend_alive が
        false)。"""
        view = self.views[session_id]
        self.views[session_id] = replace(view, backend_alive=False)

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
        #: 段 10 lane 10y: 作業場の検 — 既定はどの dir も在る(検体の charter の work_dir を無い dir で落とさない)。無い dir は
        #: missing_dirs に置く・作れない dir は unmakeable_dirs に置く。検めた path と作った path を順に数える。
        self.missing_dirs: set[str] = set()
        self.unmakeable_dirs: set[str] = set()
        self.dir_checks: list[str] = []
        self.made_dirs: list[str] = []
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
        #: 段 12 lane 12a: verify の命令の代わり — 起こした argv の列(pid は 4242 から採番)、生きている pid、
        #: 止めた pid、file として在る path(script の検 — 既定 = 無い)。結末は files に rc の path で置く(rc の file が
        #: 在れば Exited)。起こせない拍は refuse_commands に理由を置く。
        self.commands: list[tuple[str, ...]] = []
        self.command_cwds: list[str] = []
        self.alive_pids: set[int] = set()
        self.stopped_pids: list[int] = []
        self.existing_files: set[str] = set()
        self.refuse_commands: str | None = None
        self.next_pid: int = 4242

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, MintId):
            self.minted += 1
            return Resume(k, f"sid-{self.minted}")
        if isinstance(effect, CommandStart):
            self.commands.append(tuple(effect.argv))
            self.command_cwds.append(effect.cwd)
            if self.refuse_commands is not None:
                return Resume(k, CommandRefused(error=self.refuse_commands))
            pid = self.next_pid
            self.next_pid += 1
            self.alive_pids.add(pid)
            # sh の 1 行が書く pid の file(argv の $0)
            self.files[effect.argv[3]] = f"{pid}\n"
            return Resume(k, CommandStarted(pid=pid))
        if isinstance(effect, CommandProbe):
            rc_text = self.files.get(effect.rc_path)
            if rc_text is not None and rc_text.strip().isdigit():
                return Resume(k, CommandExited(rc=int(rc_text.strip())))
            pid = effect.pid
            if pid is None:
                pid_text = self.files.get(effect.pid_path)
                pid = int(pid_text.strip()) if pid_text is not None and pid_text.strip().isdigit() else None
            if pid is None:
                return Resume(k, CommandRunning(pid=0))
            return Resume(k, CommandRunning(pid=pid) if pid in self.alive_pids else CommandGone())
        if isinstance(effect, CommandStop):
            self.stopped_pids.append(effect.pid)
            self.alive_pids.discard(effect.pid)
            return Resume(k, True)
        if isinstance(effect, FsFileExists):
            return Resume(k, effect.path in self.existing_files)
        if isinstance(effect, FsReadText):
            return Resume(k, self.files.get(effect.path))
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
        if isinstance(effect, FsDirectoryExists):
            self.dir_checks.append(effect.path)
            return Resume(k, effect.path not in self.missing_dirs)
        if isinstance(effect, FsMakeDirectories):
            if effect.path in self.unmakeable_dirs:
                return Resume(k, False)
            self.made_dirs.append(effect.path)
            self.missing_dirs.discard(effect.path)
            return Resume(k, True)
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


class FakeRecord:
    """会話の記録の service と本文の spool の代わり(段 9f lane 9f-2 / 9f-4・memory)。冪等は契約どおり: 鍵(会話・stream・
    producerSeq)が既在で本文(text / summary / input / output)の sha256 が同じなら ignored、違えば 409(batch は丸ごと
    積まない)。積んだ出来事は service と同じ計算の bytes / sha256(compact・鍵 sort・UTF-8)と会話ごとに単調な recordSeq を
    持ち、RecordRead は before(latest か recordSeq)から後向きに limit 件を recordSeq 昇順で返す(cursor.next = 頁の最初の
    recordSeq・これ以上無ければ None)。test は unreachable で届かない service を、stored に既在の出来事を、refusals に届いた
    service の断り(先頭から 1 要求ずつ使う — 段 9f lane 9f-8 の決まった断り)を置く。"""

    def __init__(self) -> None:
        #: spool の鍵 → batch(RecordSpoolPut で置き、RecordSpoolRemove で消える)。
        self.spool: dict[str, RecordBatch] = {}
        #: 置いた鍵の順(同じ拍の再送が同じ鍵かを test が読む)。
        self.spooled: list[str] = []
        #: (会話, stream, producerSeq) → 積んだ出来事(契約 eventIn の形)。
        self.stored: dict[tuple[str, str, int], JSONObject] = {}
        #: (会話, stream, producerSeq) → 積んだ時の recordSeq(会話ごとに単調)。test が stored に直に置いた出来事は
        #: 最初の読みで採番する。
        self.record_seqs: dict[tuple[str, str, int], int] = {}
        #: RecordAppend を受けた batch の順(送れなかった要求も数える)。
        self.appends: list[RecordBatch] = []
        #: RecordRead を受けた (会話, before, limit) の順。
        self.reads: list[tuple[str, int | None, int]] = []
        #: 段 10f 便 1b: RecordReadStream を受けた (会話, stream) の順。
        self.stream_reads: list[tuple[str, str]] = []
        self.unreachable: bool = False
        #: 届いた要求への断り(先頭から 1 つずつ使う)。
        self.refusals: list[RecordUnsent] = []
        #: RecordSpoolGiveUp で隔離した鍵 → 理由。
        self.given_up: dict[str, str] = {}

    def dispatch(self, effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(
            effect, RecordSpoolPut | RecordSpoolList | RecordSpoolRemove | RecordSpoolGiveUp
        ):
            return Resume(k, self._spool(effect))
        if isinstance(effect, RecordAppend):
            return Resume(k, self._append(effect.batch))
        if isinstance(effect, RecordRead):
            return Resume(k, self._read(effect.conversation_id, effect.before, effect.limit))
        if isinstance(effect, RecordReadStream):
            return Resume(k, self._read_stream(effect.conversation_id, effect.stream_id))
        return Pass(effect, k)

    def _spool(
        self, effect: RecordSpoolPut | RecordSpoolList | RecordSpoolRemove | RecordSpoolGiveUp
    ) -> RecordSpoolListing | None:
        if isinstance(effect, RecordSpoolPut):
            self.spool[effect.batch.spool_key] = effect.batch
            self.spooled.append(effect.batch.spool_key)
            return None
        if isinstance(effect, RecordSpoolList):
            batches = tuple(self.spool[key] for key in sorted(self.spool))
            return RecordSpoolListing(batches=batches, unreadable=())
        self.spool.pop(effect.spool_key, None)
        if isinstance(effect, RecordSpoolGiveUp):
            self.given_up[effect.spool_key] = effect.reason
        return None

    def _append(self, batch: RecordBatch) -> RecordAppendOutcome:
        self.appends.append(batch)
        if self.unreachable:
            return RecordUnsent(0, "unreachable: fake record service")
        if self.refusals:
            return self.refusals.pop(0)
        stream_id = batch.stream.stream_id
        appended: list[int] = []
        ignored: list[int] = []
        conflicts: list[JSONObject] = []
        for event in batch.events:
            seq = _producer_seq(event)
            stored = self.stored.get((batch.conversation_id, stream_id, seq))
            if stored is None:
                appended.append(seq)
            elif record_body_sha256(stored) == record_body_sha256(event):
                ignored.append(seq)
            else:
                conflicts.append({"producerSeq": seq})
        if conflicts:
            return RecordConflicted(conflicts=tuple(conflicts))
        for event in batch.events:
            key = (batch.conversation_id, stream_id, _producer_seq(event))
            if key not in self.stored:
                self.stored[key] = dict(event)
                self._number(key)
        highest = max(
            seq
            for (cid, sid, seq) in self.stored
            if cid == batch.conversation_id and sid == stream_id
        )
        return RecordAppended(
            highest_producer_seq=highest, appended=tuple(appended), ignored=tuple(ignored)
        )

    def _number(self, key: tuple[str, str, int]) -> int:
        """recordSeq を会話ごとに単調に採番する(既に採番済みならその値)。"""
        known = self.record_seqs.get(key)
        if known is not None:
            return known
        taken = [seq for (cid, _sid, _pseq), seq in self.record_seqs.items() if cid == key[0]]
        record_seq = (max(taken) if taken else 0) + 1
        self.record_seqs[key] = record_seq
        return record_seq

    def _read(self, conversation_id: str, before: int | None, limit: int) -> RecordReadOutcome:
        self.reads.append((conversation_id, before, limit))
        if self.unreachable:
            return RecordUnread(0, "unreachable: fake record service")
        rows: list[tuple[int, tuple[str, str, int]]] = []
        for key in sorted(self.stored):
            if key[0] != conversation_id:
                continue
            record_seq = self._number(key)
            if before is None or record_seq < before:
                rows.append((record_seq, key))
        rows.sort()
        page = rows[-limit:] if limit > 0 else []
        events = tuple(self._event_of(record_seq, key) for record_seq, key in page)
        remaining = len(rows) - len(page)
        return RecordPage(events=events, next=page[0][0] if page and remaining > 0 else None)

    def _read_stream(self, conversation_id: str, stream_id: str) -> RecordReadOutcome:
        """段 10f 便 1b: 郵便 1 通の stream の出来事(producerSeq の順・1 頁)。"""
        self.stream_reads.append((conversation_id, stream_id))
        if self.unreachable:
            return RecordUnread(0, "unreachable: fake record service")
        keys = sorted(key for key in self.stored if key[0] == conversation_id and key[1] == stream_id)
        return RecordPage(events=tuple(self._event_of(self._number(key), key) for key in keys), next=None)

    def _event_of(self, record_seq: int, key: tuple[str, str, int]) -> RecordEvent:
        stored = self.stored[key]
        material = record_body_bytes(stored)
        text = stored.get("text")
        summary = stored.get("summary")
        tool_name = stored.get("toolName")
        tool_use_id = stored.get("toolUseId")
        model = stored.get("model")
        kind = stored.get("kind")
        at = stored.get("at")
        mime = stored.get("mime")
        name = stored.get("name")
        data = stored.get("data")
        return RecordEvent(
            record_seq=record_seq,
            stream_id=key[1],
            stream_kind="turn",
            producer_seq=key[2],
            at=at if isinstance(at, int) and not isinstance(at, bool) else 0,
            kind=kind if isinstance(kind, str) else "text",
            bytes=len(material),
            sha256=hashlib.sha256(material).hexdigest(),
            text=text if isinstance(text, str) else None,
            summary=summary if isinstance(summary, str) else None,
            input=stored.get("input"),
            output=stored.get("output"),
            # 段 10 lane 10o(agora-redesign #96): 添付の出来事(kind attachment)の 3 欄を素通しする。
            mime=mime if isinstance(mime, str) else None,
            name=name if isinstance(name, str) else None,
            data=data if isinstance(data, str) else None,
            tool_name=tool_name if isinstance(tool_name, str) else None,
            tool_use_id=tool_use_id if isinstance(tool_use_id, str) else None,
            model=model if isinstance(model, str) else None,
            is_error=stored.get("isError") is True,
            truncated=False,
        )

    def events_of(self, conversation_id: str, stream_id: str) -> list[JSONObject]:
        """積んだ出来事を producerSeq の順に(test の読み口)。"""
        keys = sorted(
            key for key in self.stored if key[0] == conversation_id and key[1] == stream_id
        )
        return [self.stored[key] for key in keys]

    def sha256_of(self, conversation_id: str, stream_id: str, producer_seq: int) -> str:
        """積んだ出来事の本文の sha256(service の計算と同じ — 見出しとの突合の読み口)。"""
        return record_body_sha256(self.stored[(conversation_id, stream_id, producer_seq)])


def _producer_seq(event: JSONObject) -> int:
    seq: JSON = event.get("producerSeq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise ValueError(f"record event without an integer producerSeq: {event!r}")
    return seq


def record_body_bytes(event: JSONObject) -> bytes:
    """本文の同一性の綴り(契約: text / summary / input / output / data の在る欄だけ・None は無いのと同じ・compact JSON・
    鍵は sort・UTF-8)— agora-controllers services/record/vocabulary.BODY_FIELDS と judgment.hy body-bytes-of の写し。
    段 10 lane 10o(agora-redesign #96): 添付の画像の base64(data)も本文の欄(見出しの mime / name は本文ではない)。"""
    body: JSONObject = {}
    for name in ("text", "summary", "input", "output", "data"):
        field = event.get(name)
        if field is not None:
            body[name] = field
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def record_body_sha256(event: JSONObject) -> str:
    return hashlib.sha256(record_body_bytes(event)).hexdigest()
