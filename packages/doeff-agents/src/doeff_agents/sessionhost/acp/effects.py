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
from dataclasses import dataclass, field
from typing import Literal, NamedTuple, TypeAlias, get_args

from doeff import EffectBase
from doeff_agents.sessionhost.attachment import TurnAttachment
from doeff_agents.sessionhost.drivers import DRIVER_EXECUTABLE

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
#: 段 10f 便 2(agora-redesign #82): 会話の行(status.agent.compactAt = 文脈の使用率の閾値・agentd は読むだけ)。
CONVERSATION_KIND = "conversation"
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
#: 段 12(agora-redesign #537 便 1): 走っている手番の記録だけを引く field selector(engine の status 軸の絞り — 1 回の
#: 読みに条件 1 つ)。綴りはここ 1 点で、実 I/O(handlers)と検の器(fake)が同じ語を読む。
TURN_RECORD_RUNNING_SELECTOR = f"status.state={TURN_RECORD_RUNNING}"

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
#: 手番の**出力**の見出し(model が本文のために書いた・撃った・受けた)— 依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB(D1): これが 0 本で
#: usage も無い温かい手番は completed を名乗らない(判断は judgment.turn-output-condition-of の 1 点)。system / error /
#: frame は器・走行器の見出しで、model の出力ではない。
TURN_OUTPUT_ENTRY_KINDS: tuple[EntryKind, ...] = (
    ENTRY_KIND_TEXT,
    ENTRY_KIND_TOOL_USE,
    ENTRY_KIND_TOOL_RESULT,
)
#: 行の上限(契約 conventions.turnRecordEntries.byteBudget の写し — 書き手の側の宣言点はここ)。1 行の entries の JSON
#: (UTF-8・compact)の上限 byte。超えたら古い見出しから落とし、先頭に印(TurnEntryDropMarker)を残す。段 9f lane 9f-4 で
#: entry は見出しだけになった(本文は会話の記録の service)— 切り詰めの規則(summary / text の上限)は agentd から消えた。
#: 段 9f lane 9f-5 便 2b(agora-redesign #59): 262,144 → 32,768。見出しだけの行(1 entry ≤ 256 byte)の上限へ、ACP の契約の締め
#: (便 2c — conventions.turnRecordEntries.byteBudget 32,768 と engine の statusByteBudget の門)より**先に**書き手が下げる(消費者が先)。
#: 超えた行は古い見出しから落として印を残す(本文は会話の記録の service に在るので失われない)。
#: 2026-09-21(card acp:kanban-issue:ki-c418e597017a 便 3a): 32,768 → 4,096。ACP の記録簿(acp-pg)が 1 日 5.8 GiB 育ち、
#: byte の 43% が turn-record だった。根 = **1 行の上限は在るが 1 手番に何回書くかに上限が無い**: 追記は行の entries を
#: 読んで足して**全体を書き戻す**(CAS)ので、journal には追記のたびに配列の全体が後像として入る(1 本の追跡 = 2 分 19 秒で
#: 56 回・2 B → 9,883 B へ単調増加・標本全体で書いた 15.2 MB が表す最終状態は 0.86 MB = 17.7 倍・最悪 38.2 倍)。
#: 上限を 8 分の 1 にすると平均の書き込み量が約 8 分の 1 になる。読み手は変わらない: 古い見出しは印(dropped)と
#: entriesTruncated(firstKeptSeq / recordRef)が「それより古いのは記録の service で読む」と名乗る仕組みが既に在る
#: (claim check — 全史は service・行は上限つきの要約)。前例と同じ順で、ACP の契約の締め(conventions.turnRecordEntries.
#: byteBudget と statusByteBudget = byteBudget + 4,096)より**先に**書き手が下げる(消費者が先)— 書き手の上限が契約より
#: 小さいのは常に安全で、大きいのは engine が 400 で断る側。
TURN_RECORD_ENTRIES_BYTE_BUDGET = 4_096
#: 応答の列の上限(契約 conventions.turnRecordResponses.byteBudget の写し — card acp:kanban-issue:ki-c3ac5832a0bd)。
#: turn-record の status.responses の compact JSON(UTF-8)の上限 byte。items は古い順に先頭から足し、超える手前で止めて
#: dropped が切った数を名乗る(1 本目は必ず残る — response_usage.responses-status-of の 1 点)。書くのは手番の終わりの書きだけ
#: なので、走っている間の追記の書き(journal の後像)は増やさない。
TURN_RECORD_RESPONSES_BYTE_BUDGET = 16_384
#: 見出しの 1 entry の compact JSON の上限 byte(契約 §2.2「1 entry ≤ 256 byte」の写し — 検の物差し。走行時の門は ACP の
#: engine の statusByteBudget で、agentd は見出しに本文を持てない型で守る)。
TURN_ENTRY_MAX_BYTES = 256
#: 実況(TurnDelta)の道具の呼び出しに載せる入力の切り方(契約 docs/contracts/turn-delta.json の tool_use.input /
#: clipped — 段 10 lane 10j・agora-redesign #87 の裁定 問 7)。入力の object はそのまま載せ(表示のための whitelist は
#: 使わない)、文字列は 1 つこの上限で切る(入れ子の中も同じ規則)。agora の画面の糊が記録の行を切る上限と同じ値。
DELTA_INPUT_STRING_LIMIT = 64_000
#: 実況の 1 frame の compact JSON(UTF-8)の上限 byte。越えた frame は input を載せず、切った印だけを名乗る
#: (中継の 1 frame の上限は 4 MiB〔stream-relay.json ringBytes〕だが、記録の 1 出来事の上限〔record-service.json
#: eventMaxBytes〕と同じ値で締める — 実況と記録で道具の入力の大きさをそろえる)。
DELTA_FRAME_MAX_BYTES = 1_048_576
#: 切った所の path の根(契約の clipped の綴り — 例 "input.old_string"・input ごと落ちた時は "input" ちょうど)。
DELTA_CLIPPED_INPUT_ROOT = "input"
#: turn-record の status.recordRef の綴り(`record:<cid>/<streamId>`)の頭。
RECORD_REF_PREFIX = "record:"
#: node の state(契約 agora-kinds.json node.declaration.states = joined / gone): joined = 生きている行(契約 scheduling.json
#: liveRow.nodeRow の alive)・gone = terminal(同じ名の生きた行ではない)。
NODE_JOINED = "joined"
NODE_GONE = "gone"
#: profile の terminal state(契約 agora-kinds.json profile.declaration.states — retired は観測しない)。
PROFILE_RETIRED = "retired"
#: profile の spec.budget.unit のうち agentd が残量を写せる単位(契約: status.observed.remaining は
#: budget と同じ単位で unit の欄は無い — provider の窓は percent なので percent の budget だけ)。
PROFILE_BUDGET_UNIT_PERCENT = "percent"
#: profile の status の欄の綴り(契約 agora-kinds.json kinds.profile.schema … status): observed = 残量の観測の**最新の 1 枡**
#: (node をまたぐ)・observedBy = **node ごとの枡**(鍵 = 観測した node の名・値は observed と同じ形)。段 12 lane 12j
#: (agora-redesign #351・依頼者の裁定 2026-09-16 (B)): 同じ口座の家を持つ機体が 2 台(会社 Mac と mbp)在ると observed の 1 枡を
#: 毎周期書き合い、node の名は数秒で消え generation だけが進んだ。以後 agentd は自分の枡を毎周期書き、最新の 1 枡は値が
#: 変わった時か古い時だけ置き換える(judgment.profile-latest-should-replace の 1 点)。
PROFILE_STATUS_OBSERVED_KEY = "observed"
PROFILE_STATUS_OBSERVED_BY_KEY = "observedBy"
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
#: TurnDelta の種類(docs/contracts/turn-delta.json kinds)。tool_input_delta = 道具の呼び出しの書きかけの引数の続き
#: (2026-09-19・card acp:kanban-issue:ki-0d0bcd1e81d9 — 完成した呼び出しは tool_use のまま)。
DeltaKind = Literal["text", "tool_use", "tool_input_delta", "tool_result", "usage", "status", "frame"]
#: agent-job の conditions に agentd が書く type の語彙(AgentJob.hs は type を opaque に運ぶ —
#: 閉語彙は phase だけなので、agentd 側の語をここ 1 点で閉じる)。Interrupted = 取り下げ
#: (Withdrawn)で走っている手番を止めた(phase は書き手 = 作った側のまま)。
ConditionType = Literal[
    "LaunchFailed",
    "CredentialUnavailable",
    "InputUnavailable",
    "InputUndelivered",
    "SessionFailed",
    "Interrupted",
    "RecordUnavailable",
    "CredentialSourceMissing",
    "CredentialPlaceMismatch",
    "PlaceMismatch",
    "AgentKindUnavailable",
    "AgentSettingIgnored",
    "AgentMemoryUnwritable",
    "SessionLost",
    "AgentdRestart",
    "InterruptEscalationUndeclared",
    "AttachmentIgnored",
    "WorkDirMissing",
    "ProviderLimit",
    "CredentialLeaseHeld",
    "CredentialNotLeasable",
    "TurnProducedNothing",
    "TurnOutputUnmeasured",
    "VerifyScriptMissing",
    "VerifyStartFailed",
    "VerifyCommandLost",
    "VerifyDeadlineExceeded",
    "SummarizePlanInvalid",
    "SummarizeRegionUnreadable",
    "SummarizeStartFailed",
    "SummarizeCommandLost",
    "SummarizeDeadlineExceeded",
    "SummarizeOutputUnreadable",
    "SummaryUnwritable",
]
CONDITION_INTERRUPTED: ConditionType = "Interrupted"
#: 段 10 lane 10n(agora-redesign #93・依頼者の追補 2026-09-14): 割り込みを注入したが、この job の charter に
#: interruptEscalationSeconds(期限 — 方策の行の値を Messaging が会話の宣言で重ねて charter に写す)が無いので、
#: 注入だけにして期限つきの停止の合図は出さない。agentd は charter の値だけを読み、code に既定を置かない。
CONDITION_INTERRUPT_ESCALATION_UNDECLARED: ConditionType = "InterruptEscalationUndeclared"
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
#: 口座の置き場(profile の行の spec.boundary)が自分の置き場の集合(spec.places)に無い job を起こさなかった印
#: (段 10 lane 10d 便 2・agora-redesign #85 の I5・段 11 lane 11u・#224 で集合へ — 判断は judgment.credential-place-mismatch の 1 点)。
CONDITION_CREDENTIAL_PLACE_MISMATCH: ConditionType = "CredentialPlaceMismatch"
#: charter が要求する置き場(spec.charter.place)が自分の置き場の集合(spec.places)に無い job を起こさなかった印
#: (段 12・card acp:kanban-issue:ki-d13566f4d5eb の決定 案 A — 判断は judgment.place-mismatch の 1 点)。
#: 口座の置き場の門(CredentialPlaceMismatch)とは別の軸: あちらは**資格**がその宿の外へ出るか、こちらは
#: 宿が**道具**を持つか。配置(ACP Scheduling.Decide.nodeServesPlace)が同じ要求を判じるが、配置の版が古い・
#: 手で結んだ拍にも実際の宿が名乗るための走行側の門(第 2 の方策点ではない — 要求の座は方策の 1 欄)。
CONDITION_PLACE_MISMATCH: ConditionType = "PlaceMismatch"
#: ADR-DOE-AGENTS-012 R61(card acp:kanban-issue:ki-f250d67a7157): job の agent の種類(手番 = charter.agent_type・
#: 要約 = agentd 自身が起こす claude)を、この node がいま申告していない(AgentdState.agent_kinds に無い — その種類の
#: 実行ファイルが起動する process の実効 env で見つからない)ので起こさなかった印。綴りは ACP の対の条件と同じ
#: (ACP 304a083d が carrierEndedFailureReasons に足した — この条件で閉じた job の郵便は配置が別の node へ回す)。
#: 判断は judgment.agent-kind-refusal-of の 1 点。
CONDITION_AGENT_KIND_UNAVAILABLE: ConditionType = "AgentKindUnavailable"
#: 段 10 lane 10y(agora-redesign #110・依頼者の裁定 2026-09-15 案 A): 手番の work_dir(家からの相対 `~/…` は node の HOME で展開した後)が
#: この node に無く、charter が scratch の印(CHARTER_WORK_DIR_SCRATCH_KEY = true)を持たない job を起こさなかった印。配車の係は
#: この条件を「会話 × node」で読み、同じ会話の手番の候補からこの node を外す(ACP 側・lane 10d)。判断は judgment.work-dir-step-of の 1 点。
#: 実弾 2026-09-15 02:54: charter.work_dir = /Users/s22625/.cache/acp-stage2-e2e/work(会社 Mac の絶対 path)の手番が proboscis-mbp で LaunchFailed。
CONDITION_WORK_DIR_MISSING: ConditionType = "WorkDirMissing"
#: 段 10 lane 10e(agora-redesign #53・設計 第 9 節 問 3 / 問 4): 会話の宣言(charter の欄)のうち、この node の agent の種類が
#: 受けない欄・温かい session に送る手番では変えられない欄(workDir — cwd は起こした process のもの)を黙って落とさず、
#: 手番の終わりの conditions に 1 欄 1 行で刻む(判断は judgment.ignored-settings-of の 1 点)。
CONDITION_AGENT_SETTING_IGNORED: ConditionType = "AgentSettingIgnored"
#: 段 10 lane 10o(agora-redesign #96・依頼者の追補 2026-09-14): 郵便に添付が在ったのに器へ渡せなかった
#: (器が添付の段を持たない・読めなかった・見出しと中身が食い違った)。黙って落とさず手番の conditions に 1 行。
#: 本文そのものは届いている — この条件は添付だけの話。判断は judgment.attachment-ignored-of の 1 点。
CONDITION_ATTACHMENT_IGNORED: ConditionType = "AttachmentIgnored"
#: 段 10 lane 10o(card acp:kanban-issue:ki-3149aebbf675 C): この手番の郵便を器へ渡せなかった
#: (session.send を host が断った — 走っている手番が無い・行が無い・同じ名の process が既に在る)。
#: 本文そのものが届いていない印で、添付だけの AttachmentIgnored とは別の軸。再配達はしない(落ちたことを
#: 見えるようにするだけ)— 理由の文は器の断りの逐語 + 届かなかった郵便の id。
#: 実測 2026-09-18: 断りは AgentdClientError のまま receive-bound-jobs の外まで抜けており、計器
#: agent-job-to-send も turn-record の作成も走らなかった(失敗が最も見えない形)。
CONDITION_INPUT_UNDELIVERED: ConditionType = "InputUndelivered"
#: 段 11 lane 11n 便 C(agora-redesign #179・依頼者の裁定 2026-09-15 問い 1 案 a): 手番が provider の
#: 限度の断り(「You've reached your <model> limit」等)で終わった印。**閉語彙の座はここ 1 点**で、
#: 契約 ACP docs/contracts/scheduling.json の profileExhaustion.providerRefusal はその写し。
#: 行の形 = {type, status: "True", reason: REASON_RATE_LIMITED, model: <走った model>, message: <CLI の文>}。
#: 読み手 = 予算の controller(agora-budget)で、窓の観測(profile.status.windows)より新しいこの印を
#: 「観測できない時の枯渇の証拠」として model 別の枯渇の判断に足す。判断は judgment.provider-limit-condition-of の 1 点。
#: 実弾 2026-09-15 13:2x: 会話 c-01M1XGMDHR35FBBC04W1JXM5KJ の手番が btc で Fable の限度に 5 回当たったが、
#: 器の status は done・agent-job は result も cause も無しの Ended だったので、profile の行へ戻る道が無かった。
#: ⚠ status.result の **value には書かない**(value は「手番が報告した結果」)— **cause には書く**: この条件で閉じる手番の
#: result.cause は {category: failed, reason: ProviderLimit}(段 12 lane 12k・agora-redesign #349 行 3 粒 3a・判断は
#: judgment.outcome-with-limit の 1 点 — 取り消し・停止の cause は上書きしない(合図が先に在った))。ACP の
#: awaitOutcomeOf は result の有無ではなく cause の category で終端の意味を読む。
CONDITION_PROVIDER_LIMIT: ConditionType = "ProviderLimit"
#: CONDITION_PROVIDER_LIMIT の reason の閉語彙(今日は 1 語 — 族が増えたらここに足す)。
REASON_RATE_LIMITED: str = "rate-limited"
#: agora-redesign #519(段 12): CONDITION_PROVIDER_LIMIT の記録が**自分で名乗る欄**(契約 ACP docs/contracts/scheduling.json
#: profileExhaustion.providerRefusal.fields の写し・additive)。profile = 断られた口座(記録を書いた拍の行の binding.profile)・
#: attempt = 断られた試み(行の binding.attempt・無ければ 1)・at = 断りの時刻(epoch ms)。読み手は 2 つ: 配置(ACP Scheduling)は
#: attempt = binding.attempt の記録を「この試みは口座に断られた」(supervision provider-refused)と読んで置き直し(release →
#: backoff → 別の口座で attempt + 1 → 上限で退役)、予算の係(agora-budget)は profile / at を優先して読む(置き直しの後は行の
#: binding.profile が次の口座に、observedAt は書きのたびに進む)。⚠ 断られた試みの手番は **Ended にしない**(終端の巻き戻しは
#: engine が断る・turn-record は 1 手番 1 行)— phase はそのまま、この条件を足す(judgment.refused-attempt-status-of の 1 点)。
#: 置き直し待ちの Running の行(judgment.attempt-refused?)は再起動後も拾い直さない。実弾 2026-09-17 20:29〜21:15: 計画段の会話の
#: 手番が individual spend limit で断られて Ended になり、郵便が delivered のまま 46 分止まった(撃ち直す主体が無かった)。
PROVIDER_LIMIT_PROFILE_KEY: str = "profile"
PROVIDER_LIMIT_ATTEMPT_KEY: str = "attempt"
PROVIDER_LIMIT_AT_KEY: str = "at"
#: 2026-09-23(operator の規則 2026-09-17「profile が費用の上限で止まったら、種類を問わず口座が枯れた 1 事実」): 記録が名乗る
#: **断りの範囲**。account = 口座全体の枯れ(session / weekly / spend / group の上限 $0 / credit 切れ — model の欄を**書かない**)・
#: model = 文が model を名乗る断り(「You've reached your Fable 5 limit」— model の欄 = 手番が走らせた model)。分類は文の物理の家
#: impls/markers.api-limit-names-a-model の 1 点、欄を組むのは judgment.provider-limit-condition-of の 1 点。読み手 = 予算の係
#: (account の記録は profile 全体の ProfileExhausted True{provider-refused} に、model の記録は model 別の行に畳む)。
#: 実弾 2026-09-23: 8 時間の断り 8 件が全部口座全体の文なのに model 別に記録され、Opus 5.5 を宣言した会話の最初の手番が Fable に落ちた。
PROVIDER_LIMIT_SCOPE_KEY: str = "scope"
PROVIDER_LIMIT_SCOPE_ACCOUNT: str = "account"
PROVIDER_LIMIT_SCOPE_MODEL: str = "model"
#: 文が名乗る戻りの時刻(epoch ms・「resets 6:20pm (Asia/Tokyo)」— impls/markers.api-limit-resets-at)。読めない文では欄を落とす
#: (予算の係は既定の期限 = 断りの後に来る最初の窓の戻り・上限 5 時間へ落ちる)。
PROVIDER_LIMIT_RESETS_AT_KEY: str = "resetsAt"
#: ⚠⚠ 2026-09-19 の追補: この記録を生む **409(錠の競合)は廃止された**(operator 裁定 19:2x・custody law
#: lease-counts-no-hosts・本番 be81f6f 11:18Z 配備)。預かり所は宿を数えないので、同じ口座の 2 台目の借りは断られない。
#: ⇒ **この腕は今日以降、原理的に発火しない**。残してあるのは (a) 提供側が同じ access token の同時利用を拒んだ場合に
#: 錠を戻す判断があり得ること (b) 発火しない以上、残す害が無いこと の 2 点による。消すなら custody の錠と ACP の配置の
#: 絞りを戻す便と対で。⚠ 貸与の断りの型 LeaseRefused 自体は生きている(403 = 借り手が名簿に無い / 503 = worker 不達)—
#: 死んだのは 409 の腕だけ。
#: 段 12(card acp:kanban-issue:ki-f2747267e24d B1・実弾 2026-09-19 08:44Z〜17 時台 JST): 預かり所が **409**(錠は別の借り手が
#: 握っている — 1 認証 1 宿)で手番の借りを断った印。**失った試みの記録であって手番の終わりではない**: 錠は他所の hold の
#: 期限(holdExpiresAt)で必ず解けるので、この手番は『いまこの口座を借りられなかった』だけで、口座も手番も壊れていない。
#: 行の形 = {type, status: "True", reason: <預かり所の断りの逐語>, attempt: <binding.attempt>, at: <記録を書いた時刻・epoch ms>,
#: until: <holdExpiresAt・epoch ms>, account: <binding.account>, nodeRow: <binding.nodeRow>}。
#: 既知の形 = #519 の ProviderLimit と同じ(runner は条件の記録を足すだけで phase を離す・置き直しの判断は配置の supervision)/
#: k8s Job の podFailurePolicy の Ignore。⚠ **phase / binding / sessionHandle / result は触らない**
#: (judgment.refused-attempt-status-of の形)— 置き直すか・いつまで待つか・数えるかを判じるのは ACP の配置の 1 点で、
#: agentd は断りの事実(409 と holdExpiresAt)を写すだけ。同じ試み(attempt = binding.attempt)の記録を持つ行は次の拍で
#: 起動し直さない(judgment.attempt-refused? — ProviderLimit と同じ 1 点)。
#: 409 以外の断り・hold を名乗らない断り・宣言の無い預かり所は今日どおり CONDITION_CREDENTIAL_UNAVAILABLE で Ended。
CONDITION_CREDENTIAL_LEASE_HELD: ConditionType = "CredentialLeaseHeld"
#: 『いまの試みは断られ、配置の置き直しを待っている』を名乗る記録の型の集合(judgment.attempt-refused? の 1 点が読む —
#: Bound の起動と Running の拾い直しの両方がこの 1 つの判定を通る)。どちらも runner が条件を足して phase を離す形で、
#: 置き直すのは配置(ACP Scheduling の supervision)。
REFUSED_ATTEMPT_CONDITION_TYPES: tuple[str, ...] = (
    CONDITION_PROVIDER_LIMIT,
    CONDITION_CREDENTIAL_LEASE_HELD,
)
#: 錠が別の借り手に在る時に預かり所が返す status(契約 custody-api.json の /lease/{kind} — この 1 語だけを記録に解く)。
CUSTODY_LEASE_HELD_STATUS: int = 409
#: CONDITION_CREDENTIAL_LEASE_HELD の記録が自分で名乗る欄(attempt / at は ProviderLimit と同じ綴り = 同じ読み手が同じ
#: 判定(attempt = binding.attempt)を 1 点で書けるように揃える)。until = 錠が解ける時刻・account = 借りられなかった口座・
#: nodeRow = 断られた機体の行の id。
CREDENTIAL_LEASE_HELD_UNTIL_KEY: str = "until"
CREDENTIAL_LEASE_HELD_ACCOUNT_KEY: str = "account"
CREDENTIAL_LEASE_HELD_NODE_ROW_KEY: str = "nodeRow"
#: card acp:kanban-issue:ki-b3bed1e983fb: 預かり所の断りを **「誰が答えられるか」** で分けた class。
#: HTTP の status は軸にならない(403 が 2 本に割れる — 置き場の門 Custody.Judge.Company.companyPlacementViolation は
#: 口座 × 預かり所の配置の事実で誰に対しても同じ / 借り手の門 companyBorrowerViolation は名乗った借り手の事実で
#: 宣言された機体なら通る)。判断は judgment.custody-refusal-verdict-of の 1 点。
#:   nobody          = 誰も答えない(宣言が変わるまでどの担い手でも同じ断り)— 最初の 1 回で送信者へ返す(hard rule 7)。
#:   another-carrier = 別の担い手が答える(この機体の身元・この機体の宣言・口座の worker の都合)— 有界の再投入。
#:   time            = 時間が答える(貸与の錠の hold)— 行に記録を残して phase を離す(置き直しは ACP の配置)。
CustodyRefusalAnswerer = Literal["nobody", "another-carrier", "time"]
CUSTODY_ANSWERER_NOBODY: CustodyRefusalAnswerer = "nobody"
CUSTODY_ANSWERER_ANOTHER_CARRIER: CustodyRefusalAnswerer = "another-carrier"
CUSTODY_ANSWERER_TIME: CustodyRefusalAnswerer = "time"
#: 預かり所が「その口座を預かっていない」と答える status(master の findHeading / worker の redeem — どちらも
#: 在庫の事実で、どの担い手が頼んでも同じ)。
CUSTODY_ACCOUNT_ABSENT_STATUS: int = 404
#: 預かり所の **置き場の門** の断りの文の印(Custody.Judge.Company.companyPlacementViolation の 1 点が鋳る文の
#: 逐語の一部)。⚠ これは repo をまたぐ **散文への結合**: 預かり所の断りの本文は {ok: false, error: <人が読む 1 文>}
#: だけで、貸与の口の 403 に機械可読の code が無い(契約 custody-api.json conventions.errors)。だから
#: 「置き場の門か借り手の門か」は文の印でしか分けられない。印が当たらない 403 は **another-carrier**(今日の挙動 =
#: 有界の再投入)へ倒す — 預かり所が文を書き換えた拍に壊れるのは「1 回で返せたはずの断りを 2 回試す」側だけで、
#: 「別の機体なら通る断りを 1 回で殺す」側には倒れない(実測 2026-09-19 の 403 は 4 件とも借り手の門)。
#: 直す道 = 預かり所の貸与の口の断りに code を足す(redeem の口は既に RedeemRefusal で code を名乗る)。
#: 会社境界の 2 つの門(置き場・借り手)が返す status。この 1 語だけでは class が決まらないので、
#: 置き場の門の印(上)と対で読む。
CUSTODY_PLACEMENT_REFUSAL_STATUS: int = 403
CUSTODY_PLACEMENT_REFUSAL_MARK: str = "会社の機体の外へ出さない"
#: 段 10c(agora-redesign #80)から在る語の名前(これまで agentd.hy に裸の文字列で在った)。預かり所が断り、
#: **別の担い手なら通り得る**時の終端の語 — ACP の配達はこの語を carrierEndedFailureReasons の membership で読み、
#: 有界に組み直す(Acp.App.Messaging.Contract)。
CONDITION_CREDENTIAL_UNAVAILABLE: ConditionType = "CredentialUnavailable"
#: card acp:kanban-issue:ki-b3bed1e983fb: 預かり所が **どの担い手にも** その口座を貸さない時の終端の語。
#: = 口座が預かりに無い(404)/ 会社階級の口座が会社の置き場でない預かり所に在る(403 置き場の門)。
#: ⚠ **この語が carrierEndedFailureReasons(ACP)に無いことが受入の本体**: membership から外れるので、配達は
#: 組み直さず 1 回で郵便を failed にして送信者へ返す(CredentialSourceMissing / WorkDirMissing と同じ扱い)。
#: 語を足すのは agentd 側だけ — ACP の list は触らない(reason の語彙は閉じていない: 契約 scheduling.json resultCause)。
CONDITION_CREDENTIAL_NOT_LEASABLE: ConditionType = "CredentialNotLeasable"
#: 依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB(D1・D3): 温かい session の手番が終わったのに、本文のための model の出力が 1 本も
#: 無かった(材料は読めたのに assistant の見出し text / tool_use / tool_result が 0 本・usage も無い)印。= model が 1 度も
#: 呼ばれていない = **手番が走らなかった**事実で、手番自身の結末ではない — result.cause は {category: failed, reason:
#: TurnProducedNothing}(判断は judgment.turn-output-condition-of / outcome-with-output-condition の 1 点・completed の時だけ
#: 置き換える — 取り消し・停止・限度・器の失敗の cause は上書きしない)。ACP の Messaging はこの reason を一過性の側
#: (carrierEndedFailureReasons)として有界に組み直す — 決定的な理由(WorkDirMissing・PlaceMismatch 等)は起こす前に
#: 決まって TURN-END に来ないので、この語に畳まれない。実弾 2026-09-19 07:28 JST aj-9AHT1RWPYNTTWEZBWRNN0R34T6: --resume の
#: CLI が孤児の task の報せを自分の手番として走らせ、器がその result で本文の手番を切った(根は headless_protocol の
#: CLI_OWN_TURN_ORIGINS で直した — この語は同じ形の取り違えが別の経路で起きた時に黙って completed を名乗らないための網)。
CONDITION_TURN_PRODUCED_NOTHING: ConditionType = "TurnProducedNothing"
#: card acp:kanban-issue:ki-ef537db05f7f: 出力 0 件を **測れていない** 手番の印 — 材料(start_offset から読む
#: transcript / events)がこの手番を覆っていないので、出力が 0 本なのは「出さなかった」の証拠にならない。当たるのは
#: 再起動の後に行から拾い直した手番(recover-job)のうち、読み始めが『拾い直した拍の file の大きさ』になる腕
#: (send / resume)ちょうど — 再起動の前に書かれた出力はもう読めない。⚠ **この語は cause を変えない**(結末は
#: completed のまま・郵便は消費される): failed へ倒すと ACP の配達が一過性として同じ郵便で手番を作り直し、答え終えた
#: 手番の答えが 2 度出る(実弾 2026-09-19 22:29Z aj-6EKERTYDCD4MC666PGPVA9R9HA: `ai tell` を 2 回撃って result success で
#: 終わった手番が entries 0 で TurnProducedNothing になり、作り直し aj-7EKG2XCJT01XJK9WXPDRPG04XQ が同じ郵便へもう一度
#: 答えた)。測れなかったことは黙って捨てず、この条件として行に残す(数えられる形にする — 判断は
#: judgment.turn-output-condition-of の 1 点)。
CONDITION_TURN_OUTPUT_UNMEASURED: ConditionType = "TurnOutputUnmeasured"
#: agora-redesign #519: 配置が退役させた手番(scheduling.json retirement)の印 — Withdrawn の行の条件 Unschedulable{status True,
#: reason: retry-budget-exhausted}(書き手 acp-scheduling)。最後の runner(sessionHandle の owner)がその turn-record を ended に
#: する(judgment.retired-rows-of / agentd.end-retired-records)— 記録は手番が本当に終わる時に ended(1 手番 1 行)。
#: 配置(acp-scheduling)の型 — agentd は読むだけで書かない(ConditionType の閉語彙〔agentd が書く型〕には入れない)。
CONDITION_UNSCHEDULABLE: str = "Unschedulable"
REASON_RETRY_BUDGET_EXHAUSTED: str = "retry-budget-exhausted"
#: 段 12 lane 12a(agora-redesign #230・依頼者の裁定 2026-09-16): charter.kind = verify の job(定期便の検証の命令 1 つ —
#: 会社 repo の日次の全体検証。契機は k3s の CronJob・配置は charter.place を spec.places に名乗る node・実行はこの
#: agentd が機体自身の資格で)を起こさず・起こせず・失って閉じた印。**claude / codex を起こさない**: 命令は
#: 機体の dotfiles の script(VERIFY_SCRIPTS_RELDIR/<jobId>.sh)ちょうどで、命令の文字列は行から運ばない(herdr-hud
#: D0626 決定 2 — cluster 側から機体へ任意の命令を流せる口を新設しない)。
#: VerifyScriptMissing = charter の jobId が綴りの外・script がこの機体に無い(知らない id は loud に落とす)。
CONDITION_VERIFY_SCRIPT_MISSING: ConditionType = "VerifyScriptMissing"
#: VerifyStartFailed = process を起こせなかった(handler の断り — exec の失敗等)。
CONDITION_VERIFY_START_FAILED: ConditionType = "VerifyStartFailed"
#: VerifyCommandLost = 走っていた process が結末(rc の file)を残さずに消えた(機体の再起動・kill -9)。
CONDITION_VERIFY_COMMAND_LOST: ConditionType = "VerifyCommandLost"
#: VerifyDeadlineExceeded = charter.deadlineSeconds を越えて走っていたので agentd が止めた。
CONDITION_VERIFY_DEADLINE_EXCEEDED: ConditionType = "VerifyDeadlineExceeded"
#: charter の種類の欄と閉語彙(ACP docs/contracts/scheduling.json charterKind の写し — 綴りの定義点は ACP の
#: Acp.App.Scheduling.Contract)。無い = turn(会話の手番・従来どおり)。
CHARTER_KIND_KEY: str = "kind"
CHARTER_KIND_TURN: str = "turn"
CHARTER_KIND_VERIFY: str = "verify"
#: charter が要求する置き場(ACP Acp.App.Scheduling.Contract.charterPlaceKey の写し・語彙は AGENTD_PLACES)。
#: 段 12(card acp:kanban-issue:ki-d13566f4d5eb・決定 案 A・2026-09-19): 手番の charter も置き場を持ってよく、
#: **走行係は結ばれた行の要求を自分の名乗りで検める**(claim の門 judgment.place-mismatch の 1 点)。
#: 「配置が判じ終えているから走行係は読まない」は旧い形 — 配置の版が古い・手で結んだ拍に、道具の無い宿が
#: 黙って手番を取っていた(実弾 2026-09-18: 運用の 5 手番が kubectl の無い pod に落ちた)。
CHARTER_PLACE_KEY: str = "place"
#: verify の charter の欄(契約 scheduling.json charterKind.verify.runnerCharter の写し): 便の id(= script の名・
#: ai land verify の --loop-id)・発火の鍵(k8s の Job 名・記録の材料)・命令の上限(秒)。
CHARTER_VERIFY_JOB_ID_KEY: str = "jobId"
CHARTER_VERIFY_RUN_KEY_KEY: str = "runKey"
CHARTER_VERIFY_DEADLINE_KEY: str = "deadlineSeconds"
#: 便の id の綴り(小文字の英数字と - ・64 字まで — path の要素にそのまま使うので / と . を持たない形を型で塞ぐ)。
CHARTER_VERIFY_JOB_ID_PATTERN: str = r"^[a-z0-9][a-z0-9-]{0,63}$"
#: 機体の家(AgentdSettings.home)からの verify の script の置き場(dotfiles の定期便の入口 script の dir — herdr-hud
#: deploy/periodic/jobs.json の script の欄 `dotfiles/cron_management/<id>.sh` と同じ形。D0626 の受け取り係と同じく
#: 「本体 = ~/<script> はそちらで走る」)。定義点はここ 1 つ。
VERIFY_SCRIPTS_RELDIR: str = "dotfiles/cron_management"
#: verify の命令の結末の置き場(AgentdSettings の state_dir の下・job の id ごと): stdout+stderr の log・rc・pid の 3 file。
#: 走らせ方 = sh の 1 行(judgment.verify-argv-of の 1 点)が pid を書き、script を走らせ、rc を書く — agentd が
#: 再起動しても process は残り(自分の session)、結末は file から読める(R7: 正本は行と file)。
VERIFY_RUNS_RELDIR: str = "verify-runs"
#: 段 12(card acp:kanban-issue:ki-f2747267e24d B2): 借りた錠の手元の記録(journal)の置き場 —
#: AgentdSettings の state_dir(record spool の親)の下の 1 file。中身は {jobId: leaseId} の組ちょうどで、
#: 借りた拍に足し返した拍に外す。読み手は**手番を閉じる腕**: 貸与の id は借りた process の memory にしか
#: 無かったので、agentd の入れ替え・再起動・排水で process が変わると錠を返せなかった(実弾 2026-09-19
#: 08:44Z — 錠は hold の 900 秒残り、その間の借りは全部 409)。既知の形 = kubelet の再起動後の volume の
#: 再構成(disk に残した記録から「自分が握っている物」を組み直して後始末する)。
#: ⚠ 借り手の名で一括に返す掃除(sweep)は作らない — 返すのは journal が job ごとに名乗る 1 つだけ。
LEASE_JOURNAL_FILENAME: str = "leases.json"
#: journal の読みの上限(1 組 ≈ 80 byte・機体の同時の手番は 2 桁)— 上限で切れた text は読みが断る(空に倒す)。
LEASE_JOURNAL_MAX_CHARS: int = 262_144
#: verify の job の sessionHandle の欄(agentd が Running の書きで置く — 契約は opaque・stream{owner, name} だけ共有の形)。
JOB_HANDLE_VERIFY_KEY: str = "verify"
#: verify の命令の次の 1 手(judgment.verify-step-of の閉語彙): observe = 走っている / ended = rc の file が在る /
#: lost = rc が無く pid も生きていない / timed-out = 期限を越えて走っている(止める)。
VerifyStep = Literal["observe", "ended", "lost", "timed-out"]
VERIFY_STEP_OBSERVE: VerifyStep = "observe"
VERIFY_STEP_ENDED: VerifyStep = "ended"
VERIFY_STEP_LOST: VerifyStep = "lost"
VERIFY_STEP_TIMED_OUT: VerifyStep = "timed-out"
#: 段 12 lane 12j(agora-redesign #233・operator 2026-09-16 逐語 "lets see if 1 will work"・依頼者の裁定 = two-way door):
#: charter.kind = summarize の job = 会話の履歴の段階つき要約 1 つ(#55 案 D)。契機はこの agentd(手番の終わりに測った文脈の
#: 大きさが summarize_trigger_tokens を超えた拍 — judgment.summarize-due)・配置は手番と同じ資格の路(ACP scheduling.json
#: charterKind.summarize)・実行は結ばれた node の agentd が会話と同じ profile の Claude Code(charter.model・預かり所の札)を
#: 区間ごとに 1 回起こし(claude の print モード・道具なし)、記録の service の古い区間 [from, to] を 1 段落に縮め、本文を記録の service の
#: stream(streamKind summary)へ積み、agora の kind summary の行(claim check = recordRef / bytes / sha256)を書く。
#: 会話の手番ではない(turn-record は書かない・郵便を読まない・中継へ押さない)。ACP scheduling.json charterKind の写し。
CHARTER_KIND_SUMMARIZE: str = "summarize"
#: summarize の charter の欄(契約 scheduling.json charterKind.summarize.runnerCharter の写し): 区間の上端(記録の service の
#: recordSeq・含む)と 1 区間の原文の上限 byte(任意・無ければ AgentdSettings.summarize_region_byte_budget)。
CHARTER_SUMMARIZE_UNTIL_KEY: str = "until"
CHARTER_SUMMARIZE_REGION_BYTES_KEY: str = "regionByteBudget"
#: agora の kind summary(ACP agora-kinds.json kinds.summary — 書き手 agentd・identityKey [conversationId, to])の綴り。
SUMMARY_KIND: str = "summary"
SUMMARY_SPEC_CONVERSATION_KEY: str = "conversationId"
SUMMARY_SPEC_FROM_KEY: str = "from"
SUMMARY_SPEC_TO_KEY: str = "to"
SUMMARY_SPEC_RECORD_REF_KEY: str = "recordRef"
SUMMARY_STATE_CURRENT: str = "current"
SUMMARY_STATE_SUPERSEDED: str = "superseded"
#: 記録の service の stream(streamKind summary・id = summary#<from>-<to>)と出来事(kind summary・producerSeq 0・本文は text)の
#: 綴り(agora-controllers docs/contracts/record-service.json の写し)。
SUMMARY_STREAM_KIND: str = "summary"
SUMMARY_EVENT_KIND: str = "summary"
SUMMARY_STREAM_PREFIX: str = "summary#"
#: agora の kind agent-memory(ACP agora-kinds.json kinds.agent-memory — 書き手 agentd・identityKey [conversationId, name]・
#: 法 ACP 575b1e conversation-memory-lives-in-the-row)の綴り。行は claim check(recordRef / recordSeq / bytes / sha256 /
#: version)と索引の材料(description / type / links)だけで、本文を 1 字も持たない。本文の正本は記録の service の
#: stream(streamKind memory・id = memory#<name>・出来事は kind memory の 1 つ・producerSeq 0・本文は text)。
MEMORY_KIND: str = "agent-memory"
MEMORY_SPEC_CONVERSATION_KEY: str = "conversationId"
MEMORY_SPEC_NAME_KEY: str = "name"
MEMORY_SPEC_TYPE_KEY: str = "type"
MEMORY_SPEC_DESCRIPTION_KEY: str = "description"
MEMORY_SPEC_RECORD_REF_KEY: str = "recordRef"
MEMORY_SPEC_RECORD_SEQ_KEY: str = "recordSeq"
MEMORY_SPEC_BYTES_KEY: str = "bytes"
MEMORY_SPEC_SHA256_KEY: str = "sha256"
MEMORY_SPEC_VERSION_KEY: str = "version"
MEMORY_SPEC_LINKS_KEY: str = "links"
MEMORY_SPEC_WRITTEN_BY_KEY: str = "writtenBy"
MEMORY_STATE_CURRENT: str = "current"
MEMORY_STATE_RETIRED: str = "retired"
#: 記憶の種類(frontmatter の metadata.type・契約の enum の写し)。閉語彙の外を名乗る file は書かない。
MEMORY_TYPES: tuple[str, ...] = ("user", "feedback", "project", "reference")
#: 記録の service の stream と出来事の綴り(agora-controllers docs/contracts/record-service.json の写し)。
MEMORY_EVENT_KIND: str = "memory"
MEMORY_STREAM_PREFIX: str = "memory#"
#: 記憶の置き場の中の綴り: 1 冊 = <name>.md・索引は MEMORY.md(**file として正本を持たない** — 手番の頭に
#: 行から組み直す。実測 2026-09-20: 本 18 冊に対し索引 15 行に腐っていた)。
MEMORY_FILE_SUFFIX: str = ".md"
MEMORY_INDEX_FILE: str = "MEMORY.md"
#: 畳み戻しの**基準**(baseline)の side car。手番の頭に置き場へ出した写しの claim check を、冊と同じ拍・
#: 同じ書き手(器)で置く。⚠ **正本ではない** — ACP の行にも記録の service にも 1 bit も書かない
#: (法 575b1e の正本の座は行)。これが在るから畳み戻しは「手元 ≠ 行」を『席が書いた』と『席は触って
#: いないが行が別の機体で動いた』に割れる。2 点(手元と行)だけの比較は割れず、席が 1 字も触っていない
#: 古い写しで行を巻き戻していた(実測 2026-09-21: 冊 mail-hold-has-two-exits が v2 6,503 → v3 4,620 byte)。
MEMORY_BASE_FILE: str = "MEMORY.base.json"
#: 基準の JSON の綴り: {"books": {<name>: {recordSeq, sha256, version}}}。内側の 3 語は行の spec と
#: **同じ綴り**(MEMORY_SPEC_RECORD_SEQ_KEY / _SHA256_KEY / _VERSION_KEY)を使う — 第 2 の語彙を作らない。
MEMORY_BASE_BOOKS_KEY: str = "books"
#: 置き場の予約名(索引と基準)。冊でも『読めない file』でもないので、読み手は**黙って**除く。
#: 数えると計器の unreadable が正常でも 1 を名乗り(索引 1 つ分)、直しが劣化に見える。
MEMORY_RESERVED_FILES: tuple[str, ...] = (MEMORY_INDEX_FILE, MEMORY_BASE_FILE)
#: charter が運ぶ記憶の本(起こす腕が行から読んで載せ、器の側が置き場へ書き出す)。ACP の行へは書かない
#: (history / first_turn と同じく起こすためだけの値)。
CHARTER_MEMORY_FILES_KEY: str = "memory_files"
#: charter が運ぶ**取り除く file の名**(退役した行と同じ名前ちょうど・card acp:kanban-issue:ki-6b5c4b270ca0)。
#: 置き場はエージェントが触る面なので、退役した冊の file は水入れが書かないだけでは消えない ⇒ 残った file を
#: 次の手番の畳み戻しが読んで、退役を毎回 取り消していた。⚠ 名は**行から**来る(器は名を組まない・行を読まない):
#: 器が名を組むと、行に無い名前の file を消す枝がこの層に生え、手番の途中に席が書いた記憶を消せるようになる。
CHARTER_MEMORY_RETIRED_FILES_KEY: str = "memory_retired_files"
#: 自動記憶の置き場(会話 id から導いた 1 つ・ADR-DOE-AGENTS-006 R11)を運ぶ charter の欄。
#: 綴りの家をここに置くのは、本文(上)と置き場が**同じ族**だから — 族のどちらかだけを名簿へ手で足す
#: 便が、もう片方を落としたまま通った(card acp:kanban-issue:ki-a40292ed30d9)。
CHARTER_MEMORY_DIR_KEY: str = "memory_dir"
#: 記憶を書けなかった手番の条件(手番は落とさない — 記憶が書けないことは手番の失敗ではない)。
CONDITION_MEMORY_UNWRITABLE: ConditionType = "AgentMemoryUnwritable"

