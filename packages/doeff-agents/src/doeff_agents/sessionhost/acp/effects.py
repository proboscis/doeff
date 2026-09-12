"""agentd の要求(effect)と値の型 — data だけで I/O を 1 つも行わない。

段 2(agora-redesign #19 / #20)の agentd は「ACP の cluster に参加して agent-job を受け、
session を起こし、手番の記録と実況を ACP へ書く」腕で、判断は持たない。ここはその腕が
世界に頼むこと(要求)と、頼んだ結果・判断の材料(値)の型の 1 点。

- 要求の語彙(依頼書 1.): ``AcpGet`` / ``AcpPutStatus`` / ``AcpCreate`` / ``AcpWatchSse`` /
  ``AcpStreamPush`` / ``CustodyLeaseBorrow``(+ 返却の ``CustodyLeaseRevoke``)。
  agentd 自身の器(sessionhost の RPC)への要求 = ``Session*``、時計・計器・file の
  読み書き = ``ClockNowMs`` / ``MetricLine`` / ``LogLine`` / ``FsCanonicalPath`` /
  ``FsWritePrivateText``。
- 実 I/O は handlers.py(HTTP / RPC / file)、fake は fake.py、要求を並べる判断は
  judgment.hy(純関数)と agentd.hy(program)。handler の選択は runtime.py の 1 点。
- 値の宣言の 1 点 = ``AgentdSettings``(lease の TTL と周期・watch の resync・frame の
  rate・購読の読み直しの周期)。env からの読みは runtime.py が行い、ここは既定値だけを持つ。

wire の綴り(ACP の route・kind 名・phase の語)はこの file が唯一持つ(judgment / agentd は
ここから import する — 第 2 の綴りを作らない)。
"""

# pyright: strict
from dataclasses import dataclass
from typing import Literal, TypeAlias

from doeff import EffectBase

#: JSON の値(ACP の行の spec / status・中継の frame はこの形のまま運ぶ)。
JSON: TypeAlias = "dict[str, JSON] | list[JSON] | str | int | float | bool | None"
JSONObject: TypeAlias = "dict[str, JSON]"

# ------------------------------------------------------------------ 綴り(ACP の契約)

