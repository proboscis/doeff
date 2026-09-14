"""agentd の要求(effect)と値の型 — data だけで I/O を 1 つも行わない。

段 2(agora-redesign #19 / #20)の agentd は「ACP の cluster に参加して agent-job を受け、
session を起こし、手番の記録と実況を ACP へ書く」腕で、判断は持たない。ここはその腕が
世界に頼むこと(要求)と、頼んだ結果・判断の材料(値)の型の 1 点。

- 要求の語彙(依頼書 1.): ``AcpGet`` / ``AcpPutStatus`` / ``AcpCreate`` / ``AcpWatchSse`` /
  ``AcpStreamPush`` / ``CustodyLeaseBorrow``(+ 返却の ``CustodyLeaseRevoke``)。
  agentd 自身の器(sessionhost の RPC)への要求 = ``Session*``、時計・計器・file の
  読み書き = ``ClockNowMs`` / ``MetricLine`` / ``LogLine`` / ``FsCanonicalPath`` /
  ``FsWritePrivateText``、この機体が持つ資格の残量 = ``ReadProfileUsage``(段 7 lane 7d-3)、
  履歴からの再開の材料(段 8q・段 9q)= 郵便の読み ``AcpConversationMail`` と、記録の service が答えなかった時だけの
  手番の見出しの読み ``AcpTurnHeadlines``(薄い再開 — agora-redesign #77)。
- 会話の記録の service への二重書き(段 9f lane 9f-2)= ``RecordSpoolPut`` / ``RecordSpoolList`` /
  ``RecordSpoolRemove``(本文の batch の spool — 送る前の outbox)/ ``RecordSpoolGiveUp``(決まった断りの batch の隔離 —
  段 9f lane 9f-8)と ``RecordAppend``(契約 record-service.json の
  appendEvents)。履歴からの再開の本文の読み(段 9f lane 9f-4)= ``RecordRead``(readEvents の before=latest の 1 頁)。
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
PROFILE_KIND = "profile"
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

#: turn-record の status.entries(手番の出来事の列・append-only の event log — 段 8 lane 4u・agora-redesign #49)の
#: kind の閉語彙(契約 agora-kinds.json の kinds.turn-record … entries.items.kind の写し)。text = assistant の本文の
#: 1 block / tool_use・tool_result = 道具の呼び出しと結果 / frame = tui の最後の pane の断面 / system = 器の system の
#: 出来事(init・API の retry・hook の失敗)/ error = 手番が誤りで終わった(stream の result の is_error)。
EntryKind = Literal["text", "tool_use", "tool_result", "frame", "system", "error"]
ENTRY_KIND_TEXT: EntryKind = "text"
ENTRY_KIND_TOOL_USE: EntryKind = "tool_use"
ENTRY_KIND_TOOL_RESULT: EntryKind = "tool_result"
ENTRY_KIND_FRAME: EntryKind = "frame"
ENTRY_KIND_SYSTEM: EntryKind = "system"
ENTRY_KIND_ERROR: EntryKind = "error"
#: 行の上限(契約 conventions.turnRecordEntries.byteBudget の写し — 書き手の側の宣言点はここ)。1 行の entries の JSON
#: (UTF-8・compact)の上限 byte。超えたら古い見出しから落とし、先頭に印(TurnEntryDropMarker)を残す。段 9f lane 9f-4 で
#: entry は見出しだけになった(本文は会話の記録の service)— 切り詰めの規則(summary / text の上限)は agentd から消えた。
#: 段 9f lane 9f-5 便 2b(agora-redesign #59): 262,144 → 32,768。見出しだけの行(1 entry ≤ 256 byte)の上限へ、ACP の契約の締め
#: (便 2c — conventions.turnRecordEntries.byteBudget 32,768 と engine の statusByteBudget の門)より**先に**書き手が下げる(消費者が先)。
#: 超えた行は古い見出しから落として印を残す(本文は会話の記録の service に在るので失われない)。
TURN_RECORD_ENTRIES_BYTE_BUDGET = 32_768
#: 見出しの 1 entry の compact JSON の上限 byte(契約 §2.2「1 entry ≤ 256 byte」の写し — 検の物差し。走行時の門は ACP の
#: engine の statusByteBudget で、agentd は見出しに本文を持てない型で守る)。
TURN_ENTRY_MAX_BYTES = 256
#: turn-record の status.recordRef の綴り(`record:<cid>/<streamId>`)の頭。
RECORD_REF_PREFIX = "record:"
#: node の terminal state(gone の行は同じ名の生きた行ではない)。
NODE_GONE = "gone"
#: profile の terminal state(契約 agora-kinds.json profile.declaration.states — retired は観測しない)。
PROFILE_RETIRED = "retired"
#: profile の spec.budget.unit のうち agentd が残量を写せる単位(契約: status.observed.remaining は
#: budget と同じ単位で unit の欄は無い — provider の窓は percent なので percent の budget だけ)。
PROFILE_BUDGET_UNIT_PERCENT = "percent"
#: provider の窓の名(契約 profile.status.observed.window の綴り)と周期(秒)。窓の選び方は
#: judgment.observed-window-of の 1 点: spec.reset.everySeconds と一致する窓、無ければ既定 = 5h。
UsageWindowName = Literal["5h", "7d"]
USAGE_WINDOW_5H: UsageWindowName = "5h"
USAGE_WINDOW_7D: UsageWindowName = "7d"
USAGE_WINDOW_SECONDS: dict[UsageWindowName, int] = {"5h": 18000, "7d": 604800}
PROFILE_OBSERVED_WINDOW_DEFAULT: UsageWindowName = "5h"
#: 残量の 1 窓の満量(provider の窓は percent — remaining = 満量 - 使用)。
USAGE_WINDOW_FULL_PERCENT = 100.0
#: 中継の frame の capability(docs/contracts/turn-delta.json capability.values)。
StreamCapability = Literal["events", "frames", "none"]
STREAM_CAPABILITY_EVENTS: StreamCapability = "events"
STREAM_CAPABILITY_FRAMES: StreamCapability = "frames"
#: TurnDelta の種類(docs/contracts/turn-delta.json kinds)。
DeltaKind = Literal["text", "tool_use", "tool_result", "usage", "status", "frame"]
#: agent-job の conditions に agentd が書く type の語彙(AgentJob.hs は type を opaque に運ぶ —
#: 閉語彙は phase だけなので、agentd 側の語をここ 1 点で閉じる)。Interrupted = 取り下げ
#: (Withdrawn)で走っている手番を止めた(phase は書き手 = 作った側のまま)。
ConditionType = Literal[
    "LaunchFailed",
    "CredentialUnavailable",
    "InputUnavailable",
    "SessionFailed",
    "Interrupted",
    "RecordUnavailable",
    "CredentialSourceMissing",
    "AgentSettingIgnored",
    "SessionLost",
    "AgentdRestart",
]
CONDITION_INTERRUPTED: ConditionType = "Interrupted"
#: 段 10 lane 10h(agora-redesign #84): 走っている手番の session の backend(headless の子 process / tmux の pane)が
#: host の観測(SessionView.backend_alive)で死んでいた — 手番は終わらないので turn-record を ended・job をこの条件で Ended に
#: 閉じる(reason に session・pid・観測の時刻)。実弾 2026-09-14: agentd の再起動(kickstart -k)で子 process が道連れになり、
#: 行は running のままだったので会話が永久に「動いている」・次の郵便が Pending だった。
CONDITION_SESSION_LOST: ConditionType = "SessionLost"
#: 段 10 lane 10h 便 2: agentd の停止(TERM)の前に、走っている手番を黙って残さず閉じた — headless の子 process は host と共に
#: 降りる(pipe の子)ので、turn-record を ended・job をこの条件で Ended にする(reason に node・理由・session・時刻)。
CONDITION_AGENTD_RESTART: ConditionType = "AgentdRestart"
#: 段 9p(agora-redesign #76): 手番の記録(turn-record)の行を作れないまま手番が終わった — 頭が答えない拍
#: (入れ替え・到達不能)は期限まで再試行し、期限を越えた / 決定論的に断られた時だけ理由つきで立つ。
CONDITION_RECORD_UNAVAILABLE: ConditionType = "RecordUnavailable"
#: 段 10c(agora-redesign #80): 預かり所(custody)を宣言した node に status.binding.account の無い agent-job が結ばれた —
#: 手番の資格は預かり所の貸与ちょうどなので、起こさず(Running も sessionHandle も書かず)この条件で Ended に閉じる
#: (判断は judgment.credential-source-of の 1 点)。
CONDITION_CREDENTIAL_SOURCE_MISSING: ConditionType = "CredentialSourceMissing"
#: 段 10 lane 10e(agora-redesign #53・設計 第 9 節 問 3 / 問 4): 会話の宣言(charter の欄)のうち、この node の agent の種類が
#: 受けない欄・温かい session に送る手番では変えられない欄(workDir — cwd は起こした process のもの)を黙って落とさず、
#: 手番の終わりの conditions に 1 欄 1 行で刻む(判断は judgment.ignored-settings-of の 1 点)。
CONDITION_AGENT_SETTING_IGNORED: ConditionType = "AgentSettingIgnored"
#: 段 10 lane 10e: 会話の宣言の欄の閉語彙(ACP の契約 agora-kinds.json conventions.agentSettings.settings の写し)と、
#: agent の種類(charter.agent_type の語)ごとの能力の表 = 受ける欄(settings)と変えたら session を作り直す欄(restartOn)。
#: node の status.capabilities に名乗る(judgment.capabilities-of)。restartOn = session-affinity-key-of の鍵の欄ちょうど
#: (model・profile〔= account と binding の家〕)。effort は claude が `--effort`(2.1.270 の実物)・codex が
#: `-c model_reasoning_effort` で受け、process を起こし直せば同じ session のまま変えられる(session は作り直さない)。
#: workDir は起こす時の cwd(session を作り直さないが、温かい session へ送る手番では変えられない → AgentSettingIgnored)。
AgentSetting = Literal["model", "profile", "effort", "workDir"]
AGENT_SETTINGS: tuple[AgentSetting, ...] = ("model", "profile", "effort", "workDir")
AGENT_SETTINGS_RESTART_ON: tuple[AgentSetting, ...] = ("model", "profile")
#: 種類 → {settings, restartOn}(agentd が起こせる種類は launch の argv builder を持つ claude / codex の 2 つ)。
AGENT_CAPABILITIES: dict[str, dict[str, tuple[AgentSetting, ...]]] = {
    "claude": {"settings": AGENT_SETTINGS, "restartOn": AGENT_SETTINGS_RESTART_ON},
    "codex": {"settings": AGENT_SETTINGS, "restartOn": AGENT_SETTINGS_RESTART_ON},
}
#: node の status に能力の表を書く欄の名(契約 kinds.node.schema.properties.status.properties.capabilities・書き手 agentd)。
NODE_CAPABILITIES_KEY = "capabilities"
#: charter の欄 → 会話の宣言の欄の語(契約 conventions.agentSettings.settings)。profile は charter に無い(段 10c: 配置の係が
#: 預かり所の account に解く — binding.account が家)。
CHARTER_SETTING_KEYS: dict[str, AgentSetting] = {"model": "model", "effort": "effort", "work_dir": "workDir"}
#: 手番の資格の出所(段 10c・judgment.credential-source-of の閉語彙): lease = binding.account が在り charter の agent_type に
#: 貸与の種類がある(預かり所から借りる)/ missing = それが無く、この node は預かり所を宣言している(起こさない)/
#: home = それが無く、預かり所を宣言していない node(移行前の機体 — charter の binding で起こす今日の経路)。
TurnCredentialSource = Literal["lease", "home", "missing"]
CREDENTIAL_SOURCE_LEASE: TurnCredentialSource = "lease"
CREDENTIAL_SOURCE_HOME: TurnCredentialSource = "home"
CREDENTIAL_SOURCE_MISSING: TurnCredentialSource = "missing"
#: turn-record の行を作る腕の状態(judgment.record-create-applied の閉語彙): created = 行が在る(作れた・既に在った)/
#: pending = 頭が答えず作れていない(期限まで record_retry_seconds の周期で作り直す — 出来事は pending_entries に持ち越し)/
#: given-up = 期限を越えた・決定論的に断られた(condition RecordUnavailable を Ended に載せる・以後は作らない)。
RecordCreateState = Literal["created", "pending", "given-up"]
RECORD_CREATE_CREATED: RecordCreateState = "created"
RECORD_CREATE_PENDING: RecordCreateState = "pending"
RECORD_CREATE_GIVEN_UP: RecordCreateState = "given-up"
#: 段 8 lane 4x(agora-redesign #56): agent-job の status の割り込みの 2 欄(契約
#: docs/contracts/messaging.json interrupts — ACP Acp.App.Agent.AgentJob の綴りの写し)。
#: interrupts = Messaging が載せた、まだ渡していない Message の id の並び / interruptsDelivered =
#: この手番で agentd が CLI へ渡した id(append-only)。渡したら同じ 1 回の書きで前から消し後ろへ足す。
JOB_INTERRUPTS_KEY: str = "interrupts"
JOB_INTERRUPTS_DELIVERED_KEY: str = "interruptsDelivered"
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
#: profile の残量を読む資格の種類(段 7 lane 7d-3)。契約 profile の行は資格の種類を運ばず、本番の
#: 行(区画 default・35 行)はこの Mac の claude の profile なので、観測は claude の 1 種に閉じる
#: (codex の profile の行が立つ日に spec の欄と対で広げる)。値の宣言はここ 1 点。
PROFILE_USAGE_KIND: LeaseKind = "claude"
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
#: session-lost = 器の行は非終端だが backend が死んでいる(host の観測 SessionView.backend_alive = False — 段 10 lane 10h・
#: agora-redesign #84: 手番は終わらないので記録の腕を撃ち、job は condition SessionLost で Ended。session は host の monitor が
#: 終端に倒す — agentd は片付けない)。
JobStep = Literal["observe", "record-end", "fail-missing", "turn-end", "session-lost"]
JOB_STEP_OBSERVE: JobStep = "observe"
JOB_STEP_RECORD_END: JobStep = "record-end"
JOB_STEP_FAIL_MISSING: JobStep = "fail-missing"
JOB_STEP_TURN_END: JobStep = "turn-end"
JOB_STEP_SESSION_LOST: JobStep = "session-lost"
#: sessionhost の lifecycle の語のうち agentd が使うもの(launch.hy LIFECYCLE-* の写し)。
#: multi_turn = 温かい session(手番の終わりで片付けない — 同じ会話の次の手番は send)。
#: charter に lifecycle が無い時の agentd の既定(judgment.launch-lifecycle-of の 1 点)。
LIFECYCLE_MULTI_TURN = "multi_turn"
#: Bound の job の起こし方(judgment.next-arm-for-job の閉語彙 — ADR-DOE-AGENTS-012 R10 / R20)。
#: send = 会話の session が生きて idle ∧ 同じ家(温かい)/ resume = 会話の前の session がこの器に登記
#: されて終端 ∧ 同じ家(cache を保つ cold の --resume)/ rehydrate = 会話の前の session がこの器に無い
#: (別の機体・器の行が消えた)か家が違う(profile を変えた)か resume が断られた — cache の失効を受け入れ、
#: ACP の会話の記録を最初の本文に畳んで新しい session を起こす(段 8q・operator 決定 #54: cache を保つのは
#: 同じ機体 ∧ 同じ profile の家の時だけ)/ launch = 会話に前の session が無い / defer = 会話の session が
#: 手番の途中(claim せず次の list で読み直す — 走っている手番に本文を積まない)。
NextArm = Literal["launch", "send", "resume", "rehydrate", "defer"]
NEXT_ARM_LAUNCH: NextArm = "launch"
NEXT_ARM_SEND: NextArm = "send"
NEXT_ARM_RESUME: NextArm = "resume"
NEXT_ARM_REHYDRATE: NextArm = "rehydrate"
NEXT_ARM_DEFER: NextArm = "defer"
#: agentd が起こす session の launch_attribution(sessionhost が素通しで保存し wire の眺めに返す
#: opaque な帰属)の中で agentd が持つ欄の鍵(段 8q)。値 = {conversationId, agentJobId, account,
#: home, arm}: session の会話・手番・家は session の行に刻む事実で、終端の後に回収される agent-job の
#: 行から導かない(判断 = judgment.session-attribution-of / attribution-of-view の 2 点)。
ATTRIBUTION_AGENTD_KEY = "agentd"
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
#: 会話の記録の service(段 9f lane 9f-2・agora-redesign #59)の URL — 本文の二重書きの宛先(runtime.settings_from_env の
#: 1 点)。無ければ参加を断る(段 9f lane 9f-6・join.record-sink-of の 1 点 — 本文の行き先を持たない agentd は走らない)。
#: 札は ACP_TOKEN_FILE_ENV の再利用(名簿の agentd = service の書き手・契約 record-service.json auth.principals.writers)。
RECORD_URL_ENV = "RECORD_SERVICE_URL"
#: 本文の batch の spool(送る前の outbox)の置き場。join は state_dir の下(JOIN_RECORD_SPOOL_DIR)を導く。
RECORD_SPOOL_DIR_ENV = "DOEFF_AGENTD_RECORD_SPOOL_DIR"
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
JOIN_RECORD_SPOOL_DIR = "record-spool"
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
    #: 会話の記録の service の URL(段 9f lane 9f-2 — 宣言 file の [record].url・flag --record)。None = 二重書きなし。
    record_url: str | None = None


@dataclass(frozen=True)
class JoinPlan:
    """宣言から導いた起動の形: host の argv と env の束(名と値の対の列・宣言の順)。"""

    host_argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ProbeAnswer:
    """OwnershipProbe の答え: 検の材料の値(gce-project = metadata の project-id)。None = 読めない。"""

    value: str | None


# ------------------------------------------------------------------ profile の残量(段 7 lane 7d-3)


@dataclass(frozen=True)
class UsageWindow:
    """provider の 1 窓の使用(percent)と窓の戻る時刻(epoch ms・None = 窓は空で戻りの時刻が無い)。"""

    name: UsageWindowName
    used_percent: float
    resets_at_ms: int | None


@dataclass(frozen=True)
class ProfileUsage:
    """この機体が持つ 1 つの profile の残量の断面(読み口 = agentcli の usage の 1 点)。
    ``captured_at_ms`` = 断面を取った時刻(observedAt の材料)。窓が無い profile は windows が空。"""

    profile: str
    captured_at_ms: int
    windows: tuple[UsageWindow, ...]


@dataclass(frozen=True)
class ProfileUsageUnavailable:
    """残量を読めなかった profile: 会社境界の断り(agentcli の葉が判定 — agentd は第 2 の判定を
    持たない)・provider の失敗・断面の欠け。理由は人が読む 1 文(log に 1 行)。"""

    profile: str
    reason: str


ProfileUsageOutcome: TypeAlias = "ProfileUsage | ProfileUsageUnavailable"


@dataclass(frozen=True)
class ProfileHome:
    """登録簿の 1 つの profile と、この機体にその家(config dir)が在るか(段 8e lane 4j)。
    観測の材料で、判断(どの行を観測するか・usage を読むか)は judgment.profile-rows-held。
    ``present`` = 家の dir が実在する(中身は検めない — 残量の読みの葉が答える)。"""

    name: str
    home: str
    present: bool


@dataclass(frozen=True)
class ProfileObservation:
    """profile の行に書く status.observed(契約 {window, remaining, resetAt, observedAt, node})。"""

    observed: JSONObject


@dataclass(frozen=True)
class ProfileUnobserved:
    """この拍は書かない profile(断られた・単位が違う・窓の材料が無い)。理由は log に 1 行。"""

    reason: str


@dataclass(frozen=True)
class ProfileNotHeld:
    """この機体が資格を持たない profile(usage の列に無い)— 書かず、log もしない。"""


ProfileVerdict: TypeAlias = "ProfileObservation | ProfileUnobserved | ProfileNotHeld"


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
    #: frame の capture の間隔(2〜5 Hz の中・issue #1 の決定 4)— tui(tmux / herdr)の pane の断面の周期。
    frame_interval_seconds: float = 0.4
    #: headless の器(実況 = events file の行の増分)で購読者が居る間の、events の追記を読んで押す周期(段 8 lane 4aa・
    #: agora-redesign #63)。出来事ごとの push に最も近い有界の拍(≤ 50 ms の batch): file の追記は合図を持たないので
    #: 読みの拍がそのまま push の間隔になる。pane の capture の周期(frame_interval_seconds)とは別 — capture は
    #: 断面を撮る仕事で 2〜5 Hz が上限、events の読みは offset からの追記の読みで軽い。購読 0 の間は
    #: transcript_poll_seconds(記録の追記だけ)。⚠ 記録(turn-record)への追記の周期はこれに**追随しない**
    #: (transcript_poll_seconds のまま — InFlightJob.last_record_ms)。
    events_poll_seconds: float = 0.05
    #: 購読 0 で capture を止めた後、購読者の数を読み直す周期(status frame の push で読む)。
    subscriber_recheck_seconds: float = 5.0
    #: capture する pane の行数。
    frame_lines: int = 60
    #: 貸与の錠の期限のこの秒数前に借り直す(錠の延長 = 同じ借り手の再要求)。
    lease_renew_margin_seconds: int = 120
    #: 借りた資格の家の根(claude = CLAUDE_CONFIG_DIR・codex = auth.json の置き場)。
    homes_root: str = ""
    #: この node が預かり所(custody)を宣言しているか(段 10c・agora-redesign #80)。composition root
    #: (runtime.settings_from_env)が CUSTODY_URL_ENV(join の [custody].url / --custody)の在否から導く 1 点。True の node は
    #: status.binding.account の無い agent-job を起こさない(judgment.credential-source-of)— charter の binding
    #: (機体の profile の家)で起こす経路は、預かり所を宣言していない node(移行前の機体)だけに残る。
    custody_declared: bool = False
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
    #: この機体が持つ資格の profile の残量を読んで profile の status.observed に書く周期(段 7
    #: lane 7d-3 — heartbeat より遅い別の周期・値の宣言はここ 1 点)。同じ値を usage の読み口の
    #: cache の寿命にも渡す(1 周期より若い断面は読み直さない)。判断(窓・残量・post-image)は
    #: judgment の純関数、時計は effect、拍は agentd-tick の 1 つの腕。
    profile_observe_seconds: int = 300
    #: 履歴からの再開(段 8q)で最初の本文に畳む「これまでの会話」の上限(UTF-8 の byte)。超えたら古い手番から
    #: 要約せずに落とし、落とした数と全文の在処(ACP の会話の記録)を名乗る(judgment.rehydrate-history-of)。
    rehydrate_history_byte_budget: int = 65_536
    #: node の observations.transcripts に載せる件数の上限(段 8q — 終端の session のうち transcript が
    #: この機体に残るもの・会話ごとに最新の 1 つ・新しい順)。heartbeat ごとに node の行へ書くので小さく
    #: 保つ(契約の maxItems 64 以下)。
    transcripts_observed_max: int = 16
    #: 会話の記録の service への本文の二重書き(段 9f lane 9f-2・設計 §2.4)。composition root(runtime.settings_from_env)が
    #: RECORD_URL_ENV の在否から導く 1 点 — False の間 agentd は Record* の要求を 1 つも撃たない(ACP の追記は今日どおり)。
    #: 実運転では常に True(段 9f lane 9f-6: 宛先を持たない agentd は参加の門 join.record-sink-of が理由つきで断る —
    #: 本文の行き先が無いまま見出しだけを書く形は存在しない)。False は test の対照(二重書きの有無で見出しが一致する検)だけ。
    record_enabled: bool = False
    #: spool の再送の周期(送れなかった拍の後 — 送れている間は出来事を読んだ拍の終わりに送る)。届かない service へ拍ごとに
    #: 撃って loop を塞がないための有界の backoff(judgment.record-flush-due)。
    record_retry_seconds: float = 15.0
    #: 段 9p(agora-redesign #76): 手番の記録(turn-record)の行を作れない拍(頭の入れ替え・到達不能・5xx)に作り直しを
    #: 続ける上限(手番の始まりから・秒)。周期は record_retry_seconds(spool の再送と同じ弁)。期限を越えたら理由つきで
    #: condition RecordUnavailable(judgment.record-create-verdict の 1 点)。頭の入れ替え(image beat の再起動)の実測は
    #: 数十秒〜2 分なので、その数倍。
    turn_record_create_deadline_seconds: float = 300.0


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
    #: 起こした側が launch / resume の params で渡した帰属(wire の launch_attribution・host は解釈しない)。
    #: agentd が起こした session は ATTRIBUTION_AGENTD_KEY の欄に会話・手番・家を持つ(段 8q)。
    launch_attribution: JSONObject | None = None
    #: 器が session を起こした時刻(wire の started_at・epoch ms・None = 読めない)。
    started_at_ms: int | None = None
    #: 段 10 lane 10h(agora-redesign #84): 行の backend(headless の子 process / tmux の pane)が今 host で生きているかの
    #: host の観測(wire の backend_alive — session.get / session.list が毎回観測して載せる)。None = この眺めには観測が
    #: 無い(launch / resume の応答)— 観測の無さは死亡の証拠ではない(ADR-DOE-AGENTS-009: 観測断 ≠ 死亡)ので、判断
    #: (judgment.backend-alive)は明示の False だけを死と読む。
    backend_alive: bool | None = None


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
class ArmChoice:
    """Bound の job の起こし方(judgment.next-arm-for-job の答え — 判断はその 1 点)。"""

    arm: NextArm
    #: send の宛先 / resume の元の session(launch・rehydrate・defer は None)。
    source: str | None
    #: 起こす前に片付ける温かい session(生きて idle だが家が違う — cache は失効したので、同じ会話の器を
    #: 2 つ生かさない)。None = 片付けない。
    retire: str | None


@dataclass(frozen=True)
class HistoryFold:
    """履歴からの再開の「これまでの会話」(judgment.rehydrate-history-of の答え)。text = 最初の本文に畳む
    文(記録が無ければ空)・kept_turns / dropped_turns = 残した / 上限で落とした手番の数・
    dropped_items = 落とした出来事と郵便の数・size_bytes = text の UTF-8 の大きさ・thin = 本文が無い薄い再開
    (材料が HeadlineTurns — 記録の service に届かず ACP の見出しだけで組んだ)。"""

    text: str
    kept_turns: int
    dropped_turns: int
    dropped_items: int
    size_bytes: int
    thin: bool


# ------------------------------------------------------------------ turn-record の entry(見出し・段 9f lane 9f-4)


@dataclass(frozen=True)
class TurnEntryHeadline:
    """ACP の turn-record の status.entries の 1 item = **見出しの閉じた欄**(設計 §2.2 — claim check: control plane には
    参照と見出し・本文は会話の記録の service)。本文の欄(text / summary / input / output / model)はこの型に無い —
    本文を持つ entry は型で落ちる。seq = 本文の producerSeq(採番は 1 点)・bytes / sha256 = 本文の同一性(service が
    冪等の判断に使う値と同じ計算 — judgment.record-body-bytes-of)。"""

    seq: int
    at: int
    kind: EntryKind
    bytes: int
    sha256: str
    tool_name: str | None = None
    tool_use_id: str | None = None
    is_error: bool = False


@dataclass(frozen=True)
class TurnEntryDropMarker:
    """行の上限で古い見出しを落とした印(kind system・truncated・dropped — 列の先頭に 1 つ)。本文を持たないので
    bytes / sha256 も持たない。seq = 落とした最古の seq・at = 落とした最新の at。"""

    seq: int
    at: int
    dropped: int


TurnEntry: TypeAlias = "TurnEntryHeadline | TurnEntryDropMarker"


@dataclass(frozen=True)
class DeltaBatch:
    """transcript の行の列から組んだ TurnDelta の frame と turn-record の entries(見出し)。"""

    frames: tuple[JSONObject, ...]
    entries: tuple[TurnEntryHeadline, ...]
    usage: JSONObject | None
    next_seq: int
    model: str | None
    #: 段 9f lane 9f-2: 切る前の本文(契約 record-service eventIn の形・producerSeq = entries の seq)。entries(見出し)は
    #: ここから judgment.headline-of-body の 1 点で導く(本文は切らない — 切り詰めは service の責務)。
    bodies: tuple[JSONObject, ...] = ()


@dataclass(frozen=True)
class JobOutcome:
    """器の眺め(SessionView)から読んだ手番の結末。ended = False なら残りの欄は空。"""

    ended: bool
    result: JSON
    conditions: tuple[JSONObject, ...]


# ------------------------------------------------------------------ 会話の記録の service(段 9f lane 9f-2)

#: stream の種類(契約 record-service.json streamKinds の写し)。agentd が書くのは手番(turn)だけ。
RecordStreamKind = Literal["turn", "mail"]
RECORD_STREAM_TURN: RecordStreamKind = "turn"
#: 1 要求の出来事の上限(契約 limits.batchMaxEvents の写し)— 超える拍は batch を分ける(judgment.record-batches-of)。
RECORD_BATCH_MAX_EVENTS = 1_000
#: 追記の結末の語(計器 agentd_record_append_total の outcome)= spool の扱いの閉語彙 — 決めるのは judgment.record-append-word-of の
#: 1 点(段 9f lane 9f-8): ok = 受理(消す)/ conflict = 409(同じ鍵で違う本文 — 消す・赤)/ given-up = この batch だけの決まった
#: 断り(隔離して理由を名乗り、後ろの batch へ進む)/ error = 系の側の送れなさ(残して backoff・この拍の残りも撃たない)。
RecordAppendWord = Literal["ok", "conflict", "given-up", "error"]
RECORD_APPEND_OK: RecordAppendWord = "ok"
RECORD_APPEND_CONFLICT: RecordAppendWord = "conflict"
RECORD_APPEND_GIVEN_UP: RecordAppendWord = "given-up"
RECORD_APPEND_ERROR: RecordAppendWord = "error"
#: 計器の名(MetricLine の metric — stdout の JSON 行)。
METRIC_RECORD_APPEND_TOTAL = "agentd_record_append_total"
METRIC_RECORD_SPOOL_DEPTH = "agentd_record_spool_depth"
METRIC_RECORD_LAG_SEQ = "agentd_record_lag_seq"
#: この batch だけの決まった断り(契約 record-service.json: 400 malformed・422 unstorable — 撃ち直しても通らない)。札(401 / 403)・
#: 窓(429)・届かない・5xx は batch ではなく系の側(機体の設定か一時的)なので含めない — 残しておけば、設定を直した後に
#: 自動で送れる(judgment.record-append-word-of)。
RECORD_BATCH_REFUSAL_STATUSES: frozenset[int] = frozenset({400, 422})
#: 決まった断りの batch を隔離する spool の下の置き場(RecordSpoolList は読まない — 送る順から外れる)。
RECORD_SPOOL_GIVEN_UP_DIR = "given-up"


@dataclass(frozen=True)
class RecordStream:
    """本文の stream(契約 $defs.streamRef)。手番 = agent-job の id に拾い直しの番を含めた綴り(`<jobId>#a<attempt>`)。"""

    kind: RecordStreamKind
    stream_id: str
    started_at_ms: int
    node: str
    profile: str
    attempt: int


@dataclass(frozen=True)
class RecordBatch:
    """1 batch = spool の 1 file = appendEvents の 1 要求。events は契約 eventIn の形(judgment が本文から組む)。"""

    spool_key: str
    conversation_id: str
    stream: RecordStream
    events: tuple[JSONObject, ...]


@dataclass(frozen=True)
class RecordAppended:
    """2xx(appendAnswer)— 新しく積んだ / 既在で同じ本文だった producerSeq と、stream の最大の producerSeq。"""

    highest_producer_seq: int
    appended: tuple[int, ...]
    ignored: tuple[int, ...]


@dataclass(frozen=True)
class RecordConflicted:
    """409(conflictAnswer)— 同じ鍵で本文の sha256 が違う。batch は丸ごと積まれていない(再送しても積めない)。"""

    conflicts: tuple[JSONObject, ...]


@dataclass(frozen=True)
class RecordUnsent:
    """送れなかった / 積まれなかった(status 0 = 届かない・400 / 401 / 403 / 422 / 429 / 5xx)。spool の扱い(残して再送する か
    隔離する)は judgment.record-append-word-of が status から決める。"""

    status: int
    error: str


RecordAppendOutcome: TypeAlias = "RecordAppended | RecordConflicted | RecordUnsent"


@dataclass(frozen=True)
class RecordSpoolListing:
    """spool の中身(鍵の順 = 送る順)と、読めなかった file の名(消さずに残す)。"""

    batches: tuple[RecordBatch, ...]
    unreadable: tuple[str, ...]


#: 読みの 1 頁の上限(契約 limits.pageMaxLimit の写し)。履歴からの再開はこの大きさで後向きに読む。
RECORD_PAGE_MAX_LIMIT = 1_000

#: 段 10f 便 1b(agora-redesign #82): 郵便の本文を記録の service に置いた時の出来事の kind(契約 record-service.json の
#: eventKinds の message・1 郵便 = 1 出来事・stream = 郵便の id)。
RECORD_MAIL_EVENT_KIND = "message"


@dataclass(frozen=True)
class RecordEvent:
    """service に積まれた出来事の 1 つ(契約 $defs.storedEvent — 読みの答え・最新の版)。本文の欄は service が切った後の
    値(truncated が真なら bytes が切る前の大きさ)。tombstone の行は本文の欄を持たない。"""

    record_seq: int
    stream_id: str
    stream_kind: RecordStreamKind
    producer_seq: int
    at: int
    kind: str
    bytes: int
    sha256: str
    text: str | None = None
    summary: str | None = None
    input: JSON = None
    output: JSON = None
    tool_name: str | None = None
    tool_use_id: str | None = None
    model: str | None = None
    is_error: bool = False
    truncated: bool = False


@dataclass(frozen=True)
class RecordPage:
    """readEvents の 1 頁(常に recordSeq 昇順)。next = 次の頁の cursor(before の読みでは列の最初の recordSeq・None =
    会話の最初まで読めた)。"""

    events: tuple[RecordEvent, ...]
    next: int | None


@dataclass(frozen=True)
class RecordUnread:
    """読めなかった(status 0 = 届かない・4xx / 5xx・答えの形が契約と違う)。"""

    status: int
    error: str


RecordReadOutcome: TypeAlias = "RecordPage | RecordUnread"


@dataclass(frozen=True)
class RecordedTurns:
    """履歴からの再開の手番の材料 = 会話の記録の service から読んだ本文(設計 §2.4 — before=latest から後向きに、
    上限の byte に届くまで)。complete = 会話の最初まで読めた。"""

    events: tuple[RecordEvent, ...]
    complete: bool


@dataclass(frozen=True)
class HeadlineTurns:
    """履歴からの再開の手番の材料 = ACP の turn-record の行(見出しだけ・本文なし)— 記録の service に届かなかった
    (か配線されていない)時の**薄い再開**の材料。本文の無い行を本文として扱わない(型で分ける)。reason = 届かなかった理由。"""

    records: tuple[AcpRow, ...]
    reason: str


HistorySource: TypeAlias = "RecordedTurns | HeadlineTurns"


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
    #: 手番の記録(turn-record)の行の最後に知った image(段 8 lane 4u — 出来事の追記の CAS の相手)。
    #: None = まだ読んでいない(最初の追記で鍵から読む)。書けた拍に generation + 1 と書いた status で
    #: 差し替え、Conflict は読み直して積み直す。正本は行(R7)— 再起動で消えても鍵から戻る。
    record: AcpRow | None = None
    #: 読んだが行へまだ書けていない出来事(書きが断られた / 行がまだ無い拍の持ち越し)。次の拍の
    #: 追記と手番の終わりの書きに先頭で乗る(出来事は落とさない・順は seq)。
    pending_entries: tuple[TurnEntryHeadline, ...] = ()
    #: 段 8 lane 4x: この手番で器へ渡した割り込みの Message の id(memory の写し — 行の
    #: interruptsDelivered への CAS が着地するまでの間、同じ id を二度渡さないための cache。正本は行:
    #: 再起動で消えても、行の interruptsDelivered に在る id は渡さない)。
    interrupts_sent: tuple[str, ...] = ()
    #: 段 8 lane 4aa: 手番の記録(turn-record)へ最後に出来事を追記した拍(ms・0 = まだ)。実況の push は events の周期
    #: (≤ 50 ms)で押すが、記録の追記(CAS の書き = ACP の event 1 つ)は transcript_poll_seconds の周期に保つ — 拍ごとに書くと
    #: 走っている手番 1 つで毎秒 10〜20 の event が journal に並び、画面の糊の watch の拍(1 event = 1 拍)が飽和する
    #: (実弾 2026-09-13 18:3x: 糊の受け口の占有 367 拍中 359 が 200〜500 ms・hello 15 s)。書かない拍の出来事は pending_entries
    #: に持ち越す(落とさない・手番の終わりは残りを同じ点で書く)。
    last_record_ms: int = 0
    #: 段 9f lane 9f-2: 本文の stream の拾い直しの番(stream id `<jobId>#a<attempt>`)。受けた手番 = 1、拾い直し(recover-job)=
    #: その時の turn-record の行の generation + 1(judgment.recovered-record-of — 行が進むごとに単調・ACP に新しい欄を書かない)。
    #: 採番(producerSeq)は delta_seq の 1 点のまま — service の producerSeq と ACP の見出しの seq は同じ値。
    record_attempt: int = 1
    #: 段 9p(agora-redesign #76): turn-record の行を作る腕の状態(閉語彙 RecordCreateState)。受けた手番は after-start の
    #: create の結末から(頭が答えなければ pending)、拾い直し(adopt)は行が在るので created。pending の間は observe の拍が
    #: record_retry_seconds の周期で作り直し(judgment.record-create-due)、手番の終わりは周期に依らず最後に 1 度作り直す
    #: — 記録なしで終わらない。期限を越えたら given-up(condition RecordUnavailable・理由 = 最後の断り)。
    record_create: RecordCreateState = "created"
    #: 最後に create を撃った拍(ms・0 = まだ — pending の周期の基準)。
    record_create_last_ms: int = 0
    #: 最後の断りの文(given-up の condition の理由に写す)。
    record_create_refusal: str = ""


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
    #: profile の残量の観測(段 7 lane 7d-3)の最後の拍。None = まだ 1 度も(起動直後は即・その後は
    #: AgentdSettings.profile_observe_seconds の周期)。
    last_profile_observed_ms: int | None = None
    #: 「この機体に家の在る profile が 1 つも無い」を 1 度だけ名乗った印(段 8e lane 4j — pool の
    #: pod は profile を持たないので usage を読まず、周期ごとに同じ行を吐かない)。家が現れたら戻る。
    no_profile_homes_logged: bool = False
    #: 段 9f lane 9f-2: spool の再送を止めている拍(最後に送れなかった ms・None = 送れている — 毎拍 flush する)。
    record_backoff_ms: int | None = None
    #: 最後に計器へ出した spool の深さ(None = まだ — 変わった時だけ agentd_record_spool_depth を出す)。
    record_spool_depth: int | None = None


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
class AcpConversationMail(EffectBase):
    """会話の郵便を読む(段 8q の履歴からの再開 — 手番を起こし直す時の 1 回だけ): kind message の行
    (``GET /api/resources?kind=message``・ACP に欄の絞りの口は無いので kind の全量 — ``conversation_id`` での
    絞りは judgment.rehydrate-history-of)。結果 = tuple[AcpRow, ...]。手番の本文はここでは読まない(本文は
    記録の service = RecordRead・見出しは AcpTurnHeadlines)。watch の拍では撃たない(郵便の本文は鍵で
    1 行ずつ — R14)。"""

    conversation_id: str


@dataclass(frozen=True)
class AcpTurnHeadlines(EffectBase):
    """会話の手番の見出し(kind turn-record の行)を読む — **薄い再開の拍だけ**(記録の service が配線されて
    いない・答えなかった時 — 段 9q・agora-redesign #77)。``GET /api/resources?kind=turn-record`` は kind の全量
    (実測 2026-09-14: 29,913 行・172 MB・頭の応答 59 秒)なので、service が答えた拍には撃たない — 撃つと
    claim の後の手番の準備が 2 分を超え、node の lease(TTL 90 秒)が切れて Scheduling が Running の行を
    Pending に戻す。結果 = tuple[AcpRow, ...](``conversation_id`` での絞りは judgment.rehydrate-history-of)。"""

    conversation_id: str


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


# ------------------------------------------------------------------ 要求(会話の記録の service・段 9f lane 9f-2)


@dataclass(frozen=True)
class RecordSpoolPut(EffectBase):
    """batch を spool に耐久化する(1 batch 1 file・temp + fsync + rename — 送る前の outbox)。結果 = None。"""

    batch: RecordBatch


@dataclass(frozen=True)
class RecordSpoolList(EffectBase):
    """spool の batch を鍵の順に読む。結果 = RecordSpoolListing。"""


@dataclass(frozen=True)
class RecordSpoolRemove(EffectBase):
    """受理された(か 409 で積めないと決まった)batch の file を消す(無ければ何もしない)。結果 = None。"""

    spool_key: str


@dataclass(frozen=True)
class RecordSpoolGiveUp(EffectBase):
    """決まった断り(RECORD_BATCH_REFUSAL_STATUSES)の batch を送る順から外して隔離する(段 9f lane 9f-8): file を spool の下の
    RECORD_SPOOL_GIVEN_UP_DIR へ移し、理由を隣に書く(本文は消さない — service が直った後に人が戻せる)。結果 = None。"""

    spool_key: str
    reason: str


@dataclass(frozen=True)
class RecordAppend(EffectBase):
    """batch を会話の記録の service へ追記する(契約 appendEvents: ``POST /v1/conversations/{cid}/streams/{streamId}/events``・
    Bearer = 名簿の agentd の札)。冪等 — 同じ鍵と本文の再送は ignored。結果 = RecordAppendOutcome(送れなさも値で返す)。"""

    batch: RecordBatch


@dataclass(frozen=True)
class RecordRead(EffectBase):
    """会話の出来事を後向きに 1 頁読む(契約 readEvents: ``GET /v1/conversations/{cid}/events?before=<latest|recordSeq>&limit=``・
    読み手 = 名簿の agentd)。before = None は latest(末尾の頁)。結果 = RecordReadOutcome(読めなさも値で返す)。
    履歴からの再開(段 9f lane 9f-4・設計 §2.4)の 1 回だけ撃つ — watch の拍では撃たない。"""

    conversation_id: str
    before: int | None
    limit: int


@dataclass(frozen=True)
class RecordReadStream(EffectBase):
    """段 10f 便 1b(agora-redesign #82): 郵便 1 通の本文を読む(契約 readStreamEvents:
    ``GET /v1/conversations/{cid}/streams/{streamId}/events?since=0&limit=``・読み手 = 名簿の agentd)。
    会話と stream は郵便の spec.bodyRef ちょうど(1 郵便 = 1 出来事 kind message)。結果 = RecordReadOutcome
    (読めなさも値で返す)。郵便の本文が行に無い時だけ撃つ。"""

    conversation_id: str
    stream_id: str


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
class Interjected:
    """器が割り込みの本文を走っている手番へ引き受けた(段 8 lane 4x)。"""


@dataclass(frozen=True)
class SessionInterject(EffectBase):
    """``session.send`` の mode = interrupt(段 8 lane 4x・agora-redesign #56): 割り込みの本文を
    走っている手番へ即座に(CLI の支える形 — claude は stream-json の stdin の user の行、codex は
    turn/interrupt → 同じ thread へ turn/start)。結果 = Interjected | SessionRefused(host の断り =
    走っている手番が無い・器が無い — 本文は届いていない。呼び手は行の interrupts に残し、Messaging が
    queued へ積み直す)。socket の失敗(OSError)は素通し(tick の縁が持ち越す)。
    """

    session_id: str
    text: str


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


@dataclass(frozen=True)
class ListProfileHomes(EffectBase):
    """この機体が持つ資格(kind)の profile の家の在否を読む(段 8e lane 4j)。読み口は dotfiles
    agentcli の登録簿の 1 点(handlers.py の PROFILES_COMMAND = `agentcli profiles list --json` —
    ADR-DOTFILES-005 R2 の単一の正)と、その家(dir)の実在。結果 = tuple[ProfileHome, ...]
    (登録簿の全 profile・家の無いものは present False)。家が 1 つも無い機体(pool の pod —
    personal の資格は預かり所が観測し、会社 profile は会社機体だけ)では usage を読まない
    (判断は judgment.profile-rows-held・agentd は usage の読み口の落ち方で判じない)。"""

    kind: LeaseKind


@dataclass(frozen=True)
class ReadProfileUsage(EffectBase):
    """この機体が持つ資格(kind)の profile ごとの残量を読む(段 7 lane 7d-3)。読み口は dotfiles
    agentcli の usage の 1 点(handlers.py の USAGE_COMMAND = `ai usage --json`)で、会社境界(会社
    profile の API 呼び出しは会社機体だけ)はその葉が判定する — 断られた profile は
    ProfileUsageUnavailable で返り、agentd は書かない。``cache_ttl_seconds`` より若い断面は読み直さない。
    結果 = tuple[ProfileUsageOutcome, ...](この機体に無い profile は列に無い)。"""

    kind: LeaseKind
    cache_ttl_seconds: int