#: 原文として畳む出来事の kind(記録の service の eventKinds のうち会話の中身 — frame は画面の断面・message は郵便で ACP の行から
#: 畳む・attachment は画像・summary は要約そのもの)。要約の区間の読みと履歴からの再開の読みが kinds= に渡す **1 点**。
RECORD_RAW_EVENT_KINDS: tuple[str, ...] = ("text", "tool_use", "tool_result", "system", "error", "user")
#: summarize の結末の置き場(state_dir の下・区間ごと): prompt・claude の print モードの答え(JSON)・log・rc・pid の 5 file。
SUMMARY_RUNS_RELDIR: str = "summary-runs"
#: summarize の job の sessionHandle の欄(拾い直しの材料 — R7: 正本は行)。
JOB_HANDLE_SUMMARIZE_KEY: str = "summarize"
#: summarize の条件: 行の欄が読めない(charter の until / model・binding の profile / account)/ 区間の原文を記録の service から
#: 読めない / process を起こせない / 結末を残さず消えた / 期限超過 / claude の print モードの答えが読めない(JSON でない・誤り・空)/
#: 要約の本文か行を書けなかった。
CONDITION_SUMMARIZE_PLAN_INVALID: ConditionType = "SummarizePlanInvalid"
CONDITION_SUMMARIZE_REGION_UNREADABLE: ConditionType = "SummarizeRegionUnreadable"
CONDITION_SUMMARIZE_START_FAILED: ConditionType = "SummarizeStartFailed"
CONDITION_SUMMARIZE_COMMAND_LOST: ConditionType = "SummarizeCommandLost"
CONDITION_SUMMARIZE_DEADLINE_EXCEEDED: ConditionType = "SummarizeDeadlineExceeded"
CONDITION_SUMMARIZE_OUTPUT_UNREADABLE: ConditionType = "SummarizeOutputUnreadable"
CONDITION_SUMMARY_UNWRITABLE: ConditionType = "SummaryUnwritable"
#: 段 11 lane 11n 便 C(agora-redesign #179・依頼者の裁定 2026-09-15 案 c′): 器の終端の cause の
#: category のうち agentd の ACP の腕が読む 1 語 —— provider が限度で断った(sessionhost の
#: policy.hy TERMINAL-CAUSE-CATEGORIES / launch-not-ready-category と headless.hy の手番の腕が
#: 同じ語で書き、ACP の engine も同じ綴りを読む〔Acp.Core.Types.Observed の failureKindForCause〕)。
#: policy.hy は deff / defhandler を持つ Hy で共通の品質検査が投影できないため、agentd が読む語を
#: ここに写す(SESSION_TERMINAL_STATUSES と同じ扱い — 第 2 の定義点であることは
#: ADR-DOE-AGENTS-012 R33 の報告に明記)。族の表そのものは markers.hy の 1 点のまま。
CAUSE_CATEGORY_RATE_LIMITED: str = "rate_limited"
#: charter が model を名乗らない手番の model の欄に置く語(走行器の既定で起こす事実の名 —
#: judgment.launch-plan-of と turn-record の spec.model が同じ語を使う)。この語は「どの model が
#: 走ったか分からない」の意味なので、provider の限度の条件では model の欄を落とす(発明しない)。
MODEL_UNDECLARED: str = "default"
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
#: ADR-DOE-AGENTS-012 R61(card acp:kanban-issue:ki-f250d67a7157): 上の表は種類ごとに**受け付ける欄**の意味で、node が
#: 申告する種類はその部分集合 = この node の起動する process のすべてで実行ファイル(drivers.DRIVER_EXECUTABLE)が
#: 見つかる種類(judgment.launchable-agent-kinds の 1 点)。手番は host が起こし、下の種類は agentd 自身も起こす:
#: 要約の job(charter.kind = summarize)は agentd が AgentdSettings.claude_binary を print モードで起こす(host を
#: 通らない — agentd.start-summary-region → CommandStart)ので、claude は host と agentd の両方で見つかる時だけ申告する。
AGENTD_LAUNCHED_KINDS: tuple[str, ...] = ("claude",)
#: node の status に能力の表を書く欄の名(契約 kinds.node.schema.properties.status.properties.capabilities・書き手 agentd)。
NODE_CAPABILITIES_KEY = "capabilities"
#: charter の欄 → 会話の宣言の欄の語(契約 conventions.agentSettings.settings)。profile は charter に無い(段 10c: 配置の係が
#: 預かり所の account に解く — binding.account が家)。
CHARTER_SETTING_KEYS: dict[str, AgentSetting] = {"model": "model", "effort": "effort", "work_dir": "workDir"}
#: 段 10 lane 10y: charter の作業場の鍵(綴りの定義点は契約 agora-kinds.json の delivery-policy.spec.charter)。work_dir は絶対 path か
#: 家からの相対(`~` / `~/…` — agentd が node の HOME で展開する・judgment.plan-with-node-home)。work_dir_scratch = true の時だけ、
#: 無い work_dir を agentd が作ってよい(既定 = 無し = 作らない — repo を指す work_dir を空の dir で偽装しない)。
CHARTER_WORK_DIR_KEY = "work_dir"
CHARTER_WORK_DIR_SCRATCH_KEY = "work_dir_scratch"
#: 会話の圧縮の閾値(設計記録 docs/design/auto-compact-window): 会話の圧縮の閾値(token の整数、または "auto" = CLI の窓任せ)。
#: 起こす params と同じ綴り。**丸ごと素通しになる名簿は 1 枚も無い** — launch も resume も閉じた名簿を
#: 通るので、この欄は下の CHARTER_CARRIED_KEYS の 1 点から写される。argv の導出は impls.claude_code.claude-autocompact-value
#: の 1 点(幅 100k〜1M・外れる値は "auto" へ縮退 — 幅の外を argv に載せると手番が死ぬ)。
CHARTER_AUTO_COMPACT_WINDOW_KEY = "auto_compact_window"
#: 作業場の段(judgment.work-dir-step-of の閉語彙): launch = 在る / 宣言なし・create = 無いが scratch の印・missing = 無い。
WorkDirStep = Literal["launch", "create", "missing"]
WORK_DIR_STEP_LAUNCH: WorkDirStep = "launch"
WORK_DIR_STEP_CREATE: WorkDirStep = "create"
WORK_DIR_STEP_MISSING: WorkDirStep = "missing"
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
#: 段 12 lane 12j(agora-redesign #321 = #317 の k8s 規則 1 後半・契約 scheduling.json binding.fields): 結びの node の欄 —
#: node = 機体の名前(kind node の spec.name・表示と旧い結びの読み手のため)/ nodeRow = 結んだ node の**行の id**(kind node の
#: resource id・12k の L1042 便 1 から配置が書く)。agentd の claim と取り下げの照合は nodeRow が在ればそれと自分の生きている行の id
#: (AgentdState.node_row_id)を比べ、無い結び(この欄が生まれる前の書き)だけ名前に落ちる — 判断は judgment.binding-names-me の 1 点。
BINDING_NODE_KEY: str = "node"
BINDING_NODE_ROW_KEY: str = "nodeRow"
#: 結びの口座の欄(契約 scheduling.json binding.fields.account)— 手番が借りる預かり所の口座。
BINDING_ACCOUNT_KEY: str = "account"
JOB_INTERRUPTS_KEY: str = "interrupts"
JOB_INTERRUPTS_DELIVERED_KEY: str = "interruptsDelivered"
#: card acp:kanban-issue:ki-3149aebbf675 A: agent-job の status.inputsDelivered = この手番で agentd が
#: **器へ渡せた** inputs の郵便の id の並び(行の順・行の寿命の間 append-only・書き手 = agentd だけ)。
#: claim の拍に空で宣言し(「この agentd は配達を記帳する」の名乗り)、送りが着地した拍に id を足す —
#: 断られた拍は 1 つも足さない。読み手 = ACP の配達の 1 点(Acp.App.Messaging.Decide.handedEvidenceOf)で、
#: 欄が在る行は**この欄ちょうど**で「郵便が届いたか」を判じ、欄が無い行(この欄より前の agentd が走らせた行)は
#: 今までどおり turn-record / phase = Running の推定に落ちる(版が混ざる艦隊で旧い行が全部未配達に見えないため)。
#: 実測 2026-09-18: 相乗り 10 通のうち agent に届いたのは 1 通なのに、台帳は 10 通とも handedAt だった —
#: 手番が始まったこと(phase)を「郵便が届いた」の証拠に使っていたため。
JOB_INPUTS_DELIVERED_KEY: str = "inputsDelivered"
#: 段 10 lane 10n(agora-redesign #93): 割り込みの観測の 2 欄(書き手 agentd・additive・append-only の map)。
#: interruptsRead = {Message の id: model がその本文を読んだ証拠の出来事の seq}(claude = 注入の行の
#: command_lifecycle started・codex = 止めた後の turn/started)/ interruptsEscalated = {Message の id: 停止の合図を
#: 送った時刻 ms}。契約 = ACP docs/contracts/messaging.json interrupts(便 3)。
JOB_INTERRUPTS_READ_KEY: str = "interruptsRead"
JOB_INTERRUPTS_ESCALATED_KEY: str = "interruptsEscalated"
#: 段 10 lane 10n: 期限(秒)を運ぶ charter の欄 — Messaging の Plan.charterFor が方策の行の値を会話の宣言で重ねて写す。
#: agentd はこの欄だけを読む(方策・会話の行は読まない・既定の定数を置かない — 無い job は注入だけ + 条件)。
CHARTER_INTERRUPT_ESCALATION_KEY: str = "interruptEscalationSeconds"
# ---------------------------------------------------------------------------
# charter の欄の行き先(card acp:kanban-issue:ki-a40292ed30d9 — 名簿を触らせない形)
# ---------------------------------------------------------------------------
#
# charter の欄には行き先が 2 つしかない:
#   (a) 席へ運ぶ  — 席の process の形を決める値。起こす腕(launch / resume / rehydrate)から
#                   sessionhost の wire を渡って器の argv・置き場へ届く。
#   (b) agentd が読む — 手番の割り当て・作業場の解決・verify / summarize の命令。器へは渡らない。
#
# 同じ壊れ方が 3 度(添付 2026-09-15 → 記憶の置き場 2026-09-18 → 記憶の**本文** 2026-09-21)起きたのは、
# (a) の欄が渡る名簿が 4 枚在って、欄を足す操作が 4 枚とも手で触らせたから。⇒ 名簿は
# policy.CHARTER-CARRIED-KEYS / carry-charter-fields の 1 点から写し、ここには **(b) の名簿だけ**を置く。
#
# ⚠ (a) の名簿はここに**書かない**(書くと 5 枚目になる)。(a) は「この module が宣言した CHARTER_*_KEY の
#   うち (b) でないもの」= 引き算で決まり、検 tests/sessionhost_charter_reaches_the_seat_deftests.hy が
#   module を反射で数えて「席へ届くか」を撃つ。新しい CHARTER_*_KEY を足して席へ運ばないなら、
#   下の名簿に足すまで検は赤のまま(足す操作が必ず行き先を宣言させる)。
CHARTER_KEYS_AGENTD_CONSUMES: tuple[str, ...] = (
    CHARTER_KIND_KEY,  # どの腕の job か(turn / verify / summarize)
    CHARTER_PLACE_KEY,  # 置き場の集合(配車が読む)
    CHARTER_WORK_DIR_SCRATCH_KEY,  # work_dir の解決の手順(解決した path だけが席へ行く)
    CHARTER_INTERRUPT_ESCALATION_KEY,  # 割り込みの期限(agentd が数える)
    CHARTER_VERIFY_JOB_ID_KEY,  # verify の命令(席を起こさない腕)
    CHARTER_VERIFY_RUN_KEY_KEY,
    CHARTER_VERIFY_DEADLINE_KEY,
    CHARTER_SUMMARIZE_UNTIL_KEY,  # summarize の命令(会話の手番ではない)
    CHARTER_SUMMARIZE_REGION_BYTES_KEY,
)
#: card acp:kanban-issue:ki-c3aace97d825: 組み直した transcript を席の家へ運ぶ charter の欄の綴り。
#: 値 = {"session_id": <uuid>, "text": <jsonl>}。運ぶのは policy.TURN_CARRIED_KEYS の 1 点で、
#: 家へ書くのは impls/claude_code.hy の 1 点(agentd は家の物理を知らない — 記憶の冊と同じ形)。
CHARTER_REBUILT_TRANSCRIPT_KEY = "rebuilt_transcript"
REBUILT_TRANSCRIPT_SESSION_FIELD = "session_id"
REBUILT_TRANSCRIPT_TEXT_FIELD = "text"
#: 4 枚の名簿が写す欄(= 基の名簿に名前で書かれていない、席へ運ぶ欄)。host 側の写しは
#: policy.CHARTER-CARRIED-KEYS で、**同じ語であること**は検が pin する(綴りが割れると黙って落ちる)。
CHARTER_CARRIED_KEYS: tuple[str, ...] = (
    CHARTER_AUTO_COMPACT_WINDOW_KEY,
    CHARTER_MEMORY_DIR_KEY,
    CHARTER_MEMORY_FILES_KEY,
    CHARTER_MEMORY_RETIRED_FILES_KEY,
    CHARTER_REBUILT_TRANSCRIPT_KEY,
)