#: agentd が ACP に名乗る principal(名簿の綴り = stream の持ち主 = lease の owner)。
AGENTD_PRINCIPAL = "agentd"
#: engine 自身が宣言する kind とその区画(src/Acp/App/Agent/AgentJob.hs)。
AGENT_JOB_KIND = "agent-job"
AGENT_JOB_NAMESPACE = "acp-system"
#: 段 1 で data として登録された kind(docs/contracts/agora-kinds.json・区画 default)。
NODE_KIND = "node"
MESSAGE_KIND = "message"
TURN_RECORD_KIND = "turn-record"
AGORA_KINDS_NAMESPACE = "default"
#: agent-job の phase の閉語彙(AgentJob.hs phaseWord)— agentd が書くのは Running / Ended。
PHASE_PENDING = "Pending"
PHASE_BOUND = "Bound"
PHASE_RUNNING = "Running"
PHASE_ENDED = "Ended"
PHASE_WITHDRAWN = "Withdrawn"
#: turn-record の state(契約 agora-kinds.json turn-record.declaration.states)。
TURN_RECORD_RUNNING = "running"
TURN_RECORD_ENDED = "ended"
#: node の terminal state(gone の行は同じ名の生きた行ではない)。
NODE_GONE = "gone"
#: 中継の frame の capability(docs/contracts/turn-delta.json capability.values)。
StreamCapability = Literal["events", "frames", "none"]
#: TurnDelta の種類(docs/contracts/turn-delta.json kinds)。
DeltaKind = Literal["text", "tool_use", "tool_result", "usage", "status", "frame"]
#: agent-job の conditions に agentd が書く type の語彙(AgentJob.hs は type を opaque に運ぶ —
#: 閉語彙は phase だけなので、agentd 側の語をここ 1 点で閉じる)。Interrupted = 取り下げ
#: (Withdrawn)で走っている手番を止めた(phase は書き手 = 作った側のまま)。
ConditionType = Literal[
    "LaunchFailed", "CredentialUnavailable", "InputUnavailable", "SessionFailed", "Interrupted"
]
CONDITION_INTERRUPTED: ConditionType = "Interrupted"
#: sessionhost の wire の backend_kind のうち agentd が読む語(host.hy の閉語彙 tmux | herdr |
#: headless の写し — agora-redesign #37)。headless の session は実況を events file で読み
#: (backend_ref.events_path)、node の streamCapability は events。
BACKEND_HEADLESS = "headless"
#: 実況の材料の種類(judgment.stream-source-of の閉語彙): events = headless の stdout の行
#: (claude の stream-json / codex の app-server の JSON-RPC)/ transcript = tui の transcript。
StreamSource = Literal["events", "transcript"]
STREAM_SOURCE_EVENTS: StreamSource = "events"
STREAM_SOURCE_TRANSCRIPT: StreamSource = "transcript"
#: 取り下げ(Withdrawn)を受けた job の腕(judgment.interrupt-arm-for の閉語彙): interrupt =
#: 手番の途中なので session.interrupt を撃つ / none = 手番は走っていない(合図は要らない)。
InterruptArm = Literal["interrupt", "none"]
INTERRUPT_ARM_INTERRUPT: InterruptArm = "interrupt"
INTERRUPT_ARM_NONE: InterruptArm = "none"
#: custody の貸出の口の種類(POST /lease/claude | /lease/codex)。
LeaseKind = Literal["claude", "codex"]
#: sessionhost の wire の agent_type とその貸出の種類の対応(policy.hy BINDING-KIND-AGENT-TYPE の逆)。
AGENT_TYPE_LEASE_KIND: dict[str, LeaseKind] = {"claude": "claude", "codex": "codex"}
#: 貸した Claude の札を載せる env(custodian /lease/claude の note どおり — 資格 file は書かない)。
CLAUDE_OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
#: sessionhost の wire の終端 status(policy.hy TERMINAL-STATUSES の写し — 手番の終わりの読み)。
#: policy.hy は deff / defhandler を持つ Hy で共通の品質検査が投影できないため、agentd が読む
#: 語彙をここに写す。第 2 の定義点であることは ADR-DOE-AGENTS-012 の報告に明記。
SESSION_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"done", "failed", "exited", "stopped", "cancelled"}
)
#: 自分の Running の行の次の 1 手(judgment.job-step-of の閉語彙 — ADR-DOE-AGENTS-012 R7 / R10)。
#: observe = 器が走っている(行から InFlightJob を組んで観測を続ける)/ record-end = 器が終端
#: (記録の腕だけ: turn-record ended・result・phase Ended)/ fail-missing = 器に session が無い
#: (記録が在れば ended にし、condition SessionFailed で Ended)/ turn-end = 温かい session の
#: 手番の終わり(器は生きたまま turn_ended_at が手番の始まりより後に付いた: 記録の腕だけを撃ち、
#: session は片付けない)。
JobStep = Literal["observe", "record-end", "fail-missing", "turn-end"]
JOB_STEP_OBSERVE: JobStep = "observe"
JOB_STEP_RECORD_END: JobStep = "record-end"
JOB_STEP_FAIL_MISSING: JobStep = "fail-missing"
JOB_STEP_TURN_END: JobStep = "turn-end"
#: sessionhost の lifecycle の語のうち agentd が使うもの(launch.hy LIFECYCLE-* の写し)。
#: multi_turn = 温かい session(手番の終わりで片付けない — 同じ会話の次の手番は send)。
#: charter に lifecycle が無い時の agentd の既定(judgment.launch-lifecycle-of の 1 点)。
LIFECYCLE_MULTI_TURN = "multi_turn"
#: Bound の job の起こし方(judgment.next-arm-for-job の閉語彙 — ADR-DOE-AGENTS-012 R10)。
#: send = 会話の session が生きて idle(温かい)/ resume = predecessor が在るが生きていない
#: (cold の session.resume)/ launch = 会話の session が無い / defer = 会話の session が手番の
#: 途中(claim せず次の list で読み直す — 走っている手番に本文を積まない)。
NextArm = Literal["launch", "send", "resume", "defer"]
NEXT_ARM_LAUNCH: NextArm = "launch"
NEXT_ARM_SEND: NextArm = "send"
NEXT_ARM_RESUME: NextArm = "resume"
NEXT_ARM_DEFER: NextArm = "defer"
#: node の status.observations.sessions の state(段 3 の契約の追補で閉語彙になる予定 —
#: それまで agentd 側の語: idle = 手番の間 / busy = 手番の途中)。
SessionObservationState = Literal["idle", "busy"]
SESSION_OBSERVED_IDLE: SessionObservationState = "idle"
SESSION_OBSERVED_BUSY: SessionObservationState = "busy"
#: handler が値に写さない I/O の失敗(program の tick の縁 — job ごと・heartbeat・受け — が
#: 捕まえて log し、次の拍へ持ち越す型)。ACP の HTTP = RuntimeError、器の RPC = AgentdClientError
#: (RuntimeError の子)、socket / file = OSError。これより広い例外(bug)は runtime.run_loop の縁へ。
IO_FAILURES: tuple[type[Exception], ...] = (RuntimeError, OSError)

