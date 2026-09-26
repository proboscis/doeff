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
;;;   LaunchEffect.resume_snapshot     → その session の最初の ClaudeStartTurn の ResumeSession の carry = Rebuilt(写し)(2 手番目からは無し)
;;;   ExportContextEffect(CLAUDE)      → ClaudeExportSession(SessionExported → 写しの本文・SessionNotFound → None)
;;;   SendEffect / FollowUpEffect      → 手番が走っていなければ ClaudeStartTurn(ResumeSession)、走っていれば待たせて終わりの後に始める
;;;   FollowUpEffect(mode = INJECT)    → ClaudeInjectInput(走っている手番に足す)
;;;   InterruptEffect                  → ClaudeInterruptTurn(手番だけを止める。待たせた入力は次の手番で走る)
;;;   EventsEffect / AwaitResultEffect / MonitorEffect → ClaudeReadTurnEvents(行を層 3 の出来事と手番の終わりに写す)
;;;   StopEffect / StopSessionEffect / ReleaseSessionEffect → ClaudeCloseSession(待たせた入力は discarded の運命で閉じる)
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
  StopEffect StopSessionEffect ReleaseSessionEffect AttachAgentSessionEffect ExportContextEffect
  SessionHandle Observation AwaitOutcome AwaitStatus TurnInputMode InputFateState
  AgentEventPage AgentTextEvent AgentTextDeltaEvent AgentToolUseEvent AgentToolResultEvent AgentInputFateEvent
  AgentTurnEndEvent AgentTurnCompleted AgentTurnFailed AgentTurnInterrupted AgentTurnLost
  AgentError AgentLaunchError AgentCapabilityUnsupportedError NoTurnInFlightError ResumeTargetNotFoundError
  SessionAlreadyExistsError SessionNotFoundError])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec ClaudeTurn TurnInput FreshSession ResumeSession Rebuilt
                                  checked-session-id])
(import doeff_claude_code.lines [AssistantMessage PartialMessage ToolResult InputFate
                                 Completed Failed Interrupted BackendLost])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeInjectInput ClaudeInterruptTurn ClaudeReadTurnEvents
                                   ClaudeCloseSession ClaudeSessionStatus ClaudeExportSession
                                   TurnStarted InterruptRequested TurnEventPage SessionExported
                                   SessionNotFound SessionIdInUse TurnInFlight CarryRefused LaunchFailed AttachmentRefused
                                   NoTurnInFlight UnknownTurn ProcessStillAlive TranscriptAbsent])

(setv HANDLER-NAME "headless-claude-handler")