#: 段 10 lane 10n: agent の種類ごとの割り込みの能力(node の status.capabilities[kind].interrupt の閉語彙):
#: steer-then-stop = 注入(道具の境界で読む)→ 期限で停止の合図(claude)/ stop = 即座に止めて渡す(codex)。
InterruptCapability = Literal["steer-then-stop", "stop"]
AGENT_INTERRUPT_CAPABILITY: dict[str, InterruptCapability] = {
    "claude": "steer-then-stop",
    "codex": "stop",
}
NODE_CAPABILITY_INTERRUPT_KEY: str = "interrupt"
#: 段 10 lane 10o(agora-redesign #96・裁定 問い 5 案 A): agent の種類ごとに受ける添付の種類
#: (node の status.capabilities[kind].attachments — 契約 agora-kinds.json の閉語彙)。欠落 = 何も受けない。
#: 今日の 2 種類はどちらも画像を受ける(便 1 の実測 — claude の content の block・codex の input の項)。
AttachmentKind = Literal["image"]
ATTACHMENT_KIND_IMAGE: AttachmentKind = "image"
AGENT_ATTACHMENT_CAPABILITY: dict[str, tuple[AttachmentKind, ...]] = {
    "claude": (ATTACHMENT_KIND_IMAGE,),
    "codex": (ATTACHMENT_KIND_IMAGE,),
}
NODE_CAPABILITY_ATTACHMENTS_KEY: str = "attachments"
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
#: 取り消しの 3 段(段 12 lane 12k / 12j・agora-redesign #367・既知の形 行 3 (g) = 合図 → 猶予 → 強制。契約 = ACP
#: docs/contracts/scheduling.json の cancel の節・綴りの正本 = Acp.App.Agent.AgentJob の spec.cancel と status.cancel):
#: 段 1 合図 = 持ち主(Messaging の intent cancel-job)が agent-job の spec.cancel {requestedAt(epoch ms), graceSeconds
#: (欠落 = 60), reason(閉語彙 operator / superseded / conversation-withdrawn / drained), by} を書く / 段 2 猶予 = agentd が
#: 手番の途中なら割り込み(session.interrupt — 取り下げと同じ腕)を撃ち、status.cancel {acknowledgedAt, stage: graceful}
#: を書く(見届け・1 度)/ 段 3 強制 = requestedAt + graceSeconds を過ぎても手番が終わらなければ agentd が器を片付け
#: (session.cleanup = process を殺す)Ended + result.cause {category: cancelled, stage: forced, reason}。猶予の内に手番が
#: 終われば Ended + result.cause {…, stage: graceful}。「いま強制」(猶予 0)は合図の cancel-job {graceSeconds: 0} で名乗る
#: (猶予 0 の別名だった語は契約 messaging.json v2 で消えた — 取り消しの語は cancel-job の 1 つ)。phase Withdrawn(書き手は
#: 作った側)の行は猶予を待たず、今日の腕(interrupt-job = session.interrupt・session は片付けない)のまま。
JOB_SPEC_CANCEL_KEY: str = "cancel"
CANCEL_REQUESTED_AT_KEY: str = "requestedAt"
CANCEL_GRACE_SECONDS_KEY: str = "graceSeconds"
CANCEL_REASON_KEY: str = "reason"
CANCEL_BY_KEY: str = "by"
#: 契約 scheduling.json cancel.defaultGraceSeconds の写し(合図に graceSeconds が無い時の猶予)。
DEFAULT_CANCEL_GRACE_SECONDS: int = 60
JOB_STATUS_CANCEL_KEY: str = "cancel"
CANCEL_ACKNOWLEDGED_AT_KEY: str = "acknowledgedAt"
CANCEL_STAGE_KEY: str = "stage"
CancelStage = Literal["graceful", "forced"]
CANCEL_STAGE_GRACEFUL: CancelStage = "graceful"
CANCEL_STAGE_FORCED: CancelStage = "forced"
#: 段 12 lane 12k(agora-redesign #349 行 3 粒 3a・既知の形 CI runner (i)「手番の終わりに終端の状態を必ず返す」): agent-job の
#: 終端の result.cause {category, reason?, stage?} の category の閉語彙 — **定義点はここ 1 点**(契約 ACP docs/contracts/scheduling.json
#: resultCause.categories の写し・ACP の正本は Acp.App.Agent.AgentJob.resultCauseCategoryWords で hspec が JSON との一致を撃つ・
#: この repo は契約の写しを持たないので ADR-DOE-AGENTS-012 R47 の針がこの表を pin する)。agentd は Ended の行に**必ず** cause を書く
#: (judgment.ended-status-of の 1 点が result に載せる — cause の無い Ended は書けない)。5 語で condition の型と 1:1 にしない
#: (D-349r3a-1: reason が語を運ぶ)。completed = 自然に終わった手番(value があれば同じ result に)/ cancelled = 取り消し(#367・
#: stage graceful | forced・reason = cancel.reason)/ failed = 失敗の condition で閉じた(reason = その condition の型)/ interrupted =
#: 取り下げ(phase Withdrawn — 書き手は作った側)で走っている手番を止めた(reason = withdrawn・Withdrawn の行に足す)/ agentd-stopped =
#: agentd の停止の排水の期限で閉じた(reason = drain-deadline・条件 AgentdRestart と同じ拍)。
CauseCategory = Literal["completed", "cancelled", "failed", "interrupted", "agentd-stopped"]
CAUSE_CATEGORY_COMPLETED: CauseCategory = "completed"
CAUSE_CATEGORY_CANCELLED: CauseCategory = "cancelled"
CAUSE_CATEGORY_FAILED: CauseCategory = "failed"
CAUSE_CATEGORY_INTERRUPTED: CauseCategory = "interrupted"
CAUSE_CATEGORY_AGENTD_STOPPED: CauseCategory = "agentd-stopped"
#: 閉語彙の表(judgment.terminal-cause-of / ended-status-of が検める・契約との突合の検が読む)— Literal から導く(第 2 の並びを書かない)。
CAUSE_CATEGORIES: tuple[str, ...] = get_args(CauseCategory)
RESULT_CAUSE_KEY: str = "cause"
CAUSE_CATEGORY_KEY: str = "category"
CAUSE_REASON_KEY: str = "reason"
#: object でない結果に cause を載せる時の包み {value, cause}(ACP の resultPayloadOf が value に解く)。
RESULT_VALUE_KEY: str = "value"
CAUSE_REASON_WITHDRAWN: str = "withdrawn"
CAUSE_REASON_DRAIN_DEADLINE: str = "drain-deadline"
#: 取り消しの合図を持つ job の腕(judgment.cancel-arm-for の閉語彙): acknowledge = まだ見届けていない(割り込み +
#: status.cancel)/ force = 見届け済みで猶予を過ぎても手番が走っている(殺して Ended)/ none = 見届け済みで猶予の内
#: (手番の終わりを待つ — 終われば finalize が result.cause {graceful} を書く)。
CancelArm = Literal["acknowledge", "force", "none"]
CANCEL_ARM_ACKNOWLEDGE: CancelArm = "acknowledge"
CANCEL_ARM_FORCE: CancelArm = "force"
CANCEL_ARM_NONE: CancelArm = "none"
#: 強制の段の記録の腕(settle-record)の step の語(計器 agent-job-turn の step — JobStep の外の終端の 1 語)。
JOB_STEP_CANCEL_FORCED: str = "cancel-forced"
#: 段 12 lane 12j(agora-redesign #402): 手番は終わったが agent-job の Ended の書きが着かなかった(頭の不通・監督の置き直しとの競合)job を
#: 書き直すかの答え(judgment.end-retry-verdict の閉語彙): write = 行に Ended を書く(Pending〔監督が解いた〕/ Bound〔置き直しの試み
#: attempt N〕/ 自分の session の Running)— 手番は終わっているので同じ手番を別の session で走らせない / drop = 書かず忘れる(行が
#: 無い・終端・別の session が走らせている Running・持ち越しの上限を過ぎた)。
EndRetryVerdict = Literal["write", "drop"]
END_RETRY_WRITE: EndRetryVerdict = "write"
END_RETRY_DROP: EndRetryVerdict = "drop"
#: 段 12(agora-redesign #537 便 1): 走っている turn-record を終状態から閉じる巡回の答え
#: (judgment.turn-record-sweep-verdict の閉語彙・既知の形 = k8s の controller の reconcile)。
#: end = 記録を ended にする(手番はもう走っていない — 対の agent-job が終端「Ended / Withdrawn」か行ごと無く、
#: 記録の名乗る node が自分か、生きている node の集合に無い)/ skip = 触らない(走っている手番・置き直し待ち「#519」・
#: 生きている別の機体が持つ手番・既に ended の記録)。
TurnRecordSweepVerdict = Literal["end", "skip"]
TURN_RECORD_SWEEP_END: TurnRecordSweepVerdict = "end"
TURN_RECORD_SWEEP_SKIP: TurnRecordSweepVerdict = "skip"
#: 着かなかった Ended を持ち越す上限(ms)。頭の不通が 1 時間を越えたら忘れる(行は監督の置き直しに任せ、記録は turn-record に在る)。
UNRECORDED_END_TTL_MS: int = 3_600_000
#: custody の貸出の口の種類(POST /lease/claude | /lease/codex)。
LeaseKind = Literal["claude", "codex"]
#: profile の残量を読む資格の種類(段 7 lane 7d-3)。契約 profile の行は spec.kind(claude / codex —
#: 配置の観測の腕が預かり所の在庫から写す)で資格の種類を名乗る。観測の腕は名簿の種類ごと
#: (PROFILE_USAGE_KINDS)に家と残量を読み、行は spec.kind と名(名簿の名か別名)で結ぶ(段 12 lane 12c・
#: agora-redesign #479: claude の 1 種に閉じていた間、codex の行は観測の列に一度も入らず、予算の判定が
#: 永久に unobserved だった)。spec.kind を持たない行は PROFILE_USAGE_KIND(claude)と読む(#479 より前の
#: 行は全部 claude)。値の宣言はここ 1 点。
PROFILE_USAGE_KIND: LeaseKind = "claude"
PROFILE_USAGE_KINDS: tuple[LeaseKind, ...] = ("claude", "codex")
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
#: 生きている status(policy.hy ACTIVE-STATUSES の写し — 器の status の閉語彙は終端とこれの和ちょうど)。
#: agentd が温かい session を読む時の絞り(SessionList.statuses)。写しの扱いは上と同じ。
SESSION_LIVE_STATUSES: frozenset[str] = frozenset(
    {"pending", "booting", "running", "blocked", "blocked_api"}
)
#: transcript の候補を探す終端の一覧の 1 頁の行数と、探す行数の天井(新しい順)。候補は会話ごとの
#: 最新の終端 session を transcripts_observed_max 件で、ふつうは 1 頁で満ちる。天井は、帰属の無い
#: 古い行ばかりの器でも 1 回の参加が読む量を履歴の長さに比例させないため(天井より古い会話は載せない)。
TRANSCRIPT_SCAN_PAGE_ROWS: int = 64
TRANSCRIPT_SCAN_ROWS_MAX: int = 1024
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
NextArm = Literal["launch", "send", "resume", "rehydrate", "rebuild", "defer"]
NEXT_ARM_LAUNCH: NextArm = "launch"
NEXT_ARM_SEND: NextArm = "send"
NEXT_ARM_RESUME: NextArm = "resume"
NEXT_ARM_REHYDRATE: NextArm = "rehydrate"
#: 会話の記録から Claude Code の transcript を組み直して同じ会話を --resume で続ける腕
#: (card acp:kanban-issue:ki-c3aace97d825)。rehydrate と同じ条件(別の機体・別の家)で選ばれるが、
#: 履歴を 1 通の巨大な最初の本文へ畳む代わりに、記録の出来事を手番ごとの user / assistant の行へ
#: 写した transcript を家に置き、`claude --resume <sid>` で続ける。組み立てか検査が通らなければ
#: 同じ拍で rehydrate に戻る(judgment.rebuild-arm-of / transcript-readable-of)。
NEXT_ARM_REBUILD: NextArm = "rebuild"
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
#: 機体の置き場の語(段 10 lane 10d 便 2・agora-redesign #85)— 閉語彙は ACP の契約 agora-kinds.json の
#: node.spec.places.items と**同じ綴り**(新しい語を作らない)。⚠ profile.spec.boundary の語彙は
#: この語彙の**部分集合**(company | personal)で、cluster は口座の境界にならない(下)。
#: 段 12(card acp:kanban-issue:ki-d13566f4d5eb・決定 案 A・2026-09-19): cluster =
#: **この宿で起きる手番は、PATH の kubectl と、家の k3s(eos)に届く KUBECONFIG を持つ**。
#: 資格(口座)の区画ではなく**道具の供給**の名乗りで、運用(operate)の手番が要求する語
#: (要求 = 方策 delivery-policy の reception.rules[class=operate].open.place)。kubeconfig の在処は
#: 宣言に入れない(path は機体ごと)— 名乗る宿は agentd の環境で KUBECONFIG を向け、宣言と実体の
#: 突合は据え付けの検(dotfiles `ai provision check`)が持つ。
AgentdPlace = Literal["company", "personal", "cluster"]
#: 綴りの定義点は上の型 1 つ(実行時の照合はここから導く — 語彙を 2 度書かない)。
AGENTD_PLACES: frozenset[str] = frozenset(get_args(AgentdPlace))
#: node の spec のうち置き場の**集合**を名乗る型つきの欄(契約 agora-kinds.json v4 node.spec.places・段 11 lane 11u・
#: agora-redesign #224・依頼者の裁定 2026-09-16 = two-way door)。機体が仕える置き場の部分集合(会社 Mac = company と
#: personal の両方・pool と個人の MacBook = personal だけ)。配車(ACP の nodeAcceptsProfile)は「profile の boundary が
#: この集合に含まれる node」にだけ結び、この欄だけを読む(両向き)。1 値の spec.place(v3)は退役 — 配車は place を持つ
#: 行を行の誤りとして断るので、agentd は書かず、揃える時に落とす(NODE_SPEC_PLACE_RETIRED)。
NODE_SPEC_PLACES = "places"
#: 退役した 1 値の欄(契約 v3 node.spec.place・段 10 lane 10d 便 4)。agentd は書かない。行に残っていれば揃えの写しで落とす。
NODE_SPEC_PLACE_RETIRED = "place"
#: 同じ集合を写す labels の鍵(DEPRECATED — 読み手が残る間だけ書き続ける面・値は , 区切りの 1 文字列。配車は読まない)。
NODE_LABEL_PLACES = "places"
#: 退役した 1 値の写しの鍵(labels.place)。揃えの写しで落とす。
NODE_LABEL_PLACE_RETIRED = "place"
#: 席の settings file の名乗り(card acp:kanban-issue:ki-7b52bb76aa6e・ADR-DOE-AGENTS-004 R13・依頼書 §10-2 の受入 8):
#: 参加の宣言 [agentd].claude_settings_file を名乗った機体が、その file を**参加の拍に読めたか**を node の行へ載せる鍵。
#: 無い = この機体は名指していない(今日どおり)。値は下の 2 語ちょうど。契約の欄を増やさない(labels は自由な文字列の表)
#: ので、ACP の kind の登録の同期を待たずに読める — 観測の欄を足すと、登録が古い間の書きが 400 で断られて
#: **観測ごと固まる**(image-beat の註「登録が古い契約のままなら、契約を変えた kind の書きは 400 で断られます」)。
NODE_LABEL_SEAT_SETTINGS = "seat-settings"
#: 参加の拍に file が読めた(席の起動は --settings に合流させる)。
SEAT_SETTINGS_PRESENT = "present"
#: 参加の拍に file が無かった(参加はする・席は hook 無しで起こす・起動ごとに 1 行 log する — 依頼書 §10-2)。
#: 宿の入口の degrade(先端で揃えられない日は image の下限へ戻して立つ)で checkout が ① より古い日に出る。
SEAT_SETTINGS_MISSING = "missing"
#: 宣言の綴り(1 つの文字列に , 区切り — 宣言 file の値は文字列ちょうど・work_roots と同じ形)と env・labels の区切り。
PLACES_SEPARATOR = ","
#: node が持つ作業場の根(段 10 lane 10y・agora-redesign #110・依頼者の裁定 2026-09-15 案 C・既知の形 = volume topology の先読み):
#: 契約 agora-kinds.json node.spec.workRoots(文字列の list・欄の定義点は契約 — ACP 側 10d)。配車は絶対 path の work_dir をこの根で
#: 篩う(`~/…` はどの node も通す)。agentd は宣言(join の [agentd].work_roots / --work-roots → DOEFF_AGENTD_WORK_ROOTS)が在る時だけ
#: 書く(judgment.node-work-roots-of)。各根は `/` で終わる絶対 path か `~/`(接頭辞の曖昧さを宣言で塞ぐ・検は join.work-roots-of)。
NODE_SPEC_WORK_ROOTS = "workRoots"
WORK_ROOTS_ENV = "DOEFF_AGENTD_WORK_ROOTS"
#: 宣言の綴り(1 つの文字列に , 区切り — 宣言 file の値は文字列ちょうど)と env の区切り。
WORK_ROOTS_SEPARATOR = ","
#: 根の本数の上限(契約 agora-kinds.json node.spec.workRoots.maxItems の写し — 越える宣言は行ごと断られるので起動の門で先に止める)。
WORK_ROOTS_MAX = 16


@dataclass(frozen=True)
class WorkRoots:
    """node が持つ作業場の根の宣言(join.work-roots-of の答え — 検を通った根を宣言の順・重複なしで運ぶ)。"""

    roots: tuple[str, ...]


#: node が持つ作業場(段 12 lane 12j・agora-redesign #575 便 2・#557 案 A の後半): 契約 agora-kinds.json node.spec.workDirs(家からの相対
#: `~/repos/<名>` / `~/<名>` の list・欄の定義点は契約 — ACP 側 #575 便 1)。配車は `~/…` の work_dir をこの列で篩う(持つ node だけ候補・
#: 欄の無い node は篩わない・空の list = 「何も持たない」の宣言)。agentd は join で自分の家(~ の直下と ~/repos の直下の .git を持つ dir)
#: から導いて名乗る(runtime.join_plan が一覧を読み、判断は join.held-work-dirs-of の 1 点)。env は join が据える。
NODE_SPEC_WORK_DIRS = "workDirs"
WORK_DIRS_ENV = "DOEFF_AGENTD_WORK_DIRS"
WORK_DIRS_SEPARATOR = ","
#: 上限(契約 node.spec.workDirs.maxItems の写し)。64 → 512(2026-09-18 02:32 実弾: 会社 Mac の家は ~ に 17・~/repos に 175 = 192 の
#: checkout を持ち、64 では work-dirs-of が断って agentd が crash loop に落ちた — 上限は機体の事実に合わせる・ACP agora-kinds.json と同じ値)。
WORK_DIRS_MAX = 512
#: 家のどの直下を読むか("" = ~ の直下・"repos" = ~/repos の直下)— 区画の置き場の作法(~/<名> か ~/repos/<名>)の写し。
WORK_DIRS_SCAN_PARENTS: tuple[str, ...] = ("", "repos")