# ------------------------------------------------------------------ 起動の宣言の綴り(env の束・host の argv・所有)

#: agentd の起動が読む env の名(段 6 lane 6f: 1 命令の参加 `join` はこの束を宣言から導く —
#: join.hy join-plan-of の 1 点。読み手 = runtime.settings_from_env / real_dispatchers・valve.acp_valve・
#: host.hy parse-args。名の綴りはここが唯一持つ)。
ACP_VALVE_ENV = "DOEFF_AGENTD_ACP"
ACP_URL_ENV = "ACP_DAEMON_URL"
ACP_TOKEN_FILE_ENV = "ACP_AGENTD_TOKEN_FILE"
NODE_NAME_ENV = "DOEFF_AGENTD_NODE_NAME"
HOMES_ROOT_ENV = "DOEFF_AGENTD_HOMES_ROOT"
CUSTODY_URL_ENV = "AGORA_CUSTODY_URL"
BORROWER_KEY_PATH_ENV = "AGORA_BORROWER_KEY_PATH"
HOST_BACKEND_ENV = "DOEFF_SESSIONHOST_BACKEND"
HEADLESS_DIR_ENV = "DOEFF_SESSIONHOST_HEADLESS_DIR"
SESSION_HOOKS_ENV = "DOEFF_AGENTD_SESSION_HOOKS"
OWNERSHIP_ENV = "DOEFF_AGENTD_OWNERSHIP"
OWNERSHIP_PROOF_ENV = "DOEFF_AGENTD_OWNERSHIP_PROOF"
#: host(oracle parse_args / host.hy parse-args)の argv の綴り(join が組む・valve が読む)。
HOST_DB_FLAG = "--db"
HOST_SOCKET_FLAG = "--socket"
HOST_MAX_RUNNING_FLAG = "--max-running"
HOST_MAX_RUNNING_UNLIMITED = "none"
HOST_BACKEND_FLAG = "--backend"
HOST_SERVE_COMMAND = "serve"
#: host の backend の閉語彙(host.hy parse-args と同じ 3 語)と agentd の既定(join の既定 = headless)。
HOST_BACKENDS: frozenset[str] = frozenset({"tmux", "herdr", BACKEND_HEADLESS})
HOST_BACKEND_DEFAULT = "tmux"
#: 1 命令の参加の subcommand と宣言 file の schema(段 6 lane 6f・決定 23)。
JOIN_SUBCOMMAND = "join"
JOIN_SCHEMA = "doeff.agentd-join.v1"
#: join の置き場(state_dir)の下の綴り(db・socket・headless の events)— 段 6c の宣言と同じ。
JOIN_DB_FILE = "agentd.sqlite"
JOIN_SOCKET_FILE = "agentd.sock"
JOIN_HEADLESS_DIR = "headless-events"
JOIN_STATE_DIR_DEFAULT = "doeff/acp-agentd"
JOIN_SESSION_HOOKS_DEFAULT = "inherit"
#: 機体の所有の等級(契約 agora-kinds.json node.status.observations.ownership.grade の閉語彙)と
#: 検の方法(proof)の綴り: gce-project:<project-id> = GCE の metadata server の project-id が一致 /
#: declared = 宣言のみ(検なし — 機体の所有の判定は別の座が持つ)。
OwnershipGrade = Literal["company", "personal"]
OWNERSHIP_GRADES: frozenset[OwnershipGrade] = frozenset({"company", "personal"})
OWNERSHIP_PROOF_GCE_PREFIX = "gce-project:"
OWNERSHIP_PROOF_DECLARED = "declared"

# ------------------------------------------------------------------ 起動の宣言(join・所有)


