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
;;;   LaunchEffect.turn_credential_ref → 起こす直前に RedeemTurnCredentialEffect(参照)を外側へ出し、答えの TurnCredential を子の env の
;;;                                      手番の資格の名 1 つにだけ置く(HomeTurnCredential = 家の資格のまま・TurnCredentialUnavailable =
;;;                                      TurnCredentialUnavailableError で起こさない — issue #979)
;;;   LaunchEffect.resume_snapshot     → その session の最初の ClaudeStartTurn の ResumeSession の carry = Rebuilt(写し)(2 手番目からは無し)
;;;   ExportContextEffect(CLAUDE)      → ClaudeExportSession(SessionExported → 写しの本文・SessionNotFound → None)
;;;   SendEffect / FollowUpEffect      → 手番が走っていなければ ClaudeStartTurn(ResumeSession)、走っていれば待たせて終わりの後に始める
;;;   FollowUpEffect(mode = INJECT)    → ClaudeInjectInput(走っている手番に足す)
;;;   InterruptEffect                  → ClaudeInterruptTurn(手番だけを止める。待たせた入力は次の手番で走る)
;;;   EventsEffect / AwaitResultEffect / MonitorEffect → ClaudeReadTurnEvents(行を層 3 の出来事と手番の終わりに写す)
;;;   手番の終わりの last-call-usage・last-call-model・model-windows → AgentTurn*.last_call_usage(AgentTurnUsage — 額は None)・
;;;                                      last_call_model・model_windows(会話の今の context の大きさの材料 — agora-redesign #3744)
;;;   AssistantMessage.tool-calls / ToolResult.answers → AgentToolUseEvent.tool_calls / AgentToolResultEvent.answers(道具の呼びの命令
;;;                                      ToolCall.input と結果の中身 ToolAnswer を層 2 の型のまま運ぶ — agora-redesign #3744)
;;;   Completed.usage / Failed.usage   → AgentTurnCompleted.usage / AgentTurnFailed.usage(AgentTurnUsage — cache_creation → cache_write・cache_read → cache_read。
;;;                                      CLI が名乗らない欄は None のまま・4 欄とも無ければ usage = None)
;;;   Completed.cost-usd / Failed.cost-usd → AgentTurnUsage.cost_usd(手番の額 USD — 層 2 が CLI の累積の額から手番の分に直した値。
;;;                                      分からなければ None。token の 4 欄と額がすべて無ければ usage = None・agora-redesign #883)
;;;   WarmSessionEffect                → ClaudeWarmSession(次のターンと同じ始まり方 — 1 度もターンを始めていない新しい文脈は同じ id の
;;;                                      FreshSession・ほかは ResumeSession。ターンが動いている・入力を待たせていれば事前起動しない)
;;;   StopEffect / StopSessionEffect / ReleaseSessionEffect → ClaudeCloseSession(待たせた入力は discarded の運命で閉じる・事前起動
;;;                                      しただけの runtime も停止する)
;;;   CaptureEffect / AttachAgentSessionEffect → 画面が無いので AgentCapabilityUnsupportedError
;;; この handler が起こしていない session の effect と、CLAUDE 以外の LaunchEffect は外側の handler へ回す。
(require doeff-hy.macros [defhandler defk <- val])
(import collections.abc [Callable])
(import dataclasses [dataclass field])
(import datetime [datetime])
(import uuid)
(import doeff_hy.frozen [FrozenMap frozen-json-object])
(import doeff_time [GetMonotonic GetTime])
(import doeff_agents.adapters.base [AgentType AgentSessionLifecycle])
(import doeff_agents.monitor [SessionStatus])
(import doeff_agents.shell [assert-no-forbidden-agent-env assert-session-env-is-non-auth-overlay])
(import doeff_agents.effects.agent [
  LaunchEffect SendEffect FollowUpEffect InterruptEffect EventsEffect AwaitResultEffect MonitorEffect CaptureEffect
  StopEffect StopSessionEffect ReleaseSessionEffect AttachAgentSessionEffect ExportContextEffect WarmSessionEffect
  SessionHandle Observation AwaitOutcome AwaitStatus TurnInputMode InputFateState
  AgentEventPage AgentTextEvent AgentTextDeltaEvent AgentThinkingDeltaEvent AgentToolUseEvent AgentToolResultEvent AgentInputFateEvent
  AgentTurnEndEvent AgentTurnCompleted AgentTurnFailed AgentTurnInterrupted AgentTurnLost AgentTurnUsage
  AgentError AgentLaunchError AgentCapabilityUnsupportedError NoTurnInFlightError ResumeTargetNotFoundError
  SessionAlreadyExistsError SessionNotFoundError TurnInFlightError
  RedeemTurnCredentialEffect TurnCredential HomeTurnCredential TurnCredentialUnavailable TurnCredentialUnavailableError])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec ClaudeTurn TurnInput FreshSession ResumeSession Rebuilt
                                  BypassAll PermissionPolicy checked-session-id])
(import doeff_claude_code.lines [AssistantMessage PartialMessage ToolResult InputFate DeltaKind
                                 Completed Failed Interrupted BackendLost Usage])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeInjectInput ClaudeInterruptTurn ClaudeReadTurnEvents
                                   ClaudeCloseSession ClaudeSessionStatus ClaudeExportSession ClaudeWarmSession
                                   TurnStarted InterruptRequested TurnEventPage SessionExported SessionWarmed
                                   SessionNotFound SessionIdInUse TurnInFlight CarryRefused LaunchFailed AttachmentRefused
                                   NoTurnInFlight UnknownTurn ProcessStillAlive TranscriptAbsent])

