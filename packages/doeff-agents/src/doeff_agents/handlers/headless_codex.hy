;;; headless の codex の adapter — doeff-agents の公開 effect(effects/agent.py)を doeff-codex の公開 effect へ写す
;;; (card acp:kanban-issue:ki-534a081e32eb)。
;;;
;;; 層: codex の app-server ← doeff-codex(process の寿命)← この adapter(agent の寿命)← 呼び手の Program。この module は process を
;;; 知らない: 子 process・起動の引数・pid・信号・降りた process の起こし直しは doeff-codex の handler の中にある。ここが持つのは agent の
;;; 寿命の判断だけ — ターンの順番(走っているターンの後に回す入力を待たせる)・続きの身元(codex の thread の id)・層 2 の行から層 3 の
;;; 出来事の型への写し。手本 = claude の adapter handlers/headless.hy(同じ出来事の形・同じ読み方 — agora は absorb-event を変えずに受ける)。
;;;
;;; 写し:
;;;   LaunchEffect(CODEX)           → CodexStartTurn(FreshThread か、resume_from なら ResumeThread(その thread の id))— prompt が無ければ
;;;                                    ターンを始めない。新しい会話の続きの身元は、codex が thread/start で決めた thread の id
;;;   LaunchEffect.model・effort・autocompact → 会話の宣言 CodexSessionSpec の model・effort・auto-compact-token-limit
;;;                                    (AutocompactTokens = その token 数・AutocompactAuto = codex の既定)
;;;   LaunchEffect.session_env      → 家の env に重ねる(資格の env は持てない — claude の adapter と同じ検め)
;;;   LaunchEffect.turn_credential_ref → 起こす直前に RedeemTurnCredentialEffect(参照)を外側へ出し、答えの CodexTurnCredential(借りた
;;;                                    口座の auth.json の中身)を会話の宣言の家 CodexHome.auth-json に置く — 層 2 がその process だけの
;;;                                    CODEX_HOME に auth.json を置き、process が降りたら消す(card acp:kanban-issue:ki-0b244c011ca3)。
;;;                                    HomeTurnCredential = 家の資格のまま・TurnCredentialUnavailable と codex の形でない資格(claude の
;;;                                    TurnCredential)= TurnCredentialUnavailableError で起こさない
;;;   attachments(起動・追送)       → 入力 CodexInput の images(codex は data URL の image の入力で受ける)
;;;   SendEffect / FollowUpEffect(NEXT_TURN)→ ターンが走っていなければ CodexStartTurn(ResumeThread)、走っていれば待たせて終わりの後に始める
;;;   FollowUpEffect(INJECT)        → CodexSteerTurn(走っているターンに足す — codex は次の区切りで読む)
;;;   InterruptEffect               → CodexInterruptTurn(ターンだけを止める・process は残る — 終わりは AgentTurnInterrupted の cli_kept 真)
;;;   EventsEffect / AwaitResultEffect / MonitorEffect → CodexReadTurnEvents(行を層 3 の出来事とターンの終わりに写す)
;;;   StopEffect / StopSessionEffect / ReleaseSessionEffect → CodexCloseSession(走っていたターンは cli_kept 偽の AgentTurnInterrupted で
;;;                                    終わり、待たせた入力は discarded の行方で閉じる)
;;;   WarmSessionEffect             → 偽(前もって起こさない — doeff-codex に入力の前に thread を開く口が無い)
;;;   行 → 出来事: TextDelta → AgentTextDeltaEvent・AgentMessageDone → AgentTextEvent・item の種類 reasoning の始まり →
;;;     AgentThinkingStartedEvent・ReasoningDelta → AgentThinkingDeltaEvent・TokenUsage → AgentCallUsageEvent(この呼びの分)。
;;;     道具の item(commandExecution など)・誤りの通知・語彙の外の行は、今は出来事にしない(行の記録が道具の命令と出力を運ばない)。
;;;   入力の行方: ターンを始めた入力・足した入力は、層 2 が受けた時に AgentInputFateEvent(started)を出し、完了の input_refs にも載せる
;;;     (codex は入力ごとの行方を行で名乗らない — 受けた入力はそのターンが読む)。
;;;   usage: 入力の側は cache から読んだ分を除く(codex の inputTokens は cachedInputTokens を含む — 層 3 の input_tokens は claude と同じ
;;;     「cache の外の入力」)。cache_write は名乗らない(codex の cacheWriteInputTokens が inputTokens に含まれるかを確かめていない —
;;;     数を発明しない)。ターンの usage = そのターンの呼びの分の和(額は None — codex は額を名乗らない)。呼びの model は会話の宣言の
;;;     model(codex の usage の行は model を名乗らない)、窓は codex が名乗った modelContextWindow。
;;;
;;; 断る物(AgentCapabilityUnsupportedError — 黙って捨てない): new_context_id の名指し(codex 0.162.1 の thread/start に id の欄が無く、
;;; 呼び手の id で thread を始められない)・resume_snapshot と ExportContextEffect(codex の会話の写しの口が無い)・mcp_tools・bare・
;;; CaptureEffect・AttachAgentSessionEffect(画面が無い)。
;;; この handler が起こしていない session の effect と、CODEX 以外の LaunchEffect は外側の handler へ回す。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "headless-codex-adapter" :role "foundation"})
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import datetime [datetime])
(import uuid)
(import doeff_time [GetMonotonic GetTime])
(import doeff_agents.adapters.base [AgentType AgentSessionLifecycle])
(import doeff_agents.monitor [SessionStatus])
(import doeff_agents.shell [assert-no-forbidden-agent-env assert-session-env-is-non-auth-overlay])
(import doeff_agents.effects.agent [
  LaunchEffect SendEffect FollowUpEffect InterruptEffect EventsEffect AwaitResultEffect MonitorEffect CaptureEffect
  StopEffect StopSessionEffect ReleaseSessionEffect AttachAgentSessionEffect ExportContextEffect WarmSessionEffect
  SessionHandle Observation AwaitOutcome AwaitStatus TurnInputMode InputFateState ModelWindow AutocompactAuto AutocompactTokens
  NamedContextId RedeemTurnCredentialEffect TurnCredential CodexTurnCredential HomeTurnCredential TurnCredentialUnavailable
  TurnCredentialUnavailableError
  AgentEventPage AgentTextEvent AgentTextDeltaEvent AgentThinkingStartedEvent AgentThinkingDeltaEvent AgentCallUsageEvent
  AgentInputFateEvent AgentTurnEndEvent AgentTurnCompleted AgentTurnFailed AgentTurnInterrupted AgentTurnLost AgentTurnUsage
  AgentError AgentLaunchError AgentCapabilityUnsupportedError NoTurnInFlightError ResumeTargetNotFoundError
  SessionAlreadyExistsError SessionNotFoundError TurnInFlightError])