@dataclass(frozen=True)
class Ownership:
    """機体の所有の等級と、その検の方法(node の observations.ownership の写し)。"""

    grade: OwnershipGrade
    proof: str


@dataclass(frozen=True)
class JoinArgv:
    """`join` の subcommand の後の argv(境界の入力 — 判断は join.hy が読む)。"""

    items: tuple[str, ...]


@dataclass(frozen=True)
class JoinDeclaration:
    """宣言 file(toml)を読んだ木(境界の入力・tables が空 = file なし)。検めるのは join.hy。"""

    tables: dict[str, object]


@dataclass(frozen=True)
class JoinSpec:
    """`doeff-sessionhost join` の宣言(flag > toml > 既定 — join.hy join-spec-of の 1 点で組む)。
    None = 名乗らない(handler の既定に任せる・env に現れない)。"""

    server: str
    token_file: str
    node_name: str | None
    state_dir: str
    backend: str
    session_hooks: str
    custody_url: str | None
    borrower_key_file: str | None
    ownership: Ownership | None


@dataclass(frozen=True)
class JoinPlan:
    """宣言から導いた起動の形: host の argv と env の束(名と値の対の列・宣言の順)。"""

    host_argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ProbeAnswer:
    """OwnershipProbe の答え: 検の材料の値(gce-project = metadata の project-id)。None = 読めない。"""

    value: str | None


# ------------------------------------------------------------------ 値の宣言(1 点)


@dataclass(frozen=True)
class AgentdSettings:
    """agentd の値の宣言。既定値がここ 1 点、env からの上書きは runtime.py が行う。"""

    node_name: str
    principal: str = AGENTD_PRINCIPAL
    #: 参加の lease: heartbeat ごとに expiresAt = now + TTL を書き、周期は TTL / 3。
    node_lease_ttl_seconds: int = 90
    node_heartbeat_seconds: int = 30
    #: watch が沈黙している時の list の再同期(設計 17.4: 30〜60 秒に 1 回の保険)。
    watch_resync_seconds: float = 30.0
    #: 手番が走っていない時に watch を待つ上限(それを超えたら周期の仕事を回す)。
    idle_wait_seconds: float = 5.0
    #: 手番が走っている(frame の capture は止まっている)時の transcript の追記を読む周期。
    transcript_poll_seconds: float = 1.0
    #: frame の capture の間隔(2〜5 Hz の中・issue #1 の決定 4)。
    frame_interval_seconds: float = 0.4
    #: 購読 0 で capture を止めた後、購読者の数を読み直す周期(status frame の push で読む)。
    subscriber_recheck_seconds: float = 5.0
    #: capture する pane の行数。
    frame_lines: int = 60
    #: 貸与の錠の期限のこの秒数前に借り直す(錠の延長 = 同じ借り手の再要求)。
    lease_renew_margin_seconds: int = 120
    #: 借りた資格の家の根(claude = CLAUDE_CONFIG_DIR・codex = auth.json の置き場)。
    homes_root: str = ""
    #: host の backend(wire の閉語彙 tmux | herdr | headless の写し — agentd が読む語は
    #: BACKEND_HEADLESS だけ)。composition root(runtime.settings_from_env)が host の argv / env
    #: (valve.backend_of)から導く 1 点で、stream_capability も同じ源から導く。headless の器は
    #: 起こす手番の本文に inputs の郵便を畳む(judgment.first-turn-carries-inputs — R16)。
    backend_kind: str = "tmux"
    #: agentd が観測する自分の stream の capability — host の backend から導く(runtime.py の
    #: 1 点: headless = events・tmux / herdr = frames — judgment.stream-capability-of-backend)。
    stream_capability: StreamCapability = "frames"
    #: 温かい session(multi_turn)の idle の寿命: 手番の終わり(turn_ended_at)からこの秒数を
    #: 過ぎた session は agentd が session.cleanup で片付ける(判断は judgment の純関数・時計は
    #: effect・掃きは heartbeat の拍)。値の宣言はここ 1 点。
    session_idle_ttl_seconds: int = 600
    #: 機体の所有の等級と検の方法(段 6 lane 6f)。None = 名乗らない(observations に欄を書かない =
    #: 未観測)。composition root(runtime.settings_from_env)が env から読み、起動の前に
    #: join.ownership-preflight で検めた値だけがここに据わる(不一致 = 参加しない)。
    ownership: Ownership | None = None


