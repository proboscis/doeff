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
#: 閉語彙は phase だけなので、agentd 側の語をここ 1 点で閉じる)。
ConditionType = Literal[
    "LaunchFailed", "CredentialUnavailable", "InputUnavailable", "SessionFailed"
]
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
#: 自分の Running の行の次の 1 手(judgment.job-step-of の閉語彙 — ADR-DOE-AGENTS-012 R7)。
#: observe = 器が走っている(行から InFlightJob を組んで観測を続ける)/ record-end = 器が終端
#: (記録の腕だけ: turn-record ended・result・phase Ended)/ fail-missing = 器に session が無い
#: (記録が在れば ended にし、condition SessionFailed で Ended)。
JobStep = Literal["observe", "record-end", "fail-missing"]
JOB_STEP_OBSERVE: JobStep = "observe"
JOB_STEP_RECORD_END: JobStep = "record-end"
JOB_STEP_FAIL_MISSING: JobStep = "fail-missing"
#: handler が値に写さない I/O の失敗(program の tick の縁 — job ごと・heartbeat・受け — が
#: 捕まえて log し、次の拍へ持ち越す型)。ACP の HTTP = RuntimeError、器の RPC = AgentdClientError
#: (RuntimeError の子)、socket / file = OSError。これより広い例外(bug)は runtime.run_loop の縁へ。
IO_FAILURES: tuple[type[Exception], ...] = (RuntimeError, OSError)

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
    #: agentd が観測する自分の stream の capability(tmux / herdr の pane = frames)。
    stream_capability: StreamCapability = "frames"


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
    #: 手番の始まりの transcript の offset(resume の時は前の手番の行を entries に混ぜない)。
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
    #: None = まだ 1 度も(起動直後は即・その後は周期)。
    last_heartbeat_ms: int | None
    last_resync_ms: int | None
    node_missing_logged: bool


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
    """``session.send``(本文を live の composer へ paste + Enter)。結果 = None。"""

    session_id: str
    text: str


@dataclass(frozen=True)
class SessionGet(EffectBase):
    """``session.get``。結果 = SessionView | None(未登記)。"""

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


# ------------------------------------------------------------------ 要求(時計・計器・file)


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
