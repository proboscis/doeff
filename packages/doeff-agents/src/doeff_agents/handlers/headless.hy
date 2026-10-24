;;; headless の handler — doeff-agents の公開 effect(effects/agent.py)を doeff-claude-code の公開 effect へ写す adapter
;;; (agora-redesign #604)。
;;;
;;; 層: claude の print mode の CLI ← doeff-claude-code(process の寿命)← この adapter(agent の寿命)← 呼び手の Program。
;;; この module は process を知らない: 子 process・起動の引数・pid・信号・降りた process の起こし直しは doeff-claude-code の handler の
;;; 中にある。ここが持つのは agent の寿命の判断だけ — 手番の順番(走っている手番の後に回す入力を待たせる)・続きの身元(resume_from)・
;;; 層 2 の行から層 3 の出来事の型への写し。session host(doeff_agents.sessionhost)の socket も台帳も使わない。
;;;
;;; 写し(layer2-effects-design.md 10 節):
;;;   LaunchEffect(CLAUDE)            → ClaudeStartTurn(FreshSession か、resume_from なら ResumeSession)— prompt が無ければ手番を始めない
;;;   LaunchEffect.turn_credential_ref → 起こす直前に RedeemTurnCredentialEffect(参照)を外側へ出し、答えの TurnCredential を層 2 の宣言の
;;;                                      借りた資格(ClaudeSessionSpec.credential = LentCredential)にする — 層 2 はその process だけの設定
;;;                                      dir の .credentials.json に置き、子の env には置かない(card acp:kanban-issue:ki-4ffdafa021e5)。
;;;                                      HomeTurnCredential = 家の資格のまま・TurnCredentialUnavailable = TurnCredentialUnavailableError で
;;;                                      起こさない — issue #979
;;;   ReplaceSessionCredentialEffect   → 参照を RedeemTurnCredentialEffect で引き換え、ClaudeReplaceSessionCredential(会話の id・借りた資格)
;;;                                      で走っている process の資格を替える。session の宣言の資格も替える(後の手番もその資格で起こす・
;;;                                      使い回す)。答え = SessionCredentialReplaced | SessionProcessNotLive | SessionOnHomeCredential |
;;;                                      ReplacementCredentialUnavailable
;;;   LaunchEffect.new_context_id      → 新しい文脈の id(HandlerMadeContextId = この adapter が作る・NamedContextId = 呼び手の名指した id)。
;;;                                      同じ id を名指した 2 つの起動は同じ FreshSession になり、片方の事前起動をもう片方の最初のターンが使う
;;;   LaunchEffect.resume_snapshot     → その session の最初の ClaudeStartTurn の ResumeSession の carry = Rebuilt(写し)(2 手番目からは無し)
;;;   LaunchEffect.autocompact         → 層 2 の会話の宣言の autocompact(CLI の argv の --autocompact — None = 載せない・CLI の既定)
;;;   ExportContextEffect(CLAUDE)      → ClaudeExportSession(SessionExported → 写しの本文・SessionNotFound → None)
;;;   SendEffect / FollowUpEffect      → 手番が走っていなければ ClaudeStartTurn(ResumeSession)、走っていれば待たせて終わりの後に始める
;;;   FollowUpEffect(mode = INJECT)    → ClaudeInjectInput(走っている手番に足す)
;;;   InterruptEffect                  → ClaudeInterruptTurn(手番だけを止める。待たせた入力は次の手番で走る。for_input の入力を層 2 がもう読んでいれば止めない)
;;;   EventsEffect / AwaitResultEffect / MonitorEffect → ClaudeReadTurnEvents(行を層 3 の出来事と手番の終わりに写す)
;;;   手番の終わりの last-call-usage・last-call-model・model-windows → AgentTurn*.last_call_usage(AgentTurnUsage — 額は None)・
;;;                                      last_call_model・model_windows(会話の今の context の大きさの材料 — agora-redesign #3744)
;;;   Completed / Failed の account-limit(AccountLimitHit)→ AgentTurnCompleted / AgentTurnFailed.account_limit(AgentAccountLimit — 口座の限度に
;;;                                      当たった事実・agora-redesign #3983)
;;;   Completed / Failed の account-refusal(AccountRefusalHit)→ AgentTurnCompleted / AgentTurnFailed.account_refusal(AgentAccountRefusal —
;;;                                      口座の側が要求を断った事実・本番 2026-10-08 22:32 の口座 cryptic-2 の oauth_org_not_allowed)
;;;   Completed / Failed の model-refusal(ModelRefusalHit)→ AgentTurnCompleted / AgentTurnFailed.model_refusal(AgentModelRefusal —
;;;                                      model の安全装置が要求を断った事実・本番 2026-10-20 の Fable 5.1 の reasoning_extraction)
;;;   Failed の api-error-status 529(API の混雑 — 本番 2026-10-24 09:26 JST の「API Error: 529 Overloaded. This is a server-side issue,
;;;                                      usually temporary — try again in a moment.」)→ AgentTurnFailed.provider_capacity(AgentProviderCapacity —
;;;                                      model = 会話の宣言の model・text = CLI の文)。待つ秒と試し直す回数は上の層が持ち、ここは見分けて
;;;                                      運ぶだけ(provider-capacity-of・agora card ki-0f5382d587a0)
;;;   Failed / BackendLost の exit-code・stderr-tail → AgentTurnFailed / AgentTurnLost.exit_code・stderr_tail(手番の途中で降りた CLI の
;;;                                      process の終了 code と stderr の末尾・agora-redesign #4207)
;;;   LaunchFailed の exit-code・stderr-tail → AgentLaunchError.exit_code・stderr_tail(stderr の末尾は層 2 と同じ上限 stderr-tail-within)。
;;;                                      待たせた入力の手番を起こせなかった時は AgentTurnFailed の同じ欄へ(start-failure)
;;;   AssistantMessage.tool-calls / ToolResult.answers → AgentToolUseEvent.tool_calls / AgentToolResultEvent.answers(道具の呼びの命令
;;;                                      ToolCall.input と結果の中身 ToolAnswer を層 2 の型のまま運ぶ — agora-redesign #3744)。行の
;;;                                      parent-tool-use-id(subagent の行なら親の呼びの id・本体の会話は None)も同じ名の欄へ運ぶ —
;;;                                      上の層が subagent の中の道具の呼び・答えを本体の会話の物と見分けるため(agora card ki-b25d579bf48b)
;;;   StopHookFeedback.reason          → AgentStopHookFeedbackEvent.reason(Stop hook が答えを差し戻した事実 — 上の層が差し戻された
;;;                                      答えの本文を見分けるため・agora-redesign #4020)
;;;   HookNotice(段階 RESPONSE)       → AgentHookEvent(CLI が回した hook の応答 — 行の欄 event・name・hook-id・stdout・stderr・output・
;;;                                      exit-code・outcome をそのまま写す。出力の無い応答も全部運び、どれを残すかは上の層が選ぶ。上の層が
;;;                                      会話の画面に hook の結果を出すため・card acp:kanban-issue:ki-d8b473480303)
;;;   CompactBoundary                  → AgentCompactionEvent(CLI が会話を圧縮した事実 — 行の欄 trigger・pre-tokens・post-tokens・
;;;                                      cumulative-dropped-tokens・duration-ms をそのまま写す。上の層がターンの出来事として記録するため・
;;;                                      agora-redesign #4189)
;;;   CompactEnded                     → AgentCompactionEndedEvent(CLI が圧縮を終えた事実 — 終わり方 outcome〔成功・失敗・中断・圧縮
;;;                                      しなかった〕と誤りの文 error を写す。上の層が失敗と中断も記録するため・card ki-8239be27d5f6。
;;;                                      CLI は圧縮の要求の token を行に名乗らないので運ばない)
;;;   SubagentTranscript.calls        → AgentSubagentCallUsageEvent(層 2 が subagent の task の報せで読んだ transcript の呼びごとに 1 つ —
;;;                                      親の道具の呼びの id・呼びの id〔message.id〕・usage・model・呼びの刻を運ぶ。上の層が subagent の
;;;                                      消費を本体の呼びと分けて記録するため・card ki-8239be27d5f6。背景の task を止めた CLI は subagent の
;;;                                      呼びの行を stdout に出さないので、stdout の subagent の行〔parent-tool-use-id の在る assistant の行〕
;;;                                      からは運ばない — 拾う元は transcript の 1 つ・card ki-e798888dce4e)
;;;   AssistantMessage.usage / .model  → AgentCallUsageEvent(本体の会話の行〔parent-tool-use-id が None〕が usage を名乗るたびに、その呼びの
;;;                                      usage と model を運ぶ — 上の層が手番の終わりを待たずに会話の今の context の大きさを読むため。
;;;                                      線引きは手番の終わりの last_call_usage と同じ: subagent の行は運ばず、usage を名乗らない行は
;;;                                      出来事にしない・card ki-a2e985c009ae。呼びの id〔message.id〕を添え、final = False — 行の usage の
;;;                                      出力は呼びの途中の値・agora card ki-290ec664737d)
;;;   CallClosed                       → AgentCallUsageEvent(層 2 が本体の呼びの始まり〔message_start〕と終わり〔message_delta〕から組んだ
;;;                                      呼びの最終の usage — 呼びの id・model を添え、final = True。上の層が呼びごとの使用量を最終の値で
;;;                                      記録するため・agora card ki-290ec664737d。CLI が止めた呼び〔stopped が偽 — 止まった訳を名乗らない
;;;                                      終わり〕は final = False・agora card ki-7498a039250d)
;;;   Completed.usage / Failed.usage   → AgentTurnCompleted.usage / AgentTurnFailed.usage(AgentTurnUsage — cache_creation → cache_write・cache_read → cache_read・
;;;                                      cache_creation の寿命の内訳 ephemeral_5m / ephemeral_1h → cache_write_5m / cache_write_1h〔寿命で単価が違うので
;;;                                      上の層が寿命ごとに値を付ける・card ki-75d813bebc27〕。CLI が名乗らない欄は None のまま・欄がどれも無ければ usage = None)
;;;   Completed / Failed の model-usage・model-usage-gap → AgentTurnCompleted / AgentTurnFailed.model_usage・model_usage_gap(手番の
;;;                                      model ごとの使用量 — 層 2 が CLI の会話の累積の modelUsage を額と同じ起点の差で手番の分に直した値と、
;;;                                      model ごとの額の和を手番の額と照合できなかった訳。型は層 2 の ModelUsage・ModelUsageGap のまま・
;;;                                      card ki-1d986d4f0007)
;;;   Completed.cost-usd / Failed.cost-usd → AgentTurnUsage.cost_usd(手番の額 USD — 層 2 が CLI の累積の額から手番の分に直した値。
;;;                                      分からなければ None。token の 4 欄と額がすべて無ければ usage = None・agora-redesign #883)
;;;   WarmSessionEffect                → ClaudeWarmSession(次のターンと同じ始まり方 — 1 度もターンを始めていない新しい文脈は同じ id の
;;;                                      FreshSession・ほかは ResumeSession。ターンが動いている・入力を待たせていれば事前起動しない)
;;;   StopEffect / StopSessionEffect / ReleaseSessionEffect → ClaudeCloseSession(待たせた入力は discarded の運命で閉じる・事前起動
;;;                                      しただけの runtime も停止する)
;;;   CaptureEffect / AttachAgentSessionEffect → 画面が無いので AgentCapabilityUnsupportedError
;;; この handler が起こしていない session の effect と、CLAUDE 以外の LaunchEffect は外側の handler へ回す。
(require doeff-hy.macros [defhandler defk <- val var])
(import collections.abc [Callable])
(import dataclasses [dataclass field replace])
(import datetime [datetime])
(import uuid)
(import doeff [run])
(import doeff_hy.frozen [FrozenMap frozen-json-object])
(import doeff_time [GetMonotonic GetTime])
(import doeff_agents.adapters.base [AgentType AgentSessionLifecycle])
(import doeff_agents.monitor [SessionStatus])
(import doeff_agents.shell [assert-no-forbidden-agent-env assert-session-env-is-non-auth-overlay])
(import doeff_agents.effects.agent [
  LaunchEffect SendEffect FollowUpEffect InterruptEffect EventsEffect AwaitResultEffect MonitorEffect CaptureEffect
  StopEffect StopSessionEffect ReleaseSessionEffect AttachAgentSessionEffect ExportContextEffect WarmSessionEffect
  SessionHandle Observation AwaitOutcome AwaitStatus TurnInputMode InputFateState
  AgentEventPage AgentTextEvent AgentTextDeltaEvent AgentThinkingStartedEvent AgentThinkingDeltaEvent AgentToolCallStartedEvent AgentToolInputDeltaEvent
  AgentToolUseEvent AgentToolResultEvent AgentInputFateEvent AgentStopHookFeedbackEvent AgentCompactionStartedEvent AgentCompactionEvent CompactionTrigger
  AgentCompactionEndedEvent CompactionOutcome AgentHookEvent AgentCallUsageEvent AgentSubagentCallUsageEvent
  AgentTurnEndEvent AgentTurnCompleted AgentTurnFailed AgentTurnInterrupted AgentTurnLost AgentTurnUsage AgentAccountLimit
  AgentAccountRefusal AgentModelRefusal AgentProviderCapacity
  AgentError AgentLaunchError AgentCapabilityUnsupportedError NoTurnInFlightError ResumeTargetNotFoundError
  SessionAlreadyExistsError SessionNotFoundError TurnInFlightError
  RedeemTurnCredentialEffect TurnCredential HomeTurnCredential TurnCredentialUnavailable TurnCredentialUnavailableError
  HandlerMadeContextId NamedContextId
  ReplaceSessionCredentialEffect SessionCredentialReplaced SessionProcessNotLive SessionOnHomeCredential
  ReplacementCredentialUnavailable])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec ClaudeTurn TurnInput FreshSession ResumeSession Rebuilt
                                  BypassAll PermissionPolicy checked-session-id ImageAttachment LentCredential])
(import doeff_claude_code.lines [AssistantMessage PartialMessage ToolResult InputFate DeltaKind StopHookFeedback CompactStarted CompactEnded CompactBoundary
                                 HookNotice HookPhase SubagentTranscript SubagentCall CallClosed
                                 Completed Failed Interrupted BackendLost Usage AccountRefusalHit ModelRefusalHit stderr-tail-within])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeInjectInput ClaudeInterruptTurn ClaudeReadTurnEvents
                                   ClaudeCloseSession ClaudeSessionStatus ClaudeExportSession ClaudeWarmSession
                                   TurnStarted InterruptRequested InputAlreadyRead TurnEventPage SessionExported SessionWarmed
                                   SessionNotFound SessionIdInUse TurnInFlight CarryRefused LaunchFailed AttachmentRefused
                                   NoTurnInFlight UnknownTurn ProcessStillAlive TranscriptAbsent
                                   ClaudeReplaceSessionCredential CredentialReplaced NoSuchSession SessionProcessDown
                                   HomeCredentialSession])

(setv HANDLER-NAME "headless-claude-handler")

;; 引き換えた GitHub の token を置く env の名(#3753)。綴りの家は境界の env の語彙 doeff_agents/agent_env.hy の 1 点(#708 — 家は session host
;; を import しない)。claude の access token は env に置かない — 層 2 の宣言の借りた資格(LentCredential)で渡す(card
;; acp:kanban-issue:ki-4ffdafa021e5)。
(import doeff_agents.agent_env [GITHUB-TOKEN-ENV])


;; --- 設定と状態 ---------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] HeadlessClaudeConfig []
  "composition root が渡す宣言: home = claude の家(資格は root が custody から借りて env に置く — この handler は読むだけ)/
   settings = CLI の settings に合流する宣言(JSON — 深く凍らせた写像)/ cold-resume-prompt = 冷えた続きの前に 1 回だけ走らせる命令(None = 走らせない)/
   permission = 層 2 の会話の宣言へ渡す許可の方策(既定 BypassAll・HomeSettings = 設定 dir の settings.json の permissions に任せる — #3753)/
   page-wait = 層 2 へ 1 回に待つ秒数の上限(長い待ちはこの刻みで読む)。"
  (#^ ClaudeHome home)
  (setv #^ FrozenMap settings (field :default-factory FrozenMap))
  (setv #^ (| str None) cold-resume-prompt None)
  (setv #^ PermissionPolicy permission (BypassAll))
  (setv #^ float page-wait 5.0)
  (defn __post_init__ [self]
    (object.__setattr__ self "settings" (frozen-json-object self.settings "HeadlessClaudeConfig.settings"))))

(defclass HeadlessSession []
  "1 つの session(handle)の agent の寿命の状態。process の状態は持たない。
   context-id = 続きに使う agent runtime の文脈の id(claude の会話の id)/ fresh = 次の手番を新しい文脈として始めるか /
   turn = 走っている層 2 の手番(無ければ None)/ cursor = その手番の読んだ所 / waiting = 走っている手番の後に回す入力 /
   events = 層 3 の出来事(seq = 添字)/ last-end = 最後の手番の終わり / stopped = 止めた /
   carry = 次に始める手番で文脈へ持ち込む写し(LaunchEffect.resume_snapshot — 最初の手番を始めたら None)。"
  (defn __init__ [self #^ str name #^ ClaudeSessionSpec spec #^ str context-id #^ bool fresh lifecycle
                  #^ (| Rebuilt None) [carry None]]
    (setv self.name name
          self.spec spec
          self.context-id context-id
          self.fresh fresh
          self.lifecycle lifecycle
          self.carry carry
          self.stopped False)
    (setv #^ (| ClaudeTurn None) self.turn None)
    (setv #^ int self.cursor -1)
    (setv #^ (get list TurnInput) self.waiting [])
    (setv #^ (get list (| AgentTextEvent AgentTextDeltaEvent AgentThinkingStartedEvent AgentThinkingDeltaEvent AgentToolUseEvent AgentToolResultEvent
                          AgentInputFateEvent AgentStopHookFeedbackEvent AgentCompactionStartedEvent AgentCompactionEvent
                          AgentCompactionEndedEvent AgentHookEvent AgentCallUsageEvent AgentSubagentCallUsageEvent AgentTurnEndEvent))
          self.events [])
    (setv #^ (| AgentTurnCompleted AgentTurnFailed AgentTurnInterrupted AgentTurnLost None) self.last-end None)))

(defclass HeadlessState []
  "handler の状態: session の名 → HeadlessSession(composition root が 1 つ作って渡す)。"
  (defn __init__ [self]
    (setv self.sessions {})))

(defk superseded-names [#^ HeadlessState state #^ str context-id #^ str name]
  {:pre [(: state HeadlessState) (: context-id str) (: name str)] :post [(: % tuple)] :tags {:context "headless-adapter" :role "judgment"}}
  "同じ文脈 context-id で session name を起こした時に表から外す、手の空いた前の session の名(止めた・手番も待たせた入力も無い)。
   使い手が会話の CLI を手番をまたいで生かし、手番ごとに新しい名の session を前の手番の resume_from で起こすと(層 2 は同じ process を
   使い回す)、前の session は出来事を全部持ったまま止めも片づけも来ずに残り続ける — 片づけは共有の CLI を降ろすので来ない
   (zeus の turn-host の本体が 1 時間に約 1.6 GB 伸びた・lt-KATGT933TATG1ESPPSW9QGFM7K)。外した session は片づけた session と
   同じく、以後の effect をこの handler が答えずに外へ回す。手番の走っている session は外さない。"
  (tuple (gfor #(other session) (.items state.sessions)
               :if (and (!= other name) (= session.context-id context-id)
                        (or session.stopped (and (is session.turn None) (not session.waiting))))
               other)))


;; --- 純粋な写し ---------------------------------------------------------------------------------

(defn #^ str new-ref [] (str (uuid.uuid4)))

(defk lent-of [#^ TurnCredential credential]
  {:pre [(: credential TurnCredential)] :post [(: % LentCredential)] :tags {:context "headless-adapter" :role "judgment"}}
  "引き換えた手番の資格 → 層 2 の借りた資格(access token・期限・契約の種類と階級をそのまま写す — GitHub の token は写さない)。"
  (LentCredential :access-token credential.oauth-token :expires-at credential.expires-at
                  :subscription-type credential.subscription-type :rate-limit-tier credential.rate-limit-tier))

(defn #^ ClaudeSessionSpec spec-of [#^ HeadlessClaudeConfig config #^ LaunchEffect effect #^ (| TurnCredential None) [credential None]]
  "LaunchEffect → 層 2 の会話の宣言。process の env = 家の env + session_env(非 auth の上書き)。引き換えた資格(credential —
   LaunchEffect.turn_credential_ref を RedeemTurnCredentialEffect で引き換えた答え・None = 家の資格)は宣言の借りた資格(lent-of)にし、
   env には置かない — 層 2 がその process だけの設定 dir の .credentials.json に置き、走っている間も替えられる(card
   acp:kanban-issue:ki-4ffdafa021e5)。資格に付いた契約の種類と階級(card acp:kanban-issue:ki-d81bb8c7eaaa)も同じ file に載る。設定 dir の
   env と session_env は資格の env を持てない — 資格の入口は引き換えの答え 1 つ。答えが GitHub の token(credential.github-token)も
   持てば GITHUB-TOKEN-ENV(GH_TOKEN)に置く(#3753)。借りた資格の期限は層 2 が床で生きた process を止める刻に使う(#3672 の D2)。
   許可の方策は config の permission。"
  (assert-session-env-is-non-auth-overlay effect.session-env :context "LaunchEffect.session_env (headless-claude-handler)")
  (setv env (| (dict config.home.env) (dict (or effect.session-env {}))))
  (assert-no-forbidden-agent-env env :context "headless-claude-handler の process の env")
  (when (and (is-not credential None) (is-not credential.github-token None))
    (setv (get env GITHUB-TOKEN-ENV) credential.github-token))
  (ClaudeSessionSpec :home (ClaudeHome config.home.config-dir env)
                     :cwd (str effect.work-dir)
                     :model effect.model
                     :effort effect.effort
                     :autocompact effect.autocompact
                     :settings config.settings
                     :permission config.permission
                     :cold-resume-prompt config.cold-resume-prompt
                     :credential (if (is credential None) None (run (lent-of credential)))))

(defk call-usage-builders-of [message at]
  {:pre [(: message AssistantMessage) (: at datetime)] :post [(: % list)]
   :tags {:context "headless-adapter" :role "foundation"}}
  "上の層が手番の終わりを待たずに会話の今の context の大きさを読めるように、assistant の行から、その呼びの usage の出来事を作る関数の列
   (0 か 1 つ)を作るため。本体の会話の行(parent-tool-use-id が None)が usage を名乗る時だけ 1 つ作る。線引きは手番の終わりの
   last_call_usage(層 2 の状態機械 dialogue.hy の on-assistant)と同じ — subagent の行は数えず、usage を名乗らない行・4 欄とも
   名乗らない行は出来事にしない(0 を発明しない)。"
  (val usage (if (is message.parent-tool-use-id None) (last-call-of message.usage) None))
  (if (is usage None)
      []
      [(fn [seq] (AgentCallUsageEvent :seq seq :at at :usage usage :model message.model :call-id message.message-id :final False
                                      :request-id message.request-id))]))

(defk closed-call-builders-of [closed at]
  {:pre [(: closed CallClosed) (: at datetime)] :post [(: % list)]
   :tags {:context "headless-adapter" :role "foundation"}}
  "上の層が本体の会話の呼びごとの使用量を最終の値で記録できるように(agora card ki-290ec664737d — assistant の行の usage の出力は呼びの
   途中の値)、層 2 が呼びの始まりと終わりから組んだ最終の usage(CallClosed)から、その呼びの最終の usage の出来事を作る関数の列(0 か
   1 つ)を作るため。本体の会話の呼び(parent-tool-use-id が None)だけ — subagent の呼びは transcript から読む
   (subagent-call-usage-builders-of)。4 欄とも名乗らない呼びは出来事にしない(0 を発明しない)。CLI が止めた呼び(closed.stopped が偽 —
   止まった訳を名乗らない終わり・agora card ki-7498a039250d)は final 偽で運ぶ(出力は途中の値・CLI の額はこの呼びを数えない)。"
  (val usage (if (is closed.parent-tool-use-id None) (last-call-of closed.usage) None))
  (if (is usage None)
      []
      [(fn [seq] (AgentCallUsageEvent :seq seq :at at :usage usage :model closed.model :call-id closed.message-id
                                      :final closed.stopped))]))

(defk subagent-call-builder [#^ str parent-tool-use-id #^ SubagentCall call #^ AgentTurnUsage usage]
  {:pre [(: parent-tool-use-id str) (: call SubagentCall) (: usage AgentTurnUsage)] :post [(: % Callable)]
   :tags {:context "headless-adapter" :role "foundation"}}
  "subagent の呼び 1 回の usage の出来事を、seq を受けて作る関数にするため(subagent-call-usage-builders-of が呼びごとに 1 つ作る —
   関数は自分の呼びの値だけを持つ)。刻は呼びの刻(transcript の最初の行の物)。"
  (fn [seq] (AgentSubagentCallUsageEvent :seq seq :at call.at :parent-tool-use-id parent-tool-use-id :usage usage
                                         :model call.model :call-id call.call-id :request-id call.request-id)))

(defk subagent-call-usage-builders-of [transcript]
  {:pre [(: transcript SubagentTranscript)] :post [(: % list)]
   :tags {:context "headless-adapter" :role "foundation"}}
  "上の層が subagent の消費を本体の会話の呼びと分けて記録できるように(card acp:kanban-issue:ki-8239be27d5f6 — rate limit の減りと
   実使用量の照合の材料)、層 2 が subagent の transcript から読んだ呼び(SubagentTranscript の calls — card ki-e798888dce4e。背景の task を
   止めた CLI は subagent の呼びの行を stdout に出さないので、subagent の呼びはここからだけ運ぶ)ごとに、その呼びの usage の出来事を作る
   関数の列を作るため。親の道具の呼びの id・呼びの id(message.id)・model・呼びの刻を添える。usage の 4 欄とも名乗らない呼びは
   出来事にしない(0 を発明しない)。読みに欠けの在る transcript も、読めた呼び(calls)は出来事にする(欠けの訳は層 2 が log に残す —
   card ki-3d45f5857239)。"
  (var builders [])
  (for [call transcript.calls]
    (val usage (last-call-of call.usage))
    (when (is-not usage None)
      (<- build (subagent-call-builder transcript.parent-tool-use-id call usage))
      (:= builders (+ builders [build]))))
  builders)

(defn #^ list event-builders-of [kind #^ datetime at]
  "層 2 の行の型 → 層 3 の出来事を作る関数(seq → 出来事)の列。出来事はその型の欄で直接作る(語彙の外の行は空)。"
  (cond
    (isinstance kind AssistantMessage)
      (+ (if kind.text [(fn [seq] (AgentTextEvent :seq seq :at at :text kind.text))] [])
         (if kind.tool-calls [(fn [seq] (AgentToolUseEvent :seq seq :at at :tool-calls kind.tool-calls
                                                              :parent-tool-use-id kind.parent-tool-use-id))] [])
         (run (call-usage-builders-of kind at)))
    ;; 層 2 が呼びの始まりと終わりから組んだ呼びの最終の usage(agora card ki-290ec664737d)。
    (isinstance kind CallClosed)
      (run (closed-call-builders-of kind at))
    ;; 層 2 が subagent の transcript から読んだ呼び — 呼びごとの usage の出来事(card ki-e798888dce4e)。
    (isinstance kind SubagentTranscript)
      (run (subagent-call-usage-builders-of kind))
    (and (isinstance kind PartialMessage) kind.text-delta)
      [(fn [seq] (AgentTextDeltaEvent :seq seq :at at :text kind.text-delta))]
    ;; 考えの block の始まり — 考えの最初の差分より先に、上の層が「考えている」と分かる合図(agora-redesign #4186)。
    (and (isinstance kind PartialMessage) kind.thinking-start)
      [(fn [seq] (AgentThinkingStartedEvent :seq seq :at at))]
    ;; 考えている間の差分は、中身が空でも片ごとに 1 つ出す — 本文の前に「考えている」と分かる合図(agora-redesign #3789)。
    (and (isinstance kind PartialMessage) (= kind.delta DeltaKind.THINKING))
      [(fn [seq] (AgentThinkingDeltaEvent :seq seq :at at :text kind.thinking-delta))]
    ;; 道具の呼びの始まりと、命令を書いている間の差分(片ごと — 中身が空でも出す)。上の層が「担当は <道具> の命令を書いている」と
    ;; 出すため(agora-redesign #3974 の 3)。
    (and (isinstance kind PartialMessage) (is-not kind.tool-start None))
      [(fn [seq] (AgentToolCallStartedEvent :seq seq :at at :id kind.tool-start.id :name kind.tool-start.name))]
    (and (isinstance kind PartialMessage) (= kind.delta DeltaKind.TOOL-INPUT))
      [(fn [seq] (AgentToolInputDeltaEvent :seq seq :at at :text kind.tool-input-delta))]
    (isinstance kind ToolResult)
      [(fn [seq] (AgentToolResultEvent :seq seq :at at :answers kind.answers :parent-tool-use-id kind.parent-tool-use-id))]
    ;; Stop hook が答えを差し戻した(理由つき)— 上の層が差し戻された答えの本文を見分けて画面から落とすため(agora-redesign #4020)。
    (isinstance kind StopHookFeedback)
      [(fn [seq] (AgentStopHookFeedbackEvent :seq seq :at at :reason kind.reason))]
    ;; CLI が会話の圧縮を始めた(status 行 compacting — 刻だけ)— 上の層が圧縮の間 会話の画面に圧縮中を出すため(agora card
    ;; ki-aeae6f9e643d。終わりは下の CompactBoundary)。
    (isinstance kind CompactStarted)
      [(fn [seq] (AgentCompactionStartedEvent :seq seq :at at))]
    ;; CLI が会話の圧縮を終えた(status 行 null — 終わり方は同じ綴りの層 3 の語へ写し、語彙の外は ValueError で止める)— 上の層が成功だけでなく失敗・中断・圧縮
    ;; しなかった事も手番の出来事として記録するため(card ki-8239be27d5f6。成功の token 数は下の CompactBoundary)。
    (isinstance kind CompactEnded)
      [(fn [seq] (AgentCompactionEndedEvent :seq seq :at at :outcome (CompactionOutcome kind.outcome.value)
                                            :error kind.error))]
    ;; CLI が会話を圧縮した(行の欄そのまま — 起き方は同じ綴りの層 3 の語へ写し、語彙の外は ValueError で止める)— 上の層が無人の
    ;; ターンの圧縮をターンの出来事として記録するため(agora-redesign #4189)。
    (isinstance kind CompactBoundary)
      [(fn [seq] (AgentCompactionEvent :seq seq :at at :trigger (CompactionTrigger kind.trigger.value) :pre-tokens kind.pre-tokens
                                       :post-tokens kind.post-tokens :cumulative-dropped-tokens kind.cumulative-dropped-tokens
                                       :duration-ms kind.duration-ms))]
    ;; CLI が回した hook が応答した(行の欄そのまま — 開始の行は出来事にしない)— 上の層が hook の結果(条件つきルールの助言・Stop の
    ;; 差し戻しほか)を会話の画面に出すため(card acp:kanban-issue:ki-d8b473480303)。
    (and (isinstance kind HookNotice) (= kind.phase HookPhase.RESPONSE))
      [(fn [seq] (AgentHookEvent :seq seq :at at :hook-event kind.event :name kind.name :hook-id kind.hook-id :stdout kind.stdout
                                 :stderr kind.stderr :output kind.output :exit-code kind.exit-code :outcome kind.outcome))]
    (isinstance kind InputFate)
      [(fn [seq] (AgentInputFateEvent :seq seq :at at :input-ref kind.ref :state (InputFateState kind.state)))]
    True []))

(defn #^ list events-of [line #^ int first-seq]
  "層 2 の 1 行 → 層 3 の出来事の列(語彙の外の行は空 — 生の行は上へ渡さない)。seq は first-seq から続けて振る。"
  (lfor #(index build) (enumerate (event-builders-of line.kind line.at))
        (build (+ first-seq index))))

(defn #^ (| AgentTurnUsage None) usage-of [#^ Usage usage #^ (| float None) cost-usd]
  "層 2 の手番の usage と額 → 層 3 の AgentTurnUsage。手番の消費と額を記録へ運ぶための写しで、cache の書きの寿命の内訳(5 分・1 時間 —
   寿命で単価が違う・card ki-75d813bebc27)も名乗られたまま運ぶ。token の欄と額がすべて名乗られなければ None(0 を発明しない)。"
  (setv turn-usage (AgentTurnUsage :input-tokens usage.input-tokens
                                   :output-tokens usage.output-tokens
                                   :cache-write-tokens usage.cache-creation-input-tokens
                                   :cache-write-5m-tokens usage.cache-creation-5m-input-tokens
                                   :cache-write-1h-tokens usage.cache-creation-1h-input-tokens
                                   :cache-read-tokens usage.cache-read-input-tokens
                                   :cost-usd cost-usd))
  (if (= turn-usage (AgentTurnUsage)) None turn-usage))

(defn #^ (| AgentTurnUsage None) last-call-of [#^ (| Usage None) usage]
  "層 2 の手番の最後の呼びの usage → 層 3 の AgentTurnUsage(会話の今の context の大きさを記録へ運ぶため — #3744)。呼びごとの額は
   CLI が名乗らないので None。usage が無い・欄がどれも名乗られなければ None。"
  (if (is usage None) None (usage-of usage None)))

(defn #^ (| AgentAccountLimit None) account-limit-of [hit]
  "層 2 の口座の限度に当たった事実(AccountLimitHit か None)→ 層 3 の AgentAccountLimit(#3983 — 欄はそのまま写す)。"
  (if (is hit None) None (AgentAccountLimit :window hit.window :resets-at hit.resets-at :text hit.text)))

(defk account-refusal-of [hit]
  {:pre [(: hit (| AccountRefusalHit None))] :post [(: % (| AgentAccountRefusal None))]
   :tags {:context "headless-adapter" :role "foundation"}}
  "上の層が「この口座は使えない」と知って口座を替えられるように、層 2 の口座の側が断った事実(AccountRefusalHit か None)を層 3 の
   AgentAccountRefusal へ写すため(欄はそのまま写す)。"
  (if (is hit None) None (AgentAccountRefusal :error hit.error :text hit.text)))

(defk model-refusal-of [hit]
  {:pre [(: hit (| ModelRefusalHit None))] :post [(: % (| AgentModelRefusal None))]
   :tags {:context "headless-adapter" :role "foundation"}}
  "上の層が「この transcript はこの model で続けられない」と知って続け方を決められるように、層 2 の model の安全装置が断った事実
   (ModelRefusalHit か None)を層 3 の AgentModelRefusal へ写すため(欄はそのまま写す)。"
  (if (is hit None) None (AgentModelRefusal :category hit.category :model hit.model :text hit.text)))

;; API の誤りの HTTP status のうち、API の提供側が混んで要求を断った事を名乗る status(Anthropic の API の overloaded_error — CLI は
;; 自分の試し直しを使い切った後に「API Error: 529 Overloaded. This is a server-side issue, usually temporary — try again in a moment.」の
;; 文と result の行の api_error_status 529 で手番を失敗で終える・実測 CLI 2.1.292 の code・本番 2026-10-24 09:26〜09:30 JST に 8 本)。
(val API-STATUS-OVERLOADED 529)

(defk provider-capacity-of [end model]
  {:pre [(: end Failed) (: model (| str None))] :post [(: % (| AgentProviderCapacity None))]
   :tags {:context "headless-adapter" :role "judgment"}}
  "純粋: 上の層が少し後に同じ会話で試し直せるように、CLI が API の混雑(api-error-status が API-STATUS-OVERLOADED)で終えた失敗を
   AgentProviderCapacity にするため(混雑でない失敗は None)。文は CLI の失敗の文そのまま、model は会話の宣言の model(None なら None)。
   口座の限度・口座の断り・model の安全装置の断りの失敗は、混雑より先にそれぞれの事実で運ぶので混雑と言わない。"
  (if (and (= end.api-error-status API-STATUS-OVERLOADED)
           (is end.account-limit None) (is end.account-refusal None) (is end.model-refusal None))
      (AgentProviderCapacity :model model :text end.detail)
      None))

(defn end-of [end #^ str context-id #^ (| str None) [model None]]
  "層 2 の手番の終わり → 層 3 の手番の終わり(続きの身元 resume-from を載せる)。どの終わりも本体の最後の呼びの usage と model・
   model ごとの窓を運ぶ(#3744)。model = 会話の宣言の model(API の混雑の失敗の事実が名乗る — provider-capacity-of)。"
  (when (not (isinstance end #(Completed Failed Interrupted BackendLost)))
    (raise (TypeError (.format "層 2 の手番の終わりが閉語彙の外: {!r}" end))))
  (setv last-call-usage (last-call-of end.last-call-usage)
        last-call-model end.last-call-model
        model-windows end.model-windows)
  (cond
    (isinstance end Completed)
      (AgentTurnCompleted :result-text end.result-text :input-refs end.input-refs :resume-from context-id
                          :usage (usage-of end.usage end.cost-usd)
                          :last-call-usage last-call-usage :last-call-model last-call-model :model-windows model-windows
                          :model-usage end.model-usage :model-usage-gap end.model-usage-gap
                          :account-limit (account-limit-of end.account-limit)
                          :account-refusal (run (account-refusal-of end.account-refusal))
                          :model-refusal (run (model-refusal-of end.model-refusal)))
    (isinstance end Failed)
      (AgentTurnFailed :detail end.detail :input-refs end.input-refs :resume-from context-id
                       :usage (usage-of end.usage end.cost-usd)
                       :last-call-usage last-call-usage :last-call-model last-call-model :model-windows model-windows
                       :model-usage end.model-usage :model-usage-gap end.model-usage-gap
                       :account-limit (account-limit-of end.account-limit)
                       :account-refusal (run (account-refusal-of end.account-refusal))
                       :model-refusal (run (model-refusal-of end.model-refusal))
                       :provider-capacity (run (provider-capacity-of end model))
                       :exit-code end.exit-code :stderr-tail end.stderr-tail)
    (isinstance end Interrupted)
      ;; 止めた手番を閉じた result の行が在れば、止めるまでの消費も運ぶ(層 2 が持たない止めは None のまま — card ki-47e64b7b642f)。
      (AgentTurnInterrupted :cli-kept end.process-kept :surviving-refs end.surviving-refs :dropped-refs end.dropped-refs
                            :resume-from context-id
                            :usage (if (is end.usage None) None (usage-of end.usage end.cost-usd))
                            :last-call-usage last-call-usage :last-call-model last-call-model :model-windows model-windows
                            :model-usage end.model-usage :model-usage-gap end.model-usage-gap)
    (isinstance end BackendLost)
      (AgentTurnLost :detail end.detail :resume-from context-id
                     :last-call-usage last-call-usage :last-call-model last-call-model :model-windows model-windows
                     :exit-code end.exit-code :stderr-tail end.stderr-tail)))

(defn #^ str detail-of [end]
  (cond
    (isinstance end AgentTurnFailed) end.detail
    (isinstance end AgentTurnLost) end.detail
    (isinstance end AgentTurnInterrupted) "interrupted"
    True ""))

(defn #^ AwaitOutcome outcome-of [end lifecycle #^ bool stopped]
  "手番の終わり → AwaitOutcome(L2 の見え方)。exit-code は 0 = 完了・1 = それ以外(headless の層 3 に process の終了コードは無い)。"
  (setv status (if (or stopped (= lifecycle AgentSessionLifecycle.RUN-TO-COMPLETION))
                   AwaitStatus.EXITED
                   AwaitStatus.AWAITING-INPUT))
  (if (isinstance end AgentTurnCompleted)
      (AwaitOutcome status :result end.result-text :exit-code 0 :continuable (not stopped) :turn-end end)
      (AwaitOutcome status :validation-error (detail-of end) :exit-code 1 :continuable (not stopped) :turn-end end)))

(defn status-of [#^ HeadlessSession session]
  "session の今の状態 → SessionStatus(pid は読まない — 手番が走っているか・最後の終わりだけ)。"
  (setv end session.last-end)
  (cond
    session.stopped SessionStatus.STOPPED
    (or (is-not session.turn None) session.waiting) SessionStatus.RUNNING
    (isinstance end #(AgentTurnFailed AgentTurnLost)) SessionStatus.FAILED
    (and (isinstance end AgentTurnCompleted) (= session.lifecycle AgentSessionLifecycle.RUN-TO-COMPLETION)) SessionStatus.DONE
    True SessionStatus.BLOCKED))

(defn #^ AgentEventPage page-of [#^ HeadlessSession session #^ int after-seq]
  "after-seq より後の出来事。end は手番が走っておらず待たせた入力も無い時だけ最後の終わり。"
  (setv fresh (tuple (cut session.events (max 0 (+ after-seq 1)) None)))
  (setv idle (and (is session.turn None) (not session.waiting)))
  (AgentEventPage :events fresh
                  :next-seq (if fresh (. (get fresh -1) seq) after-seq)
                  :end (if idle session.last-end None)))

(defn start-refusal [outcome #^ HeadlessSession session]
  "ClaudeStartTurn の断り → 層 3 の例外(型で名乗る)。process が init の行の前に降りた断り(LaunchFailed)は、終了 code と stderr の
   末尾(層 2 の手番の終わりと同じ上限の内 — stderr-tail-within)を AgentLaunchError の欄で運ぶ(#4207 — 文の中だけでなく)。"
  (cond
    (isinstance outcome SessionNotFound) (ResumeTargetNotFoundError :resume-from outcome.session-id)
    (isinstance outcome SessionIdInUse)
      (SessionAlreadyExistsError (.format "agent runtime の文脈 {} は既に在る(session {})" outcome.session-id session.name))
    (isinstance outcome LaunchFailed)
      (AgentLaunchError (.format "session {} の手番を起こせない(exit {}): {}" session.name outcome.exit-code outcome.stderr-tail)
                        :exit-code outcome.exit-code :stderr-tail (run (stderr-tail-within outcome.stderr-tail)))
    (isinstance outcome CarryRefused) (AgentLaunchError (.format "session {}: {}" session.name outcome.detail))
    (isinstance outcome AttachmentRefused) (AgentLaunchError (.format "session {}: 受けない添付 {}" session.name outcome.mime))
    ;; 文脈で別の手番が走っている(この handler の知らない手番 — 別の session が同じ文脈を続けている)。起動の失敗と分けて型で名乗る
    ;; (agora は session-busy と読む・agora-redesign #789)。
    (isinstance outcome TurnInFlight)
      (TurnInFlightError :session-id session.name :context-id session.context-id)
    True (AgentError (.format "session {}: 層 2 の答えが閉語彙の外: {!r}" session.name outcome))))


;; --- 層 2 との往復 --------------------------------------------------------------------------------

(defk next-origin [#^ HeadlessSession session]
  {:pre [(: session HeadlessSession)] :post [(: % (| FreshSession ResumeSession))]
   :tags {:context "headless-adapter" :role "judgment"}}
  "session の次のターン(と、その前の事前起動)の文脈の始まり方を 1 か所で決めるため: 1 度もターンを始めていない新しい文脈は
   FreshSession(事前起動した後の最初のターンも同じ id の新しい文脈)、ほかは ResumeSession(持ち込むコピーが在れば添える)。"
  (if session.fresh
      (FreshSession session.context-id)
      (ResumeSession session.context-id :carry session.carry)))

(defk start-turn [#^ HeadlessSession session #^ TurnInput input]
  {:pre [(: session HeadlessSession) (: input TurnInput)] :post [(: % ClaudeTurn)]}
  "次のターンを始める。始まり方は next-origin(終了した process を再起動するか・事前起動した process を使うかは層 2 の判断 — ここは
   続けるとだけ言う)。持ち込むコピー(carry)が在れば、始められたターンで使い切る(層 2 は既に在る transcript を上書きしない)。拒否は
   例外で示す。"
  (<- origin (next-origin session))
  (<- outcome (ClaudeStartTurn origin session.spec input))
  (when (not (isinstance outcome TurnStarted))
    (raise (start-refusal outcome session)))
  (setv session.fresh False session.turn outcome.turn session.cursor -1 session.carry None)
  outcome.turn)

(defn #^ Callable turn-end-builder [#^ AgentTurnFailed end]
  "手番の終わりの出来事を作る関数((seq at) → AgentTurnEndEvent)。"
  (fn [seq at] (AgentTurnEndEvent :seq seq :at at :end end)))

(defk append-event [#^ HeadlessSession session #^ Callable build]
  {:pre [(: session HeadlessSession) (: build Callable)] :post [(: % (type None))]}
  "出来事を 1 つ置く。build = (seq at) → 出来事(呼び手がその型の欄で直接作る)。"
  (<- at (GetTime))
  (.append session.events (build (len session.events) at))
  None)

(defk start-failure [#^ AgentError error #^ str input-ref #^ str context-id]
  {:pre [(: error AgentError) (: input-ref str) (: context-id str)] :post [(: % AgentTurnFailed)]
   :tags {:context "headless-adapter" :role "foundation"}}
  "待たせた入力の手番を始められなかった事を、上の層が読む失敗の終わり(AgentTurnFailed)にするため。起動の失敗(AgentLaunchError)は、
   手番を始める前に降りた process の終了 code と stderr の末尾も欄で運ぶ(#4207 — 文 detail の中だけでなく)。"
  (match error
    (AgentLaunchError)
      (AgentTurnFailed :detail (str error) :input-refs #(input-ref) :resume-from context-id
                       :exit-code error.exit-code :stderr-tail error.stderr-tail)
    _ (AgentTurnFailed :detail (str error) :input-refs #(input-ref) :resume-from context-id)))

(defk start-waiting [#^ HeadlessSession session]
  {:pre [(: session HeadlessSession)] :post [(: % (type None))]}
  "待たせた入力の手番を順に始める。始められなかった入力は失敗の終わり(AgentTurnFailed — start-failure)として出来事に置き、次へ進む。"
  (while (and session.waiting (is session.turn None) (not session.stopped))
    (val input (.pop session.waiting 0))
    (var failure None)
    (try
      (<- (start-turn session input))
      (except [error AgentError]
        (:= failure (! (start-failure error input.ref session.context-id)))))
    (when (is-not failure None)
      (<- (append-event session (turn-end-builder failure)))
      (setv session.last-end failure)))
  None)

(defk close-turn [#^ HeadlessSession session turn end]
  {:pre [(: session HeadlessSession) (: turn ClaudeTurn) (: end (| Completed Failed Interrupted BackendLost))]
   :post [(: % (type None))]}
  "層 2 の手番 turn の終わりを出来事に置く。止めた手番の入力が生き残って次の層 2 の手番で続く時はその手番を追い、
   ほかは待たせた入力の手番を始める。時刻を読んで戻った後に、別の task が同じ手番を既に閉じていれば何もしない。"
  (<- at (GetTime))
  (when (!= session.turn turn) (return None))
  (setv mapped (end-of end session.context-id session.spec.model))
  (.append session.events (AgentTurnEndEvent :seq (len session.events) :at at :end mapped))
  (setv session.last-end mapped)
  (if (and (isinstance end Interrupted) (is-not end.continued-by None) (not session.stopped))
      (setv session.turn end.continued-by session.cursor -1)
      (do
        (setv session.turn None)
        (<- (start-waiting session))))
  None)

(defk pull [#^ HeadlessSession session #^ float wait]
  {:pre [(: session HeadlessSession) (: wait float)] :post [(: % (type None))]}
  "走っている手番の出来事を層 2 から 1 頁読み(wait 秒まで待つ)、層 3 の出来事へ写す。"
  (when (is session.turn None) (return None))
  ;; 読む手番と位置を控える: 待つ間に同じ session の別の task(割り込み・入力を届ける)が先に読み進めたり手番を閉じたり
  ;; できるので、戻った後は控えと照らし、既に写した行と既に閉じた手番を写し直さない(seq の重複と二重の終わりを作らない)。
  (setv turn session.turn cursor session.cursor)
  (<- page (ClaudeReadTurnEvents turn cursor wait))
  (when (!= session.turn turn) (return None))
  (when (isinstance page UnknownTurn)
    ;; 層 2 がこの手番を知らない(層 2 の handler が作り直された後など)= 終わりの行を読めずに失った。
    (setv page (TurnEventPage #() session.cursor (BackendLost "the agent runtime no longer knows this turn"))))
  (for [line page.lines :if (> line.seq session.cursor)]
    (.extend session.events (events-of line (len session.events)))
    (setv session.cursor line.seq))
  (when (is-not page.end None)
    (<- (close-turn session turn page.end)))
  None)

(defk read-events [#^ HeadlessClaudeConfig config #^ HeadlessSession session #^ int after-seq #^ float wait]
  {:pre [(: config HeadlessClaudeConfig) (: session HeadlessSession) (: after-seq int) (: wait float)]
   :post [(: % AgentEventPage)]}
  "after-seq より後の出来事を、新しい出来事が在るか・手番が無くなるか・wait 秒が過ぎるまで待って返す(層 2 は page-wait の刻みで読む)。"
  (<- started (GetMonotonic))
  (while True
    (setv have-new (> (len session.events) (+ after-seq 1)))
    (<- now (GetMonotonic))
    (setv left (- wait (- now started)))
    (when (and (not have-new) (is-not session.turn None))
      (<- (pull session (max 0.0 (min left config.page-wait))))
      (<- now (GetMonotonic))
      (setv left (- wait (- now started))
            have-new (> (len session.events) (+ after-seq 1))))
    (when (or have-new (is session.turn None) (<= left 0))
      (return (page-of session after-seq)))))

(defk await-turn-end [#^ HeadlessClaudeConfig config #^ HeadlessSession session timeout]
  {:pre [(: config HeadlessClaudeConfig) (: session HeadlessSession) (: timeout (| float int None))]
   :post [(: % AwaitOutcome)]}
  "走っている手番の終わりを待ち、その手番の結果を返す(待たせた入力の手番は続けて走る)。手番が無ければ最後の終わりを返す。"
  (setv mark (len session.events))
  (<- started (GetMonotonic))
  (while True
    (setv ends (lfor event (cut session.events mark None) :if (isinstance event AgentTurnEndEvent) event.end))
    (when ends
      (return (outcome-of (get ends 0) session.lifecycle session.stopped)))
    (when (is session.turn None)
      (return (if (is session.last-end None)
                  (AwaitOutcome AwaitStatus.AWAITING-INPUT :continuable (not session.stopped))
                  (outcome-of session.last-end session.lifecycle session.stopped))))
    (<- now (GetMonotonic))
    (setv elapsed (- now started))
    (when (and (is-not timeout None) (>= elapsed timeout))
      (return (AwaitOutcome AwaitStatus.TIMED-OUT)))
    (<- (pull session (if (is timeout None) config.page-wait (max 0.0 (min (- timeout elapsed) config.page-wait)))))))


;; --- 節の中身 -----------------------------------------------------------------------------------

(defk redeemed-credential [#^ LaunchEffect request]
  {:pre [(: request LaunchEffect)] :post [(: % (| TurnCredential None))]}
  "LaunchEffect.turn_credential_ref を手番の資格へ引き換える(issue #979)。参照が無い・答えが家の資格なら None(家の資格で起こす)。
   引き換えられなければ TurnCredentialUnavailableError で断る(session を起こさない)。答えは外側(参照を出した環境)が返す —
   token は LaunchEffect に載らない。"
  (val ref request.turn-credential-ref)
  (when (is ref None)
    (return None))
  (<- answer (RedeemTurnCredentialEffect :credential-ref ref))
  (match answer
    (TurnCredential) answer
    (HomeTurnCredential) None
    (TurnCredentialUnavailable :reason reason) (raise (TurnCredentialUnavailableError :credential-ref ref :reason reason))
    ;; 答えの型だけを名乗る(値は資格を運び得るので文に写さない)。
    _ (raise (AgentError (.format "手番の資格 {} の引き換えの答えが閉語彙の外: {}" ref (. (type answer) __name__))))))

(defk launch-context-id [#^ LaunchEffect request]
  {:pre [(: request LaunchEffect)] :post [(: % str)] :tags {:context "headless-adapter" :role "judgment"}}
  "session の文脈の id を 1 か所で決めるため: 前の文脈の続き(resume_from)はその id、新しい文脈は LaunchEffect.new_context_id の
   とおり — handler が作る(HandlerMadeContextId)か、呼び手の名指した id(NamedContextId)。同じ id を名指した 2 つの起動は同じ新しい
   文脈になるので、片方で事前起動した runtime を、もう片方の最初のターンが層 2 で使い回す。名指した id が層 2 の会話の id の綴り
   (UUID)でなければ、層 2 の値の例外を上へ漏らさず AgentLaunchError で拒否する。"
  (when (is-not request.resume-from None)
    (return request.resume-from))
  (val choice request.new-context-id)
  (match choice
    (HandlerMadeContextId) (new-ref)
    (NamedContextId)
      (try
        (checked-session-id choice.context-id "LaunchEffect.new_context_id")
        (except [error #(ValueError TypeError)]
          (raise (AgentLaunchError (.format "session {} を起動できない: {}" request.session-name error)))))
    _ (raise (AgentError (.format "LaunchEffect.new_context_id が閉じた型の外: {}" (. (type choice) __name__))))))

(defk launch [#^ HeadlessClaudeConfig config #^ HeadlessState state #^ LaunchEffect request]
  {:pre [(: config HeadlessClaudeConfig) (: state HeadlessState) (: request LaunchEffect)] :post [(: % SessionHandle)]}
  "session を起こす。prompt が在れば最初の手番を始める。resume_from は前の文脈の続き(手元に無ければ ResumeTargetNotFoundError)。
   resume_snapshot が在れば、最初の手番の前にその写しを文脈として持ち込む(手元に無くても続けられる)。"
  (setv name request.session-name)
  (when (in name state.sessions)
    (raise (SessionAlreadyExistsError (.format "Session {} already exists" name))))
  (when request.mcp-tools
    (raise (AgentCapabilityUnsupportedError :capability "LaunchEffect.mcp_tools" :handler HANDLER-NAME)))
  (when request.bare
    (raise (AgentCapabilityUnsupportedError :capability "LaunchEffect.bare" :handler HANDLER-NAME)))
  ;; 続きの身元は層 2 の会話の id の綴り(UUID)。外れは層 2 の値の例外を上へ漏らさず、型で断る。
  (when (is-not request.resume-from None)
    (try
      (checked-session-id request.resume-from "LaunchEffect.resume_from")
      (except [#(ValueError TypeError)]
        (raise (ResumeTargetNotFoundError :resume-from (str request.resume-from))))))
  (<- context-id (launch-context-id request))
  ;; 手番の資格は起こす直前に引き換える(断りの検めを通った起動だけが資格を受ける)。
  (<- credential (redeemed-credential request))
  (setv spec (spec-of config request credential))
  (val carry (if (is request.resume-snapshot None) None (Rebuilt request.resume-snapshot)))
  (val session (HeadlessSession name spec context-id (is request.resume-from None) request.lifecycle carry))
  ;; 写しを持ち込む時は、最初の手番で在るようになるので手元の在否を確かめない。
  (when (and (is-not request.resume-from None) (is request.prompt None) (is carry None))
    (<- status (ClaudeSessionStatus spec.home spec.cwd request.resume-from))
    (when (isinstance status.transcript TranscriptAbsent)
      (raise (ResumeTargetNotFoundError :resume-from request.resume-from))))
  (when (is-not request.prompt None)
    (<- images tuple (images-of request.attachments))
    (<- (start-turn session (TurnInput request.prompt (new-ref) images))))
  ;; 起こせた後に、同じ文脈の手の空いた前の session を表から外す(superseded-names — 層 2 の会話はこの session が続ける)。
  (<- superseded (superseded-names state context-id name))
  (for [old superseded]
    (del (get state.sessions old)))
  (setv (get state.sessions name) session)
  (SessionHandle :session-id name))

(defk images-of [attachments]
  {:pre [(: attachments tuple)] :post [(: % tuple)] :tags {:context "headless-adapter" :role "judgment"}}
  "効果に添えた画像(InputImage の列)を、層 2 の手番の入力の画像(ImageAttachment — 層 2 が CLI の入力の画像の block にする)へ写すため
   (利用者が貼った画像を prompt の一部として CLI へ渡す・card ki-0faa366b76c0)。mime の受理は層 2 が検める(外れは AttachmentRefused)。"
  (tuple (gfor image attachments (ImageAttachment image.mime image.data-base64))))

(defk deliver [#^ HeadlessSession session #^ TurnInput input mode]
  {:pre [(: session HeadlessSession) (: input TurnInput) (: mode TurnInputMode)] :post [(: % (type None))]}
  "入力を届ける: NEXT_TURN = 手番が走っていなければ始め、走っていれば待たせる / INJECT = 走っている手番に足す。"
  (when session.stopped
    (raise (SessionNotFoundError (.format "session {} は止めた" session.name))))
  (<- (pull session 0.0))
  (cond
    (= mode TurnInputMode.INJECT)
      (do
        (when (is session.turn None) (raise (NoTurnInFlightError :session-id session.name)))
        (<- outcome (ClaudeInjectInput session.turn input))
        (cond
          (isinstance outcome NoTurnInFlight) (raise (NoTurnInFlightError :session-id session.name))
          (isinstance outcome AttachmentRefused)
            (raise (AgentError (.format "session {}: 受けない添付 {}" session.name outcome.mime)))))
    (is session.turn None) (<- (start-turn session input))
    True (.append session.waiting input))
  None)

(defk warm [#^ HeadlessSession session]
  {:pre [(: session HeadlessSession)] :post [(: % bool)] :tags {:context "headless-adapter" :role "foundation"}}
  "session の次のターンの CLI を、入力の前に層 2 で起動して待たせるため(WarmSessionEffect — 起動してから入力を受けられるまでの秒を
   入力の前に済ませる)。始まり方は次のターンと同じ next-origin なので、次のターンは層 2 でその process を使い回す。持ち込むコピーは
   事前起動で持ち込む(次のターンでは持ち込まない)。ターンが動いている・入力を待たせていれば事前起動するものは無い(偽)。止めた
   session は SessionNotFoundError、層 2 の拒否はターンと同じ例外(start-refusal)。結果 = 真(次の入力を待つ runtime が在る)か偽。"
  (when session.stopped
    (raise (SessionNotFoundError (.format "session {} は止めた" session.name))))
  (<- (pull session 0.0))
  (when (or (is-not session.turn None) session.waiting)
    (return False))
  (<- origin (next-origin session))
  (<- outcome (ClaudeWarmSession origin session.spec))
  (match outcome
    (SessionWarmed) (do (setv session.carry None) True)
    _ (raise (start-refusal outcome session))))

(defk interrupt [#^ HeadlessSession session #^ (| str None) for-input]
  {:pre [(: session HeadlessSession) (: for-input (| str None))] :post [(: % bool)]}
  "走っている手番だけを止める(session と文脈は残る)。for-input = この止めで早く読ませたい足した入力(None = 名指さない止め)— 層 2 が
   その入力をもう読んだと答えたら(InputAlreadyRead)手番は止めずに走らせ続ける。答え = 走っている手番が在ったか。"
  (<- (pull session 0.0))
  (when (is session.turn None) (return False))
  (<- outcome (ClaudeInterruptTurn session.turn for-input))
  (isinstance outcome #(InterruptRequested InputAlreadyRead)))

(defk stop [#^ HeadlessSession session #^ str reason]
  {:pre [(: session HeadlessSession) (: reason str)] :post [(: % (type None))]}
  "session を止める(冪等)。走っていた手番は Interrupted で終わり、待たせた入力は discarded の運命で閉じる。"
  (when session.stopped (return None))
  (<- closed (ClaudeCloseSession session.context-id reason))
  ;; 層 2 が降ろし切れなかった時は止めた印を付けない — 次の Stop が層 2 に降ろし直しを頼む。
  (when (isinstance closed ProcessStillAlive)
    (raise (AgentError (.format "session {} を止められない: {}" session.name closed.detail))))
  ;; 印を付けてから終わりを読む(止めた session は待たせた入力の手番を始めない)。
  (setv session.stopped True)
  (<- (pull session 0.0))
  (for [input session.waiting]
    (<- (append-event session (fn [seq at] (AgentInputFateEvent :seq seq :at at :input-ref input.ref
                                                                :state InputFateState.DISCARDED)))))
  (setv session.waiting [] session.turn None)
  None)

(defk export-context [#^ HeadlessClaudeConfig config #^ ExportContextEffect request]
  {:pre [(: config HeadlessClaudeConfig) (: request ExportContextEffect)] :post [(: % (| str None))]}
  "文脈の写しを層 2 から取り出す(cwd は spec-of と同じ綴り)。答え = 写しの本文か、この家に無ければ None
   (文脈の id の綴りでない値もこの家には無い)。"
  (try
    (checked-session-id request.context-id "ExportContextEffect.context_id")
    (except [#(ValueError TypeError)]
      (return None)))
  (<- outcome (ClaudeExportSession config.home (str request.work-dir) request.context-id :tail-bytes request.tail-bytes
                                    :from-byte request.from-byte))
  (cond
    (isinstance outcome SessionExported) outcome.jsonl-text
    (isinstance outcome SessionNotFound) None
    True (raise (AgentError (.format "文脈 {} の写し: 層 2 の答えが閉語彙の外: {!r}" request.context-id outcome)))))

(defk replacement-credential [#^ str ref]
  {:pre [(: ref str)] :post [(: % (| LentCredential ReplacementCredentialUnavailable))]
   :tags {:context "headless-adapter" :role "foundation"}}
  "走っている process の資格を替えるための参照を、層 2 の借りた資格へ引き換えるため(引き換えは起こす時と同じ RedeemTurnCredentialEffect
   — 答えは参照を出した外側が返す)。引き換えられない参照・家の資格を名指す参照(借りた資格を家の資格では替えられない)は
   ReplacementCredentialUnavailable。閉語彙の外の答えは型の名だけを名乗って AgentError。"
  (<- answer (RedeemTurnCredentialEffect :credential-ref ref))
  (match answer
    (TurnCredential) (do (<- lent (lent-of answer)) lent)
    (HomeTurnCredential) (ReplacementCredentialUnavailable
                           (.format "手番の資格 {} は家の資格を名指す — 借りた資格を家の資格では替えられない" ref))
    (TurnCredentialUnavailable :reason reason) (ReplacementCredentialUnavailable reason)
    ;; 答えの型だけを名乗る(値は資格を運び得るので文に写さない)。
    _ (raise (AgentError (.format "手番の資格 {} の引き換えの答えが閉語彙の外: {}" ref (. (type answer) __name__))))))

(defk replace-credential [#^ HeadlessSession session #^ str ref]
  {:pre [(: session HeadlessSession) (: ref str)]
   :post [(: % (| SessionCredentialReplaced SessionProcessNotLive SessionOnHomeCredential ReplacementCredentialUnavailable))]
   :tags {:context "headless-adapter" :role "foundation"}}
  "session の走っている process の資格を替えるため(ReplaceSessionCredentialEffect — card acp:kanban-issue:ki-4ffdafa021e5)。家の資格で
   起こした session は引き換えずに SessionOnHomeCredential。止めた session は引き換えずに SessionProcessNotLive。ほかは参照を引き換え、
   session の宣言の資格を替えて(後の手番もその資格で起こす・使い回す process にもその資格を書く)から、層 2 の
   ClaudeReplaceSessionCredential で生きた process の資格を替える。層 2 の答え: CredentialReplaced → SessionCredentialReplaced /
   SessionProcessDown・NoSuchSession(まだ手番を始めていない)→ SessionProcessNotLive / HomeCredentialSession → SessionOnHomeCredential。"
  (when (is session.spec.credential None)
    (return (SessionOnHomeCredential)))
  (when session.stopped
    (return (SessionProcessNotLive)))
  (<- lent (replacement-credential ref))
  (when (isinstance lent ReplacementCredentialUnavailable)
    (return lent))
  (setv session.spec (replace session.spec :credential lent))
  (<- outcome (ClaudeReplaceSessionCredential session.context-id lent))
  (match outcome
    (CredentialReplaced) (SessionCredentialReplaced)
    (SessionProcessDown) (SessionProcessNotLive)
    (NoSuchSession) (SessionProcessNotLive)
    (HomeCredentialSession) (SessionOnHomeCredential)
    _ (raise (AgentError (.format "session {}: 資格の差し替えの層 2 の答えが閉語彙の外: {}" session.name
                                  (. (type outcome) __name__))))))

(defn refuse-keys [#^ bool literal #^ bool enter]
  (when (or (not literal) (not enter))
    (raise (AgentCapabilityUnsupportedError :capability "SendEffect keys (literal=False / enter=False)" :handler HANDLER-NAME))))


;; --- handler -----------------------------------------------------------------------------------

(defhandler headless-claude-handler [config state]
  (LaunchEffect [agent-type]
    :when (= agent-type AgentType.CLAUDE)
    (<- handle (launch config state effect))
    (resume handle))

  (ExportContextEffect [agent-type]
    :when (= agent-type AgentType.CLAUDE)
    (<- copied (export-context config effect))
    (resume copied))

  (SendEffect [handle message enter literal]
    :when (in handle.session-id state.sessions)
    (refuse-keys literal enter)
    (<- (deliver (get state.sessions handle.session-id) (TurnInput message (new-ref)) TurnInputMode.NEXT-TURN))
    (resume None))

  (FollowUpEffect [handle message mode input-ref attachments]
    :when (in handle.session-id state.sessions)
    (<- images tuple (images-of attachments))
    (<- (deliver (get state.sessions handle.session-id) (TurnInput message (or input-ref (new-ref)) images) mode))
    (resume handle))

  (InterruptEffect [handle for-input]
    :when (in handle.session-id state.sessions)
    (<- asked (interrupt (get state.sessions handle.session-id) for-input))
    (resume asked))

  (WarmSessionEffect [handle]
    :when (in handle.session-id state.sessions)
    (<- warmed (warm (get state.sessions handle.session-id)))
    (resume warmed))

  (ReplaceSessionCredentialEffect [handle credential-ref]
    :when (in handle.session-id state.sessions)
    (<- replaced (replace-credential (get state.sessions handle.session-id) credential-ref))
    (resume replaced))

  (EventsEffect [handle after-seq wait-seconds]
    :when (in handle.session-id state.sessions)
    (<- page (read-events config (get state.sessions handle.session-id) after-seq (float wait-seconds)))
    (resume page))

  (AwaitResultEffect [handle timeout-seconds]
    :when (in handle.session-id state.sessions)
    (<- outcome (await-turn-end config (get state.sessions handle.session-id) timeout-seconds))
    (resume outcome))

  (MonitorEffect [handle]
    :when (in handle.session-id state.sessions)
    (setv session (get state.sessions handle.session-id))
    (<- (pull session 0.0))
    (resume (Observation :status (status-of session))))

  (CaptureEffect [handle]
    :when (in handle.session-id state.sessions)
    (raise (AgentCapabilityUnsupportedError :capability "CaptureEffect (no screen)" :handler HANDLER-NAME)))

  (AttachAgentSessionEffect [session-id]
    :when (in session-id state.sessions)
    (raise (AgentCapabilityUnsupportedError :capability "AttachAgentSessionEffect (no screen)" :handler HANDLER-NAME)))

  (StopEffect [handle]
    :when (in handle.session-id state.sessions)
    (<- (stop (get state.sessions handle.session-id) "stop"))
    (resume None))

  (StopSessionEffect [handle reason]
    :when (in handle.session-id state.sessions)
    (<- (stop (get state.sessions handle.session-id) (or reason "stop")))
    (resume None))

  (ReleaseSessionEffect [handle]
    :when (in handle.session-id state.sessions)
    (<- (stop (get state.sessions handle.session-id) "release"))
    (del (get state.sessions handle.session-id))
    (resume None)))