# ------------------------------------------------------------------ ACP の値


@dataclass(frozen=True)
class AcpRow:
    """ACP の資源の 1 行(GET /api/resources の行を境界で検めた形)。"""

    namespace: str
    key: str
    kind: str
    resource_id: str
    version: str
    generation: int
    created_at_ms: int
    #: 書き手が所有する欄(status の書きは post-image を丸ごと運ぶので、写して返す)。
    labels: JSONObject
    payload: JSONObject
    spec: JSONObject
    status: JSONObject | None
    #: この revision が store に着地した engine の時計(wire の resourceLandedAt・ns 精度・
    #: None = 欄が無い古い行)。generation 1 の image(SpecApplied の post-image)では行の生まれ —
    #: 秒の粒度の resourceCreatedAt より正確な「郵便から agent まで」の始点(judgment.birth-ms-of)。
    landed_at_ms: int | None = None


@dataclass(frozen=True)
class Written:
    event_id: str


@dataclass(frozen=True)
class Conflict:
    """ifGeneration の前提が外れた(409)— 読み直して判断し直す合図。"""

    current_generation: int | None


@dataclass(frozen=True)
class Refused:
    """engine が書き(または push)を断った(400 / 403 / 404 / 413 / 503 …)。"""

    status: int
    error: str


WriteOutcome: TypeAlias = "Written | Conflict | Refused"


@dataclass(frozen=True)
class Pushed:
    seq: int
    #: いま stream を読んでいる購読者の数。段 2 lane 2a が push の応答に足す欄 — 応答に
    #: 無ければ None(古い中継。未知を 0 にも 1 にも倒さない)。
    subscribers: int | None


PushOutcome: TypeAlias = "Pushed | Refused"

WatchKind = Literal["changed", "gap", "idle", "closed"]

#: watch の拍にどう行を読み直すか(judgment.list-mode-for の閉語彙): full = 全量 list
#: (周期の保険・gap・接続の張り直し)/ window = 変わった行だけ(``GET /api/event-window`` の
#: post-image — watch で起きた拍)/ none = 読み直さない(idle)。
ListMode = Literal["full", "window", "none"]
LIST_MODE_FULL: ListMode = "full"
LIST_MODE_WINDOW: ListMode = "window"
LIST_MODE_NONE: ListMode = "none"
#: 1 回の event-window の読みの上限(engine の maxEventWindowLimit = 2000)。
EVENT_WINDOW_LIMIT = 2000


@dataclass(frozen=True)
class EventWindow:
    """``GET /api/event-window?after=&limit=`` の答え: (after, through] の event の post-image の行
    (同じ鍵は最後の image・retire は rows に無く retired に鍵)と、続きの cursor。
    ``complete`` = 窓を読めた(False = cursor が retention の床の下(409)か読めない — 全量 list へ)。
    ``exhausted`` = through が latest に届いた(False = まだ続きが在る — 次の窓)。"""

    complete: bool
    through: int
    latest: int
    rows: tuple[AcpRow, ...]
    retired: tuple[str, ...]
    #: 窓の中で生まれた行(generation 1 の image)の id → 着地の時刻(ms)。同じ鍵の後の image で
    #: rows から消えても生まれは残す(計器 agent-job-to-send の始点)。
    births: tuple[tuple[str, int], ...] = ()

    @property
    def exhausted(self) -> bool:
        return self.through >= self.latest


@dataclass(frozen=True)
class WatchAdvance:
    """watch(0c の SSE)の 1 回の待ちの答え。

    changed = sequence が進んだ / gap = 中継が続きを保証できない(list で再同期する合図)/
    idle = 待ちの上限まで何も来なかった / closed = 接続が切れた(handler が張り直す)。
    ``sequence`` は読み手が次に名乗る since。
    """

    kind: WatchKind
    sequence: int


# ------------------------------------------------------------------ custody の値


@dataclass(frozen=True)
class LeaseGrant:
    """預かり所が貸した札。``access_token`` は秘密 — log・簿・argv に出さない。"""

    lease_id: str
    kind: LeaseKind
    renewed: bool
    hold_expires_at_ms: int
    #: claude: env CLAUDE_CODE_OAUTH_TOKEN に入れる access token ちょうど。
    access_token: str | None
    #: codex: $CODEX_HOME/auth.json の中身ちょうど(JSON 文字列)。
    auth_json: str | None