@dataclass(frozen=True)
class WorkDirs:
    """node が持つ作業場の宣言(join.work-dirs-of / held-work-dirs-of の答え — 検を通った作業場を綴りの順・重複なしで運ぶ)。"""

    dirs: tuple[str, ...]


#: node が持つ作業場の**根**(段 12 lane 12j 追補・card acp:kanban-issue:ki-3bfe48a9d5dc・2026-09-19): 契約 agora-kinds.json
#: node.spec.workDirRoots(家からの相対の根 `~/.worktrees/` の list・欄の定義点は契約)。名簿(workDirs)は根の下の中身を
#: 綴れない — 作法どおり worktree は全部 `~/.worktrees/` の下に在り(実測 2026-09-19 で会社 Mac に 3,105・名簿の上限は 512)、
#: 名簿の導出(held-work-dirs-of)は隠し dir を 1 つも数えない。⇒ `~/.worktrees/…` を作業場にした手番はどの機体も持たず
#: no-node-for-partition で座り続けた(実測 aj-FADYB38ND05SQWWJCGJHHTSPMT)。配車は `~/…` の work_dir を workDirs と
#: この根の両方で篩う(ACP Decide.nodeHoldsWorkDir の 1 点)。
NODE_SPEC_WORK_DIR_ROOTS = "workDirRoots"
WORK_DIR_ROOTS_ENV = "DOEFF_AGENTD_WORK_DIR_ROOTS"
WORK_DIR_ROOTS_SEPARATOR = ","
#: 根の本数の上限(契約 node.spec.workDirRoots.maxItems の写し)。
WORK_DIR_ROOTS_MAX = 16
#: 候補の根(この機体で名乗りうる根の**宣言**)。実勢で篩う前の一覧で、agentd は **その dir が現に在る機体でだけ**
#: 名乗る(runtime.home_root_entries が在否を読み、判断は join.held-work-dir-roots-of の 1 点)。既定の 1 本は作業場の
#: 置き場の作法(dotfiles agentcli/worktree_provision.worktrees_root — worktree は全部 ~/.worktrees/ の下)の写し。
#: ⚠ 宣言 file 由来の根(work_roots・pod は `~/` を名乗る)はここに入れない — 家からの相対の根として名乗ると
#: 「家を持つ = 家の下の何でも持つ」と読まれ、checkout を持たない pod へ手番が飛ぶ。
WORK_DIR_ROOT_CANDIDATES: tuple[str, ...] = ("~/.worktrees/",)

#: card acp:kanban-issue:ki-40021864e62f(2026-09-19・ACP 側の依頼 lt-FMEPYFTCRQSKV4V8V0A82VQQFC・既知の形 = k8s の volume
#: topology key): 契約 agora-kinds.json node.spec.custodyBorrower —— この機体が**預かり所へ名乗る借り手の身元の等価鍵**。
#: 配車(ACP Decide.nodeCustodyKey / leaseHeldNodes)は口座の錠(1 認証 1 借り手)をこの鍵で束ね、**等値比較だけ**をする
#: (ACP は預かり所の知識を持たない)。⚠ 根 = 預かり所の錠の単位は**借り手名**で、同じ借り手の再要求は再具現・409 は別の
#: 借り手にだけ(custody 冊 0008 law codex-lease-locks-one-host-per-account ②)。pool の pod は全部同じ ServiceAccount で
#: 名乗るので借り手は 1 つ(実読 2026-09-19 05:5xZ: 9 口座の heldBy が全部 acp-control/default)。ACP が node 行を鍵に
#: していた間、pool の入れ替え(旧 pod を cap 0 で排水)のたびに旧 pod へ束ねられた口座の待ちが凍った(05:34Z: 待ち 137 本の
#: うち 110 本・入れ替えは 17.5 時間に 11 回)。⚠ 欄は**任意**で、名乗らない agentd の判定は今日と 1 bit も変わらない
#: (ACP は node 名へ落ちる)= 版が混ざる艦隊の排水路。綴りの定義点はここ 1 点で、材料は預かり所へ名乗る身元の 2 つ
#: ちょうど(handlers.CustodyHttp._identity_headers と同じ材料 —— 判断は join.custody-borrower-of の 1 点・I/O は
#: composition root の runtime.settings_from_env)。
NODE_SPEC_CUSTODY_BORROWER = "custodyBorrower"
#: SA token で名乗る機体(pod)の鍵の綴り: `sa:<namespace>/<serviceaccount>` —— 預かり所の backend が TokenReview で解く
#: 借り手名(ns/sa)と 1 対 1。token の claims から読む(署名は検めない —— 名乗るだけで、認証するのは預かり所の側)。
CUSTODY_BORROWER_SA_PREFIX = "sa:"
#: 借り手札で名乗る機体(Mac)の鍵の綴り: `key:<sha256(札) の先頭 16 hex>` —— **札の実値は 1 byte も載せない**
#: (行は誰でも読める)。16 hex = 64 bit で、艦隊の機体の数に対して衝突は無視できる。
CUSTODY_BORROWER_KEY_PREFIX = "key:"
CUSTODY_BORROWER_KEY_HEX_CHARS = 16
#: projected token の JWT の claims の綴り(k8s の ServiceAccount token の正本)。
CUSTODY_SA_NAMESPACE_CLAIM = "kubernetes.io/serviceaccount/namespace"
CUSTODY_SA_NAME_CLAIM = "kubernetes.io/serviceaccount/service-account.name"


@dataclass(frozen=True)
class WorkDirRoots:
    """node が持つ作業場の根の宣言(join.work-dir-roots-of / held-work-dir-roots-of の答え — 検を通った根を綴りの順・重複なしで運ぶ)。"""

    roots: tuple[str, ...]


@dataclass(frozen=True)
class Places:
    """機体が仕える置き場の集合の宣言(join.places-of の答え — 検を通った語を宣言の順・重複なしで運ぶ・段 11 lane 11u)。"""

    words: tuple[str, ...]


@dataclass(frozen=True)
class SeatEnv:
    """席へ運ぶ env の宣言(join.seat-env-of の答え — 参加の門を通った対を宣言の順・重複なしで運ぶ・
    段 12・agora-redesign #520)。Places / WorkRoots と同じ形の器で、値の**語彙は持たない**。"""

    pairs: tuple[tuple[str, str], ...]
#: 置き場の集合の env(join が宣言 file の [agentd].places / flag --places から , 区切りで据える — 段 11 lane 11u)。
#: 無い agentd は参加しない。1 値の DOEFF_AGENTD_PLACE(段 10 lane 10d 便 2)は退役。
PLACES_ENV = "DOEFF_AGENTD_PLACES"
#: node の spec.capacity(同時に走らせられる手番の数 — 段 10 lane 10d・agora-redesign #85)。join が宣言 file の
#: [agentd].capacity / flag --capacity から据える。無い agentd は参加しない(runtime.settings_from_env)。
CAPACITY_ENV = "DOEFF_AGENTD_CAPACITY"
#: 段 12 lane 12j(agora-redesign #304 便 2): 停止(SIGTERM)の排水の上限(秒)。宣言 file の [agentd].drain_seconds から join が運ぶ。
#: 0 / 無し = 今日どおり即座に走っている手番を閉じる(AgentdRestart)。
DRAIN_SECONDS_ENV = "DOEFF_AGENTD_DRAIN_SECONDS"
#: node の status.observations.transcripts に載せる件数の上限(card acp:kanban-issue:ki-95169e9e265d 便 1・
#: ADR-DOE-AGENTS-012 R56)。宣言 file の [agentd].transcripts_observed_max から join が運ぶ。
#: 無し = AgentdSettings.transcripts_observed_max の既定(この file の 1 点)。
TRANSCRIPTS_OBSERVED_MAX_ENV = "DOEFF_AGENTD_TRANSCRIPTS_OBSERVED_MAX"
#: 契約 agora-kinds.json kinds.node.status.observations.transcripts の maxItems(宣言で重ねられる上限の天井)。
#: 契約の値の写しで、超える宣言は join が断る(行ごと 400 で断られるより、参加の拍で名指す)。
TRANSCRIPTS_OBSERVED_MAX_CEILING = 64
#: 段 12 lane 12j(agora-redesign #367・既知の形 行 3 (h) = kubelet の最小版 / Buildkite の agent version floor): agentd が参加時に名乗る
#: 自分の版(契約 agora-kinds.json kinds.node.spec.agentd {protocol, revision, build})。protocol = ACP との wire の版(整数・
#: **doeff-agents が wire を変える時にここを 1 進める** — 配置の床 scheduling.json ladder.fields.agentdProtocolFloor が比べる)。
#: revision = doeff-agents の git sha(人が読む・据え付けの側が宣言 file の [agentd].revision か env で刻む — 刻まれていなければ
#: AGENTD_REVISION_UNSTAMPED を名乗る〔嘘の sha を書かない・契約の minLength 7 を満たす語〕)/ build = image の tag か local。
#: 2 = 段 12 lane 12k(agora-redesign #349 行 3 粒 3a): Ended の行は必ず status.result.cause を運ぶ(契約 scheduling.json resultCause)。
#: 読み手(kanban-health の不変条件 ended-jobs-carry-a-cause)は protocol >= 2 の node に結ばれた Ended だけを数える —
#: 「版が新しい agentd」を sha の順ではなく wire の整数で言う(依頼者の裁定 2026-09-17 00:5x「protocol の整数で比べる」)。
AGENTD_PROTOCOL = 2
AGENTD_REVISION_ENV = "DOEFF_AGENTD_REVISION"
AGENTD_BUILD_ENV = "DOEFF_AGENTD_BUILD"
AGENTD_REVISION_UNSTAMPED = "unstamped"
AGENTD_BUILD_LOCAL = "local"
#: node の spec の欄の綴り(契約 kinds.node.spec.agentd とその中の 3 欄)。
NODE_SPEC_AGENTD_KEY = "agentd"
NODE_SPEC_AGENTD_PROTOCOL_KEY = "protocol"
NODE_SPEC_AGENTD_REVISION_KEY = "revision"
NODE_SPEC_AGENTD_BUILD_KEY = "build"
HOMES_ROOT_ENV = "DOEFF_AGENTD_HOMES_ROOT"
#: 自動記憶(auto-memory)の置き場の根。会話 1 つにつき <根>/<会話 id> が 1 つ(judgment.memory-home-of)。
#: 既定は homes-root と同じ導き方(runtime.settings_from_env)で、資格の家の**外**に置く —
#: 家の中に置くと預かり所が別の account を貸した拍に置き場が変わり、記憶が会話から剥がれる。
MEMORY_ROOT_ENV = "DOEFF_AGENTD_MEMORY_ROOT"
#: card acp:kanban-issue:ki-c3aace97d825: 会話の記録から transcript を組み直して `--resume` で続けるか
#: (AgentdSettings.transcript_rebuild_enabled)。"0" / "false" / "no" / "off" で切る(戻し方 = この 1 つ)。
#: 既定は立っている —— 組み立てか起動前の検査が通らない拍は同じ拍で rehydrate に戻るので、切らなくても止まらない。
TRANSCRIPT_REBUILD_ENV = "DOEFF_AGENTD_TRANSCRIPT_REBUILD"
CUSTODY_URL_ENV = "AGORA_CUSTODY_URL"
BORROWER_KEY_PATH_ENV = "AGORA_BORROWER_KEY_PATH"
#: 段 10 lane 10y(agora-redesign #110・依頼者の裁定 問い 3 案 A): k8s の pod の身元 = ServiceAccount の token の file。
#: 宣言(join の [custody].service_account_token_file / flag --service-account-token-file)が在る時だけ、預かり所への要求に
#: Authorization: Bearer で載せる(預かり所の k3s の backend が TokenReview で解く・借り手名 = ns/sa)。借り手札の経路
#: (X-Borrower-Key — Mac の agentd)はそのまま残す。
CUSTODY_SA_TOKEN_PATH_ENV = "AGORA_CUSTODY_SA_TOKEN_PATH"
#: 段 10 lane 10y(agora-redesign #110・依頼者の裁定 2026-09-15): agentd が読んだ宣言 file の指紋(sha256 の小文字 hex)。join が
#: --config の file の bytes から導いて据える。ACP の kind node の capacity は declaredByFile(ACP 段 10 lane 10t 便 1b)で、誕生を
#: 含む書きは header x-declaration-sha256 を伴わなければ 403 declaration-needs-fingerprint — 宣言 file を読まない agentd は行を作れない。
DECLARATION_SHA256_ENV = "DOEFF_AGENTD_DECLARATION_SHA256"
#: 指紋を運ぶ header の綴り(ACP docs/contracts/operator-approval.json の fingerprint.header の写し)。
DECLARATION_FINGERPRINT_HEADER = "x-declaration-sha256"
#: 段 10f 便 2 追補 3(agora-redesign #82・依頼者 2026-09-14 17:1x 実測「agent が自分の会話 id を答えられない」): 手番の process
#: の env(charter.session_env — host の launch-spawn-env が非 auth の overlay として spawn の env に混ぜる)に置く会話の身元。
#: AGORA_CONVERSATION_ID = 会話の id(c-…・agent-job の spec.subject)/ AGORA_SEAT_OPENER = 会話の行の spec.opener の逐語
#: (operator / machine / system)。`ai tell` / `ai forward` / `ai artifact put` の差出人・著者はこの会話 id ちょうど(便 3 = CLI が読む側)。
#: 置く点は judgment.charter-with-conversation-env の 1 点(incarnation-charter-of が呼ぶ — launch / resume / rehydrate)。
CONVERSATION_ID_ENV = "AGORA_CONVERSATION_ID"
SEAT_OPENER_ENV = "AGORA_SEAT_OPENER"
#: agora-redesign #520(段 12・既知の形 = kubelet が node 局所の宣言の表を workload の env へ具現化する): 機体の参加の宣言
#: (join の [agentd].seat_env — 改行区切りの `NAME=value` の行)が名乗った、席へ運ぶ env の対。join が解いて JoinSpec →
#: この env(同じ改行区切りの形)→ AgentdSettings.seat_env → charter.session_env(judgment.charter-with-seat-env の 1 点)。
#: ⚠ doeff は値の**語彙を知らない**(宛先の綴りは宣言の側 = ACP の ConfigMap と読み手 dotfiles が持つ)— ここは写しを運ぶ口で、
#: 第 2 の既定を doeff に作らない。継承の名簿(policy.SPAWN_INHERITED_ENV_KEYS)は 1 語も開けない: 席が機体の env を継ぐ形
#: (実弾 #95)はそのまま締めたまま、**宣言された値だけ**が charter を通って届く。
SEAT_ENV_ENV = "DOEFF_AGENTD_SEAT_ENV"
#: seat_env の宣言の行の区切り(宣言 file と env の両方で同じ 1 つの綴り — 読みは join.seat-env-of の 1 点)。
SEAT_ENV_SEPARATOR = "\n"
HOST_BACKEND_ENV = "DOEFF_SESSIONHOST_BACKEND"
HEADLESS_DIR_ENV = "DOEFF_SESSIONHOST_HEADLESS_DIR"
SESSION_HOOKS_ENV = "DOEFF_AGENTD_SESSION_HOOKS"
#: card acp:kanban-issue:ki-7b52bb76aa6e(2026-09-21・ADR-DOE-AGENTS-004 R13・既知の形 = seat_env と同じ kubelet 型): 機体の参加の
#: 宣言 [agentd].claude_settings_file が名指した「席の settings file」(dotfiles claude-hooks/seat-settings.json = hook の proxy
#: 登録 1 枚)の**絶対 path**。join が 3 つの門(session_hooks = inherit / 読めたなら JSON の object / 読めたなら doeff が置く鍵を
#: 含まない — **宣言そのものの誤りだけ**)を通してから据え、launch / headless が**起動の拍ごとに**読んで params claude_settings に載せ、
#: impls/claude_code.hy build-claude-argv(`--settings` の唯一の合流点)が記憶の置き場の鍵(CLAUDE-AUTO-MEMORY-DIR-SETTING)と合わせて
#: 1 つの JSON に合流する。名指しが無い = 今日どおり(argv は 1 byte も変わらない)。⚠ **名指した file が読めないのは断る理由にしない**
#: (R13 の訂正・依頼書 §10-2): 不在でもこの env には path が載る — 読むのは起動の拍ごとなので、宿の checkout が後から追いついた日は
#: その拍から hook が届く。不在の名乗りは join の 1 行(seat-settings-file-absent)・起動ごとの 1 行・node の行の labels.seat-settings。
#: inherit の委ね先(config-dir の持ち主)は doeff 自身なので、doeff が運ぶ。
CLAUDE_SETTINGS_FILE_ENV = "DOEFF_AGENTD_CLAUDE_SETTINGS_FILE"
OWNERSHIP_ENV = "DOEFF_AGENTD_OWNERSHIP"
OWNERSHIP_PROOF_ENV = "DOEFF_AGENTD_OWNERSHIP_PROOF"
#: host(oracle parse_args / host.hy parse-args)の argv の綴り(join が組む・valve が読む)。
HOST_DB_FLAG = "--db"
HOST_SOCKET_FLAG = "--socket"
HOST_MAX_RUNNING_FLAG = "--max-running"
HOST_MAX_RUNNING_UNLIMITED = "none"
HOST_BACKEND_FLAG = "--backend"
#: 2026-09 の従量課金の便(lane A): 従量課金の binding kind を
#: 受ける host の方針。値を取らない旗で、既定 off。綴りの定義点はここ 1 つ(host.hy の
#: parse-args が読み、join が argv に足す)。同名の env は **作らない** — 課金の方針を
#: env の 1 語で変えられる形は ADR-DOE-AGENTS-004 R10(d) が退けた形と同じ。
HOST_ALLOW_METERED_BILLING_FLAG = "--allow-metered-billing"
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
#: 排水の合図の file(card acp:kanban-issue:ki-567f2dd6140f): state_dir の下に置いた **在否**だけで
#: settings.draining が決まる(中身は log に出す理由の 1 行)。signal ではなく file なのは、入れ替えの
#: 途中で agentd 自身が再起動しても排水の意思が残るため。判断の座は judgment.declared-capacity-of の
#: 1 点のまま(この file を読むのは handler 側 = runtime.drain_file_path / run_loop の port)。
JOIN_DRAIN_FILE = "drain"
#: process の役(card acp:kanban-issue:ki-567f2dd6140f): 1 つの `join` の宣言から、ACP の node agent
#: (agentd)と セッションの所有者(host)を**別々の process** として起こせるようにする閉語彙。
#: both = 今日どおり 1 process で両方(既定 — 既定のまま撃った起動は env の束も host の argv も今日と同じ)。
#: agentd = ACP 側だけ(host は起こさない・socket の client として繋ぐ)。host = 器だけ(agentd の thread を起こさない)。
JOIN_ROLE_BOTH = "both"
JOIN_ROLE_AGENTD = "agentd"
JOIN_ROLE_HOST = "host"
JOIN_ROLES: frozenset[str] = frozenset({JOIN_ROLE_BOTH, JOIN_ROLE_AGENTD, JOIN_ROLE_HOST})
#: 機体の所有の等級(契約 agora-kinds.json node.status.observations.ownership.grade の閉語彙)と
#: 検の方法(proof)の綴り: gce-project:<project-id> = GCE の metadata server の project-id が一致 /
#: file:<絶対 path>=<期待する値> = その file の中身(strip)が値と一致 /
#: declared = 宣言のみ(検なし — 機体の所有の判定は別の座が持つ)。
OwnershipGrade = Literal["company", "personal"]
OWNERSHIP_GRADES: frozenset[OwnershipGrade] = frozenset({"company", "personal"})
#: 会社の綴り(段 10 lane 10y・agora-redesign #110): 機体の所有の等級(OwnershipGrade)と口座の置き場
#: (profile.spec.boundary・AgentdPlace)の company は契約で同じ綴り。会社の口座の行を観測してよい機体かの
#: 判断(judgment.profile-rows-held)が両側をこの 1 語で読む。
OWNERSHIP_GRADE_COMPANY: OwnershipGrade = "company"
OWNERSHIP_PROOF_GCE_PREFIX = "gce-project:"
#: 機体の耐久の file の中身を証拠にする検の方法(card ki-d6cc49cbf33f 決定 D4 ①): 綴りは
#: `file:<絶対 path>=<期待する値>`。据え付けの側(宣言を描く道具)が機体の身元を台帳で判じ、その証拠の
#: file と値を proof に描く — agentd は台帳を持たず(hostname も置き場も所有の判定に使わない)、描かれた
#: 証拠が動いていないことだけを検める。綴りの割り方は下の ownership_proof_file_parts の 1 点で、判断
#: (join.ownership-of / ownership-verdict)と読み(handlers.probe_ownership)が同じ割りを借りる。
OWNERSHIP_PROOF_FILE_PREFIX = "file:"
OWNERSHIP_PROOF_FILE_SEPARATOR = "="
OWNERSHIP_PROOF_DECLARED = "declared"
#: 特権の置き場の綴り(card acp:kanban-issue:ki-d6cc49cbf33f 決定 D4 ③): spec.places にこの語を名乗る
#: 機体は、参加の前に所有の証拠(OWNERSHIP_PROOF_GCE_PREFIX / OWNERSHIP_PROOF_FILE_PREFIX)を撃たなければ
#: ならない(declared も、所有を 1 欄も名乗らない宣言も断る — join.ownership-verdict の 1 点)。綴りは上の
#: OWNERSHIP_GRADE_COMPANY を借りる(会社の語を 2 度書かない)。
#: ⚠ ACP の配備の宣言 privilegedPlaces とは**別の役**で、写しではない: あちらは配備の方策
#: 「この行をこの語で信じてよいか」・こちらは機体の自制「証拠なしにこの語を名乗らない」。参加の前の
#: 機体から配備の宣言は読めない(ACP へ繋ぐ前の拍)ので写しにできず、第 2 の定義点にもならない。
#: 非特権の語(personal / cluster)の扱いはこの集合の外 = 今日のまま(証拠を要求しない)。
PRIVILEGED_PLACES: frozenset[str] = frozenset({OWNERSHIP_GRADE_COMPANY})


class OwnershipProofFile(NamedTuple):
    """検の方法 `file:<path>=<値>` を割った結果(読む file の path と、そこに在るべき値)。"""

    path: str
    expected: str