;; 借りた access token(LaunchEffect.turn_credential)を置く env の名(agora-redesign #665)。綴りの家は境界の env の語彙
;; doeff_agents/agent_env.hy の 1 点(TURN-AUTH-ENV-KEYS の要素・#708 — 家は session host を import しない)。
(import doeff_agents.agent_env [CLAUDE-TURN-CREDENTIAL-ENV :as TURN-CREDENTIAL-ENV])


;; --- 設定と状態 ---------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] HeadlessClaudeConfig []
  "composition root が渡す宣言: home = claude の家(資格は root が custody から借りて env に置く — この handler は読むだけ)/
   settings = CLI の settings に合流する宣言(JSON — 深く凍らせた写像)/ cold-resume-prompt = 冷えた続きの前に 1 回だけ走らせる命令(None = 走らせない)/
   page-wait = 層 2 へ 1 回に待つ秒数の上限(長い待ちはこの刻みで読む)。"
  (#^ ClaudeHome home)
  (setv #^ FrozenMap settings (field :default-factory FrozenMap))
  (setv #^ (| str None) cold-resume-prompt None)
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
    (setv #^ (get list (| AgentTextEvent AgentTextDeltaEvent AgentToolUseEvent AgentToolResultEvent AgentInputFateEvent
                          AgentTurnEndEvent))
          self.events [])
    (setv #^ (| AgentTurnCompleted AgentTurnFailed AgentTurnInterrupted AgentTurnLost None) self.last-end None)))

(defclass HeadlessState []
  "handler の状態: session の名 → HeadlessSession(composition root が 1 つ作って渡す)。"
  (defn __init__ [self]
    (setv self.sessions {})))


;; --- 純粋な写し ---------------------------------------------------------------------------------

(defn #^ str new-ref [] (str (uuid.uuid4)))

(defn #^ ClaudeSessionSpec spec-of [#^ HeadlessClaudeConfig config #^ LaunchEffect effect]
  "LaunchEffect → 層 2 の会話の宣言。process の env = 家の env + session_env(非 auth の上書き)+ 借りた access token
   (turn_credential — 手番の資格の env の名 1 つにだけ置く。家の env と session_env は資格の env を持てない — 資格の入口は型の欄 1 つ)。"
  (assert-session-env-is-non-auth-overlay effect.session-env :context "LaunchEffect.session_env (headless-claude-handler)")
  (setv env (| (dict config.home.env) (dict (or effect.session-env {}))))
  (assert-no-forbidden-agent-env env :context "headless-claude-handler の process の env")
  (when (is-not effect.turn-credential None)
    (setv (get env TURN-CREDENTIAL-ENV) effect.turn-credential.oauth-token))
  (ClaudeSessionSpec :home (ClaudeHome config.home.config-dir env)
                     :cwd (str effect.work-dir)
                     :model effect.model
                     :effort effect.effort
                     :settings config.settings
                     :cold-resume-prompt config.cold-resume-prompt))

(defn #^ list event-builders-of [kind #^ datetime at]
  "層 2 の行の型 → 層 3 の出来事を作る関数(seq → 出来事)の列。出来事はその型の欄で直接作る(語彙の外の行は空)。"
  (cond
    (isinstance kind AssistantMessage)
      (+ (if kind.text [(fn [seq] (AgentTextEvent :seq seq :at at :text kind.text))] [])
         (if kind.tool-names [(fn [seq] (AgentToolUseEvent :seq seq :at at :tool-names kind.tool-names))] []))
    (and (isinstance kind PartialMessage) kind.text-delta)
      [(fn [seq] (AgentTextDeltaEvent :seq seq :at at :text kind.text-delta))]
    (isinstance kind ToolResult)
      [(fn [seq] (AgentToolResultEvent :seq seq :at at :tool-use-ids kind.tool-use-ids))]
    (isinstance kind InputFate)
      [(fn [seq] (AgentInputFateEvent :seq seq :at at :input-ref kind.ref :state (InputFateState kind.state)))]
    True []))

(defn #^ list events-of [line #^ int first-seq]
  "層 2 の 1 行 → 層 3 の出来事の列(語彙の外の行は空 — 生の行は上へ渡さない)。seq は first-seq から続けて振る。"
  (lfor #(index build) (enumerate (event-builders-of line.kind line.at))
        (build (+ first-seq index))))

(defn end-of [end #^ str context-id]
  "層 2 の手番の終わり → 層 3 の手番の終わり(続きの身元 resume-from を載せる)。"
  (cond
    (isinstance end Completed)
      (AgentTurnCompleted :result-text end.result-text :input-refs end.input-refs :resume-from context-id)
    (isinstance end Failed)
      (AgentTurnFailed :detail end.detail :input-refs end.input-refs :resume-from context-id)
    (isinstance end Interrupted)
      (AgentTurnInterrupted :surviving-refs end.surviving-refs :dropped-refs end.dropped-refs :resume-from context-id)
    (isinstance end BackendLost)
      (AgentTurnLost :detail end.detail :resume-from context-id)
    True (raise (TypeError (.format "層 2 の手番の終わりが閉語彙の外: {!r}" end)))))

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
    (isinstance outcome TurnInFlight)
      (AgentError (.format "session {} の文脈に、この handler の知らない手番が走っている: {!r}" session.name outcome.turn))
    True (AgentError (.format "session {}: 層 2 の答えが閉語彙の外: {!r}" session.name outcome))))


;; --- 層 2 との往復 --------------------------------------------------------------------------------

(defk start-turn [#^ HeadlessSession session #^ TurnInput input]
  {:pre [(: session HeadlessSession) (: input TurnInput)] :post [(: % ClaudeTurn)]}
  "次の手番を始める。1 度も手番を始めていない新しい文脈は FreshSession、ほかは ResumeSession
   (降りた process を起こし直すかは層 2 の判断 — ここは続けるとだけ言う)。持ち込む写し(carry)が在れば、始められた手番で
   使い切る(層 2 は既に在る transcript を上書きしない)。断りは例外で名乗る。"
  (val origin (if session.fresh
                  (FreshSession session.context-id)
                  (ResumeSession session.context-id :carry session.carry)))
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
  (setv spec (spec-of config request))
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