@dataclass(frozen=True)
class LeaseRefused:
    status: int
    error: str
    #: 409(1 認証 1 宿)の時に預かり所が名乗る「いつまで一時か」。
    hold_expires_at_ms: int | None


LeaseOutcome: TypeAlias = "LeaseGrant | LeaseRefused"


# ------------------------------------------------------------------ session(器)の値


@dataclass(frozen=True)
class SessionView:
    """sessionhost の wire snapshot のうち agentd が読む欄。"""

    session_id: str
    agent_type: str
    status: str
    work_dir: str
    lifecycle: str
    conversation: dict[str, str] | None
    effective_identity: dict[str, str] | None
    result_payload: JSON
    terminal_cause: JSONObject | None
    #: 温かい session(multi_turn)で host の monitor が手番の終わりを最初に観測した時刻
    #: (wire の turn_ended_at・None = 手番の途中か run_to_completion / interactive)。
    turn_ended_at_ms: int | None
    #: 器の backend(wire の backend_kind: tmux | herdr | headless)と backend の参照
    #: (headless = {events_path, pid, argv} — 実況の材料の在処)。
    backend_kind: str = "tmux"
    backend_ref: JSONObject | None = None


@dataclass(frozen=True)
class SessionRefused:
    """器が launch / resume を断った(RPC の error)。"""

    error: str
    error_code: str | None


SessionOutcome: TypeAlias = "SessionView | SessionRefused"


@dataclass(frozen=True)
class TranscriptChunk:
    """transcript の file の ``offset`` から読んだ追記(text)と次の offset。"""

    text: str
    offset: int


@dataclass(frozen=True)
class CaptureFrame:
    """pane の断面(frame の材料)。"""

    text: str


@dataclass(frozen=True)
class CaptureGone:
    """pane も server も無い — 実況の終わりの合図であって例外ではない(ADR-DOE-AGENTS-012 R8)。

    片付いた session(run_to_completion の cleanup で pane が消え、唯一の window なら tmux の
    server も exit する)の capture を host が断った形。``reason`` は host の断りの文(log 用)。
    """

    reason: str


CaptureOutcome: TypeAlias = "CaptureFrame | CaptureGone"


# ------------------------------------------------------------------ 判断の値(judgment.hy が返す形)


@dataclass(frozen=True)
class LaunchPlan:
    """Bound の行から読み解いた「どう起こすか」— 判断ではなく行の欄の写し。"""

    charter: JSONObject
    predecessor: str | None
    lease_kind: LeaseKind | None
    account: str | None
    profile: str
    model: str


@dataclass(frozen=True)
class DeltaBatch:
    """transcript の行の列から組んだ TurnDelta の frame と turn-record の entries。"""

    frames: tuple[JSONObject, ...]
    entries: tuple[JSONObject, ...]
    usage: JSONObject | None
    next_seq: int
    model: str | None


@dataclass(frozen=True)
class JobOutcome:
    """器の眺め(SessionView)から読んだ手番の結末。ended = False なら残りの欄は空。"""

    ended: bool
    result: JSON
    conditions: tuple[JSONObject, ...]


# ------------------------------------------------------------------ agentd の状態


@dataclass(frozen=True)
class InFlightJob:
    """受けて走らせている 1 つの agent-job(agentd の memory の状態・ACP には無い)。"""

    job_key: str
    job_namespace: str
    job_id: str
    subject: str
    session_id: str
    agent_type: str
    node: str
    profile: str
    model: str
    started_ms: int
    #: この手番の始まりの下限(本文を送った時刻・拾い直しは行の createdAt)— 温かい session の
    #: 手番の終わりは、これより後に付いた turn_ended_at だけを読む(前の手番の終わりと区別)。
    turn_floor_ms: int
    #: 手番の始まりの transcript の offset(send / resume の時は前の手番の行を entries に混ぜない)。
    start_offset: int
    transcript_offset: int
    delta_seq: int
    lease_id: str | None
    lease_kind: LeaseKind | None
    lease_account: str | None
    lease_hold_ms: int | None
    #: frame の capture が生きているか(購読 0 で False・読み直しで True へ)。
    capturing: bool
    #: 実況が終わった(capture が gone を返した)— 以後 capture も購読の読み直しもせず、器の
    #: 終端を待って記録の腕へ進む。
    stream_gone: bool
    last_frame_ms: int
    last_probe_ms: int
    #: 手番の途中で判った事実(inputs の欠け等)— Ended の書きで conditions に足す。
    pending_conditions: tuple[JSONObject, ...]