def ownership_proof_file_parts(proof: str) -> OwnershipProofFile | None:
    """検の方法 `file:<path>=<値>` を (path, 期待する値) に割る(最初の `=` 1 つで割る — 値に `=` が
    在ってよい)。綴りが外れる(prefix が違う・`=` が無い・path か値が空)= None。"""
    if not proof.startswith(OWNERSHIP_PROOF_FILE_PREFIX):
        return None
    rest = proof[len(OWNERSHIP_PROOF_FILE_PREFIX) :]
    path, separator, expected = rest.partition(OWNERSHIP_PROOF_FILE_SEPARATOR)
    if not separator or not path or not expected:
        return None
    return OwnershipProofFile(path=path, expected=expected)

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
    #: 宣言 file の bytes の sha256(小文字の hex 64 桁 — 段 10 lane 10y・agora-redesign #110)。file なし = None。
    #: 読んだ file そのものの指紋で、node の capacity(ACP の kind node の declaredByFile)を書く時に
    #: header DECLARATION_FINGERPRINT_HEADER で運ぶ(engine は値を検めず出来事の封筒に記録・突合は日次の見張り)。
    sha256: str | None = None


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
    #: node の spec.capacity(宣言 file の [agentd].capacity・flag --capacity・必須 — 段 10 lane 10d)。
    capacity: int
    #: 機体が仕える置き場の集合(宣言 file の [agentd].places・flag --places・, 区切り・必須で空でない — 段 11 lane 11u)。
    #: node の spec.places に名乗り、集合に無い置き場の口座の job は起こさない(I5)。宣言の順・重複なし。
    #: ⚠ 閉語彙(AGENTD_PLACES)と重複の検は join.places-of の 1 点 — ここは検を通った値を運ぶ欄で、
    #: 型は tuple[str, ...](Hy の側は Literal へ絞れないので、2 つ目の検を型で偽装しない)。
    places: tuple[str, ...]
    #: 会話の記録の service の URL(段 9f lane 9f-2 — 宣言 file の [record].url・flag --record)。None = 二重書きなし。
    record_url: str | None = None
    #: 預かり所へ名乗る ServiceAccount の token の file(段 10 lane 10y — 宣言 file の [custody].service_account_token_file・
    #: flag --service-account-token-file)。None = 名乗らない(借り手札だけ)。
    service_account_token_file: str | None = None
    #: 読んだ宣言 file の指紋(JoinDeclaration.sha256 の写し・段 10 lane 10y)。None = 宣言 file なし(flag だけの参加)。
    declaration_sha256: str | None = None
    #: node が持つ作業場の根(段 10 lane 10y 案 C — 宣言 file の [agentd].work_roots)。None = 名乗らない(spec に欄を書かない)。
    work_roots: tuple[str, ...] | None = None
    #: node が持つ作業場(段 12 lane 12j・#575 便 2 — composition root が家の一覧から導く)。None = 導いていない(欄を書かない)。
    work_dirs: tuple[str, ...] | None = None
    #: node が持つ作業場の**根**(段 12 lane 12j 追補・card acp:kanban-issue:ki-3bfe48a9d5dc — composition root が
    #: 候補の根の**在否**から導く)。None = 導いていない(欄を書かない)・空の tuple = どの候補の根も無い。
    work_dir_roots: tuple[str, ...] | None = None
    #: 従量課金の binding kind を受けるか(従量課金の便 lane A — 宣言 file の
    #: [agentd].allow_metered_billing・flag --allow-metered-billing)。False = 受けない(既定)。
    #: 真のときだけ join が host の argv へ値なしの旗を足す。env は作らない(方針は argv の 1 点)。
    allow_metered_billing: bool = False
    #: 停止(SIGTERM)の排水の上限(秒 — 宣言 file の [agentd].drain_seconds・段 12 lane 12j・agora-redesign #304 便 2)。0 = 排水しない(既定)。
    drain_seconds: int = 0
    #: node の観測に載せる transcript の件数の上限(宣言 file の [agentd].transcripts_observed_max・
    #: ADR-DOE-AGENTS-012 R56)。None = 名乗らない(env に現れず、agentd は AgentdSettings の既定を使う)。
    transcripts_observed_max: int | None = None
    #: agentd の版の刻印(段 12 lane 12j・agora-redesign #367 — 宣言 file の [agentd].revision / build・任意)。None = 名乗らない(env に現れず、
    #: agentd は AGENTD_REVISION_UNSTAMPED / AGENTD_BUILD_LOCAL を名乗る)。
    revision: str | None = None
    build: str | None = None
    #: 席へ運ぶ env の対(agora-redesign #520 — 宣言 file の [agentd].seat_env・宣言の順・重複なし)。空 = 宣言しない
    #: (今日どおりの機体 — env SEAT_ENV_ENV に現れない)。⚠ 資格の形の名と会話の身元の名は join.seat-env-of の門が
    #: 断る(参加しない)ので、ここは検を通った宛先の対を運ぶ欄。
    seat_env: tuple[tuple[str, str], ...] = ()
    #: 席の settings file(card acp:kanban-issue:ki-7b52bb76aa6e — 宣言 file の [agentd].claude_settings_file・任意)。
    #: join-spec-of は宣言の綴りを運び(空 = None = 名乗らない)、composition root(runtime.join_plan)が `~` を agentd の HOME で
    #: 展開して 3 つの門(session_hooks = inherit / 読めたなら JSON の object / 読めたなら doeff が置く鍵を含まない)を通し、
    #: **file が読めた日も読めない日も同じ**絶対 path をこの欄へ据え直す(不在は断らない — R13 の訂正・依頼書 §10-2)。
    #: None = 名指していない(env CLAUDE_SETTINGS_FILE_ENV に現れない)。
    claude_settings_file: str | None = None
    #: 席の家へ運ぶ共通の指示の**宣言の綴り**(card acp:kanban-issue:ki-62aa1f4e9c9c 決定 D11 —
    #: 名簿は policy.CARRIED-INSTRUCTION-SOURCES の 1 点)。(名簿の鍵, 宣言の綴り)の対の列で、
    #: 名簿の順・名指した種だけ。空 = 1 種も名指していない(env に 1 本も現れない = 今日どおり)。
    #: 形の門(絶対 path か `~/…`)は join.instruction-sources-of、`~` の展開と env への据え付けは
    #: composition root(runtime.join_plan)。⚠ 欄を**種ごとに**増やさない: 1 種足す操作が欄を 2 つ
    #: 増やす形は D11 が退けた形そのもの(d8472e1a の教訓)。
    instruction_sources: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class JoinPlan:
    """宣言から導いた起動の形: host の argv と env の束(名と値の対の列・宣言の順)。"""

    host_argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ProbeAnswer:
    """OwnershipProbe の答え: 検の材料の値(gce-project = metadata の project-id・file: = その file の
    中身を strip したもの)。None = 読めない。"""

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
    ``present`` = 家の dir が実在する(中身は検めない — 残量の読みの葉が答える)。
    ``aliases`` = 登録簿の別名(段 12 lane 12c・agora-redesign #479: ACP の行の名が預かり所の別名
    〔codex-personal〕で名簿の名〔personal〕と違う口座を、行の名 → 家に結ぶ材料。既定は無し)。"""

    name: str
    home: str
    present: bool
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class PaneSeat:
    """この機体の pane の席が担っている 1 つの会話の席(段 12・agora-redesign #577)。

    読み口は dotfiles の 1 点(handlers.py の PANE_SESSIONS_COMMAND = `ai pane-sessions --json`)で、
    agentd は pane や herdr の語彙を持たない — 受けるのは会話の id・断面の id・状態の 2 語・profile の名
    ちょうど。``profile`` = "" は「機体が実測できなかった」(口座を推し量らない材料)。"""

    conversation_id: str
    session_id: str
    #: idle | busy(SESSION_OBSERVED_IDLE / SESSION_OBSERVED_BUSY と同じ綴り)。
    state: SessionObservationState
    profile: str


@dataclass(frozen=True)
class PaneSeatsUnavailable:
    """pane の席を読めなかった(読み口が無い機体・期限・非 0 の終了・JSON でない答え)。

    ⚠ 例外にしない: 参加の腕は自分の session の観測を**必ず**書く(pane の読みの失敗が node の
    観測そのものを止めると、機体が持つ温かい session が ACP から見えなくなる)。理由は人が読む 1 文で、
    同じ理由は 1 度だけ log する(pool の pod は読み口を持たないので周期ごとに同じ行を吐かない)。"""

    reason: str


PaneSeatsOutcome: TypeAlias = "tuple[PaneSeat, ...] | PaneSeatsUnavailable"


@dataclass(frozen=True)
class DriverResolution:
    """host の読み口 ``drivers.list`` の答えの 1 項(ADR-DOE-AGENTS-012 R61): 種類(charter.agent_type の語)・
    起動する実行ファイルの名(drivers.DRIVER_EXECUTABLE)・host が子 process を起こす実効 env で見つかった path
    (None = 見つからない)。観測の材料で、申告の判断は judgment.launchable-agent-kinds。"""

    agent_type: str
    executable: str
    path: str | None


@dataclass(frozen=True)
class DriversUnavailable:
    """host の実行ファイルの在否を読めなかった(ADR-DOE-AGENTS-012 R61)。

    ``reachable`` = host の socket には届いた(断り — 読み口を持たない旧い host の unknown method・答えの形の誤り)か。
    False = 届かない(host が降りている・入れ替えの途中)— その拍の器の眺めの読み(SessionList)も落ちる。
    ⚠ 例外にしない: どちらも「この node は何も起動できると言えない」の観測で、申告は空になる(前の申告や固定の表へ
    戻らない)。理由は人が読む 1 文で、同じ理由は 1 度だけ log する(AgentdState.drivers_note)。"""

    reason: str
    reachable: bool


HostDriversOutcome: TypeAlias = "tuple[DriverResolution, ...] | DriversUnavailable"


@dataclass(frozen=True)
class ProfileObservation:
    """profile の行に書く status.observed(契約 {window, remaining, resetAt, observedAt, node})。"""

    observed: JSONObject


@dataclass(frozen=True)
class ProfileUnobserved:
    """この拍は書かない profile(断られた・単位が違う・窓の材料が無い)。理由は log に 1 行。"""

    reason: str


@dataclass(frozen=True)
class PublishWorker(EffectBase):
    """この機体の worker の面(階級・空き・借りられる profile)と残量行を cluster の艦隊の断面へ公開する
    (段 12 lane 12j・agora-redesign #445 — 既知の形 = kubelet の NodeStatus: 容量の報告は worker の義務で
    controller は置かない)。口は dotfiles agentcli の 1 点(handlers.py の PUBLISH_COMMAND =
    `ai route publish-worker --json` — 何を公開するかはその葉が組み、ここに写しを作らない)。
    ``cadence_seconds`` = 公開の拍の申告(読み手は拍から古さの閾を導く — 秒の定数を発明しない)・
    ``running_turns`` = いま走らせている手番の id(測った事実・空 = 測って 0 本)・``poll_tick_at_ms`` = この拍の壁時計。
    家の在る profile を持つ機体(Mac)だけが撃つ(判断は observe-profiles の held の有無の 1 点・pool の pod は撃たない)。
    結果 = WorkerPublished(失敗は値で返り、agentd は log 1 行 + 計器 — 配車は止めない)。"""

    cadence_seconds: int
    running_turns: tuple[str, ...]
    poll_tick_at_ms: int


@dataclass(frozen=True)
class WorkerPublished:
    """PublishWorker の結末: ok = 公開が着いた・worker = 名乗った worker の名(読めなければ空)・detail = 失敗の理由(ok なら空)。"""

    ok: bool
    worker: str
    detail: str


@dataclass(frozen=True)
class ProfileNotHeld:
    """この機体が資格を持たない profile(usage の列に無い)— 書かず、log もしない。"""


ProfileVerdict: TypeAlias = "ProfileObservation | ProfileUnobserved | ProfileNotHeld"


# ------------------------------------------------------------------ 値の宣言(1 点)


@dataclass(frozen=True)
class AgentdSettings:
    """agentd の値の宣言。既定値がここ 1 点、env からの上書きは runtime.py が行う。"""

    node_name: str
    #: 自分の node の spec.capacity(段 10 lane 10d・agora-redesign #85): 機体の宣言 file の [agentd].capacity の写し。
    #: agentd は自分の node の行を宣言から名乗る(judgment.node-spec-of / node-spec-declared)。composition root
    #: (runtime.settings_from_env)は宣言が無ければ参加を断るので、この既定 0(手番を受けない)は検体の値。
    node_capacity: int = 0
    #: 自分が仕える置き場の集合(段 11 lane 11u・#224)— 宣言 file の [agentd].places の写し(宣言の順・重複なし)。
    #: node の spec.places に名乗り、集合に無い置き場の口座を持つ job は起こさない(judgment.credential-place-mismatch)。
    #: composition root は宣言が無い・空なら参加を断るので、この空の既定は検体の値。
    places: tuple[str, ...] = ()
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
    #: 自動記憶(auto-memory)の置き場の根 — 会話 1 つにつき <根>/<会話 id>(judgment.memory-home-of)。
    #: 既定は composition root(runtime.settings_from_env)が homes-root と同じ導き方で据える。空 = 据えない
    #: (charter に欄が立たず、CLI の既定 = 家の projects/<cwd>/memory に落ちる)。
    memory_root: str = ""
    #: この node の家(段 10 lane 10y — composition root が env HOME から据える)。charter の work_dir の `~` はこの値で展開する
    #: (judgment.plan-with-node-home)。空 = 展開しない(`~` のままの path は無い dir として WorkDirMissing に落ちる)。
    home: str = ""
    #: 段 12 lane 12a(agora-redesign #230): verify の命令の結末(log / rc / pid の 3 file)の置き場 — composition root が
    #: state_dir(record spool の親 = join の宣言 [agentd].state_dir)の下の VERIFY_RUNS_RELDIR に据える。verify の script の
    #: 置き場は home/VERIFY_SCRIPTS_RELDIR(judgment.verify-plan-of の 1 点)。
    verify_runs_dir: str = ""
    #: 段 12 lane 12j(agora-redesign #233): 会話の履歴の段階つき要約の契機 = 手番の終わりに測った文脈の大きさ(token —
    #: DeltaBatch.context.tokens)がこの値を超えた(operator 2026-09-16「50 % を超えていたら(0.5M)」= 上限 1M の 50 %)。
    #: ACP agora-kinds.json conventions.stagedSummaries.triggerTokens はこの写し。0 = 契機を置かない(検体の値)。
    summarize_trigger_tokens: int = 500_000
    #: 1 区間の原文の上限 byte(区間ごとに Claude Code を 1 回起こす)。charter.regionByteBudget が在ればそちらが勝つ。
    summarize_region_byte_budget: int = 262_144
    #: 要約の 1 区間の上限(秒)— 越えたら止めて条件 SummarizeDeadlineExceeded。
    summarize_deadline_seconds: int = 900
    #: 要約の model(operator の決定 2026-09-16 = Opus 5・2026-09-23 に Opus 5.5 へ)。契機が書く agent-job の charter.model はこの値。
    summarize_model: str = "claude-opus-5-5"
    #: 要約の結末の置き場(runtime.summarize_runs_dir の 1 点 — state_dir の下の SUMMARY_RUNS_RELDIR)。
    summarize_runs_dir: str = ""
    #: 段 12(card acp:kanban-issue:ki-f2747267e24d B2): 借りた錠の手元の journal の file(runtime.lease_journal_path の
    #: 1 点 — state_dir の下の LEASE_JOURNAL_FILENAME)。空 = journal を持たない機体(検体の既定 — 借りも返しも
    #: 今日どおり memory だけで回る)。
    lease_journal_path: str = ""
    #: Claude Code の binary(要約の job の argv の先頭 — agentd の PATH で解く)。名の定義は drivers.DRIVER_EXECUTABLE の
    #: 1 点(ADR-DOE-AGENTS-012 R61)— 参加の拍ごとに agentd の実効 env で探し(ResolveLocalExecutable)、見つからなければ
    #: claude を申告しない。
    claude_binary: str = DRIVER_EXECUTABLE["claude"]
    #: この node が預かり所(custody)を宣言しているか(段 10c・agora-redesign #80)。composition root
    #: (runtime.settings_from_env)が CUSTODY_URL_ENV(join の [custody].url / --custody)の在否から導く 1 点。True の node は
    #: status.binding.account の無い agent-job を起こさない(judgment.credential-source-of)— charter の binding
    #: (機体の profile の家)で起こす経路は、預かり所を宣言していない node(移行前の機体)だけに残る。
    custody_declared: bool = False
    #: 読んだ宣言 file の指紋(段 10 lane 10y・agora-redesign #110)— join が据えた DECLARATION_SHA256_ENV の写し(検は
    #: join.declaration-sha256-of の 1 点)。node の行の誕生と spec の揃えに header で運ぶ。None = 宣言 file なし
    #: (その agentd は kind node の capacity を書けない — ACP が 403 declaration-needs-fingerprint で断る)。
    declaration_sha256: str | None = None
    #: node が持つ作業場の根(段 10 lane 10y 案 C — join が据えた WORK_ROOTS_ENV の写し・検は join.work-roots-of の 1 点)。
    #: None = 宣言なし(node の spec に workRoots を書かない — 欄の無い node の読み方は配車の側が決める)。
    work_roots: tuple[str, ...] | None = None
    #: node が持つ作業場(段 12 lane 12j・#575 便 2 — join が据えた WORK_DIRS_ENV の写し・検は join.work-dirs-of の 1 点)。
    #: None = 導いていない(spec に workDirs を書かない)・空の tuple = 何も持たない。
    work_dirs: tuple[str, ...] | None = None
    #: node が持つ作業場の**根**(段 12 lane 12j 追補 — join が据えた WORK_DIR_ROOTS_ENV の写し・検は
    #: join.work-dir-roots-of の 1 点)。None = 導いていない(spec に workDirRoots を書かない)・空の tuple = 根が 1 つも無い。
    work_dir_roots: tuple[str, ...] | None = None
    #: 預かり所へ名乗る借り手の身元の**等価鍵**(card acp:kanban-issue:ki-40021864e62f — 契約
    #: node.spec.custodyBorrower・綴りの定義点は NODE_SPEC_CUSTODY_BORROWER の註)。composition root
    #: (runtime.settings_from_env)が預かり所へ名乗る材料 2 つ(借り手札・SA token の file — handlers の
    #: read_secret_file で読む)を渡し、判断は join.custody-borrower-of の 1 点。None = 名乗らない
    #: (node の spec に欄を書かない —— 配車は node 名で束ねる = この軸が無かった時と同じ)。
    custody_borrower: str | None = None
    #: 席へ運ぶ env の対(agora-redesign #520 — join が据えた SEAT_ENV_ENV の写し・読みは join.seat-env-of の 1 点)。
    #: 空 = 宣言しない(手番の charter に欄が増えない = 今日どおり)。起こす手番の charter.session_env へ
    #: judgment.charter-with-seat-env が重ねる(会話の身元より**先** — 宣言は身元を偽れない)。
    seat_env: tuple[tuple[str, str], ...] = ()
    #: 席の settings file の**名指し**(card acp:kanban-issue:ki-7b52bb76aa6e — join が門を通して据えた
    #: CLAUDE_SETTINGS_FILE_ENV の絶対 path の写し)。None = 名乗らない(今日どおり)。席の起動はこの欄ではなく
    #: env を use-site で読む(launch.claude-settings-declaration)— ここは node の行の名乗りのためだけに持つ。
    claude_settings_file: str | None = None
    #: その file が**参加の拍に**在ったか(composition root runtime.settings_from_env の 1 読み)。
    #: node の行の labels.seat-settings(NODE_LABEL_SEAT_SETTINGS)へ present / missing で名乗る。
    #: 参加の拍の事実 = spec(名乗り)であって観測ではない: pod の checkout は pod の生涯で動かず、Mac は
    #: agentd-follow が据え直す拍に参加し直す。起動の拍の実勢は席ごとの log の 1 行が持つ。
    claude_settings_file_present: bool = False
    #: 席の家へ運ぶ共通の指示の名指し(card acp:kanban-issue:ki-62aa1f4e9c9c D11 — join が据えた
    #: 名簿の env の写し)。(名簿の鍵, **絶対 path**)の対の列で、名簿の順・名指した種だけ。
    #: 空 = 名乗らない(node の行に labels を書かない = 今日どおり)。席の起動はこの欄ではなく env を
    #: use-site で読む(launch.claude-instruction-sources)— ここは node の行の名乗りのためだけに持つ。
    instruction_sources: tuple[tuple[str, str], ...] = ()
    #: そのうち**参加の拍に現物が在った**種の鍵(composition root runtime.settings_from_env の 1 読み)。
    #: node の行の labels(名簿の row.label)へ SEAT_SETTINGS_PRESENT / SEAT_SETTINGS_MISSING の
    #: 1 語で名乗る(seat-settings と同じ 2 語 — 第 2 の語彙を作らない)。
    instruction_sources_present: tuple[str, ...] = ()
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
    #: 段 12 lane 12j(agora-redesign #304 便 2): 停止(SIGTERM)の排水の上限(秒・宣言 file の [agentd].drain_seconds・DRAIN_SECONDS_ENV)。
    #: 0 = 排水しない(今日どおり走っている手番を AgentdRestart で閉じる)。> 0 = 新しい claim を止め・node の capacity を 0 に名乗り、
    #: 走っている手番の終わりまで(上限まで)待ってから残りを閉じる。pod の terminationGracePeriodSeconds はこの値より長く取る。
    drain_seconds: int = 0
    #: 段 12 lane 12j(agora-redesign #367): 参加時に node の spec.agentd に名乗る自分の版 — revision(git sha・AGENTD_REVISION_ENV・
    #: 無ければ AGENTD_REVISION_UNSTAMPED)と build(image の tag か local・AGENTD_BUILD_ENV)。protocol は AGENTD_PROTOCOL の 1 点。
    agentd_revision: str = AGENTD_REVISION_UNSTAMPED
    agentd_build: str = AGENTD_BUILD_LOCAL
    #: 排水の最中か(停止の腕が立て、拍が読む — 判断は judgment.declared-capacity-of と agentd.receive-bound-jobs の 1 点ずつ)。
    #: 宣言ではなく拍ごとの状態(run_loop が settings を写して立てる)。
    draining: bool = False
    #: 機体の所有の等級と検の方法(段 6 lane 6f)。None = 名乗らない(observations に欄を書かない =
    #: 未観測)。composition root(runtime.settings_from_env)が env から読み、起動の前に
    #: join.ownership-preflight で検めた値だけがここに据わる(不一致 = 参加しない)。
    ownership: Ownership | None = None
    #: この機体が持つ資格の profile の残量を読んで profile の status.observed に書く周期(段 7
    #: lane 7d-3 — heartbeat より遅い別の周期・値の宣言はここ 1 点)。同じ値を usage の読み口の
    #: cache の寿命にも渡す(1 周期より若い断面は読み直さない)。判断(窓・残量・post-image)は
    #: judgment の純関数、時計は effect、拍は agentd-tick の 1 つの腕。
    profile_observe_seconds: int = 300
    #: 履歴からの再開(段 8q)で最初の本文に畳む「これまでの会話」の上限(UTF-8 の byte)。超えたらまず古い手番から道具の項を
    #: 薄くし(R35・先頭 = この値 / HISTORY_THIN_DIVISOR byte)、それでも超えたら古い手番から要約せずに落とし、落とした区間を
    #: 見出し 1 行(期間・kind ごとの件数・道具の名・全文の在処 = ACP の会話の記録)に畳んで残す(judgment.rehydrate-history-of —
    #: 段 11 lane 11v・agora-redesign #55 / #225・R34 / R35。model は呼ばない)。
    rehydrate_history_byte_budget: int = 65_536
    #: card acp:kanban-issue:ki-c3aace97d825: 別の機体・別の家で始まる手番を、履歴の畳み直し(rehydrate)ではなく
    #: 「会話の記録から transcript を組み直して --resume」で続けるか。False = 今日どおり rehydrate だけ。
    #: True でも、組み立てか起動前の検査が通らなければ同じ拍で rehydrate に戻る(戻し方 = この旗を切る)。
    #: composition root(runtime.settings_from_env)が TRANSCRIPT_REBUILD_ENV から据える。
    transcript_rebuild_enabled: bool = True
    #: 組み直す transcript の上限(UTF-8 の byte)。超えたら古い手番から落とし、落とした区間は見出し 1 行に畳む
    #: (judgment.transcript-lines-of — 落とし方の向きは rehydrate と同じ)。
    #: ⚠ **既定を rehydrate_history_byte_budget と同じ値に揃えてある**。畳み直しは「無駄な書き直し」であると同時に
    #: **文脈の肥大を止める栓**でもあり(費用の実測 ~/experiments/agent-subtask-cost/out/kanban-worth/cost/README.md:
    #: 履歴を 64 KB に切るので 1 応答が対話の 1/3.8 の文脈〔中央 107,722 トークン〕で済み、1 応答あたりでは
    #: 温かい送りより 29% 安い)、組み直しで上限を緩めると**読み直しが増えて書き直しの節約を相殺する**。
    #: ⇒ 組み直しが運ぶ履歴の量は畳み直しと同じにし、節約は「先頭が byte 同一になって読みに変わる」分だけで採る。
    #: 上げるのは、1 応答の文脈の実測を見た上での別の決定(値はこの 1 行)。
    transcript_rebuild_byte_budget: int = 65_536
    #: node の observations.transcripts に載せる件数の上限(段 8q — 終端の session のうち transcript が
    #: この機体に残るもの・会話ごとに最新の 1 つ・新しい順)。heartbeat ごとに node の行へ書くので小さく
    #: 保つ(契約の maxItems 64 以下 = TRANSCRIPTS_OBSERVED_MAX_CEILING)。
    #: ⚠ **既定の宣言はこの 1 行ちょうど**(ADR-DOE-AGENTS-012 R56): 宿は宣言 file の
    #: [agentd].transcripts_observed_max で**重ねられる**(composition root が TRANSCRIPTS_OBSERVED_MAX_ENV から据える)。
    #: 席の枠(capacity)より小さいと、器の死んだ行が transcripts へ移った拍(R25 の改訂)に上限で落ちて、
    #: 配置が affinity.predecessor を名指せない会話が出る。
    transcripts_observed_max: int = 16
    #: 会話の記録の service への本文の二重書き(段 9f lane 9f-2・設計 §2.4)。composition root(runtime.settings_from_env)が
    #: RECORD_URL_ENV の在否から導く 1 点 — False の間 agentd は Record* の要求を 1 つも撃たない(ACP の追記は今日どおり)。
    #: 実運転では常に True(段 9f lane 9f-6: 宛先を持たない agentd は参加の門 join.record-sink-of が理由つきで断る —
    #: 本文の行き先が無いまま見出しだけを書く形は存在しない)。False は test の対照(二重書きの有無で見出しが一致する検)だけ。
    record_enabled: bool = False
    #: spool の再送の周期(送れなかった拍の後 — 送れている間は出来事を読んだ拍の終わりに送る)。届かない service へ拍ごとに
    #: 撃って loop を塞がないための有界の backoff(judgment.record-flush-due)。
    record_retry_seconds: float = 15.0
    #: 1 拍に送る spool の batch の上限(ADR-DOE-AGENTS-012 R22 の追補・card acp:kanban-issue:ki-6eb745f6d528)。
    #: 拍の終わりの flush が spool の全部を上限なく回すと、溜まった拍の周期を **spool の深さ**が決める
    #: (実測の 24 秒級の外れ値の候補)。残りは spool に残して次の拍へ持ち越す(落とさない — 今日の規律は変えない)。
    #: 既定 20 の根拠: 1 batch = 1 往復。保った接続(handlers.HttpConnections)の 1 往復は実射で p50 0.46 ms
    #: ⇒ 20 往復 ≈ 10 ms で実況の周期(events_poll_seconds = 50 ms)の中に収まる。接続を保てなかった拍は
    #: 1 往復が名引き込みで 25 ms 級まで伸びる(同じ実射の点なしの綴り)⇒ 20 往復 ≈ 0.5 秒 —— その日も
    #: **上限が在るから**拍の周期を spool の深さが決めない。上限の値打ちは往復の速さに依存しない。
    record_flush_max_batches: int = 20
    #: 段 9p(agora-redesign #76): 手番の記録(turn-record)の行を作れない拍(頭の入れ替え・到達不能・5xx)に作り直しを
    #: 続ける上限(手番の始まりから・秒)。周期は record_retry_seconds(spool の再送と同じ弁)。期限を越えたら理由つきで
    #: condition RecordUnavailable(judgment.record-create-verdict の 1 点)。頭の入れ替え(image beat の再起動)の実測は
    #: 数十秒〜2 分なので、その数倍。
    turn_record_create_deadline_seconds: float = 300.0
    #: 段 12(agora-redesign #537 便 1): 走っている turn-record の終状態を読む巡回の周期(秒・値の宣言はここ 1 点)。
    #: 1 度の書き(手番の終わりの end-turn-record)が着かなかった記録を level-triggered に閉じる腕の拍 —
    #: profile の観測と同じ「遅い周期」の族。起動の拍(AgentdState.last_turn_record_sweep_ms = None)は即。
    turn_record_sweep_seconds: int = 300


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

#: session = 器の出来事の journal が進んだ(host の session.wait_events — 段 12 lane 12b): ACP の sequence は
#: 進んでいないので行は読み直さず(judgment.list-mode-for → none)、走っている job の観測だけを即座に行う。
WatchKind = Literal["changed", "gap", "idle", "closed", "session"]

#: watch の拍にどう行を読み直すか(judgment.list-mode-for の閉語彙): full = 全量 list
#: (周期の保険・gap・接続の張り直し)/ window = 変わった行だけ(``GET /api/event-window`` の
#: post-image — watch で起きた拍)/ none = 読み直さない(idle)。
ListMode = Literal["full", "window", "none"]
#: 窓の答えが名乗る store の版(read-freshness.json・段 12 lane 12d)をどう扱うかの閉語彙 — 判断は judgment.window_epoch_verdict の 1 点。
EpochVerdict = Literal["continue", "adopt", "relist"]
EPOCH_VERDICT_CONTINUE: EpochVerdict = "continue"
EPOCH_VERDICT_ADOPT: EpochVerdict = "adopt"
EPOCH_VERDICT_RELIST: EpochVerdict = "relist"
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
    #: 答えが来た store の版(契約 read-freshness.json の storeEpoch)。None = この契約より前の engine(欄が無い)。
    store_epoch: str | None = None

    @property
    def exhausted(self) -> bool:
        return self.through >= self.latest


@dataclass(frozen=True)
class WatchAdvance:
    """watch(0c の SSE)の 1 回の待ちの答え。

    changed = sequence が進んだ / gap = 中継が続きを保証できない(list で再同期する合図)/
    idle = 待ちの上限まで何も来なかった / closed = 接続が切れた(handler が張り直す)/
    session = 器(host)の出来事の journal が進んだ — 手番の終わりを monitor が刻んだ拍など(段 12 lane 12b:
    拍の待ちの定義点は AcpWatchSse の 1 つのまま、host の合図も同じ列に載る)。ACP の sequence は進まない。
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
    #: 預かり所が「いつまで一時か」を名乗る時の期限。⚠ 2026-09-19 に『1 認証 1 宿』の錠は
    #: 廃止された(custody law lease-counts-no-hosts)ので、錠の競合による 409 はもう出ない —
    #: この欄が埋まるのは、預かり所が別の理由で期限を名乗った拍だけ(欠落 = 期限を知らない)。
    hold_expires_at_ms: int | None


LeaseOutcome: TypeAlias = "LeaseGrant | LeaseRefused"


@dataclass(frozen=True)
class CustodyRefusalVerdict:
    """預かり所の断りを **「誰が答えられるか」** に解いた答え(card acp:kanban-issue:ki-b3bed1e983fb)。

    根: agentd は断りの status(型のある int)と逐語を ``custody refused (403): …`` の 1 文へ畳み、
    終端の語を 1 つ書いていた。配達の側(ACP ``Acp.App.Messaging.Decide.carrierEndedOf``)は phase と
    result.cause と回数しか読まないので、**再試行してよいかを判ずる材料が 1 つも残らない**。
    ⇒ 直す場所は判定の側ではなく **語を鋳る側** — class を語にして終端へ載せる。

    ``answerer`` が 3 つの処置を名指す:

    * ``nobody``          — ``condition_type`` = :data:`CONDITION_CREDENTIAL_NOT_LEASABLE`(組み直さない語)。
    * ``another-carrier`` — ``condition_type`` = :data:`CONDITION_CREDENTIAL_UNAVAILABLE`(有界に組み直す語)。
    * ``time``            — ``held`` に :data:`CONDITION_CREDENTIAL_LEASE_HELD` の記録 1 項。呼び手は
      phase を触らず条件だけ足す(``condition_type`` は書かない語として同じ型を名乗る)。

    ``reason`` は終端に載る文で、**預かり所の逐語をそのまま含む**(畳まない)。頭に class の意味
    (次の一手)が付くので、送信者は「宣言が変わるまで誰が頼んでも同じ」か「別の機体なら通る」かを読める。
    """

    answerer: CustodyRefusalAnswerer
    condition_type: ConditionType
    reason: str
    #: answerer == "time" の拍だけ非 None(CredentialLeaseHeld の記録 1 項)。
    held: JSONObject | None


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
    #: 依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB(D2): 温かい session の手番が**失敗で**終わった時に走行器が名乗った文(wire の
    #: turn_error — turn_ended_at と対の level-triggered の欄・成功の終わりと次の手番の送りで欄ごと無い)。None = 成功で
    #: 終わった / 終わっていない / 走行器が名乗らない器(tmux)。読み手は手番の終わりの判断(judgment.turn-output-condition-of)。
    turn_error: str | None = None
    #: この session で**最後に成功した専用操作**の完了時刻(epoch ms・wire の cache_last_success_at_ms・
    #: None = 1 度も成功していない)。host が観測して名乗る事実ちょうどで、期限でも資格でもない —
    #: 「いつまで送信先として保つか」の判断は judgment.cache-resident-retention-of の 1 点
    #: (card acp:kanban-issue:ki-567f2dd6140f §3.1e: host は仕組みだけを持ち、判断を持たない)。
    cache_last_success_at_ms: int | None = None
    #: この眺めを返したのが**降りる途中の器**(器の入れ替えの blue/green — host_slots)か。真なら
    #: その器には新しい手番を送らない(judgment.next-arm-for-job が send の腕を採らない — 同じ家なら
    #: 片付けて --resume で新しい器へ移す)。器の wire の欄ではない(腕の経路 SessionRoutes が付ける)。
    draining: bool = False


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
class VerifyPlan:
    """Bound の verify の行から読み解いた「何を走らせるか」(段 12 lane 12a)— 判断ではなく行の欄の写しと、
    機体の家から導いた置き場。command は運ばない: script は VERIFY_SCRIPTS_RELDIR/<verify_id>.sh ちょうど。"""

    #: agent-job の id(行の resource_id)。
    job_id: str
    #: 便の id(charter.jobId = script の名 = ai land verify の --loop-id)。
    verify_id: str
    #: 発火の鍵(charter.runKey・記録の材料)。
    run_key: str
    #: 命令の上限(秒・charter.deadlineSeconds)。
    deadline_seconds: int
    #: 機体の script の絶対 path(home/VERIFY_SCRIPTS_RELDIR/<verify_id>.sh)。
    script_path: str
    #: 結末の 3 file(state_dir/VERIFY_RUNS_RELDIR/<job_id>.{log,rc,pid})。
    log_path: str
    rc_path: str
    pid_path: str


@dataclass(frozen=True)
class InFlightCommand:
    """走らせている 1 つの verify の命令(agentd の memory・正本は行の sessionHandle.verify と結末の file — R7)。"""

    job_key: str
    job_namespace: str
    job_id: str
    verify_id: str
    run_key: str
    #: 起こした時刻(ms)— 期限の起点。
    started_ms: int
    deadline_seconds: int
    #: sh の pid(pid の file の値・拾い直しは file から読む)。None = まだ読めていない。
    pid: int | None
    script_path: str
    log_path: str
    rc_path: str
    pid_path: str


@dataclass(frozen=True)
class SummarizePlan:
    """Bound の summarize の行から読み解いた「何を要約するか」(段 12 lane 12j)— 判断ではなく行の欄の写し: 会話(subject)・
    区間の上端(charter.until)・1 区間の上限 byte(charter.regionByteBudget か宣言の値)・model(charter.model)・資格(binding の
    profile / account — 配置が結んだ会話の profile)・期限(宣言の値)。"""

    job_id: str
    conversation_id: str
    until: int
    region_byte_budget: int
    model: str
    profile: str
    account: str
    deadline_seconds: int


@dataclass(frozen=True)
class SummaryRegion:
    """次に要約する 1 区間(judgment.summary-region-of の答え): recordSeq の閉区間 [from_seq, to_seq] とその原文の出来事
    (RECORD_RAW_EVENT_KINDS の kind だけ・recordSeq 昇順)、原文の bytes の合計(storedEvent.bytes の和)。"""

    from_seq: int
    to_seq: int
    events: tuple[RecordEvent, ...]
    source_bytes: int


@dataclass(frozen=True)
class MemoryBook:
    """置き場の 1 冊(card acp:kanban-issue:ki-9fc7d4bca4dc): file の逐語と、frontmatter から読んだ索引の材料。
    name は file 名(``.md`` を落とした綴り)が正本 — frontmatter の name と食い違っても file 名を採る
    (置き場の身元は path で、行の identityKey [conversationId, name] もそれを写す)。"""

    name: str
    text: str
    type: str
    description: str
    links: tuple[str, ...]


@dataclass(frozen=True)
class MemoryMalformed:
    """冊として読めない file(frontmatter が無い・種類が閉語彙の外・名が綴れない)。書かずに理由を名乗る —
    欄を発明して行を作らない。"""

    name: str
    reason: str


#: 置き場の file 1 つの読み(純関数 judgment.memory-book-of の答え)。
MemoryReading: TypeAlias = "MemoryBook | MemoryMalformed"


@dataclass(frozen=True)
class MemoryUnchanged:
    """撃つ理由が無い — 何も撃たない(冪等)。立つ規則は 2 つで、**呼び手の 1 手が違う**:

    ``proven_by_row`` True  = 規則 1(行の sha256 == 手元)。行が『置き場の写しは行の今の版そのもの』を
                              証明している ⇒ 呼び手はこの拍で基準を据えてよい(据える値は証明つきで正しい)。
    ``proven_by_row`` False = 規則 2a(手元 == 基準)。席が 1 字も触っていないだけで、行は先へ動いて
                              いるかもしれない ⇒ **基準を動かさない**。動かすと次の手番が 2c / 2d へ落ち、
                              席が触っていない古い写しで行を巻き戻す(この族が直した壊れ方そのもの)。

    2 つを 1 語に畳むと呼び手が割れない。割るために呼び手側で sha を比べ直すと判定点が 2 つになり、
    片方だけ直る日が来る ⇒ 判定は judgment.memory-write-verdict の 1 点のまま、**どちらの規則で
    立ったかを欄で運ぶ**(依頼者の裁定 2026-09-21 (f) と c-H89Q の指摘)。"""

    name: str
    proven_by_row: bool = False


@dataclass(frozen=True)
class MemoryAppend:
    """行が無い(この会話で初めての冊)— stream へ producerSeq 0 で append。409 が返れば『行が消えて stream が
    残っている』形なので、stream を読み直して MemorySupersede へ落ちる。"""

    name: str


@dataclass(frozen=True)
class MemorySupersede:
    """行が在る — **手元の写しが降りてきた版**(基準の recordSeq)へ supersede(producerSeq は 0 のまま)。
    前の版は鎖として残る。

    ⚠ 撃ち先は基準であって**行の recordSeq ではない**(法 575b1e
    R-the-second-write-supersedes-the-named-version-3f70・2026-09-21 改訂): 行は記録の頭の遅れる投影で、
    遅れている拍に行の番号を撃つと既に置き換えられた版を撃ち、409 でその冊は永久に書けなくなる。

    ``base_seq`` / ``row_seq`` / ``conflicted`` = 撃ち先(= 基準)と行の今の値、そしてそれが割れていたか。
    割れる形は 2 つ — 行が遅れている(投影が古い)/ 行が先へ動いた(手番の**間に**別の機体が書いた)。
    どちらでも断らない: 席の編集を捨てず、重ねて名乗る(両方の版が鎖に残る)。"""

    name: str
    record_seq: int
    version: int
    base_seq: int | None = None
    row_seq: int | None = None
    conflicted: bool = False


@dataclass(frozen=True)
class MemoryUnbased:
    """基準が無いのに行が在る — 手元の写しが『席がこの手番で書いた本文』か『前の手番の古い写し』かを
    判る材料が無い。**撃たずに名乗る**(安全側): ここで supersede に倒すのが、触っていない写しで行を
    巻き戻していた壊れ方そのものだった。次の水入れが基準を置けば、その手番から普通に畳み戻る。"""

    name: str


@dataclass(frozen=True)
class MemoryRetire:
    """置き場から消えた冊(card acp:kanban-issue:ki-6b5c4b270ca0)— **控えに在って手元に無い** =
    エージェントがこの手番で撤回した。行の ``status.state`` を ``retired`` にする。本文の版は 1 つも
    消さない(tombstone を撃たない — 記録の鎖はそのまま残り、同じ鍵で戻れる)。

    ⚠ 立つのは控え(MEMORY.base.json)に**その名が在る**拍ちょうど。控えは『器がこの手番の頭に
    **現に書けた**冊』の claim check なので、控えに名が在る = その file は確かに置き場に在った。
    控えが無い置き場(旧い版の機体・生まれたての pod・読めない控え)は控えが空に倒れる ⇒ この語は
    1 度も立たない(実測 2026-09-21 会社 Mac: 置き場 278 会話・記憶 1,025 file に対し控えの file は 0 個
    — この機体は 1 件も退役させない)。控えを守りに使わずに『置き場に無い』だけで撃つと、未修正の
    機体の空の置き場が全件を退役させる。"""

    name: str


@dataclass(frozen=True)
class MemoryRevive:
    """退役した行に、**行の claim check と中身の違う** file が置き場に現れた = エージェントが同じ名前で
    書き直した。本文を記録の stream へ重ね、行の ``status.state`` を ``current`` へ戻す(identityKey が
    同じ鍵で戻れるのは identitySupersedes を宣言していないから — 法 575b1e の context)。

    撃ち先は行の claim check の版。行は記録の頭の遅れる投影なので 409 は起き得るが、それは
    ``MemorySupersede`` と同じ『頭が動いた』の合図で、呼び手が頭を読み直して重ねる(枝を増やさない)。

    ⚠ 中身が行と**同じ** file は復活ではなく残骸(旧い機体の置き場に残った写し)⇒ ``MemoryUnchanged``。
    残骸で復活させると、退役が別の機体の古い写しで毎手番 取り消される。"""

    name: str
    record_seq: int
    version: int


@dataclass(frozen=True)
class MemoryBaseline:
    """手番の頭に置き場へ出した 1 冊の claim check(= 基準の 1 項)。正本ではない(行が正本)— 手元の写しが
    出した時のままかを次の畳み戻しが判ずるためだけに置く。"""

    name: str
    record_seq: int
    sha256: str
    version: int


@dataclass(frozen=True)
class MemoryFold:
    """1 冊を畳み戻した結末(agentd.fold-one-memory の答え)。

    ⚠ **『撃った』と『撃つ理由が無かった』を 1 つに畳まない**: 畳むと計器の written が「error が
    出なかった冊の数」になり、艦隊の agent-memory-folded 221 行が 100% ``written == books`` を
    名乗っていた(冊が毎手番全部変わっているはずがない — この 100% がその指紋)。

    ``baseline`` = 次の手番へ渡す基準の 1 項(None = 基準を据え置く)。値を持つのは 2 つの拍だけ —
    **撃てた冊**(書いた版が次の基準)と、**行が手元の写しを証明した冊**(規則 1・裁定 (f))。"""

    name: str
    written: bool = False
    unchanged: bool = False
    unbased: bool = False
    conflicted: bool = False
    #: card acp:kanban-issue:ki-6b5c4b270ca0: 行を ``retired`` にした冊(置き場から消えた = 撤回)。
    #: ``written`` とは別の欄 — 退役は本文を 1 byte も書かない(記録の stream は 1 版も動かない)ので、
    #: written に混ぜると計器の「書けた冊」が撤回の数で膨らむ。控えの項もこの拍で落とす。
    retired: bool = False
    #: 退役した行に中身の違う file が現れて ``current`` へ戻した冊。本文は書くので ``written`` も立つ —
    #: この欄は「戻した」の側だけを名乗る(計器で撤回と復活の往復が見える)。
    revived: bool = False
    reason: str | None = None
    baseline: MemoryBaseline | None = None
    base_seq: int | None = None
    row_seq: int | None = None


#: 1 冊の書き方(法 ACP 575b1e)。突き合わせるのは 3 点 — 手元・行・基準(judgment.memory-write-verdict)。
#: card acp:kanban-issue:ki-6b5c4b270ca0 で **手元が無い**(置き場に file が無い)拍と**行が退役している**拍が
#: 同じ関数に入り、撤回(MemoryRetire)と復活(MemoryRevive)がこの語彙に加わった。
MemoryWriteVerdict: TypeAlias = (
    "MemoryUnchanged | MemoryAppend | MemorySupersede | MemoryUnbased | MemoryRetire | MemoryRevive"
)


@dataclass(frozen=True)
class MemoryTurnFiles:
    """手番の頭に器へ渡す記憶の荷(agentd.memory-files-for-turn の答え)。

    ``files`` = 置き場へ書き出す file の列(冊 + 索引 + 控え)。
    ``swept`` = 置き場から**取り除く** file の名の列 = 退役した行と同じ名前ちょうど
                (card acp:kanban-issue:ki-6b5c4b270ca0)。

    ⚠ 2 つを 1 つの型で運ぶのは、**同じ拍・同じ書き手(器)で**置き場へ当てるため: 別の腕が掃除を
    持つと『冊は書けたが掃除は落ちた』が起き、退役した冊の file が残って次の畳み戻しが読む(控えを
    冊と同じ列に載せたのと同じ理由 — judgment.memory-files-of の頭注)。"""

    files: tuple[JSONObject, ...] = ()
    swept: tuple[str, ...] = ()


@dataclass(frozen=True)
class SummaryOutcome:
    """claude の print モード(--output-format json)の答えの読み(judgment.summarize-output-of): 要約の本文・消費(契約 turn-record の usage と
    同じ 4 欄 + 任意の内訳・無ければ None)・答えが名乗った model(無ければ None)。"""

    text: str
    usage: JSONObject | None
    model: str | None


@dataclass(frozen=True)
class InFlightSummarize:
    """走らせている 1 つの summarize(agentd の memory・正本は行の sessionHandle.summarize と結末の file — R7)。1 job は区間を
    古い順に 1 つずつ進み、区間ごとに Claude Code を 1 回起こす。from_seq / to_seq = いま走っている区間。"""

    job_key: str
    job_namespace: str
    job_id: str
    conversation_id: str
    until: int
    from_seq: int
    to_seq: int
    source_events: int
    source_bytes: int
    model: str
    profile: str
    account: str
    region_byte_budget: int
    #: この区間の process を起こした時刻(ms)— 期限の起点。
    started_ms: int
    deadline_seconds: int
    #: sh の pid(pid の file の値・拾い直しは file から読む)。None = まだ読めていない。
    pid: int | None
    #: 借りている札(返す時の鍵)。再起動の拾い直しは None(次の区間で借り直す)。
    lease_id: str | None
    prompt_path: str
    out_path: str
    log_path: str
    rc_path: str
    pid_path: str
    #: この job で書いた summary の行の数(結末の result に写す)。
    regions_done: int = 0


@dataclass(frozen=True)
class ArmChoice:
    """Bound の job の起こし方(judgment.next-arm-for-job の答え — 判断はその 1 点)。"""

    arm: NextArm
    #: send の宛先 / resume の元の session(launch・rehydrate・defer は None)。
    source: str | None
    #: 起こす前に片付ける温かい session(生きて idle だが家が違う — cache は失効したので、同じ会話の器を
    #: 2 つ生かさない)。None = 片付けない。
    retire: str | None
    #: 段 10f 便 2(agora-redesign #82): この rehydrate は文脈の圧縮(会話の宣言 compactAt を直前の手番の文脈の使用率が
    #: 超えた)のために選ばれた — 同じ家の温かい session を送らず片付けて履歴から再開する。計器 agentd_compactions_total の根拠。
    compacts: bool = False


#: 組み直しを見送る理由の閉語彙(card ki-c3aace97d825)。
#: no-history      = 記録に写せる出来事が 1 つも無い(履歴の無い会話は launch / rehydrate のまま)
#: thin-record     = 記録の service に届かず見出しだけ(本文が無いので transcript にならない)
#: empty-lines     = 畳んだ結果 1 行も残らなかった
#: broken-chain    = 組んだ行の親子の鎖が繋がらない(器が読めない形)
#: role-disorder   = user / assistant の並びが交互でない(器が読めない形)
#: session-mismatch= 行の名乗る会話の id が揃っていない
TranscriptRefusal = Literal[
    "no-history",
    "thin-record",
    "empty-lines",
    "broken-chain",
    "role-disorder",
    "session-mismatch",
]

@dataclass(frozen=True)
class TranscriptBuilt:
    """組み直した Claude Code の transcript(judgment.transcript-lines-of の答え・card ki-c3aace97d825)。

    session_id   この transcript が名乗る会話の id(`claude --resume` の引数・器が家へ置く file 名)
    lines        transcript の行(1 行 1 dict — 書き出しは jsonl)
    turns        写した手番の数(落とした後)
    dropped      上限で落とした手番の数
    cut_bytes    最新の手番の先頭から切った byte(0 = 切っていない)
    size_bytes   jsonl にした時の UTF-8 の大きさ
    """

    session_id: str
    lines: tuple[JSON, ...]
    turns: int
    dropped: int
    #: 最新の手番 1 つでも上限を超えた時に、その先頭から切った byte(0 = 切っていない)。
    cut_bytes: int
    size_bytes: int


@dataclass(frozen=True)
class TranscriptRefused:
    """組み直しを見送った(judgment.transcript-lines-of / transcript-readable-of の答え)。

    reason は閉語彙 — 呼び手はこの型を見たら同じ拍で rehydrate に戻る(第 2 の判断を置かない)。
    """

    reason: TranscriptRefusal


TranscriptOutcome: TypeAlias = "TranscriptBuilt | TranscriptRefused"


@dataclass(frozen=True)
class HeadlineCounts:
    """見出しの数(段 8q・段 11 lane 11v・agora-redesign #55): 出来事と郵便の kind ごとの件数(初出の順)・道具の名(初出の順)・
    期間(first_at / last_at = 材料の最初と最後の時刻・時刻を持つ材料が無ければ呼び手が渡した行の時刻)。turn-record の 1 行の見出し
    (judgment.history-headline-line)も、上限で落とした手番の区間の見出し(judgment.history-dropped-headline)も、この 1 つの
    形から judgment.history-counts-note が同じ綴りで組む。"""

    counts: tuple[tuple[str, int], ...]
    tools: tuple[str, ...]
    first_at: int
    last_at: int


#: 「これまでの会話」の見出しの数で郵便を数える kind の綴り(記録の出来事の kind = text / tool_use / … と並ぶ・段 11 lane 11v)。
HISTORY_MAIL_KIND = "郵便"
#: 「これまでの会話」の見出しの数で要約(kind summary の行 — 段 12 lane 12j 便 3)を数える kind の綴り。
HISTORY_SUMMARY_KIND = "要約"
#: 契機が書く summarize の agent-job の id の頭(id = <頭><会話 id>-<until> — identity は engine の (subject, inputs=[]) で、id は記録の綴り)。
SUMMARIZE_JOB_ID_PREFIX = "aj-summary-"
#: 履歴からの再開の段階的圧縮(段 11 lane 11v 便 3・agora-redesign #225・R35): 手番を丸ごと落とす前に道具の項(tool_use の入力・
#: tool_result の本文)を薄くする時に残す先頭の byte = 上限 / この値(65,536 なら 256)。上限の宣言(AgentdSettings.
#: rehydrate_history_byte_budget)からの比で導き、2 つ目の値の宣言は置かない。
HISTORY_THIN_DIVISOR = 256


@dataclass(frozen=True)
class HistoryItem:
    """「これまでの会話」の 1 項(judgment.rehydrate-history-of の材料の 1 つ = 郵便 1 通・記録の出来事 1 つ・薄い再開の
    turn-record 1 行)。at / until = この項の最初と最後の時刻(郵便と出来事は同じ・turn-record の見出しは entries の範囲)・
    order = 同じ時刻の安定な並び・inbound = 会話へ届いた郵便(手番の区切り)・line = 畳む 1 行・counts = 見出しの数
    (落とした区間の見出しに畳む材料 — 本文を捨てても数と道具の名と期間は残る)・thin_line = 薄くした 1 行(段 11 lane 11v 便 3・
    R35: 道具の項 = tool_use の入力・tool_result の本文 — の先頭 HISTORY_THIN_DIVISOR 分の byte + 元の byte の名乗り。薄くならない項
    = 郵便・agent の text・user / system / error・薄い再開の見出し・短い本文 — は None)。"""

    at: int
    until: int
    order: int
    inbound: bool
    line: str
    counts: HeadlineCounts
    thin_line: str | None


@dataclass(frozen=True)
class HistorySummary:
    """履歴からの再開に畳む要約 1 区間(段 12 lane 12j 便 3 — agora の kind summary の行 + 記録の service の本文): recordSeq の閉区間
    [from_seq, to_seq]・書いた時刻と model・本文。原文は to_seq より新しい出来事だけを読む(agentd.record-turns-for の floor)。"""

    from_seq: int
    to_seq: int
    at: int
    model: str
    text: str


@dataclass(frozen=True)
class HistoryFold:
    """履歴からの再開の「これまでの会話」(judgment.rehydrate-history-of の答え)。text = 最初の本文に畳む
    文(記録が無ければ空)・kept_turns / dropped_turns = 残した / 上限で落とした手番の数・
    dropped_items = 落とした出来事と郵便の数・thinned_turns = 手番を落とす前に道具の項を薄くした手番の数(段 11 lane 11v 便 3・
    R35: 古い手番から・薄くなる項を持つ手番だけ数える)・dropped_headline = 落とした区間(古い手番の連なり)を畳んだ見出しの 1 行
    (段 11 lane 11v・agora-redesign #55: 期間・kind ごとの件数・道具の名・全文の在処 — 落とした手番が無ければ None・
    在れば text の中にちょうど 1 度)・cut_bytes = 最新の手番 1 つだけでも上限を超える時にその先頭から切った byte(切って
    いなければ 0)・size_bytes = text の UTF-8 の大きさ・thin = 本文が無い薄い再開(材料が HeadlineTurns — 記録の service に
    届かず ACP の見出しだけで組んだ)。"""

    text: str
    kept_turns: int
    dropped_turns: int
    dropped_items: int
    thinned_turns: int
    dropped_headline: str | None
    cut_bytes: int
    size_bytes: int
    thin: bool
    #: 段 12 lane 12j 便 3: 畳みに載せた要約(kind summary)の区間の数(古い順・原文の前に置く)。0 = 要約なし(今日どおりの畳み)。
    summary_regions: int = 0
    #: 段 12 lane 12j 追補 5: 上限で落とした要約(kind summary)の区間の数 — 原文の手番を最新の 1 つまで落としても超えた時だけ、古い要約から。
    dropped_summaries: int = 0
    #: 段 12 lane 12j 追補 6: 要約が覆う記録の終わり(floor の出来事の at)より古い郵便の数 — 要約が担うので畳まない(上限を食わせない)。
    summarized_mails: int = 0


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
class InterruptRead:
    """model が割り込みの本文を読んだ証拠(段 10 lane 10n): ``ref`` = 注入の行の名(claude の command_lifecycle の
    command_uuid = Message の id)/ None = 名を運ばない器(codex — 止めた後の turn/started は積んであった注入を全部
    読む)。``seq`` = その証拠の出来事(kind system の entry)の seq。"""

    ref: str | None
    seq: int


@dataclass(frozen=True)
class JobCancel:
    """agent-job の spec.cancel(段 1 の合図・段 12 lane 12j・agora-redesign #367)の読み — judgment.job-cancel-of の 1 点。
    requested_at_ms = 合図の拍(epoch ms)/ grace_seconds = 猶予(欠落は契約の既定 60)/ reason = 閉語彙の 1 語 / by = 合図の主。"""

    requested_at_ms: int
    grace_seconds: int
    reason: str
    by: str


@dataclass(frozen=True)
class UnrecordedEnd:
    """手番は終わったが agent-job の Ended の書きが着かなかった job(段 12 lane 12j・agora-redesign #402)— 次の拍から行を読み直して
    書き直す材料(memory の持ち越し・上限 UNRECORDED_END_TTL_MS)。実弾 2026-09-17 03:52: 頭の不通の後、監督が走っていた手番を
    lease-expired で Pending へ戻して attempt 2 を同じ行に結び、attempt 1 の Ended は Conflict で落ち、agentd は attempt 2 を新しい
    session で走らせた(同じ手番が 2 本)。持ち越した Ended を置き直しの行に書けば試みは閉じ、claim の門(known に持ち越しの id)が
    2 本目を起こさない。"""

    job_key: str
    job_id: str
    session_id: str
    result: JSON
    #: 終端の cause(#349 行 3 粒 3a — 持ち越した Ended も cause を運ぶ)。
    cause: JSONObject
    conditions: tuple[JSONObject, ...]
    at_ms: int


@dataclass(frozen=True)
class OpenToolBlock:
    """走行器が開いた(content_block_start の tool_use を見た)道具の呼び出しの block 1 つ — 書きかけの引数
    (input_json_delta の partial_json)を完成の呼び出しと同じ id・同じ名で名乗るための表の 1 行。

    走行器の差分は block を ``index`` でしか名指さないので、開始の拍に見た id と名を読みをまたいで持つ
    (InFlightJob.open_tool_blocks — 判断は judgment.claude-deltas-of の入力と出力で、純関数のまま)。
    ``parent`` = 行の parent_tool_use_id(無ければ空)— index は message ごとの番号なので、下請けの agent の
    message と番号が重なっても別の道具へ誤って結ばない。開始を見ていない差分は frame にしない(id も名も発明しない)。
    """

    parent: str
    index: int
    tool_use_id: str
    name: str


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
    #: 段 10f 便 2(agora-redesign #82): 材料の末尾で測った文脈の大きさ ``{"tokens": int, "window": int | None}``
    #: (claude = 最後の assistant の message の usage の入力側 + 出力・window = result の modelUsage[model].contextWindow /
    #: codex = token_count の last_token_usage と model_context_window)。None = 材料に無い。turn-record の usage には
    #: 同等の欄が無い(和は文脈の大きさではない)ので agentd が自分で測る — 判断は judgment.context-percent-of の 1 点。
    context: JSONObject | None = None
    #: 段 10 lane 10n(agora-redesign #93): この材料で読めた「model が割り込みを読んだ」証拠(順は出来事の順)。
    interrupt_reads: tuple[InterruptRead, ...] = ()
    #: この材料を読み終えた時点でまだ開いている道具の block(次の読みの入力 — OpenToolBlock)。
    open_tool_blocks: tuple[OpenToolBlock, ...] = ()
    #: 開始(content_block_start)を見ていない引数の差分の数 — frame にせず数える(黙って捨てない・名前を発明しない)。
    orphan_input_deltas: int = 0
    #: card acp:kanban-issue:ki-2bd49c68b042: この読みの中に**走行器が名乗ったこの手番の結末の記録**が在ったか
    #: (claude = CLI 自身の手番ではない ``result`` の行 / codex = ``turn/completed`` の通知)。events の材料でだけ立つ。
    #: 読み手は agentd の次の 1 手(judgment.job-step-of)—— 降りた process が結果を器へ出していたなら、その手番は
    #: 失われたのではなく終わっている。ここは**事実の写し**で、手番の終わりの判定ではない(判定点は job-step-of の 1 つ)。
    turn_result: bool = False
    #: 最後の主agentのAPI応答の時刻とキャッシュ利用。配達・poll時刻ではない。
    cache_observation: JSONObject | None = None
    #: card acp:kanban-issue:ki-c3ac5832a0bd: この材料の応答ごとの消費(最初に見た順・output は最終値 —
    #: response_usage.response-usages-of)。手番の終わりの読み直し(turn-batch-of)だけが行の status.responses へ写す。
    responses: tuple[JSONObject, ...] = ()


@dataclass(frozen=True)
class JobOutcome:
    """器の眺め(SessionView)から読んだ手番の結末。ended = False なら残りの欄は空(cause も None)。
    ended = True なら cause は必ず在る(段 12 lane 12k・agora-redesign #349 行 3 粒 3a: 終端は必ず result.cause を運ぶ —
    {category: CauseCategory, reason?, stage?}・judgment.ended-status-of が result に載せる 1 点で、None は書けない)。"""

    ended: bool
    result: JSON
    cause: JSONObject | None
    conditions: tuple[JSONObject, ...]


# ------------------------------------------------------------------ 会話の記録の service(段 9f lane 9f-2)

#: stream の種類(契約 record-service.json streamKinds の写し)。agentd が書くのは手番(turn)だけ。
RecordStreamKind = Literal["turn", "mail", "summary", "memory"]
RECORD_STREAM_TURN: RecordStreamKind = "turn"
#: 段 12 lane 12j(agora-redesign #233): 会話の履歴の段階つき要約の本文の stream の種類(型つきの綴り — SUMMARY_STREAM_KIND と同じ語)。
RECORD_STREAM_SUMMARY: RecordStreamKind = "summary"
#: card acp:kanban-issue:ki-9fc7d4bca4dc: 会話の自動記憶 1 冊の本文の stream の種類(MEMORY_EVENT_KIND と同じ語)。
RECORD_STREAM_MEMORY: RecordStreamKind = "memory"
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
#: 段 12 lane 12d(agora-redesign #250 の追補・契約 read-freshness.json): 窓の答えが別の store の版を名乗り全量 list へ落ちた回数
#: (1 回 = 1 行・label = from / to の版)。本番で「agentd が storeEpoch を読んで判断した」を測る計器。
METRIC_STORE_EPOCH_RELISTS = "agentd_store_epoch_relists"
#: 段 10f 便 2(agora-redesign #82): 会話の宣言 compactAt を超えたので履歴からの再開で文脈を縮めた回数(label = conversation)。
METRIC_COMPACTIONS_TOTAL = "agentd_compactions_total"
#: 段 12 lane 12j 便 3(agora-redesign #233): 手番の終わりの文脈の大きさが summarize_trigger_tokens を超え、要約の job を書いた回数
#: (欄 conversationId・until・agentJobId)。書けなかった拍(既在・断り)は数えない(log の 1 行)。
METRIC_SUMMARIZE_TRIGGERS_TOTAL = "agentd_summarize_triggers_total"
#: card acp:kanban-issue:ki-c3aace97d825: 会話の記録から transcript を組み直した拍の 1 行。
#: outcome = "built"(組めた)か judgment の断りの語(thin-record / no-history / broken-chain …)。
#: 「組み直しが何回に何回通ったか」はこの 1 行の集計から読む(log と 2 か所で数えない)。
METRIC_TRANSCRIPT_REBUILDS_TOTAL = "agentd_transcript_rebuilds_total"
#: 段 12(agora-redesign #537 便 1): 終状態を読む巡回が閉じた / 触らなかった走っている turn-record(1 行 = 1 記録・
#: 欄 agentJobId / node)。本番の針「running のまま取り残された記録 = 0」を測る材料。
METRIC_TURN_RECORD_SWEEP_ENDED = "agentd_turn_record_sweep_ended"
METRIC_TURN_RECORD_SWEEP_SKIPPED = "agentd_turn_record_sweep_skipped"
#: card acp:kanban-issue:ki-6eb745f6d528(依頼者の便 2026-09-19): 1 拍 = 1 行の計器。ACP 側の
#: acp_stream_push_interval_seconds は「面が受け取る実況の粒が 26 秒だった」とは言えても、**どの腕が
#: 遅かったか**は言えない(中継は store を読めないので行から引くこともできない — ACP 法 fabff2)。
#: この 1 行が拍の総所要(total)と腕ごとの内訳(ms)を同じ場所で名乗るので、粗い拍が来た時に往復なのか
#: 機体の CPU なのか記録の service なのかがその場で分かる。欄 jobs = その拍に同時に持っていた手番の数
#: (粗さは N の単調な関数ではない — 依頼者の実測 2026-09-19: 載り 10 の Mac が 25.8 秒・載り 20 の pod が
#: 2.5 秒。だから N は「原因」ではなく**同じ行に居る観測**として持つ)。
METRIC_TICK_MS = "agentd_tick_ms"
#: 実況の push が「どの機体が押したか」を名乗る header(card acp:kanban-issue:ki-6eb745f6d528・依頼者の便
#: 2026-09-19 lt-BM9E73V8EWSK72K9E0JMQ1RXPT)。ACP 側の acp_stream_push_interval_seconds は「26 秒」とは
#: 言えても「どの機体が」とは言えない —— 中継は store を読めない(ACP 法 fabff2)ので行から node を引くことも
#: できない。だから押す側が名乗る。⚠ 綴りの写しは ACP 側(Acp.App.Server の push の口)に 1 つ在る。
STREAM_SOURCE_HEADER = "X-Acp-Stream-Source"
#: 拍の腕の名(計器 agentd_tick_ms の欄・順は拍の中で通る順)。閉語彙 1 点 —— 腕を足す日はここと
#: agentd.agentd-tick の計りが同じ commit で動き、検 test_agentd_tick_emits_one_metric_line_with_the_arm_split
#: が「腕の和 = 拍の総所要」で名の無い仕事を許さない。
TICK_ARMS: tuple[str, ...] = (
    "watch",
    "heartbeat",
    "profiles",
    "receive",
    "sweep",
    "interrupts",
    "cancel",
    "ends",
    "jobs-fast",
    "jobs-slow",
    "commands",
    "summaries",
    "flush",
)
#: 1 行の欄ちょうど(型の集合を固定する — 読み手が欄を拾って数えなくてよい)。
TICK_LINE_FIELDS: tuple[str, ...] = ("metric", "node", "total", "jobs", *TICK_ARMS)
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
class RecordSuperseded:
    """2xx(versionAnswer)— 新しい版の recordSeq と版の番号(最初の追記が 1・置き換えるたびに 1 つ進む)。"""

    record_seq: int
    version: int


@dataclass(frozen=True)
class RecordSupersedeConflicted:
    """409(sha256-conflict / already-superseded)— 名指した版はもう置き換えられている = **記録の頭が動いた**合図。

    送れなさ(RecordUnsent)と**同じ語にしない**のは、この 409 だけが立ち直れるから: 頭を読み直して
    そこへ重ねれば席の編集は生き残る(法 575b1e R-the-second-write-supersedes-the-named-version-3f70)。
    追記の 409 を RecordConflicted に割っているのと同じ理由で、置き換えの 409 も別の語で返す。
    """

    error: str


#: 置き換えの結末(送れなさは追記と同じ RecordUnsent・409 は「頭が動いた」の RecordSupersedeConflicted)。
RecordSupersedeOutcome: TypeAlias = "RecordSuperseded | RecordSupersedeConflicted | RecordUnsent"


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

#: 段 10 lane 10o(agora-redesign #96): 郵便の添付の画像 1 枚を置いた出来事の kind(契約 record-service.json の
#: eventKinds の attachment・1 添付 1 出来事・stream = 郵便の id・producerSeq は 1 から〔0 は本文〕)。
RECORD_ATTACHMENT_EVENT_KIND = "attachment"
#: 郵便の行が運ぶ添付の見出しの列の欄(契約 agora-kinds.json message.spec.attachments)と、見出しの欄の綴り。
#: 中身(data)は行に載らない — 見出しの ref と seq で記録の service から取り寄せる(claim check)。
MESSAGE_ATTACHMENTS_KEY = "attachments"
ATTACHMENT_REF_KEY = "ref"
ATTACHMENT_SEQ_KEY = "seq"
ATTACHMENT_MIME_KEY = "mime"
ATTACHMENT_BYTES_KEY = "bytes"
ATTACHMENT_SHA256_KEY = "sha256"
ATTACHMENT_NAME_KEY = "name"
ATTACHMENT_DATA_KEY = "data"


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
    #: 段 10 lane 10o(agora-redesign #96): 添付の出来事(kind attachment)の欄 — mime と元の file 名は
    #: 見出しの欄・data は本文の欄(base64 の逐語・tombstone で消える)。他の kind では欠ける。
    mime: str | None = None
    name: str | None = None
    data: str | None = None
    #: 段 12 lane 12l(agora-redesign #383 粒 2): 本文が消された刻(storedEvent の tombstonedAt・epoch ms)。保存期間の係
    #: (retention)か手の tombstone で本文の欄が消えた行だけが持つ — 履歴の畳みは「空の本文」と「消えた本文」を見分けて印を付ける。
    tombstoned_at: int | None = None
    #: card acp:kanban-issue:ki-9fc7d4bca4dc: 出来事の版(契約 storedEvent.version・最初の追記は 1)。
    #: 記憶の畳み戻しが「行が消えて stream が残っている」形から今の版を読み直す材料(読みは最新の版だけを返す)。
    version: int = 1


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


@dataclass(frozen=True)
class HistoryMaterial:
    """会話の再開に使う材料の 1 度の読み(card acp:kanban-issue:ki-c3aace97d825 で agentd.history-for から抽出)。

    畳み先は 2 つある —— 履歴を 1 通の最初の本文にする rehydrate と、手番ごとの行にする transcript の組み直し。
    **材料の読みは 1 度**(記録の service と ACP の行を 2 度引かない): 組み直しが通らなかった拍に rehydrate へ
    戻る時も、同じ材料をそのまま使う。

    messages   この会話を名指す ACP の kind message の行
    source     手番の材料(記録の service の本文 = RecordedTurns / 届かなかった時の見出し = HeadlineTurns)
    fetched    本文を記録の service に置いた郵便の表(郵便 id → 本文)
    summaries  この会話の要約(古い順)
    floor_at   要約が覆う記録の終わりの時刻(epoch ms・None = 要約なし / 読めない)
    head_seq   読めた出来事の recordSeq の最大(0 = 出来事なし)。組み直した transcript の会話の id の材料。
    """

    messages: tuple["AcpRow", ...]
    source: HistorySource
    fetched: dict
    summaries: tuple
    floor_at: int | None
    head_seq: int


# ------------------------------------------------------------------ agentd の状態


@dataclass(frozen=True)
class TurnReopen:
    """この手番が前の手番の実行環境をどう引き継いだか(契約 agora-kinds.json turn-record spec.reopen)。

    card acp:kanban-issue:ki-4c0a0aa06b07。mode = next-arm-for-job が選んだ腕(defer はここに来ない —
    defer は手番を始めないので turn-record も無い)。home_digest = session-affinity-key-of の鍵
    (account・binding・model の組)の sha256 で、**値は不透明・比較にだけ使う**。

    なぜ行に載せるか: keepalive の controller は「次の手番も同じ prefix cache を読むか」を知る手がかりを
    1 つも持っていなかった(実測 2026-09-23 ~/experiments/agent-subtask-cost/out/turn-reopen-cold-cache:
    ping 102 本は cache を温めたが、次の手番が rehydrate で先頭を捨てるので 4/4 が冷えた)。
    """

    mode: NextArm
    home_digest: str


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
    #: card acp:kanban-issue:ki-ef537db05f7f: この手番の材料(start_offset から読む transcript / events)が
    #: **手番の始まりから覆っているか**。手番の始まりに取った offset(after-start)は必ず覆う。再起動の後に行から
    #: 拾い直した手番(recover-job)は、offset を取ったのが手番の始まりではなく拾い直した拍なので、file の頭から読む腕
    #: (launch / rehydrate = この手番が session を起こした)だけが覆い、send / resume の腕は覆わない。覆っていない材料の
    #: 「出力 0 件」は『出さなかった』の証拠にならない(judgment.turn-output-condition-of が TurnOutputUnmeasured に分ける)。
    materials_cover_the_turn: bool
    #: 起動・sendを呼ぶ前に読んだ時計。復旧時は不明(None)。送信完了のturn_floor_msで代用しない。
    request_start_lower_bound_ms: int | None = None
    #: job回収後もkeepaliveが照合する実行元。bindingの実値をturn-recordへ残す。
    cache_context: JSONObject | None = None
    #: card acp:kanban-issue:ki-4c0a0aa06b07: この手番の引き継ぎ方(turn-record の spec.reopen へ写す)。
    #: None = 不明(組み立て側が腕を渡さなかった拍 — 欄を書かない。現在の設定から補わない)。
    reopen: TurnReopen | None = None
    #: この手番の結びの試みの回数(status.binding.attempt の写し・欄の無い結びは 1)。turn-record の spec.attempt に写し、
    #: 記録を続ける拍の揃え直しを単調にする(古い試みの agentd は新しい試みの記録を書き戻せない — card ki-90019f023e19)。
    attempt: int = 1
    #: 記録を続ける拍の spec の揃え直しがまだ書けていない印(Conflict が続いた・断られた — card ki-90019f023e19)。
    #: 立っている間は、次に行を鍵で読む拍(append-entries・end-turn-record)が同じ判断(agentd.realign-record-spec)を
    #: もう 1 度掛ける。単調性(spec.attempt)があるので追記の拍に掛けても古い試みと新しい試みが書き合わない。
    record_spec_dirty: bool = False
    #: 手番の記録(turn-record)の行の最後に知った image(段 8 lane 4u — 出来事の追記の CAS の相手)。
    #: None = まだ読んでいない(最初の追記で鍵から読む)。書けた拍に generation + 1 と書いた status で
    #: 差し替え、Conflict は読み直して積み直す。正本は行(R7)— 再起動で消えても鍵から戻る。
    record: AcpRow | None = None
    #: 読んだが行へまだ書けていない出来事(書きが断られた / 行がまだ無い拍の持ち越し)。次の拍の
    #: 追記と手番の終わりの書きに先頭で乗る(出来事は落とさない・順は seq)。
    pending_entries: tuple[TurnEntryHeadline, ...] = ()
    #: 会話の記録の service が受理した答え(本文の在処 recordRef・受理済みの最大 producerSeq)のうち、行へまだ
    #: 写していないもの(card acp:kanban-issue:ki-c418e597017a 便 3)。走っている手番では受理のたびに行を書かず、
    #: **次の追記の書きか手番の終わりの書きに同乗させる** — 行の書きは status の全体(見出しの配列ごと)の post-image
    #: なので、整数 1 つを進めるための単独の書きが ACP の journal の 4 分の 1 を占めていた(実測 2026-09-21: 連続する
    #: 書き 745 対のうち 375 対が recordedSeq だけの差)。None = 写すものが無い。正本は service(行の値は写し)。
    recorded_mark: tuple[str, int] | None = None
    #: 段 8 lane 4x: この手番で器へ渡した割り込みの Message の id(memory の写し — 行の
    #: interruptsDelivered への CAS が着地するまでの間、同じ id を二度渡さないための cache。正本は行:
    #: 再起動で消えても、行の interruptsDelivered に在る id は渡さない)。
    interrupts_sent: tuple[str, ...] = ()
    #: 段 10 lane 10n(agora-redesign #93): 期限(秒)= 行の charter.interruptEscalationSeconds の写し(None = 宣言なし —
    #: 注入だけにして条件 InterruptEscalationUndeclared・停止の合図は出さない)。code に既定を置かない。
    interrupt_escalation_seconds: int | None = None
    #: 注入した割り込み(Message の id → 注入した時刻 ms・注入した順)。期限の判断(judgment.interrupts-due-for-escalation)
    #: の材料。拾い直し(recover-job)は行の interruptsDelivered のうち読まれていない id を拾い直した時刻で積む。
    interrupts_injected: tuple[tuple[str, int], ...] = ()
    #: model が読んだ証拠(Message の id → 証拠の出来事の seq)— 行の interruptsRead の写し(memory)。
    interrupts_read: tuple[tuple[str, int], ...] = ()
    #: 停止の合図を送った(Message の id → 送った時刻 ms)— 行の interruptsEscalated の写し(memory)。
    interrupts_escalated: tuple[tuple[str, int], ...] = ()
    #: 読んだ / 止めた印のうち行へまだ書けていないものが在る(書きが断られた拍の持ち越し — 次の拍に撃ち直す)。
    interrupt_marks_dirty: bool = False
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
    #: 段 12 lane 12j(agora-redesign #367): 行の spec.cancel(段 1 の合図)の写し(None = 取り消されていない)。見届けの拍
    #: (acknowledge-cancel)に据え、手番の終わりの result.cause と強制の段の判断(judgment.cancel-arm-for)の材料。拾い直し
    #: (recover-job)は行から写す(judgment.recovered-cancel-of)。
    cancel: JobCancel | None = None
    #: 走行器が開いたままの道具の block(書きかけの引数の差分を id と名に結ぶ表 — OpenToolBlock)。実況のための memory の
    #: 写しで、行にも記録にも書かない。再起動で消えたら、以後に開く道具から書きかけが出る(完成の呼び出しは変わらない)。
    open_tool_blocks: tuple[OpenToolBlock, ...] = ()
    #: 見届けた拍(ms・None = まだ)= 行の status.cancel.acknowledgedAt の写し。行への書きが断られても memory に置く
    #: (割り込みを毎拍撃ち直さない)— 再起動で消えれば行から戻り、行にも無ければ改めて見届ける(割り込みは新しい器へ)。
    cancel_acknowledged_at_ms: int | None = None
    #: 段 12 lane 12j(agora-redesign #422): 見届けの拍に器へ割り込み(session.interrupt = SIGINT)を実際に撃ったか。撃った
    #: 取り消しは器の transcript に「利用者が tool を拒んだ」印を残すので、手番の終わりに温かい session を片付ける(判断は
    #: judgment.retire-reason-after-job)。手番が既に終わっていて割り込まなかった取り消しは片付けない。拾い直し(再起動後)は
    #: 行の見届けが在れば「撃った」とみなす(印の有無は読めない — 片付ける側に倒す)。
    cancel_interrupted: bool = False
    #: card acp:kanban-issue:ki-2bd49c68b042: この手番の材料(start_offset から読んだ events)に、走行器が名乗った
    #: 結末の記録が出たか(DeltaBatch.turn_result の積み上げ)。手番ごとの InFlightJob に載るので前の手番の結末は
    #: 継がない(次の手番の start_offset は送る前の file の大きさ)。判断の材料は**その拍で読んだもの**でなければ
    #: 意味が無い(CLI が result を出して降りた拍と host の monitor の拍の競合)ので、拍の 1 周目は材料を読んでから
    #: 次の 1 手を決める(agentd.observe-job-fast)。拾い直し(recover-job)は材料の進みを知らないので False。
    turn_result_seen: bool = False


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
    #: 段 10 lane 10d: 自分の node の行の spec を宣言へ揃えられなかった(書き手の断り等)ことを 1 度だけ名乗った印。
    #: 揃えられた拍に戻る(heartbeat ごとに同じ断りを吐かない)。
    node_spec_refusal_logged: bool = False
    #: 段 9f lane 9f-2: spool の再送を止めている拍(最後に送れなかった ms・None = 送れている — 毎拍 flush する)。
    record_backoff_ms: int | None = None
    #: 最後に計器へ出した spool の深さ(None = まだ — 変わった時だけ agentd_record_spool_depth を出す)。
    record_spool_depth: int | None = None
    #: 段 12(agora-redesign #577): pane の席を読めなかった最後の理由(同じ理由は 1 度だけ log する印 —
    #: 読み口を持たない機体〔pool の pod〕が周期ごとに同じ行を吐かない)。読めた拍に "" へ戻る。
    pane_seats_note: str = ""
    #: ADR-DOE-AGENTS-012 R61(card acp:kanban-issue:ki-f250d67a7157): この node がいま申告している agent の種類(参加の拍が
    #: judgment.launchable-agent-kinds で決め、node の status.capabilities に書いた種類・種類の名の順)。claim の起動前の検査
    #: (judgment.agent-kind-refusal-of)はこれに無い種類の job を AgentKindUnavailable で閉じる。None = この process はまだ
    #: 1 度も観測していない(起動直後の参加の拍より前・参加の拍が I/O の失敗で途中で落ちた)— 判らないもので止めない
    #: (観測した「空」は () で、host に届かない拍・読み口の無い host はこちら)。
    agent_kinds: tuple[str, ...] | None = None
    #: 実行ファイルの在否を読めなかった最後の理由(同じ理由は 1 度だけ log する印 — pane_seats_note と同じ作法)。読めた拍に "" へ戻る。
    drivers_note: str = ""
    #: 段 10f 便 2(agora-redesign #82): session ごとの直前の手番の文脈の使用率(%・手番の終わりに材料の末尾から測る —
    #: judgment.context-percent-of)。次の手番の claim が会話の宣言 compactAt と比べる材料(judgment.compaction-due)。
    #: memory の cache — agentd の再起動で消え、次の手番の終わりに測り直す(turn-record に同等の欄が無い間の実測)。
    context_by_session: tuple[tuple[str, int], ...] = ()
    #: 行の cache を読んだ store の版(read-freshness.json)。窓の答えが別の版を名乗れば全量 list へ(judgment の 1 点)。
    store_epoch: str | None = None
    #: 段 12 lane 12a(agora-redesign #230): 走らせている verify の命令(memory の写し — 正本は行の
    #: sessionHandle.verify と結末の file。再起動で消えても Running の行から組み直す: agentd.recover-command)。
    commands: tuple[InFlightCommand, ...] = ()
    #: 段 12 lane 12j(agora-redesign #233): 走らせている summarize(memory の写し — 正本は行の sessionHandle.summarize と
    #: 結末の file。再起動で消えても Running の行から組み直す: agentd.recover-summarize)。
    summaries: tuple[InFlightSummarize, ...] = ()
    #: 段 12 lane 12j(agora-redesign #321): 自分の**生きている** node の行の id(join の拍が live_row の判断で解いた行・作った行の
    #: resource id)。None = まだ参加していない(結びの nodeRow は照合できない = その結びは受けない)。再起動で消えても次の参加で戻る。
    node_row_id: str | None = None
    #: 段 12 lane 12j(agora-redesign #402): 着かなかった Ended の持ち越し(job ごとに 1 つ)。毎拍 agentd.record-unrecorded-ends が行を
    #: 読み直して書き直す。この id の Bound の行は claim しない(同じ手番を別の session で走らせない)。
    unrecorded_ends: tuple[UnrecordedEnd, ...] = ()
    #: 段 12(agora-redesign #537 便 1): 走っている turn-record の巡回の最後の拍。None = まだ 1 度も(起動直後は即・
    #: その後は AgentdSettings.turn_record_sweep_seconds の周期)。巡回は memory を持たない(正本は行)。
    last_turn_record_sweep_ms: int | None = None


# ------------------------------------------------------------------ 要求(ACP)


@dataclass(frozen=True)
class AcpGet(EffectBase):
    """kind の生きた行をすべて読む(``GET /api/resources?kind=<kind>``)。結果 = tuple[AcpRow, ...]。"""

    kind: str


@dataclass(frozen=True)
class AcpGetRow(EffectBase):
    """1 行を鍵で読む(``GET /api/resources/<key>``)。結果 = AcpRow | None(404)。"""

    key: str


#: 段 10 lane 10ba(agora-redesign #115): 郵便の行が会話を名指す spec の欄(契約 agora-kinds の message.spec の
#: to / from)。郵便の読み(AcpConversationMail)は ACP の一覧の field selector を欄ごとに 1 回撃つ(ACP の
#: field selector は 1 回の読みに条件 1 つ)。どの郵便を履歴に入れるかの判断は judgment.rehydrate-history-of の 1 点。
MESSAGE_CONVERSATION_FIELDS: tuple[str, ...] = ("to", "from")
#: 段 10 lane 10ba: 手番の記録の行が会話を名指す spec の欄(契約 agora-kinds の turn-record.spec.conversationId)。
TURN_RECORD_CONVERSATION_FIELD = "conversationId"


@dataclass(frozen=True)
class AcpConversationMail(EffectBase):
    """会話の郵便を読む(段 8q の履歴からの再開 — 手番を起こし直す時の 1 回だけ): kind message のうち、
    spec の MESSAGE_CONVERSATION_FIELDS(to / from)のどれかがこの会話の行だけ(段 10 lane 10ba・agora-redesign #115:
    ``GET /api/resources?kind=message&fieldSelector=spec.<欄>=<conversation_id>`` を欄ごとに 1 回撃ち、行の鍵で
    合わせる — 旧来は kind の全量で、本番の実測 1,630 行・6 MB・5.4〜5.7 秒)。どの郵便を履歴に入れるかの判断は
    judgment.rehydrate-history-of の 1 点のまま。結果 = tuple[AcpRow, ...]。手番の本文はここでは読まない(本文は
    記録の service = RecordRead・見出しは AcpTurnHeadlines)。watch の拍では撃たない(郵便の本文は鍵で
    1 行ずつ — R14)。"""

    conversation_id: str


@dataclass(frozen=True)
class AcpTurnHeadlines(EffectBase):
    """会話の手番の見出し(kind turn-record の行)を読む — **薄い再開の拍だけ**(記録の service が配線されて
    いない・答えなかった時 — 段 9q・agora-redesign #77)。読みはこの会話の行だけ(段 10 lane 10ba・#115:
    ``GET /api/resources?kind=turn-record&fieldSelector=spec.conversationId=<conversation_id>``)。旧来の kind の
    全量(実測 2026-09-14: 29,913 行・172 MB・頭の応答 59 秒)は、claim の後の手番の準備を 2 分超えさせ、node の
    lease(TTL 90 秒)が切れて Scheduling が Running の行を Pending に戻した。service が答えた拍には撃たない。
    結果 = tuple[AcpRow, ...](畳みの判断は judgment.rehydrate-history-of)。"""

    conversation_id: str


@dataclass(frozen=True)
class AcpRunningTurnRecords(EffectBase):
    """走っている手番の記録の行(段 12・agora-redesign #537 便 1:
    ``GET /api/resources?kind=turn-record&fieldSelector=status.state=running``)。結果 = tuple[AcpRow, ...]。

    終状態を読む巡回(agentd.sweep-turn-records)の入口 — kind の全量(実測 29,913 行 / 172 MB)ではなく、engine の
    status 軸の field selector で走っている記録だけを引く。読んだ行は判断の材料で、**書く相手ではない**(書く前に
    鍵で読み直す — 正本は行)。"""


@dataclass(frozen=True)
class AcpConversationSummaries(EffectBase):
    """この会話の kind summary の行(段 12 lane 12j — ``GET /api/resources?kind=summary&fieldSelector=spec.conversationId=<cid>``)。
    結果 = tuple[AcpRow, ...]。要約の区間の下端(既に在る summary の to の最大 + 1)と履歴からの再開の材料。"""

    conversation_id: str


@dataclass(frozen=True)
class AcpConversationMemories(EffectBase):
    """この会話の kind agent-memory の行(``GET /api/resources?kind=agent-memory&fieldSelector=spec.conversationId=<cid>``・
    宣言の indexes が引く)。結果 = tuple[AcpRow, ...]。手番の頭の水入れ(行 → 置き場)と、手番の終いの畳み戻しが
    append と supersede を撃ち分ける材料(行の recordSeq の在否 — 法 ACP 575b1e)。"""

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
class AcpPutSpec(EffectBase):
    """行の spec を丸ごと書く(``POST /api/events`` の spec_applied・ifGeneration = 行の generation)。

    段 10 lane 10d(agora-redesign #85): node の spec は agentd が機体の宣言から名乗る。engine の SpecApplied は
    status の軸を触らない(Acp.App.Schema.State.axisScopedResource)。結果 = WriteOutcome。
    """

    row: AcpRow
    spec: JSONObject
    #: 読んだ宣言 file の指紋(段 10 lane 10y)— 在れば header x-declaration-sha256 で運ぶ(kind node の declaredByFile の欄を
    #: 変える書き)。None = 運ばない。
    declaration_sha256: str | None = None


@dataclass(frozen=True)
class AcpCreate(EffectBase):
    """行を作る(``POST /api/events`` の spec_applied・status は運ばない = engine が生まれの state を刻む)。

    結果 = WriteOutcome。
    """

    namespace: str
    kind: str
    resource_id: str
    spec: JSONObject
    #: 読んだ宣言 file の指紋(段 10 lane 10y)— 在れば header x-declaration-sha256 で運ぶ(誕生も declaredByFile の欄を置く書き)。
    declaration_sha256: str | None = None


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
class RecordSupersede(EffectBase):
    """既に在る出来事を**新しい版で置き換える**(契約 supersede: ``POST /v1/conversations/{cid}/events/{recordSeq}/supersede``・
    Bearer = 名簿の agentd の札)。上書きではない — 前の版は recordSeq + version + supersedes の鎖として残り、
    tombstone を撃たない限り本文ごと在る(法 ACP 575b1e)。event.producerSeq は置き換える行と同じでなければ 400。
    結果 = RecordSupersedeOutcome(送れなさも値で返す)。"""

    conversation_id: str
    record_seq: int
    reason: str
    event: JSONObject


@dataclass(frozen=True)
class RecordRead(EffectBase):
    """会話の出来事を後向きに 1 頁読む(契約 readEvents: ``GET /v1/conversations/{cid}/events?before=<latest|recordSeq>&limit=``・
    読み手 = 名簿の agentd)。before = None は latest(末尾の頁)。結果 = RecordReadOutcome(読めなさも値で返す)。
    履歴からの再開(段 9f lane 9f-4・設計 §2.4)の 1 回だけ撃つ — watch の拍では撃たない。"""

    conversation_id: str
    before: int | None
    limit: int


@dataclass(frozen=True)
class RecordReadSince(EffectBase):
    """会話の出来事を**前向きに** 1 頁読む(契約 readEvents: ``GET /v1/conversations/{cid}/events?since=&limit=&kinds=``・
    読み手 = 名簿の agentd)— 段 12 lane 12j の要約の区間の読み(since = 区間の始まりの 1 つ前・kinds = 原文の kind だけ)。
    結果 = RecordReadOutcome(読めなさも値で返す)。"""

    conversation_id: str
    since: int
    limit: int
    kinds: tuple[str, ...]


@dataclass(frozen=True)
class RecordReadStream(EffectBase):
    """段 10f 便 1b(agora-redesign #82): 郵便 1 通の本文を読む(契約 readStreamEvents:
    ``GET /v1/conversations/{cid}/streams/{streamId}/events?since=0&limit=``・読み手 = 名簿の agentd)。
    会話と stream は郵便の spec.bodyRef ちょうど(1 郵便 = 1 出来事 kind message)。結果 = RecordReadOutcome
    (読めなさも値で返す)。郵便の本文が行に無い時だけ撃つ。"""

    conversation_id: str
    stream_id: str


# ------------------------------------------------------------------ 要求(custody)


#: 走行係が**話せる**貸与の契約の版(段 10 lane 10d 便 4・agora-redesign #85)。預かり所が
#: /health の欄 contract で名乗る版とこの数が合わない機体は参加しない(起動の前段で断る)。
#: ⚠ 版の**定義点は預かり所**(custody の Custody.Contract.Version と契約 custody-api.json の
#: version)で、ここは「この客が話せる版」の宣言 — 上げるのは預かり所が先・客は後。
#: 起点(実弾 2026-09-15 01:48〜02:37): 版 2 を話す agentd が版 1 の預かり所より先に本番へ出て、
#: 貸与の答えを malformed grant と読み、本番の手番が 49 分間 1 つも走らなかった。
CUSTODY_CONTRACT_VERSION = 2


@dataclass(frozen=True)
class CustodyHealth(EffectBase):
    """預かり所の健康と**契約の版**の読み(``GET /health``・札は要らない)。

    結果 = 答えの body(JSONObject)か None(届かない / JSON でない)。判断は持たない —
    版が話せるかを判じるのは judgment.custody-contract-refusal の 1 点。
    """


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


def _empty_json_object() -> JSONObject:
    """既定の空の env(型のついた工場 — 既定値を共有しない)。"""
    return {}


@dataclass(frozen=True)
class SessionSend(EffectBase):
    """``session.send``(本文を live の composer へ paste + Enter)。結果 = ``str | None |
    SessionRefused``: str = 器が添付を落とした理由(呼び手が条件 AttachmentIgnored に写す)・
    None = 全部渡った・SessionRefused = host が送りを断った(RPC の error 封筒 — 走っている手番が
    無い・行が無い・同じ名の process が既に在る。**本文は届いていない**ので呼び手が条件
    InputUndelivered に写す)。socket の失敗(OSError)は素通し(tick の縁が持ち越す)。

    ``awaiting`` = 送った本文は agent への prompt で owed(host が awaiting latch を立て、正の
    作業証拠が出るまで見かけの turn-end を評価しない — 温かい手番の始まりの印)。

    ``session_env`` = **この手番の** env(段 10 lane 10d 便 2 の追補 2・実弾 #92)。降りた process を
    器が ``--resume`` で起こし直す時に重ねる値で、預かり所の貸与の札はここで運ぶ(行には残らない)。
    値は秘密 — log・簿・argv に出さない。
    """

    session_id: str
    text: str
    awaiting: bool
    session_env: JSONObject = field(default_factory=_empty_json_object)
    #: card acp:kanban-issue:ki-a40292ed30d9(4 つ目の腕): **この手番の荷**(policy.TURN-CARRIED-KEYS =
    #: 記憶の置き場 memory_dir と冊 memory_files)。器が降りた process を ``--resume`` で起こし直す時に
    #: charter の代わりになる値で、行には残らない(正本は ACP の行 — 法 ACP 575b1e)。
    #: ⚠ ``session_env`` とは**別の口**: あちらは資格(秘密 — log にも argv にも出さない)の袋で、
    #: こちらは置き場の file に落ちる本文。同じ袋に入れると、冊の本文が秘密の規律の側へ紛れる。
    turn_charter: JSONObject = field(default_factory=_empty_json_object)
    #: 段 10 lane 10o(agora-redesign #96・依頼者の追補 2026-09-14・法 012 R21): 郵便の添付を**型つき**で
    #: 器へ渡す。CLI の綴り(block / input の項)は kind ごとの Dialogue が組む — agentd は組まない。
    #: 受けない器は SessionRefused で断り、呼び手が条件 AttachmentIgnored に写す(黙って落とさない)。
    attachments: tuple[TurnAttachment, ...] = ()


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
    #: 段 10 lane 10n: 注入の行の名(headless の claude は user の行の uuid — CLI の command_lifecycle がこの綴りで運命を
    #: 名乗る)。agentd は Message の id そのものを渡す(対応表なし・events の行が messageId を名指す)。
    ref: str = ""
    #: 段 10 lane 10o: 割り込みの郵便の添付(型つき — 綴りは Dialogue)。
    attachments: tuple[TurnAttachment, ...] = ()


@dataclass(frozen=True)
class Escalated:
    """器が停止の合図を出した(段 10 lane 10n)。"""


@dataclass(frozen=True)
class SessionEscalate(EffectBase):
    """``session.escalate``(段 10 lane 10n・agora-redesign #93): 注入した割り込みの本文を model が期限まで読まなかった
    時の停止の合図 — headless の claude = control_request interrupt(走っている道具 / 生成を止め、注入の行が同じ session の
    次の手番として即座に走る・host から見た手番は続く)/ codex は注入の段が無い(inject が止めて渡す)ので host が断る。
    結果 = Escalated | SessionRefused(host の断り = 出す物が無い・器が無い)。socket の失敗(OSError)は素通し。
    """

    session_id: str


@dataclass(frozen=True)
class SessionGet(EffectBase):
    """``session.get``。結果 = SessionView | None(未登記)。"""

    session_id: str


@dataclass(frozen=True)
class SessionList(EffectBase):
    """``session.list``(lifecycle で絞る)。結果 = tuple[SessionView, ...](新しい順)。

    statuses = 器の status の集合で絞る(器は SQL で絞る — 終端の履歴を読まない)。None = 全 status。
    limit / offset = 一致した行の頁(新しい順に offset 件飛ばして limit 件)。limit None = 全件。
    2026-09-23 会社 Mac の実弾: 参加の腕が絞らずに一覧を読み、終端の履歴 6,087 行(15 MB)を毎回
    受け取って器の読みの期限(10 秒)を越え続けた。呼び手は要る集合だけを名指す。"""

    lifecycle: str
    statuses: tuple[str, ...] | None = None
    limit: int | None = None
    offset: int = 0


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
class FsDirectoryExists(EffectBase):
    """path がこの機体に dir として在るか(段 10 lane 10y — 手番の work_dir の検)。結果 = bool。"""

    path: str


@dataclass(frozen=True)
class FsMakeDirectories(EffectBase):
    """dir を親ごと作る(段 10 lane 10y — scratch の印の在る work_dir だけ)。結果 = bool(作れた / 既に在る = True)。"""

    path: str


@dataclass(frozen=True)
class FsFileExists(EffectBase):
    """path がこの機体に file として在るか(段 12 lane 12a — verify の script の検)。結果 = bool。"""

    path: str


@dataclass(frozen=True)
class FsListDirectory(EffectBase):
    """dir の直下の file の名(card acp:kanban-issue:ki-9fc7d4bca4dc — 記憶の置き場の冊を数える)。
    結果 = tuple[str, ...](名だけ・path ではない・名の順)。dir が無い・読めない = 空(黙って空へ倒すのではなく
    『冊が 0』と同じ扱い — 置き場は手番の頭に作られるので、無い = まだ 1 冊も書いていない)。"""

    path: str


#: 記憶の 1 冊の読みの上限(置き場の file は人が読む散文 1 冊 — 実測 2026-09-20 の最大は 4,247 byte)。
#: 上限で切れた text は frontmatter が閉じないか本文が短くなるので、書き戻しの前に sha256 が変わって
#: 別の版として積まれうる ⇒ 器は大きく取る(1 MiB)。
MEMORY_BOOK_MAX_CHARS: int = 1_048_576

#: 小さな text の file の読みの既定の上限(rc / pid の file — 数字 1 行)。
FS_READ_TEXT_DEFAULT_MAX_CHARS: int = 256
#: summarize の claude の print モードの答え(result の JSON・本文 + usage + modelUsage)の読みの上限(段 12 lane 12j 便 4 の実弾 2026-09-16 16:04:
#: 9,207 byte の答えを既定 256 字で読んで「non-JSON」と断った)。要約の本文に上限は置かないので、答えの器は大きく(4 MiB)。
SUMMARY_ANSWER_MAX_CHARS: int = 4_194_304


@dataclass(frozen=True)
class FsReadText(EffectBase):
    """text の file を先頭から max_chars 字まで読む(段 12 lane 12a — verify の結末の rc / pid の file は既定 256・
    段 12 lane 12j — summarize の答えの JSON は SUMMARY_ANSWER_MAX_CHARS)。結果 = str | None(不在・読めない = None)。
    ⚠ 上限で切れた text は呼び手の読み(json.loads 等)が断る — 黙って短くならない。"""

    path: str
    max_chars: int = FS_READ_TEXT_DEFAULT_MAX_CHARS


@dataclass(frozen=True)
class CommandStarted:
    """命令の process を起こした(段 12 lane 12a)— sh の pid。"""

    pid: int


@dataclass(frozen=True)
class CommandRefused:
    """命令の process を起こせなかった(exec の失敗等)。"""

    error: str


CommandStartOutcome: TypeAlias = "CommandStarted | CommandRefused"


@dataclass(frozen=True)
class CommandStart(EffectBase):
    """verify の命令を機体で起こす(段 12 lane 12a・agora-redesign #230): argv(judgment.verify-argv-of の 1 点が組む
    sh の 1 行 — pid を書き、script を走らせ、rc を書く)を**自分の session で**(start_new_session)起こし、待たずに戻る。
    stdin は閉じ、stdout / stderr は argv の中の sh が log の file へ向ける。agentd が再起動しても process は残る。
    結果 = CommandStarted(pid) | CommandRefused(error)。"""

    argv: tuple[str, ...]
    cwd: str
    #: 段 12 lane 12j: process に足す env(親の env に重ねる — 借りた札 CLAUDE_CODE_OAUTH_TOKEN と家 CLAUDE_CONFIG_DIR)。
    #: 値は秘密を含み得る — log・簿・argv に出さない(偽の handler は名だけ数える)。空 = 足さない(verify)。
    env: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CommandRunning:
    """probe: まだ走っている(pid が生きている・rc の file が無い)。"""

    pid: int


@dataclass(frozen=True)
class CommandExited:
    """probe: 終わった(rc の file が在る)。"""

    rc: int


@dataclass(frozen=True)
class CommandGone:
    """probe: rc の file が無く pid も生きていない(結末を残さずに消えた)。"""


CommandProbeOutcome: TypeAlias = "CommandRunning | CommandExited | CommandGone"


@dataclass(frozen=True)
class CommandProbe(EffectBase):
    """verify の命令の現況(段 12 lane 12a): rc の file が在れば Exited(rc)・無ければ pid の生死で Running / Gone。
    pid = None(pid の file がまだ無い・読めない)は Gone ではなく Running と読まない — handler は pid の file を読み直す。
    handler が自分で起こした process なら poll()(reap)、拾い直した pid は kill -0 で生死を問う。
    結果 = CommandRunning(pid) | CommandExited(rc) | CommandGone。"""

    pid: int | None
    pid_path: str
    rc_path: str


@dataclass(frozen=True)
class CommandStop(EffectBase):
    """verify の命令を止める(SIGTERM を process group へ — 期限超過・取り下げ)。結果 = bool(合図を送れたか)。"""

    pid: int


@dataclass(frozen=True)
class OwnershipProbe(EffectBase):
    """検の方法(proof)に従って機体の所有の証拠を読む(gce-project:<id> = GCE の metadata server の
    project-id / file:<path>=<値> = その path の file の中身)。結果 = ProbeAnswer(読めなければ
    value None — 判断は join.ownership-verdict)。"""

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
class ListPaneSeats(EffectBase):
    """この機体の pane の席が担っている会話の席を読む(段 12・agora-redesign #577)。読み口は dotfiles の
    1 点(handlers.py の PANE_SESSIONS_COMMAND = `ai pane-sessions --json`)で、席の一覧・席の会話 id・
    席の家の解きはすべて向こうが単一所有する — agentd は答えの 4 欄を受けるだけ(第 2 の観測点を持たない)。
    結果 = PaneSeatsOutcome(席の列 / 読めなかった理由)。読めない拍は観測の pane の半分が空になるだけで、
    自分の session の観測は書く(判断は judgment.pane-observations-of・log は 1 度)。"""


@dataclass(frozen=True)
class ListHostDrivers(EffectBase):
    """host に、種類ごとの実行ファイルが子 process の実効 env で見つかるかを問う(ADR-DOE-AGENTS-012 R61 —
    host の読み口 ``drivers.list``・判定は host の drivers.driver_path_in の 1 点 = Popen の探索と同じ手順)。
    ``env`` = 呼び手が重ねる env(= 手番の charter に重ねる宣言の env AgentdSettings.seat_env — 認証は含まない)。
    結果 = HostDriversOutcome(種類ごとの在否の列 / 読めなかった理由と届いたか)。参加の周期ごとに問い直す
    (host も agentd も結果を持たない — 稼働中に消えた実行ファイルは次の周期に申告から落ちる)。"""

    env: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ResolveLocalExecutable(EffectBase):
    """agentd 自身の実効 env(``os.environ``)で実行ファイル ``word`` が見つかる path(ADR-DOE-AGENTS-012 R61)。
    agentd が自分で起こす要約の job(AGENTD_LAUNCHED_KINDS)の在否の観測 — 判定は drivers.driver_path_in の 1 点
    (host と同じ関数)。結果 = str | None(None = 見つからない)。"""

    word: str


@dataclass(frozen=True)
class ReadProfileUsage(EffectBase):
    """この機体が持つ資格(kind)の profile ごとの残量を読む(段 7 lane 7d-3)。読み口は dotfiles
    agentcli の usage の 1 点(handlers.py の USAGE_COMMAND = `ai usage --json`)で、会社境界(会社
    profile の API 呼び出しは会社機体だけ)はその葉が判定する — 断られた profile は
    ProfileUsageUnavailable で返り、agentd は書かない。``cache_ttl_seconds`` より若い断面は読み直さない。
    結果 = tuple[ProfileUsageOutcome, ...](この機体に無い profile は列に無い)。"""

    kind: LeaseKind
    cache_ttl_seconds: int