;; 手番の終わり → AwaitOutcome の写し(L2 の見え方)は claude の adapter と同じ 1 つの関数を使う。
(import doeff_agents.handlers.headless [outcome-of])
(import doeff_codex.values [CodexHome CodexSessionSpec CodexTurn CodexInput CodexImage FreshThread ResumeThread])
(import doeff_codex.rpc [ApprovalPolicy SandboxMode])
(import doeff_codex.lines [CodexLine TextDelta AgentMessageDone ReasoningDelta ItemStarted TokenUsage TokenCount TurnEnded TurnStatus])
(import doeff_codex.effects [CodexStartTurn CodexSteerTurn CodexInterruptTurn CodexReadTurnEvents CodexCloseSession
                             BackendLost TurnStarted Steered InterruptRequested TurnEventPage
                             ThreadUnknown TurnInFlight LaunchFailed RequestRefused NoTurnInFlight UnknownTurn ProcessStillAlive])

(val HANDLER-NAME "headless-codex-handler")


;; --- 設定と状態 ---------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] HeadlessCodexConfig []
  "composition root が渡す宣言: home = codex の家(env — PATH・HOME・CODEX_HOME。資格は root が置く — この handler は読むだけ)/
   approval-policy・sandbox = 会話の宣言の許可の方針と sandbox(既定は問わない NEVER と DANGER-FULL-ACCESS — 答え手の無い許可の問いで
   ターンを止めないため。claude の adapter の既定 BypassAll と同じ構え)/ page-wait = 層 2 へ 1 回に待つ秒数の上限。"
  (#^ CodexHome home)
  (setv #^ ApprovalPolicy approval-policy ApprovalPolicy.NEVER)
  (setv #^ SandboxMode sandbox SandboxMode.DANGER-FULL-ACCESS)
  (setv #^ float page-wait 5.0))

(defrecord WaitingInput
  "走っているターンの後に回す入力 1 つ: ref = 入力の行方の参照 / input = codex のターンの入力。"
  {:tags {:context "headless-adapter" :role "type"}}
  (#^ str ref)
  (#^ CodexInput input))

(defclass CodexHeadlessSession []
  "1 つの session(handle)の agent の寿命の状態。process の状態は持たない。
   thread-id = 続きの身元(codex の thread の id — 新しい会話は最初のターンを始めるまで None)/ turn = 走っている層 2 のターン /
   cursor = そのターンの読んだ所 / turn-refs = 走っているターンが受けた入力の参照 / waiting = 走っているターンの後に回す入力
   (WaitingInput の tuple)/ events = 層 3 の出来事(seq → 出来事 — seq は 0 から欠けずに続く。答えの文字の途中が 1 つずつ積もるので、
   頁は読む分の seq だけを引く)/ last-end = 最後のターンの終わり / stopped = 止めた / answer = 走っているターンの最後の答えの全文 /
   turn-usage = 走っているターンの呼びの分の和 / last-call = 最後の呼びの分 / window = codex が名乗った model の窓の大きさ。"
  (defn __init__ [self #^ str name #^ CodexSessionSpec spec thread-id lifecycle]
    (setv self.name name
          self.spec spec
          self.thread-id thread-id
          self.lifecycle lifecycle
          self.stopped False)
    (setv #^ (| CodexTurn None) self.turn None)
    (setv #^ int self.cursor -1)
    (setv #^ tuple self.turn-refs #())
    (setv #^ tuple self.waiting #())
    (setv #^ (get dict #(int object)) self.events {})
    (setv self.last-end None)
    (.reset-turn self))

  (defn reset-turn [self]
    "ターンごとの数え(答えの全文・usage・窓)を、新しいターンの始めに戻すため。"
    (setv self.answer ""
          self.turn-usage None
          self.last-call None
          self.window None)))

(defclass HeadlessCodexState []
  "handler の状態: session の名 → CodexHeadlessSession(composition root が 1 つ作って渡す)。"
  (defn __init__ [self]
    (setv self.sessions {})))


;; --- 純粋な写し ---------------------------------------------------------------------------------

(defk new-ref []
  {:pre [] :post [(: % str)] :tags {:context "headless-codex-adapter" :role "foundation"}}
  "呼び手が参照を名乗らない入力に、入力の行方の出来事で使う参照を振るため。"
  (str (uuid.uuid4)))

(defk compact-limit-of [autocompact]
  {:pre [(: autocompact (| AutocompactAuto AutocompactTokens None))] :post [(: % (| int None))]
   :tags {:context "headless-adapter" :role "judgment"}}
  "起動の圧縮の閾値を codex の宣言の token 数へ写すため: token 数の宣言はその数・auto と宣言なしは None(codex の既定 — 窓に合わせる)。"
  (match autocompact
    (AutocompactTokens) autocompact.tokens
    _ None))

(defk images-of [attachments]
  {:pre [(: attachments tuple)] :post [(: % tuple)] :tags {:context "headless-adapter" :role "judgment"}}
  "効果に添えた画像(InputImage の列)を、codex のターンの入力の画像へ写すため(利用者が貼った画像を prompt の一部として渡す)。"
  (tuple (gfor image attachments (CodexImage :mime image.mime :data-base64 image.data-base64))))

(defk spec-of [#^ HeadlessCodexConfig config #^ LaunchEffect request auth-json]
  {:pre [(: config HeadlessCodexConfig) (: request LaunchEffect) (: auth-json (| str None))] :post [(: % CodexSessionSpec)]
   :tags {:context "headless-adapter" :role "judgment"}}
  "LaunchEffect を層 2 の会話の宣言にするため。process の env = 家の env + session_env(資格を持てない上書き)。auth-json = 引き換えた
   借りた口座の auth.json の中身(None = 家の資格のまま — 層 2 がその process だけの家に置く)。許可の方針と sandbox は config から。"
  (assert-session-env-is-non-auth-overlay request.session-env :context "LaunchEffect.session_env (headless-codex-handler)")
  (val env (| (dict config.home.env) (dict (or request.session-env {}))))
  (assert-no-forbidden-agent-env env :context "headless-codex-handler の process の env")
  (<- limit (compact-limit-of request.autocompact))
  (CodexSessionSpec :home (CodexHome :env env :auth-json auth-json)
                    :cwd (str request.work-dir)
                    :model request.model
                    :approval-policy config.approval-policy
                    :sandbox config.sandbox
                    :effort request.effort
                    :auto-compact-token-limit limit))

(defk refuse-launch [#^ LaunchEffect request]
  {:pre [(: request LaunchEffect)] :post [(: % None)] :tags {:context "headless-adapter" :role "judgment"}}
  "この adapter が持たない起動の欄を、黙って捨てずに型で断るため(頭の註の「断る物」)。"
  (val asked #(#("LaunchEffect.mcp_tools" (bool request.mcp-tools))
               #("LaunchEffect.bare" request.bare)
               #("LaunchEffect.new_context_id" (isinstance request.new-context-id NamedContextId))
               #("LaunchEffect.resume_snapshot" (is-not request.resume-snapshot None))))
  (val refused (next (gfor #(capability present) asked :if present capability) None))
  (when (is-not refused None)
    (raise (AgentCapabilityUnsupportedError :capability refused :handler HANDLER-NAME)))
  None)

(defk redeemed-auth-json [#^ LaunchEffect request]
  {:pre [(: request LaunchEffect)] :post [(: % (| str None))] :tags {:context "headless-adapter" :role "judgment"}}
  "LaunchEffect.turn_credential_ref を、codex の process に置く借りた口座(auth.json の中身)へ引き換えるため。参照が無い・答えが家の
   資格なら None(家の資格で起こす)。引き換えられない・codex の形でない資格(claude の TurnCredential)は TurnCredentialUnavailableError
   で断る(session を起こさない)。答えは外側(参照を出した環境)が返す — 資格は LaunchEffect に載らない。断りの文には答えの型の名だけを
   書く(値は資格を運び得る)。"
  (val ref request.turn-credential-ref)
  (when (is ref None)
    (return None))
  (<- answer (RedeemTurnCredentialEffect :credential-ref ref))
  (match answer
    (CodexTurnCredential) answer.auth-json
    (HomeTurnCredential) None
    (TurnCredentialUnavailable :reason reason) (raise (TurnCredentialUnavailableError :credential-ref ref :reason reason))
    (TurnCredential) (raise (TurnCredentialUnavailableError :credential-ref ref
                                                            :reason "codex のターンに codex の形でない資格(TurnCredential)が答えられた"))
    _ (raise (AgentError (.format "ターンの資格 {} の引き換えの答えが閉語彙の外: {}" ref (. (type answer) __name__))))))

(defk added [left right]
  {:pre [(: left (| int None)) (: right (| int None))] :post [(: % (| int None))] :tags {:context "headless-adapter" :role "foundation"}}
  "token の数を足すため(両方とも名乗らなければ None — 0 を発明しない)。"
  (if (and (is left None) (is right None)) None (+ (or left 0) (or right 0))))

(defk call-usage-of [#^ TokenCount count]
  {:pre [(: count TokenCount)] :post [(: % (| AgentTurnUsage None))] :tags {:context "headless-adapter" :role "judgment"}}
  "codex の 1 つの呼びの token の数を層 3 の usage へ写すため(上の層が会話の今の context の大きさを読む材料)。入力の側は cache から
   読んだ分を除く(頭の註)。どの欄も名乗らなければ None。"
  (val fresh-input (if (is count.input-tokens None) None (- count.input-tokens (or count.cached-input-tokens 0))))
  (val usage (AgentTurnUsage :input-tokens fresh-input :output-tokens count.output-tokens :cache-read-tokens count.cached-input-tokens))
  (if (= usage (AgentTurnUsage)) None usage))

(defk summed [total #^ AgentTurnUsage usage]
  {:pre [(: total (| AgentTurnUsage None)) (: usage AgentTurnUsage)] :post [(: % AgentTurnUsage)]
   :tags {:context "headless-adapter" :role "foundation"}}
  "ターンの usage(呼びの分の和)に、呼び 1 つの分を足すため。"
  (if (is total None)
      usage
      (AgentTurnUsage :input-tokens (! (added total.input-tokens usage.input-tokens))
                      :output-tokens (! (added total.output-tokens usage.output-tokens))
                      :cache-read-tokens (! (added total.cache-read-tokens usage.cache-read-tokens)))))

(defk events-of [record #^ datetime at #^ int first-seq model]
  {:pre [(: record CodexLine) (: at datetime) (: first-seq int) (: model (| str None))] :post [(: % tuple)]
   :tags {:context "headless-adapter" :role "judgment"}}
  "層 2 の行の記録 1 つを層 3 の出来事(0 か 1 つ)へ写すため(頭の註の「行 → 出来事」— 語彙の外の行は空・生の行は上へ渡さない)。
   seq は first-seq。"
  (match record
    (TextDelta) #((AgentTextDeltaEvent :seq first-seq :at at :text record.text))
    (AgentMessageDone) (if record.text #((AgentTextEvent :seq first-seq :at at :text record.text)) #())
    ;; 考えの item の始まり — 考えの最初の差分より先に、上の層が「考えている」と分かる合図(claude の adapter と同じ)。
    (ItemStarted :item-type "reasoning") #((AgentThinkingStartedEvent :seq first-seq :at at))
    (ReasoningDelta) #((AgentThinkingDeltaEvent :seq first-seq :at at :text record.text))
    (TokenUsage) (do (<- usage (call-usage-of record.last))
                     (if (is usage None) #() #((AgentCallUsageEvent :seq first-seq :at at :usage usage :model model))))
    _ #()))

(defk absorb-record [#^ CodexHeadlessSession session record]
  {:pre [(: session CodexHeadlessSession) (: record CodexLine)] :post [(: % None)] :tags {:context "headless-adapter" :role "judgment"}}
  "ターンの終わりが運ぶ数え(最後の答えの全文・呼びの usage の和・最後の呼び・窓)を、行の記録 1 つで進めるため。"
  (match record
    (AgentMessageDone) (setv session.answer record.text)
    (TokenUsage) (do (<- usage (call-usage-of record.last))
                     (when (is-not usage None)
                       (<- total (summed session.turn-usage usage))
                       (setv session.turn-usage total
                             session.last-call usage))
                     (when (is-not record.model-context-window None)
                       (setv session.window record.model-context-window)))
    _ None)
  None)

(defk end-of [#^ CodexHeadlessSession session end]
  {:pre [(: session CodexHeadlessSession) (: end (| TurnEnded BackendLost))]
   :post [(: % (| AgentTurnCompleted AgentTurnFailed AgentTurnInterrupted AgentTurnLost))]
   :tags {:context "headless-adapter" :role "judgment"}}
  "層 2 のターンの終わりを層 3 のターンの終わりへ写すため(続きの身元 resume-from = thread の id・どの終わりも最後の呼びの usage と
   model と窓を運ぶ)。止めた session(stopped)の終わりは、閉じた process の終わりでも割り込みの終わり(cli_kept 偽)にする。"
  (val model session.spec.model)
  (val last-model (if (is session.last-call None) None model))
  (val windows (if (and (is-not model None) (is-not session.window None)) #((ModelWindow model session.window None)) #()))
  (val thread-id session.thread-id)
  (match end
    (TurnEnded :status TurnStatus.COMPLETED)
    (AgentTurnCompleted :result-text session.answer :input-refs session.turn-refs :resume-from thread-id :usage session.turn-usage
                        :last-call-usage session.last-call :last-call-model last-model :model-windows windows)
    (TurnEnded :status TurnStatus.INTERRUPTED)
    (AgentTurnInterrupted :cli-kept (not session.stopped) :resume-from thread-id
                          :last-call-usage session.last-call :last-call-model last-model :model-windows windows)
    (TurnEnded :status TurnStatus.FAILED)
    (AgentTurnFailed :detail (or end.error-message "codex のターンが失敗した(状態 failed)") :input-refs session.turn-refs
                     :resume-from thread-id :usage session.turn-usage
                     :last-call-usage session.last-call :last-call-model last-model :model-windows windows)
    ;; 終わりの行の状態が語彙の外(版で増えた綴り)か inProgress — 終わりは届いたので失敗の終わりとして名乗る(待ち続けない)。
    (TurnEnded)
    (AgentTurnFailed :detail (.format "codex のターンの終わりの状態が語彙の外: {!r}" end.status) :input-refs session.turn-refs
                     :resume-from thread-id :usage session.turn-usage
                     :last-call-usage session.last-call :last-call-model last-model :model-windows windows)
    (BackendLost)
    (if session.stopped
        (AgentTurnInterrupted :cli-kept False :resume-from thread-id
                              :last-call-usage session.last-call :last-call-model last-model :model-windows windows)
        (AgentTurnLost :detail end.detail :resume-from thread-id :exit-code end.exit-code :stderr-tail end.stderr-tail
                       :last-call-usage session.last-call :last-call-model last-model :model-windows windows))))

(defk start-refusal [outcome #^ CodexHeadlessSession session]
  {:pre [(: outcome (| ThreadUnknown TurnInFlight LaunchFailed RequestRefused)) (: session CodexHeadlessSession)]
   :post [(: % AgentError)] :tags {:context "headless-adapter" :role "judgment"}}
  "CodexStartTurn の断りを、上の層が型で見分ける層 3 の例外へ写すため(agora は続きの文脈が無い = resume-unavailable・別のターンが
   走っている = session-busy・ほかを launch-failed と読む)。"
  (match outcome
    (ThreadUnknown) (ResumeTargetNotFoundError :resume-from outcome.thread-id)
    (TurnInFlight) (TurnInFlightError :session-id session.name :context-id outcome.turn.thread-id)
    (LaunchFailed) (AgentLaunchError (.format "session {} のターンを起こせない(exit {}): {} {}" session.name outcome.exit-code
                                              outcome.detail outcome.stderr-tail)
                                     :exit-code outcome.exit-code :stderr-tail outcome.stderr-tail)
    (RequestRefused) (AgentLaunchError (.format "session {}: codex が {} を断った({}): {}" session.name outcome.method outcome.code
                                                outcome.message))))

(defk page-of [#^ CodexHeadlessSession session #^ int after-seq]
  {:pre [(: session CodexHeadlessSession) (: after-seq int)] :post [(: % AgentEventPage)]
   :tags {:context "headless-adapter" :role "judgment"}}
  "after-seq より後の出来事を頁にするため。end はターンが走っておらず待たせた入力も無い時だけ最後の終わり。"
  (val fresh (tuple (gfor seq (range (max 0 (+ after-seq 1)) (len session.events)) (get session.events seq))))
  (val idle (and (is session.turn None) (not session.waiting)))
  (AgentEventPage :events fresh :next-seq (if fresh (. (get fresh -1) seq) after-seq) :end (if idle session.last-end None)))

(defk status-of [#^ CodexHeadlessSession session]
  {:pre [(: session CodexHeadlessSession)] :post [(: % SessionStatus)] :tags {:context "headless-adapter" :role "judgment"}}
  "session の今の状態を L2 の見え方(SessionStatus)にするため(pid は読まない — ターンが走っているか・最後の終わりだけ)。"
  (val end session.last-end)
  (cond
    session.stopped SessionStatus.STOPPED
    (or (is-not session.turn None) session.waiting) SessionStatus.RUNNING
    (isinstance end #(AgentTurnFailed AgentTurnLost)) SessionStatus.FAILED
    (and (isinstance end AgentTurnCompleted) (= session.lifecycle AgentSessionLifecycle.RUN-TO-COMPLETION)) SessionStatus.DONE
    True SessionStatus.BLOCKED))


;; --- 層 2 との往復 --------------------------------------------------------------------------------

(defk append-event [#^ CodexHeadlessSession session #^ Callable build]
  {:pre [(: session CodexHeadlessSession) (: build Callable)] :post [(: % None)] :tags {:context "headless-adapter" :role "foundation"}}
  "出来事を 1 つ置くため。build = (seq at) → 出来事(呼び手がその型の欄で直接作る)。"
  (<- at (GetTime))
  (val seq (len session.events))
  (setv (get session.events seq) (build seq at))
  None)

(defk taken [#^ CodexHeadlessSession session #^ str ref]
  {:pre [(: session CodexHeadlessSession) (: ref str)] :post [(: % None)] :tags {:context "headless-adapter" :role "foundation"}}
  "層 2 が受けた入力を、上の層が「読まれた」と数える入力の行方(started)にし、走っているターンの入力に数えるため。"
  (setv session.turn-refs (+ session.turn-refs #(ref)))
  (<- (append-event session (fn [seq at] (AgentInputFateEvent :seq seq :at at :input-ref ref :state InputFateState.STARTED))))
  None)

(defk next-origin [#^ CodexHeadlessSession session]
  {:pre [(: session CodexHeadlessSession)] :post [(: % (| FreshThread ResumeThread))]
   :tags {:context "headless-adapter" :role "judgment"}}
  "session の次のターンの始まり方を 1 か所で決めるため: thread の id がまだ無い(1 度もターンを始めていない新しい会話)なら
   FreshThread、ほかはその thread の続き。"
  (if (is session.thread-id None)
      (FreshThread)
      (ResumeThread :thread-id session.thread-id)))

(defk start-turn [#^ CodexHeadlessSession session #^ str ref #^ CodexInput input]
  {:pre [(: session CodexHeadlessSession) (: ref str) (: input CodexInput)] :post [(: % CodexTurn)]
   :tags {:context "headless-adapter" :role "judgment"}}
  "次のターンを始めるため(process を起こすか使い回すかは層 2 の判断 — ここは続けるとだけ言う)。始めた入力は読まれた行方にする。
   断りは例外で示す(start-refusal)。"
  (<- origin (next-origin session))
  (<- outcome (CodexStartTurn origin session.spec input))
  (when (not (isinstance outcome TurnStarted))
    (<- error (start-refusal outcome session))
    (raise error))
  (setv session.thread-id outcome.turn.thread-id
        session.turn outcome.turn
        session.cursor -1
        session.turn-refs #())
  (.reset-turn session)
  (<- (taken session ref))
  outcome.turn)

(defk start-failure [#^ AgentError error #^ str ref thread-id]
  {:pre [(: error AgentError) (: ref str) (: thread-id str)] :post [(: % AgentTurnFailed)]
   :tags {:context "headless-adapter" :role "foundation"}}
  "待たせた入力のターンを始められなかった事を、上の層が読む失敗の終わりにするため(起動の失敗は process の終了 code と stderr の末尾も
   欄で運ぶ)。"
  (match error
    (AgentLaunchError)
    (AgentTurnFailed :detail (str error) :input-refs #(ref) :resume-from thread-id :exit-code error.exit-code
                     :stderr-tail error.stderr-tail)
    _ (AgentTurnFailed :detail (str error) :input-refs #(ref) :resume-from thread-id)))

(defk start-waiting [#^ CodexHeadlessSession session]
  {:pre [(: session CodexHeadlessSession)] :post [(: % None)] :tags {:context "headless-adapter" :role "judgment"}}
  "待たせた入力のターンを順に始めるため。始められなかった入力は失敗の終わり(start-failure)として出来事に置き、次へ進む。"
  (while (and session.waiting (is session.turn None) (not session.stopped))
    (val waiting (get session.waiting 0))
    (setv session.waiting (cut session.waiting 1 None))
    (var failure None)
    (try
      (<- (start-turn session waiting.ref waiting.input))
      (except [error AgentError]
        (:= failure (! (start-failure error waiting.ref session.thread-id)))))
    (when (is-not failure None)
      (<- (append-event session (fn [seq at] (AgentTurnEndEvent :seq seq :at at :end failure))))
      (setv session.last-end failure)))
  None)

(defk close-turn [#^ CodexHeadlessSession session #^ CodexTurn turn end]
  {:pre [(: session CodexHeadlessSession) (: turn CodexTurn) (: end (| TurnEnded BackendLost))] :post [(: % None)]
   :tags {:context "headless-adapter" :role "judgment"}}
  "層 2 のターン turn の終わりを出来事に置き、待たせた入力のターンを始めるため。時刻を読んで戻った後に、別の task が同じターンを
   既に閉じていれば何もしない。"
  (<- at (GetTime))
  (when (!= session.turn turn) (return None))
  (<- mapped (end-of session end))
  (val seq (len session.events))
  (setv (get session.events seq) (AgentTurnEndEvent :seq seq :at at :end mapped))
  (setv session.last-end mapped
        session.turn None)
  (<- (start-waiting session))
  None)

(defk pull [#^ CodexHeadlessSession session #^ float wait]
  {:pre [(: session CodexHeadlessSession) (: wait float)] :post [(: % None)] :tags {:context "headless-adapter" :role "judgment"}}
  "走っているターンの出来事を層 2 から 1 頁読み(wait 秒まで待つ)、層 3 の出来事へ写すため。"
  (when (is session.turn None) (return None))
  ;; 読むターンと位置を控える: 待つ間に同じ session の別の task(割り込み・入力を届ける)が先に読み進めたりターンを閉じたりできるので、
  ;; 戻った後は控えと照らし、既に写した行と既に閉じたターンを写し直さない(seq の重複と二重の終わりを作らない)。
  (val turn session.turn)
  (<- page (CodexReadTurnEvents turn session.cursor wait))
  (when (!= session.turn turn) (return None))
  (val read (if (isinstance page UnknownTurn)
                ;; 層 2 がこのターンを知らない(層 2 の handler が作り直された後など)= 終わりの行を読めずに失った。
                (TurnEventPage :events #() :next-seq session.cursor :end (BackendLost :detail "the agent runtime no longer knows this turn"))
                page))
  (<- at (GetTime))
  (for [event read.events :if (> event.seq session.cursor)]
    (<- (absorb-record session event.record))
    (<- mapped (events-of event.record at (len session.events) session.spec.model))
    (.update session.events (dfor made mapped made.seq made))
    (setv session.cursor event.seq))
  (when (is-not read.end None)
    (<- (close-turn session turn read.end)))
  None)

(defk read-events [#^ HeadlessCodexConfig config #^ CodexHeadlessSession session #^ int after-seq #^ float wait]
  {:pre [(: config HeadlessCodexConfig) (: session CodexHeadlessSession) (: after-seq int) (: wait float)]
   :post [(: % AgentEventPage)] :tags {:context "headless-adapter" :role "judgment"}}
  "after-seq より後の出来事を、新しい出来事が在るか・ターンが無くなるか・wait 秒が過ぎるまで待って返すため(層 2 は page-wait の
   刻みで読む — 層 2 が新しい行か終わりで起こす)。"
  (<- started (GetMonotonic))
  (while True
    (<- now (GetMonotonic))
    (val left (- wait (- now started)))
    (when (and (<= (len session.events) (+ after-seq 1)) (is-not session.turn None) (> left 0))
      (<- (pull session (min left config.page-wait))))
    (<- later (GetMonotonic))
    (val have-new (> (len session.events) (+ after-seq 1)))
    (when (or have-new (is session.turn None) (<= (- wait (- later started)) 0))
      (<- page (page-of session after-seq))
      (return page))))

(defk await-turn-end [#^ HeadlessCodexConfig config #^ CodexHeadlessSession session timeout]
  {:pre [(: config HeadlessCodexConfig) (: session CodexHeadlessSession) (: timeout (| float int None))]
   :post [(: % AwaitOutcome)] :tags {:context "headless-adapter" :role "judgment"}}
  "走っているターンの終わりを待ち、そのターンの結果を返すため(待たせた入力のターンは続けて走る)。ターンが無ければ最後の終わり。"
  (val mark (len session.events))
  (<- started (GetMonotonic))
  (while True
    (val ends (tuple (gfor seq (range mark (len session.events)) :setv event (get session.events seq)
                           :if (isinstance event AgentTurnEndEvent) event.end)))
    (when ends
      (return (outcome-of (get ends 0) session.lifecycle session.stopped)))
    (when (is session.turn None)
      (return (if (is session.last-end None)
                  (AwaitOutcome AwaitStatus.AWAITING-INPUT :continuable (not session.stopped))
                  (outcome-of session.last-end session.lifecycle session.stopped))))
    (<- now (GetMonotonic))
    (val elapsed (- now started))
    (when (and (is-not timeout None) (>= elapsed timeout))
      (return (AwaitOutcome AwaitStatus.TIMED-OUT)))
    (<- (pull session (if (is timeout None) config.page-wait (max 0.0 (min (- timeout elapsed) config.page-wait)))))))


;; --- 節の中身 -----------------------------------------------------------------------------------

(defk launch [#^ HeadlessCodexConfig config #^ HeadlessCodexState state #^ LaunchEffect request]
  {:pre [(: config HeadlessCodexConfig) (: state HeadlessCodexState) (: request LaunchEffect)] :post [(: % SessionHandle)]
   :tags {:context "headless-adapter" :role "judgment"}}
  "session を起こすため。prompt が在れば最初のターンを始める。resume_from は前の codex の thread の続き(codex が知らなければ
   ResumeTargetNotFoundError — prompt の無い起動は最初のターンで分かる)。"
  (val name request.session-name)
  (when (in name state.sessions)
    (raise (SessionAlreadyExistsError (.format "Session {} already exists" name))))
  (<- (refuse-launch request))
  ;; ターンの資格は起こす直前に引き換える(断りの検めを通った起動だけが資格を受ける)。
  (<- auth-json (redeemed-auth-json request))
  (<- spec (spec-of config request auth-json))
  (val session (CodexHeadlessSession name spec request.resume-from request.lifecycle))
  (when (is-not request.prompt None)
    (<- images (images-of request.attachments))
    (<- ref (new-ref))
    (<- (start-turn session ref (CodexInput :text request.prompt :images images))))
  (setv (get state.sessions name) session)
  (SessionHandle :session-id name))

(defk deliver [#^ CodexHeadlessSession session #^ str ref #^ CodexInput input mode]
  {:pre [(: session CodexHeadlessSession) (: ref str) (: input CodexInput) (: mode TurnInputMode)] :post [(: % None)]
   :tags {:context "headless-adapter" :role "judgment"}}
  "入力を届けるため: NEXT_TURN = ターンが走っていなければ始め、走っていれば待たせる / INJECT = 走っているターンに足す(codex が受けた
   入力は読まれた行方にする)。"
  (when session.stopped
    (raise (SessionNotFoundError (.format "session {} は止めた" session.name))))
  (<- (pull session 0.0))
  (cond
    (= mode TurnInputMode.INJECT)
    (do
      (when (is session.turn None) (raise (NoTurnInFlightError :session-id session.name)))
      (<- outcome (CodexSteerTurn session.turn input))
      (match outcome
        (Steered) (<- (taken session ref))
        (NoTurnInFlight) (raise (NoTurnInFlightError :session-id session.name))
        _ (raise (AgentError (.format "session {}: 走っているターンに入力を足せない: {!r}" session.name outcome)))))
    (is session.turn None) (<- (start-turn session ref input))
    True (setv session.waiting (+ session.waiting #((WaitingInput :ref ref :input input)))))
  None)

(defk interrupt [#^ CodexHeadlessSession session]
  {:pre [(: session CodexHeadlessSession)] :post [(: % bool)] :tags {:context "headless-adapter" :role "judgment"}}
  "走っているターンだけを止めるため(session と thread は残る)。答え = 止めるターンが在ったか。"
  (<- (pull session 0.0))
  (when (is session.turn None) (return False))
  (<- outcome (CodexInterruptTurn session.turn))
  (isinstance outcome InterruptRequested))

(defk stop [#^ CodexHeadlessSession session #^ str reason]
  {:pre [(: session CodexHeadlessSession) (: reason str)] :post [(: % None)] :tags {:context "headless-adapter" :role "judgment"}}
  "session を止めるため(冪等)。走っていたターンは cli_kept 偽の AgentTurnInterrupted で終わり、待たせた入力は discarded の行方で閉じる。"
  (when session.stopped (return None))
  (when (is-not session.thread-id None)
    (<- closed (CodexCloseSession session.thread-id reason))
    ;; 層 2 が降ろし切れなかった時は止めた印を付けない — 次の Stop が層 2 に降ろし直しを頼む。
    (when (isinstance closed ProcessStillAlive)
      (raise (AgentError (.format "session {} を止められない: {}" session.name closed.detail)))))
  ;; 印を付けてから終わりを読む(止めた session は待たせた入力のターンを始めない・閉じた process の終わりを割り込みと読む)。
  (setv session.stopped True)
  (<- (pull session 0.0))
  (for [waiting session.waiting]
    (<- (append-event session (fn [seq at] (AgentInputFateEvent :seq seq :at at :input-ref waiting.ref
                                                                :state InputFateState.DISCARDED)))))
  (setv session.waiting #() session.turn None)
  None)

(defk warm [#^ CodexHeadlessSession session]
  {:pre [(: session CodexHeadlessSession)] :post [(: % bool)] :tags {:context "headless-adapter" :role "judgment"}}
  "前もっての起動の頼みに答えるため: codex の層 2 は入力の前に thread を開く口を持たないので、起こさず偽を答える(止めた session は
   SessionNotFoundError)。"
  (when session.stopped
    (raise (SessionNotFoundError (.format "session {} は止めた" session.name))))
  False)

(defk refuse-keys [#^ bool literal #^ bool enter]
  {:pre [(: literal bool) (: enter bool)] :post [(: % None)] :tags {:context "headless-adapter" :role "judgment"}}
  "画面の鍵(literal でない文字・Enter なし)を、画面の無い adapter が型で断るため。"
  (when (or (not literal) (not enter))
    (raise (AgentCapabilityUnsupportedError :capability "SendEffect keys (literal=False / enter=False)" :handler HANDLER-NAME)))
  None)


;; --- handler -----------------------------------------------------------------------------------

;; 引数に残す理由: config は composition root の宣言(codex の家と許可の方針)、state は session の持ち主で、どちらも root が 1 つ作って
;; 渡す(claude の adapter headless-claude-handler と同じ)。
(defhandler headless-codex-handler [config state]
  (LaunchEffect [agent-type]
    :when (= agent-type AgentType.CODEX)
    (<- handle (launch config state effect))
    (resume handle))

  (ExportContextEffect [agent-type]
    :when (= agent-type AgentType.CODEX)
    (raise (AgentCapabilityUnsupportedError :capability "ExportContextEffect(codex)" :handler HANDLER-NAME)))

  (SendEffect [handle message enter literal]
    :when (in handle.session-id state.sessions)
    (<- (refuse-keys literal enter))
    (<- ref (new-ref))
    (<- (deliver (get state.sessions handle.session-id) ref (CodexInput :text message) TurnInputMode.NEXT-TURN))
    (resume None))

  (FollowUpEffect [handle message mode input-ref attachments]
    :when (in handle.session-id state.sessions)
    (<- images (images-of attachments))
    (<- fresh (new-ref))
    (<- (deliver (get state.sessions handle.session-id) (or input-ref fresh) (CodexInput :text message :images images) mode))
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
    (<- (pull (get state.sessions handle.session-id) 0.0))
    (<- status (status-of (get state.sessions handle.session-id)))
    (resume (Observation :status status)))

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