@dataclass(frozen=True)
class AgentdState:
    since: int
    jobs: tuple[InFlightJob, ...]
    #: 知っている agent-job の行(鍵ごとの最新の image)— 全量 list で置き換え、watch の拍の
    #: event-window で差し替える cache。判断(bound-to-me・会話 → session・withdraw)はこの上で
    #: 行う。再起動で消えても最初の拍の全量 list で戻る(R7: 正本は行)。
    rows: tuple[AcpRow, ...]
    #: agent-job の id → 生まれの着地の時刻(generation 1 の image の landed_at_ms — 計器
    #: agent-job-to-send の始点)。欄が無い行は created_at_ms に落ちる(judgment.birth-ms-of)。
    births: tuple[tuple[str, int], ...]
    #: 行の cache が追いついている event の sequence(次の窓の after)。全量 list の後は
    #: その拍の since(list は since までの書きを含む)。
    last_window_seq: int
    #: None = まだ 1 度も(起動直後は即・その後は周期)。
    last_heartbeat_ms: int | None
    last_resync_ms: int | None
    node_missing_logged: bool
    #: 取り下げ(Withdrawn)を処理した job の id(行が list に残る間、同じ job に割り込みと
    #: 記録を撃ち直さないための cache — 再起動で消えても memory に無い job には撃たない)。
    retired: tuple[str, ...]
    #: claim を持ち越した job(会話の session が手番の途中)— log を 1 度にする cache。
    deferred: tuple[str, ...]


# ------------------------------------------------------------------ 要求(ACP)


@dataclass(frozen=True)
class AcpGet(EffectBase):
    """kind の生きた行をすべて読む(``GET /api/resources?kind=<kind>``)。結果 = tuple[AcpRow, ...]。"""

    kind: str


@dataclass(frozen=True)
class AcpGetRow(EffectBase):
    """1 行を鍵で読む(``GET /api/resources/<key>``)。結果 = AcpRow | None(404)。"""

    key: str


@dataclass(frozen=True)
class AcpPutStatus(EffectBase):
    """行の status を丸ごと書く(``POST /api/events`` の status_synced・ifGeneration = 行の generation)。

    status は committed の status を写して自分の欄だけ変えたもの(欄ごとの書き手の判定は
    変わった欄で行われる — 他人の欄を落とすと他人の欄の書きとして断られる)。
    結果 = WriteOutcome。
    """

    row: AcpRow
    status: JSONObject


@dataclass(frozen=True)
class AcpCreate(EffectBase):
    """行を作る(``POST /api/events`` の spec_applied・status は運ばない = engine が生まれの state を刻む)。

    結果 = WriteOutcome。
    """

    namespace: str
    kind: str
    resource_id: str
    spec: JSONObject


@dataclass(frozen=True)
class AcpEventWindow(EffectBase):
    """変わった行だけを読む(``GET /api/event-window?after=<since>&limit=``)。結果 = EventWindow。"""

    after: int
    limit: int


@dataclass(frozen=True)
class AcpWatchSse(EffectBase):
    """0c の SSE(``GET /api/watch/stream?since=``)を ``wait_seconds`` まで待つ。結果 = WatchAdvance。"""

    since: int
    wait_seconds: float


@dataclass(frozen=True)
class AcpStreamPush(EffectBase):
    """中継へ frame を push する(``POST /api/streams/{owner}/{name}``)。結果 = PushOutcome。"""

    owner: str
    name: str
    frames: tuple[JSONObject, ...]


# ------------------------------------------------------------------ 要求(custody)


@dataclass(frozen=True)
class CustodyLeaseBorrow(EffectBase):
    """預かり所から札を借りる(``POST /lease/<kind>``・身元は借り手札)。結果 = LeaseOutcome。"""

    kind: LeaseKind
    account: str
    purpose: str


@dataclass(frozen=True)
class CustodyLeaseRevoke(EffectBase):
    """借りた札を返す(``POST /lease/{id}/revoke``)。結果 = bool(返せた / 既に他の物か期限切れ)。"""

    lease_id: str


# ------------------------------------------------------------------ 要求(器 = sessionhost の RPC)