(setv HANDLER-NAME "headless-claude-handler")

;; 引き換えた access token(LaunchEffect.turn_credential_ref の RedeemTurnCredentialEffect の答え — #665・#979)を置く env の名。綴りの家は境界の env の語彙
;; doeff_agents/agent_env.hy の 1 点(TURN-AUTH-ENV-KEYS の要素・#708 — 家は session host を import しない)。
(import doeff_agents.agent_env [CLAUDE-TURN-CREDENTIAL-ENV :as TURN-CREDENTIAL-ENV GITHUB-TOKEN-ENV])


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
    (setv #^ (get list (| AgentTextEvent AgentTextDeltaEvent AgentThinkingDeltaEvent AgentToolUseEvent AgentToolResultEvent
                          AgentInputFateEvent AgentTurnEndEvent))
          self.events [])
    (setv #^ (| AgentTurnCompleted AgentTurnFailed AgentTurnInterrupted AgentTurnLost None) self.last-end None)))

(defclass HeadlessState []
  "handler の状態: session の名 → HeadlessSession(composition root が 1 つ作って渡す)。"
  (defn __init__ [self]
    (setv self.sessions {})))


;; --- 純粋な写し ---------------------------------------------------------------------------------

(defn #^ str new-ref [] (str (uuid.uuid4)))

(defn #^ ClaudeSessionSpec spec-of [#^ HeadlessClaudeConfig config #^ LaunchEffect effect #^ (| TurnCredential None) [credential None]]
  "LaunchEffect → 層 2 の会話の宣言。process の env = 家の env + session_env(非 auth の上書き)+ 引き換えた access token
   (credential — LaunchEffect.turn_credential_ref を RedeemTurnCredentialEffect で引き換えた答え・None = 家の資格。手番の資格の env の名
   1 つにだけ置く。設定 dir の env と session_env は資格の env を持てない — 資格の入口は引き換えの答え 1 つ)。答えが GitHub の token
   (credential.github-token)も持てば GITHUB-TOKEN-ENV(GH_TOKEN)に置く(#3753)。許可の方策は config の permission。"
  (assert-session-env-is-non-auth-overlay effect.session-env :context "LaunchEffect.session_env (headless-claude-handler)")
  (setv env (| (dict config.home.env) (dict (or effect.session-env {}))))
  (assert-no-forbidden-agent-env env :context "headless-claude-handler の process の env")
  (when (is-not credential None)
    (setv (get env TURN-CREDENTIAL-ENV) credential.oauth-token))
  (when (and (is-not credential None) (is-not credential.github-token None))
    (setv (get env GITHUB-TOKEN-ENV) credential.github-token))
  ;; 借りた資格の期限は層 2 の宣言へ写す(層 2 が床で生きた process を止める — #3672 の D2)。家の資格は期限を知らない。
  (ClaudeSessionSpec :home (ClaudeHome config.home.config-dir env)
                     :cwd (str effect.work-dir)
                     :model effect.model
                     :effort effect.effort
                     :settings config.settings
                     :permission config.permission
                     :cold-resume-prompt config.cold-resume-prompt
                     :credential-expires-at (if (is credential None) None credential.expires-at)))

(defn #^ list event-builders-of [kind #^ datetime at]
  "層 2 の行の型 → 層 3 の出来事を作る関数(seq → 出来事)の列。出来事はその型の欄で直接作る(語彙の外の行は空)。"
  (cond
    (isinstance kind AssistantMessage)
      (+ (if kind.text [(fn [seq] (AgentTextEvent :seq seq :at at :text kind.text))] [])
         (if kind.tool-calls [(fn [seq] (AgentToolUseEvent :seq seq :at at :tool-calls kind.tool-calls))] []))
    (and (isinstance kind PartialMessage) kind.text-delta)
      [(fn [seq] (AgentTextDeltaEvent :seq seq :at at :text kind.text-delta))]
    ;; 考えている間の差分は、中身が空でも片ごとに 1 つ出す — 本文の前に「考えている」と分かる合図(agora-redesign #3789)。
    (and (isinstance kind PartialMessage) (= kind.delta DeltaKind.THINKING))
      [(fn [seq] (AgentThinkingDeltaEvent :seq seq :at at :text kind.thinking-delta))]
    (isinstance kind ToolResult)
      [(fn [seq] (AgentToolResultEvent :seq seq :at at :answers kind.answers))]
    (isinstance kind InputFate)
      [(fn [seq] (AgentInputFateEvent :seq seq :at at :input-ref kind.ref :state (InputFateState kind.state)))]
    True []))

(defn #^ list events-of [line #^ int first-seq]
  "層 2 の 1 行 → 層 3 の出来事の列(語彙の外の行は空 — 生の行は上へ渡さない)。seq は first-seq から続けて振る。"
  (lfor #(index build) (enumerate (event-builders-of line.kind line.at))
        (build (+ first-seq index))))

(defn #^ (| AgentTurnUsage None) usage-of [#^ Usage usage #^ (| float None) cost-usd]
  "層 2 の手番の usage と額 → 層 3 の AgentTurnUsage。手番の消費と額を記録へ運ぶための写しで、token の 4 欄と額がすべて
   名乗られなければ None(0 を発明しない)。"
  (setv turn-usage (AgentTurnUsage :input-tokens usage.input-tokens
                                   :output-tokens usage.output-tokens
                                   :cache-write-tokens usage.cache-creation-input-tokens
                                   :cache-read-tokens usage.cache-read-input-tokens
                                   :cost-usd cost-usd))
  (if (= turn-usage (AgentTurnUsage)) None turn-usage))

(defn #^ (| AgentTurnUsage None) last-call-of [#^ (| Usage None) usage]
  "層 2 の手番の最後の呼びの usage → 層 3 の AgentTurnUsage(会話の今の context の大きさを記録へ運ぶため — #3744)。呼びごとの額は
   CLI が名乗らないので None。usage が無い・4 欄とも名乗られなければ None。"
  (if (is usage None) None (usage-of usage None)))

(defn end-of [end #^ str context-id]
  "層 2 の手番の終わり → 層 3 の手番の終わり(続きの身元 resume-from を載せる)。どの終わりも本体の最後の呼びの usage と model・
   model ごとの窓を運ぶ(#3744)。"
  (when (not (isinstance end #(Completed Failed Interrupted BackendLost)))
    (raise (TypeError (.format "層 2 の手番の終わりが閉語彙の外: {!r}" end))))
  (setv last-call-usage (last-call-of end.last-call-usage)
        last-call-model end.last-call-model
        model-windows end.model-windows)
  (cond
    (isinstance end Completed)
      (AgentTurnCompleted :result-text end.result-text :input-refs end.input-refs :resume-from context-id
                          :usage (usage-of end.usage end.cost-usd)
                          :last-call-usage last-call-usage :last-call-model last-call-model :model-windows model-windows)
    (isinstance end Failed)
      (AgentTurnFailed :detail end.detail :input-refs end.input-refs :resume-from context-id
                       :usage (usage-of end.usage end.cost-usd)
                       :last-call-usage last-call-usage :last-call-model last-call-model :model-windows model-windows)
    (isinstance end Interrupted)
      (AgentTurnInterrupted :cli-kept end.process-kept :surviving-refs end.surviving-refs :dropped-refs end.dropped-refs
                            :resume-from context-id
                            :last-call-usage last-call-usage :last-call-model last-call-model :model-windows model-windows)
    (isinstance end BackendLost)
      (AgentTurnLost :detail end.detail :resume-from context-id
                     :last-call-usage last-call-usage :last-call-model last-call-model :model-windows model-windows)))

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
  "ClaudeStartTurn の断り → 層 3 の例外(型で名乗る)。"
  (cond
    (isinstance outcome SessionNotFound) (ResumeTargetNotFoundError :resume-from outcome.session-id)
    (isinstance outcome SessionIdInUse)
      (SessionAlreadyExistsError (.format "agent runtime の文脈 {} は既に在る(session {})" outcome.session-id session.name))
    (isinstance outcome LaunchFailed)
      (AgentLaunchError (.format "session {} の手番を起こせない(exit {}): {}" session.name outcome.exit-code outcome.stderr-tail))
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

(defk start-waiting [#^ HeadlessSession session]
  {:pre [(: session HeadlessSession)] :post [(: % (type None))]}
  "待たせた入力の手番を順に始める。始められなかった入力は失敗の終わり(AgentTurnFailed)として出来事に置き、次へ進む。"
  (while (and session.waiting (is session.turn None) (not session.stopped))
    (setv input (.pop session.waiting 0) failure None)
    (try
      (<- (start-turn session input))
      (except [error AgentError]
        (setv failure (AgentTurnFailed :detail (str error) :input-refs #(input.ref) :resume-from session.context-id))))
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
  (setv mapped (end-of end session.context-id))
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
  ;; 手番の資格は起こす直前に引き換える(断りの検めを通った起動だけが資格を受ける)。
  (<- credential (redeemed-credential request))
  (setv spec (spec-of config request credential))
  (val carry (if (is request.resume-snapshot None) None (Rebuilt request.resume-snapshot)))
  (val session (HeadlessSession name spec (or request.resume-from (new-ref)) (is request.resume-from None) request.lifecycle
                                carry))
  ;; 写しを持ち込む時は、最初の手番で在るようになるので手元の在否を確かめない。
  (when (and (is-not request.resume-from None) (is request.prompt None) (is carry None))
    (<- status (ClaudeSessionStatus spec.home spec.cwd request.resume-from))
    (when (isinstance status.transcript TranscriptAbsent)
      (raise (ResumeTargetNotFoundError :resume-from request.resume-from))))
  (when (is-not request.prompt None)
    (<- (start-turn session (TurnInput request.prompt (new-ref)))))
  (setv (get state.sessions name) session)
  (SessionHandle :session-id name))

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

(defk interrupt [#^ HeadlessSession session]
  {:pre [(: session HeadlessSession)] :post [(: % bool)]}
  "走っている手番だけを止める(session と文脈は残る)。答え = 止める手番が在ったか。"
  (<- (pull session 0.0))
  (when (is session.turn None) (return False))
  (<- outcome (ClaudeInterruptTurn session.turn))
  (isinstance outcome InterruptRequested))

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
  (<- outcome (ClaudeExportSession config.home (str request.work-dir) request.context-id))
  (cond
    (isinstance outcome SessionExported) outcome.jsonl-text
    (isinstance outcome SessionNotFound) None
    True (raise (AgentError (.format "文脈 {} の写し: 層 2 の答えが閉語彙の外: {!r}" request.context-id outcome)))))

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

  (FollowUpEffect [handle message mode input-ref]
    :when (in handle.session-id state.sessions)
    (<- (deliver (get state.sessions handle.session-id) (TurnInput message (or input-ref (new-ref))) mode))
    (resume handle))

  (InterruptEffect [handle]
    :when (in handle.session-id state.sessions)
    (<- asked (interrupt (get state.sessions handle.session-id)))
    (resume asked))

  (WarmSessionEffect [handle]
    :when (in handle.session-id state.sessions)
    (<- warmed (warm (get state.sessions handle.session-id)))
    (resume warmed))

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