@dataclass(frozen=True)
class SessionLaunch(EffectBase):
    """``session.launch``(params = charter そのもの)。結果 = SessionOutcome。"""

    params: JSONObject


@dataclass(frozen=True)
class SessionResume(EffectBase):
    """``session.resume``(params = judgment.resume-params-of の形)。結果 = SessionOutcome。"""

    params: JSONObject


@dataclass(frozen=True)
class SessionSend(EffectBase):
    """``session.send``(本文を live の composer へ paste + Enter)。結果 = None。

    ``awaiting`` = 送った本文は agent への prompt で owed(host が awaiting latch を立て、正の
    作業証拠が出るまで見かけの turn-end を評価しない — 温かい手番の始まりの印)。
    """

    session_id: str
    text: str
    awaiting: bool


@dataclass(frozen=True)
class SessionGet(EffectBase):
    """``session.get``。結果 = SessionView | None(未登記)。"""

    session_id: str


@dataclass(frozen=True)
class SessionList(EffectBase):
    """``session.list``(lifecycle で絞る)。結果 = tuple[SessionView, ...]。"""

    lifecycle: str


@dataclass(frozen=True)
class SessionInterrupt(EffectBase):
    """``session.interrupt``(走っている手番だけを止める — headless = SIGINT / turn/interrupt・
    tmux = Escape。session は残す)。結果 = None。agora-redesign #37: withdraw は中断の合図。"""

    session_id: str


@dataclass(frozen=True)
class SessionCleanup(EffectBase):
    """``session.cleanup``(pane を消し、非終端なら stopped)。結果 = bool(host が受けたか)。"""

    session_id: str


@dataclass(frozen=True)
class SessionCapture(EffectBase):
    """``session.capture``(pane の断面 = frame の材料)。結果 = CaptureOutcome(断面 | gone)。"""

    session_id: str
    lines: int


@dataclass(frozen=True)
class SessionTranscript(EffectBase):
    """transcript の file を ``offset`` から読む。結果 = TranscriptChunk(不在は空文字と同じ offset)。"""

    path: str
    offset: int


@dataclass(frozen=True)
class SessionEvents(EffectBase):
    """headless の events file(host が stdout の行を 1 行 1 event で追記する実況の正本)を
    ``offset`` から読む。結果 = TranscriptChunk(完全な行だけ・不在は空文字と同じ offset)。"""

    path: str
    offset: int


# ------------------------------------------------------------------ 要求(時計・計器・file)


@dataclass(frozen=True)
class MintId(EffectBase):
    """session の id を鋳造する(ULID・26 字 Crockford base32 — 時刻と乱数は handler の I/O)。
    結果 = str。agentd が起こす session の id は charter(Messaging が組む launch の params)の
    session_id ではなくこれ(実弾 2026-09-12: charter の固定の id が idle TTL で片付いた後の
    launch で `session is already registered` に落ちた)。"""


@dataclass(frozen=True)
class ClockNowMs(EffectBase):
    """epoch ミリ秒(契約 conventions.time と同じ物差し)。結果 = int。"""


@dataclass(frozen=True)
class MetricLine(EffectBase):
    """計器の 1 行(stdout の JSON 行・後で p99 を出す)。結果 = None。"""

    fields: JSONObject


@dataclass(frozen=True)
class LogLine(EffectBase):
    """運用 log の 1 行(stderr)。結果 = None。"""

    text: str


@dataclass(frozen=True)
class FsCanonicalPath(EffectBase):
    """realpath(claude の transcript の家は canonical な work_dir で鍵づけられる)。結果 = str。"""

    path: str


@dataclass(frozen=True)
class FsFileSize(EffectBase):
    """file の大きさ(byte・不在は 0)— resume の手番の transcript の始まりの offset。結果 = int。"""

    path: str


@dataclass(frozen=True)
class FsWritePrivateText(EffectBase):
    """0600 の file を temp + rename で書く(codex の借りた auth.json の置き場 — 家の中の auth file)。結果 = None。"""

    path: str
    text: str


@dataclass(frozen=True)
class OwnershipProbe(EffectBase):
    """検の方法(proof)に従って機体の所有の証拠を読む(gce-project:<id> = GCE の metadata server の
    project-id)。結果 = ProbeAnswer(読めなければ value None — 判断は join.ownership-verdict)。"""

    proof: str
