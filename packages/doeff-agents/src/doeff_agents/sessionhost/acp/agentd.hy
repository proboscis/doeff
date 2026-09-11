;;; agentd の program — 参加(join)・agent-job の受け・記録(turn-record)・実況(TurnDelta)・
;;; 借用(custody)を effect の列として書く(段 2・agora-redesign #19 / #20)。
;;;
;;; ここは要求を並べるだけで、判断は judgment.hy の純関数(自分に結ばれた job か・自分が持つ
;;; Running か・その次の 1 手・capture の是非・待ちの長さ)、I/O は handlers.py(実)/
;;; fake.py(test)。handler の選択は runtime.py の composition root ちょうど。1 tick =
;;; agentd-tick で、状態(AgentdState)は値として出入りする(loop は runtime.py が回す)。
;;;
;;; job の進みは行から導く(ADR-DOE-AGENTS-012 R7): memory の InFlightJob は cache で、
;;; 再起動で消えても agent-job の行(phase Running・sessionHandle)と器の現況(session.get)
;;; から組み直す(recover-job)。次の 1 手は memory の有無に依らず judgment.job-step-of の
;;; 1 点(器が無い → fail-missing / 終端 → record-end / 走っている → observe)。
;;; capture の gone は終端の合図で例外ではない(R8)。tick の縁(heartbeat・受け・job ごと)は
;;; 互いの I/O の失敗で止まらない(R9)。
;;;
;;; 温かい session(R10・設計 17.4): session は会話の資源で job は手番。会話 → 生きている
;;; session の対応は行(agent-job の subject と sessionHandle)から導き、Bound の job の起こし方は
;;; judgment.next-arm-for-job の 1 点(launch | send | resume | defer)。同じ会話の次の手番は
;;; launch せず session.send(awaiting)だけ。手番の終わりは host の monitor が刻む
;;; turn_ended_at(policy.hy の turn-end の連言)を job-step-of が読む(turn-end)— session は
;;; 生かしたまま turn-record を ended・job を Ended にする。idle の寿命は
;;; AgentdSettings.session_idle_ttl_seconds(heartbeat の拍に sessions-to-retire で片付ける)。
;;;
;;; headless の 1 手番目(R16): headless の器は 1 手番 = 1 prompt で、走っている手番の途中に次の本文を
;;; 積めない。起こす腕(launch / resume)は inputs の郵便を charter の prompt に畳んで起こし(判定は
;;; judgment.first-turn-carries-inputs・畳みは first-turn-prompt-of の 1 点)、after-start は send を
;;; 撃たない。tui は今日どおり launch の後に send。郵便の読み(mail-of)は腕を選んだ後・起こす前。
;;;
;;; 書く欄は契約の writers どおり: agent-job の phase / sessionHandle / result / conditions、
;;; node の status.lease / status.observations、turn-record の create と status。
;;; binding は書かない・Node の行は作らない(scheduling の欄と動詞)。

(require doeff-hy.macros [defk <-])

(import dataclasses [replace])

(import doeff_agents.sessionhost.acp.effects [
  AGENT-JOB-KIND
  AGORA-KINDS-NAMESPACE
  AcpCreate
  AcpEventWindow
  AcpGet
  AcpGetRow
  AcpPutStatus
  AcpRow
  AcpStreamPush
  AcpWatchSse
  AgentdSettings
  AgentdState
  CaptureFrame
  CaptureGone
  ClockNowMs
  Conflict
  CustodyLeaseBorrow
  CustodyLeaseRevoke
  DeltaBatch
  EVENT-WINDOW-LIMIT
  EventWindow
  FsCanonicalPath
  FsFileSize
  FsWritePrivateText
  IO-FAILURES
  InFlightJob
  JOB-STEP-FAIL-MISSING
  JOB-STEP-OBSERVE
  JOB-STEP-RECORD-END
  JOB-STEP-TURN-END
  JobOutcome
  LIFECYCLE-MULTI-TURN
  LIST-MODE-FULL
  LIST-MODE-NONE
  LIST-MODE-WINDOW
  LaunchPlan
  LeaseGrant
  LeaseRefused
  LogLine
  MESSAGE-KIND
  MetricLine
  MintId
  NEXT-ARM-DEFER
  NEXT-ARM-RESUME
  NEXT-ARM-SEND
  NODE-KIND
  Pushed
  Refused
  INTERRUPT-ARM-INTERRUPT
  STREAM-SOURCE-EVENTS
  SessionCapture
  SessionCleanup
  SessionEvents
  SessionGet
  SessionInterrupt
  SessionLaunch
  SessionList
  SessionRefused
  SessionResume
  SessionSend
  SessionTranscript
  SessionView
  TURN-RECORD-KIND
  TranscriptChunk
  WatchAdvance
  Written])
(import doeff_agents.sessionhost.acp.judgment [
  birth-ms-of
  births-of-rows
  births-with
  capture-verdict
  charter-with-first-turn
  charter-with-grant
  charter-with-session-id
  cleanup-after-end
  condition-of
  deltas-of
  due
  ended-status-of
  first-turn-carries-inputs
  frame-lines-of
  in-flight-ids
  in-flight-job-of
  inputs-of
  interrupt-arm-for
  interrupted-status-of
  job-outcome-of
  job-rows-bound-to
  job-rows-running-on
  job-step-of
  launch-plan-of
  lease-renew-due
  list-mode-for
  merge-rows
  message-bodies-of
  message-key-of
  next-arm-for-job
  node-row-named
  node-status-with-lease
  pane-frame
  recovered-arm-of
  resume-params-of
  rows-of-kind
  running-status-of
  session-alive
  session-id-of-handle
  session-observations-of
  sessions-to-retire
  status-frame
  status-object-of
  stream-source-of
  turn-record-ended-status
  turn-record-key-of
  turn-record-spec-of
  wait-seconds-for
  warm-candidate-of
  with-job
  withdrawn-rows-of
  without-job])


;; ---------------------------------------------------------------------------
;; 参加(join): Node の行は読むだけ・lease と観測を書く
;; ---------------------------------------------------------------------------

(defk retire-sessions [session-ids reason]
  {:pre [(: session-ids tuple) (: reason str)]
   :post [(: % int)]}
  "温かい session を片付ける(session.cleanup — pane を消し、非終端なら stopped)。
   戻り = host が受けた数。"
  (setv accepted-count 0)
  (for [session-id session-ids]
    (<- accepted bool (SessionCleanup :session-id session-id))
    (when accepted
      (setv accepted-count (+ accepted-count 1)))
    (<- (LogLine :text (+ f"agentd: session {session-id} cleaned up ({reason})"
                               (if accepted "" " — host did not accept")))))
  accepted-count)


(defk join-tick [settings state now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: now-ms int)]
   :post [(: % AgentdState)]}
  "heartbeat の腕: 自分の Node の行を読み、在れば status.lease と status.observations
   (行と器から導いた会話の session の一覧)を書き、idle が TTL を過ぎた温かい session を
   片付ける。無ければ 1 行 log して次の周期に読み直し(行を作るのは acp-scheduling — 段 2
   では lane 2c の道具 register-node)、node が退いた以上は温かい session を残さない。"
  (<- rows tuple (AcpGet :kind NODE-KIND))
  (<- node (| AcpRow None) (node-row-named rows settings.node-name))
  (<- views tuple (SessionList :lifecycle LIFECYCLE-MULTI-TURN))
  (setv next (replace state :last-heartbeat-ms now-ms))
  (if (is node None)
      (do
        (when (not state.node-missing-logged)
          (<- (LogLine :text (+ f"agentd: node row {settings.node-name !r} is not in ACP "
                                     "yet (created by acp-scheduling / register-node); "
                                     "re-reading each heartbeat"))))
        (setv alive [])
        (for [view views]
          (<- live bool (session-alive view))
          (when live
            (.append alive view.session-id)))
        (<- (retire-sessions (tuple alive) "node is not in ACP"))
        (replace next :node-missing-logged True))
      (do
        ;; 片付けてから観測を書く(観測 = 片付けた後の現況)。
        (<- expired tuple (sessions-to-retire views now-ms settings.session-idle-ttl-seconds))
        (<- (retire-sessions expired f"idle past {settings.session-idle-ttl-seconds}s"))
        (setv kept (tuple (lfor view views :if (not-in view.session-id expired) view)))
        (<- observations list
            (session-observations-of kept state.rows settings.node-name settings.principal))
        (<- status dict (node-status-with-lease node settings now-ms observations))
        (<- outcome (| Written Conflict Refused) (AcpPutStatus :row node :status status))
        (when (isinstance outcome Refused)
          (<- (LogLine :text f"agentd: node lease refused ({outcome.status}): {outcome.error}")))
        (replace next :node-missing-logged False))))


;; ---------------------------------------------------------------------------
;; agent-job の受け: Bound → Running → session を起こす → inputs を送る
;; ---------------------------------------------------------------------------

(defk end-job-now [settings row reason-type reason pending now-ms]
  {:pre [(: settings AgentdSettings) (: row AcpRow) (: reason-type str) (: reason str)
         (: pending tuple) (: now-ms int)]
   :post [(: % bool)]}
  "session の結末なしに job を Ended + condition で閉じる(fresh な行の generation で書く)。
   pending = 手番の途中で判った事実(inputs の欠け等)を condition に添える。
   戻り = Ended の書きが着地したか。"
  (<- fresh (| AcpRow None) (AcpGetRow :key row.key))
  (setv target (if (is fresh None) row fresh))
  (<- status dict (status-object-of target))
  (<- condition dict (condition-of reason-type reason))
  (<- ended dict (ended-status-of status None (+ pending #(condition))))
  (<- outcome (| Written Conflict Refused) (AcpPutStatus :row target :status ended))
  (when (not (isinstance outcome Written))
    (<- (LogLine :text f"agentd: could not end job {row.resource-id}: {outcome}")))
  (<- (LogLine :text f"agentd: job {row.resource-id} ended without a session: {reason-type} — {reason}"))
  (isinstance outcome Written))


(defk borrow-for [settings plan purpose]
  {:pre [(: settings AgentdSettings) (: plan LaunchPlan) (: purpose str)]
   :post [(: % tuple)]}
  "binding.account が在れば預かり所から借り、charter を組み直す。戻り =
   #(charter lease-grant-or-None refusal-or-None)。"
  (if (or (is plan.lease-kind None) (is plan.account None))
      #(plan.charter None None)
      (do
        (<- lease (| LeaseGrant LeaseRefused)
            (CustodyLeaseBorrow :kind plan.lease-kind :account plan.account :purpose purpose))
        (if (isinstance lease LeaseRefused)
            #(plan.charter None lease)
            (do
              (<- rebuilt tuple (charter-with-grant plan.charter plan.lease-kind plan.account
                                                    lease.access-token lease.auth-json
                                                    settings.homes-root))
              (setv auth-file (get rebuilt 1))
              (when (and (is-not auth-file None) (is-not lease.auth-json None))
                (<- (FsWritePrivateText :path auth-file :text lease.auth-json)))
              #((get rebuilt 0) lease None))))))


(defk incarnate [plan charter arm view]
  {:pre [(: plan LaunchPlan) (: charter dict) (: arm str) (: view (| SessionView None))]
   :post [(: % (| SessionView SessionRefused))]}
  "起こし方の腕を器に写す: send = 既存の session(眺めはそのまま)/ resume = predecessor から
   cold に起こし直す / launch = charter で起こす。"
  (cond
    (and (= arm NEXT-ARM-SEND) (isinstance view SessionView)) view
    (and (= arm NEXT-ARM-RESUME) (is-not plan.predecessor None))
    (do
      (<- params dict (resume-params-of plan.predecessor charter))
      (<- resumed (| SessionView SessionRefused) (SessionResume :params params))
      resumed)
    True
    (do
      (<- launched (| SessionView SessionRefused) (SessionLaunch :params charter))
      launched)))


(defk claim-job [settings state rows row previously-deferred now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: rows tuple) (: row AcpRow)
         (: previously-deferred tuple) (: now-ms int)]
   :post [(: % AgentdState)]}
  "1 つの Bound の行を受ける: 会話の session の候補(行から)を器で眺め、起こし方を
   next-arm-for-job の 1 点で決める。defer(会話の session が手番の途中)なら claim せず次の
   list へ。それ以外は Running + sessionHandle を CAS で書き(負けたら次の list へ)、札を
   借り、inputs の郵便の本文を届ける — headless の起こす腕(launch / resume)は charter の prompt に
   畳んで起こし(1 手番 = 1 prompt・判定は first-turn-carries-inputs の 1 点)、それ以外(tui の
   起こす腕・温かい send)は起こした / 既存の session に send — 所要を計器に 1 行、turn-record を
   作り、status frame を 1 つ押す。起こす session の id は agentd が鋳造する(MintId — charter の
   id は読まない)。"
  (<- plan LaunchPlan (launch-plan-of row))
  (setv job-id row.resource-id)
  (setv subject (str (.get row.spec "subject" job-id)))
  (<- candidate (| str None)
      (warm-candidate-of plan rows subject settings.node-name settings.principal))
  (setv view None)
  (when (is-not candidate None)
    (<- looked (| SessionView None) (SessionGet :session-id candidate))
    (setv view looked))
  (<- arm str (next-arm-for-job plan view))
  (setv session-id None)
  (if (and (= arm NEXT-ARM-SEND) (isinstance view SessionView))
      (setv session-id view.session-id)
      (when (!= arm NEXT-ARM-DEFER)
        (<- minted str (MintId))
        (<- charter dict (charter-with-session-id plan.charter minted))
        (setv plan (replace plan :charter charter))
        (setv session-id minted)))
  (cond
    (= arm NEXT-ARM-DEFER)
    (do
      (when (not-in job-id previously-deferred)
        (<- (LogLine :text (+ f"agentd: claim of job {job-id} deferred — session {candidate} of "
                                   f"conversation {subject} is mid-turn; re-listing"))))
      (replace state :deferred (+ state.deferred #(job-id))))
    (not (isinstance session-id str))
    (do
      (<- (end-job-now settings row "LaunchFailed" "no session id could be minted" #() now-ms))
      state)
    True
    (do
      (<- running dict (running-status-of row session-id settings.principal))
      (<- claimed (| Written Conflict Refused) (AcpPutStatus :row row :status running))
      (if (not (isinstance claimed Written))
          (do
            (<- (LogLine :text f"agentd: claim of job {job-id} did not land ({claimed}); will re-list"))
            state)
          (do
            (<- mail tuple (mail-of row))
            (<- folds bool (first-turn-carries-inputs settings.backend-kind arm))
            (when folds
              (<- folded dict (charter-with-first-turn plan.charter (get mail 0)))
              (setv plan (replace plan :charter folded)))
            (setv to-send (if folds #() (get mail 0)))
            (<- borrowed tuple (borrow-for settings plan f"agent-job {job-id}"))
            (setv charter (get borrowed 0))
            (setv lease (get borrowed 1))
            (setv refusal (get borrowed 2))
            (if (is-not refusal None)
                (do
                  (<- (end-job-now settings row "CredentialUnavailable"
                                          f"custody refused ({refusal.status}): {refusal.error}"
                                          #() now-ms))
                  state)
                (do
                  (<- outcome (| SessionView SessionRefused) (incarnate plan charter arm view))
                  (if (isinstance outcome SessionRefused)
                      (do
                        (when (is-not lease None)
                          (<- (CustodyLeaseRevoke :lease-id lease.lease-id)))
                        (<- (end-job-now settings row "LaunchFailed" outcome.error #() now-ms))
                        state)
                      (do
                        (<- started AgentdState
                            (after-start settings state row plan outcome lease arm now-ms
                                         to-send (get mail 1)))
                        started)))))))))


(defk mail-of [row]
  {:pre [(: row AcpRow)]
   :post [(: % tuple)]}
  "inputs の郵便の本文と見つからなかった id: #(bodies missing)。本文は鍵で 1 行ずつ読む
   (郵便の全量 list を watch の拍ごとに撃たない — R14)。"
  (<- inputs tuple (inputs-of row))
  (setv found [])
  (for [input-id inputs]
    (<- key str (message-key-of input-id))
    (<- message (| AcpRow None) (AcpGetRow :key key))
    (when (is-not message None)
      (.append found message)))
  (<- pair tuple (message-bodies-of (tuple found) inputs))
  pair)


(defk start-offset-of [view arm]
  {:pre [(: view SessionView) (: arm str)]
   :post [(: % tuple)]}
  "手番の始まりの実況の材料(transcript / events)の offset と path: send(温かい)と resume の
   手番は前の手番の行を entries に混ぜない — 今の file の大きさが始まり。戻り =
   #(path-or-None offset)。"
  (<- canon str (FsCanonicalPath :path view.work-dir))
  (<- source tuple (stream-source-of view canon))
  (setv path (get source 1))
  (setv start-offset 0)
  (when (and (in arm #{NEXT-ARM-SEND NEXT-ARM-RESUME}) (is-not path None))
    (<- size int (FsFileSize :path path))
    (setv start-offset size))
  #(path start-offset))


(defk after-start [settings state row plan view lease arm now-ms bodies missing]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: row AcpRow) (: plan LaunchPlan)
         (: view SessionView) (: lease (| LeaseGrant None)) (: arm str) (: now-ms int)
         (: bodies tuple) (: missing tuple)]
   :post [(: % AgentdState)]}
  "手番の始まり(session を起こした後・温かい session ならそのまま): 郵便の本文(bodies — headless の
   起こす腕では空: 本文は起こした prompt に畳んである)を送る(awaiting — 送った本文は owed)・
   見つからなかった id(missing)は condition InputUnavailable・計器・turn-record・status frame・
   in-flight に登記。手番の始まりの offset は送る前の file の大きさ(send / resume)。"
  (setv job-id row.resource-id)
  (setv pending [])
  (<- start tuple (start-offset-of view arm))
  (for [body bodies]
    (<- (SessionSend :session-id view.session-id :text body :awaiting True)))
  (when missing
    (<- condition dict (condition-of "InputUnavailable"
                                     (+ "messages not found: " (.join ", " missing))))
    (.append pending condition))
  (<- sent-ms int (ClockNowMs))
  ;; 始点 = 行の生まれの着地(generation 1 の image の landed_at・ns 精度 — 秒の粒度の
  ;; createdAt ではない。判断は birth-ms-of の 1 点・欄が無ければ今日の値)。
  (<- born-ms int (birth-ms-of row state.births))
  (<- (MetricLine :fields {"metric" "agent-job-to-send"
                                  "agentJobId" job-id
                                  "sessionId" view.session-id
                                  "arm" arm
                                  "createdAtMs" born-ms
                                  "sentAtMs" sent-ms
                                  "ms" (- sent-ms born-ms)}))
  (<- job InFlightJob
      (in-flight-job-of row plan view settings.node-name now-ms sent-ms (get start 1) lease
                        (tuple pending)))
  (<- spec dict (turn-record-spec-of job))
  (<- created (| Written Conflict Refused)
      (AcpCreate :namespace AGORA-KINDS-NAMESPACE :kind TURN-RECORD-KIND :resource-id job-id :spec spec))
  (when (not (isinstance created Written))
    (<- (LogLine :text f"agentd: turn-record for job {job-id} was not created ({created})")))
  (<- job InFlightJob (probe-subscribers settings job sent-ms "running"))
  (<- next AgentdState (with-job state job))
  next)


;; ---------------------------------------------------------------------------
;; 実況(TurnDelta): transcript の追記と pane の frame
;; ---------------------------------------------------------------------------

(defk push-frames [settings job frames]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: frames tuple)]
   :post [(: % (| int None))]}
  "frame を中継へ押し、応答の購読者の数を返す(断られたら None)。"
  (<- pushed (| Pushed Refused)
      (AcpStreamPush :owner settings.principal :name job.session-id :frames frames))
  (if (isinstance pushed Pushed)
      pushed.subscribers
      (do
        (<- (LogLine :text f"agentd: stream push for {job.session-id} refused ({pushed.status}): {pushed.error}"))
        None)))


(defk probe-subscribers [settings job now-ms phase]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: now-ms int) (: phase str)]
   :post [(: % InFlightJob)]}
  "status frame を 1 つ押して購読者の数を読み直し、capture の是非を決める(純関数 1 点)。"
  (<- frame dict (status-frame job.job-id job.delta-seq now-ms phase))
  (<- subscribers (| int None) (push-frames settings job #(frame)))
  (<- verdict str (capture-verdict subscribers))
  (replace job :delta-seq (+ job.delta-seq 1)
               :last-probe-ms now-ms
               :capturing (and (not job.stream-gone) (= verdict "continue"))))


(defk read-stream [source path offset]
  {:pre [(: source str) (: path str) (: offset int)]
   :post [(: % TranscriptChunk)]}
  "実況の材料の追記を offset から読む(events = headless の stdout の行の file・transcript =
   tui の transcript)。どちらも完全な行だけ。"
  (if (= source STREAM-SOURCE-EVENTS)
      (do (<- events TranscriptChunk (SessionEvents :path path :offset offset))
          events)
      (do (<- lines TranscriptChunk (SessionTranscript :path path :offset offset))
          lines)))


(defk stream-records [settings job source path now-ms]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: source str) (: path str)
         (: now-ms int)]
   :post [(: % InFlightJob)]}
  "実況の材料の追記を読み、TurnDelta(text / tool_use / tool_result / usage)を押す
   (events は text の delta が 1 行ずつ・判断は純関数 deltas-of / events-to-deltas)。"
  (<- chunk TranscriptChunk (read-stream source path job.transcript-offset))
  (<- batch DeltaBatch (deltas-of job.agent-type source chunk.text job.job-id job.delta-seq now-ms))
  (setv next (replace job :transcript-offset chunk.offset :delta-seq batch.next-seq))
  (if batch.frames
      (do
        (<- subscribers (| int None) (push-frames settings next batch.frames))
        (<- verdict str (capture-verdict subscribers))
        (replace next :capturing (and (not next.stream-gone) (= verdict "continue"))))
      next))


(defk capture-frame [settings job now-ms]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: now-ms int)]
   :post [(: % InFlightJob)]}
  "pane の断面を 1 枚押す(購読者が居る間だけ呼ばれる)。答えが gone(pane も server も無い)
   なら実況の終わり — 例外ではない(R8): capture を止め、器の終端を待って記録の腕へ。"
  (<- outcome (| CaptureFrame CaptureGone)
      (SessionCapture :session-id job.session-id :lines settings.frame-lines))
  (if (isinstance outcome CaptureGone)
      (do
        (<- (LogLine :text (+ f"agentd: stream of job {job.job-id} is gone ({outcome.reason}); "
                                   "waiting for the session to end")))
        (replace job :capturing False :stream-gone True :last-frame-ms now-ms))
      (do
        (<- lines tuple (frame-lines-of outcome.text))
        (<- frame dict (pane-frame job.job-id job.delta-seq now-ms lines))
        (<- subscribers (| int None) (push-frames settings job #(frame)))
        (<- verdict str (capture-verdict subscribers))
        (replace job :delta-seq (+ job.delta-seq 1)
                     :last-frame-ms now-ms
                     :capturing (= verdict "continue")))))


(defk stream-job [settings job view now-ms]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: view SessionView) (: now-ms int)]
   :post [(: % InFlightJob)]}
  "走っている 1 つの job の実況の拍: 材料(transcript / events)の追記 → frame(tui で
   購読者が居れば)→ 購読の読み直し(止まっていれば)→ 札の延長。実況が終わった
   (stream-gone)job は frame も読み直しも撃たない。headless(events)の器に pane は
   無いので frame の capture は撃たない(実況は events の行そのもの)。"
  (<- canon str (FsCanonicalPath :path view.work-dir))
  (<- source tuple (stream-source-of view canon))
  (setv path (get source 1))
  (setv current job)
  (when (is-not path None)
    (<- current InFlightJob (stream-records settings current (get source 0) path now-ms)))
  (setv frames-possible (!= (get source 0) STREAM-SOURCE-EVENTS))
  (<- frame-due bool (due current.last-frame-ms now-ms settings.frame-interval-seconds))
  (when (and frames-possible current.capturing frame-due)
    (<- current InFlightJob (capture-frame settings current now-ms)))
  (<- probe-due bool (due current.last-probe-ms now-ms settings.subscriber-recheck-seconds))
  (when (and frames-possible (not current.capturing) (not current.stream-gone) probe-due)
    (<- current InFlightJob (probe-subscribers settings current now-ms "running")))
  (<- renew bool (lease-renew-due current now-ms settings))
  (when (and renew (is-not current.lease-kind None) (is-not current.lease-account None))
    (<- lease (| LeaseGrant LeaseRefused)
        (CustodyLeaseBorrow :kind current.lease-kind :account current.lease-account
                            :purpose f"agent-job {current.job-id} (renew)"))
    (if (isinstance lease LeaseGrant)
        (setv current (replace current :lease-id lease.lease-id
                                       :lease-hold-ms lease.hold-expires-at-ms))
        (<- (LogLine :text f"agentd: lease renew for job {current.job-id} refused ({lease.status}): {lease.error}"))))
  current)


;; ---------------------------------------------------------------------------
;; 記録(turn-record)と手番の終わり
;; ---------------------------------------------------------------------------

(defk end-turn-record [job-id usage entries]
  {:pre [(: job-id str) (: usage (| dict None)) (: entries tuple)]
   :post [(: % bool)]}
  "turn-record を ended に(usage・entries)。戻り = 行が在って書けたか(無ければ False —
   受けた直後に落ちた job には記録が無いのが普通なので、ここでは log しない)。"
  (<- key str (turn-record-key-of job-id))
  (<- record (| AcpRow None) (AcpGetRow :key key))
  (if (is record None)
      False
      (do
        (<- record-status dict (status-object-of record))
        (<- ended-record dict (turn-record-ended-status record-status usage entries))
        (<- wrote (| Written Conflict Refused) (AcpPutStatus :row record :status ended-record))
        (when (not (isinstance wrote Written))
          (<- (LogLine :text f"agentd: turn-record of job {job-id} not ended ({wrote})")))
        (isinstance wrote Written))))


(defk turn-batch-of [job source path now-ms]
  {:pre [(: job InFlightJob) (: source (| str None)) (: path (| str None)) (: now-ms int)]
   :post [(: % DeltaBatch)]}
  "手番の始まりから今までの材料(transcript / events)を読み直し、記録の entries と usage を
   組む(frame は押さない — 実況は拍ごとに押した)。材料が無ければ空。"
  (if (or (is path None) (is source None))
      (DeltaBatch :frames #() :entries #() :usage None :next-seq 0 :model None)
      (do
        (<- chunk TranscriptChunk (read-stream source path job.start-offset))
        (<- whole DeltaBatch (deltas-of job.agent-type source chunk.text job.job-id 0 now-ms))
        whole)))


(defk finalize-job [settings state job view source path step now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: job InFlightJob)
         (: view SessionView) (: source (| str None)) (: path (| str None)) (: step str)
         (: now-ms int)]
   :post [(: % AgentdState)]}
  "手番の終わり(記録の腕): transcript から entries と usage を組み turn-record を ended に、
   agent-job を Ended(result / conditions)に、status frame ended を押し、札を返す。
   turn-end(温かい session の手番の終わり)は session を生かしたまま。record-end(器が終端)
   で器が multi_turn なら、host の掃き取りの対象外なので agentd が片付ける。"
  (<- outcome JobOutcome (job-outcome-of view))
  (<- batch DeltaBatch (turn-batch-of job source path now-ms))
  ;; turn-record → ended
  (<- recorded bool (end-turn-record job.job-id batch.usage batch.entries))
  (when (not recorded)
    (<- (LogLine :text f"agentd: turn-record for job {job.job-id} is missing at turn end")))
  ;; agent-job → Ended
  (<- fresh (| AcpRow None) (AcpGetRow :key job.job-key))
  (if (is fresh None)
      (<- (LogLine :text f"agentd: agent-job {job.job-id} vanished before Ended"))
      (do
        (<- job-status dict (status-object-of fresh))
        (<- ended dict (ended-status-of job-status outcome.result
                                        (+ job.pending-conditions outcome.conditions)))
        (<- wrote-job (| Written Conflict Refused) (AcpPutStatus :row fresh :status ended))
        (when (not (isinstance wrote-job Written))
          (<- (LogLine :text f"agentd: agent-job {job.job-id} not ended ({wrote-job})")))))
  ;; 実況の終わりの印
  (<- frame dict (status-frame job.job-id job.delta-seq now-ms "ended"))
  (<- (push-frames settings job #(frame)))
  ;; 札を返す
  (when (is-not job.lease-id None)
    (<- (CustodyLeaseRevoke :lease-id job.lease-id)))
  (<- (MetricLine :fields {"metric" "agent-job-turn"
                                  "agentJobId" job.job-id
                                  "sessionId" job.session-id
                                  "status" view.status
                                  "step" step
                                  "ms" (- now-ms job.started-ms)}))
  (<- retire bool (cleanup-after-end view))
  (when (and (= step JOB-STEP-RECORD-END) retire)
    (<- (retire-sessions #(job.session-id) f"session {view.status} at the end of job {job.job-id}")))
  (<- next AgentdState (without-job state job.job-id))
  next)


(defk fail-missing-arm [settings job-key job-id pending lease-id now-ms]
  {:pre [(: settings AgentdSettings) (: job-key str) (: job-id str) (: pending tuple)
         (: lease-id (| str None)) (: now-ms int)]
   :post [(: % bool)]}
  "器に session が無い job の腕: 記録が在れば ended に、job は SessionFailed で Ended、
   借りていた札は返す。戻り = Ended の書きが着地したか。"
  (<- (end-turn-record job-id None #()))
  (<- fresh (| AcpRow None) (AcpGetRow :key job-key))
  (setv landed False)
  (if (is fresh None)
      (<- (LogLine :text f"agentd: agent-job {job-id} vanished before Ended"))
      (<- landed bool (end-job-now settings fresh "SessionFailed"
                                   "session is not registered in the host" pending now-ms)))
  (when (is-not lease-id None)
    (<- (CustodyLeaseRevoke :lease-id lease-id)))
  landed)


(defk settle-known [settings state job view step now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: job InFlightJob)
         (: view (| SessionView None)) (: step str) (: now-ms int)]
   :post [(: % AgentdState)]}
  "job-step-of の答えを腕に写す: fail-missing → 記録と SessionFailed で閉じる /
   record-end・turn-end → 記録の腕(finalize)/ observe → memory に置く(観測は次の拍)。"
  (cond
    (= step JOB-STEP-FAIL-MISSING)
    (do
      (<- (fail-missing-arm settings job.job-key job.job-id job.pending-conditions job.lease-id now-ms))
      (<- dropped AgentdState (without-job state job.job-id))
      dropped)
    (and (in step #{JOB-STEP-RECORD-END JOB-STEP-TURN-END}) (isinstance view SessionView))
    (do
      (<- canon str (FsCanonicalPath :path view.work-dir))
      (<- source tuple (stream-source-of view canon))
      (<- finished AgentdState
          (finalize-job settings state job view (get source 0) (get source 1) step now-ms))
      finished)
    True
    (do
      (<- kept AgentdState (with-job state job))
      kept)))


(defk progressed-of [job view]
  {:pre [(: job InFlightJob) (: view SessionView)]
   :post [(: % bool)]}
  "この手番の記録が進んだか(送った本文が届いて手番が始まった証拠)。記録の path を引けない
   器(材料の欠け)は進みを読めないので True(host の判定だけを信じる)。"
  (<- canon str (FsCanonicalPath :path view.work-dir))
  (<- source tuple (stream-source-of view canon))
  (or (is (get source 1) None) (> job.transcript-offset job.start-offset)))


(defk observe-job [settings state job now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: job InFlightJob) (: now-ms int)]
   :post [(: % AgentdState)]}
  "走っている 1 つの job の拍: 器の眺め → 次の 1 手(純関数 1 点)。observe なら実況を回し、
   その拍で実況が終わった(capture が gone)なら器を読み直して同じ拍で腕を決める。
   record-end / turn-end / fail-missing は実況を撃たずに記録の腕へ(片付いた pane を
   capture しない)。"
  (<- view (| SessionView None) (SessionGet :session-id job.session-id))
  (setv progressed True)
  (when (isinstance view SessionView)
    (<- moved bool (progressed-of job view))
    (setv progressed moved))
  (<- step str (job-step-of view job.turn-floor-ms progressed))
  (if (and (= step JOB-STEP-OBSERVE) (isinstance view SessionView))
      (do
        (<- current InFlightJob (stream-job settings job view now-ms))
        (if (and current.stream-gone (not job.stream-gone))
            (do
              (<- again (| SessionView None) (SessionGet :session-id job.session-id))
              (<- step-again str (job-step-of again job.turn-floor-ms progressed))
              (<- settled AgentdState (settle-known settings state current again step-again now-ms))
              settled)
            (do
              (<- kept AgentdState (with-job state current))
              kept)))
      (do
        (<- settled AgentdState (settle-known settings state job view step now-ms))
        settled)))


;; ---------------------------------------------------------------------------
;; 自分の Running の拾い直し(再起動後・memory に無い行)
;; ---------------------------------------------------------------------------

(defk recover-job [settings state row now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: row AcpRow) (: now-ms int)]
   :post [(: % AgentdState)]}
  "自分が持つ Running の行(memory に無い)の続きを行と器の現況から決める(R7): 器が無ければ
   記録と SessionFailed で閉じる / 終端なら記録の腕だけ / 走っていれば札を借り直して
   InFlightJob を行から組み、観測を続ける。launch も send もし直さない。"
  (<- session-id (| str None) (session-id-of-handle row))
  (if (is session-id None)
      (do
        (<- (fail-missing-arm settings row.key row.resource-id #() None now-ms))
        state)
      (do
        (<- view (| SessionView None) (SessionGet :session-id session-id))
        ;; 拾い直しは記録の進みを知らない(下限 = 行の createdAt・進み = host の判定だけ)。
        (<- step str (job-step-of view row.created-at-ms True))
        (if (not (isinstance view SessionView))
            (do
              (<- (fail-missing-arm settings row.key row.resource-id #() None now-ms))
              state)
            (do
              (<- plan LaunchPlan (launch-plan-of row))
              (setv lease None)
              (when (= step JOB-STEP-OBSERVE)
                (<- borrowed tuple (borrow-for settings plan f"agent-job {row.resource-id} (recovered)"))
                (setv lease (get borrowed 1))
                (setv refusal (get borrowed 2))
                (when (is-not refusal None)
                  (<- (LogLine :text (+ f"agentd: lease for recovered job {row.resource-id} refused "
                                             f"({refusal.status}): {refusal.error}; observing without it")))))
              (<- recovered-arm str (recovered-arm-of plan))
              (<- start tuple (start-offset-of view recovered-arm))
              (<- job InFlightJob
                  (in-flight-job-of row plan view settings.node-name row.created-at-ms
                                    row.created-at-ms (get start 1) lease #()))
              (<- (LogLine :text f"agentd: recovered running job {row.resource-id} from its row ({step})"))
              (<- settled AgentdState (settle-known settings state job view step now-ms))
              settled)))))


;; ---------------------------------------------------------------------------
;; 1 tick
;; ---------------------------------------------------------------------------

(defk interrupt-job [settings state job row now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: job InFlightJob) (: row AcpRow)
         (: now-ms int)]
   :post [(: % AgentdState)]}
  "取り下げられた自分の走っている job の腕(agora-redesign #37): 手番の途中なら
   session.interrupt(判断は interrupt-arm-for の 1 点 — headless = SIGINT / turn/interrupt・
   tmux = Escape)、記録の腕(ここまでの entries と usage で turn-record を ended)、agent-job に
   condition Interrupted(phase は書かない — Withdrawn のまま・書き手は作った側)、status frame
   ended、札を返し、観測をやめる。session は残す(温かい — 次の手番は send)。"
  (<- view (| SessionView None) (SessionGet :session-id job.session-id))
  (<- arm str (interrupt-arm-for job view))
  (when (= arm INTERRUPT-ARM-INTERRUPT)
    (<- (SessionInterrupt :session-id job.session-id)))
  (setv source None)
  (setv path None)
  (when (isinstance view SessionView)
    (<- canon str (FsCanonicalPath :path view.work-dir))
    (<- found tuple (stream-source-of view canon))
    (setv source (get found 0))
    (setv path (get found 1)))
  (<- batch DeltaBatch (turn-batch-of job source path now-ms))
  (<- (end-turn-record job.job-id batch.usage batch.entries))
  (<- fresh (| AcpRow None) (AcpGetRow :key row.key))
  (setv target (if (is fresh None) row fresh))
  (<- status dict (status-object-of target))
  (<- interrupted dict (interrupted-status-of status))
  (<- wrote (| Written Conflict Refused) (AcpPutStatus :row target :status interrupted))
  (when (not (isinstance wrote Written))
    (<- (LogLine :text f"agentd: Interrupted condition of job {job.job-id} not written ({wrote})")))
  (<- frame dict (status-frame job.job-id job.delta-seq now-ms "ended"))
  (<- (push-frames settings job #(frame)))
  (when (is-not job.lease-id None)
    (<- (CustodyLeaseRevoke :lease-id job.lease-id)))
  (<- (LogLine :text (+ f"agentd: job {job.job-id} withdrawn ({arm}); turn-record ended, "
                             f"session {job.session-id} kept")))
  (<- dropped AgentdState (without-job state job.job-id))
  dropped)


(defk withdraw-jobs [settings state rows now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: rows tuple) (: now-ms int)]
   :post [(: % AgentdState)]}
  "取り下げ(phase Withdrawn — 書き手は作った側)の行のうち自分が観測している job を
   interrupt-job の腕へ。処理した job の id を memory に置いて同じ行に撃ち直さない。
   session は片付けない(取り下げは中断の合図 — 温かい session の寿命は sessions-to-retire)。
   agent-job の phase は書かない。"
  (<- withdrawn tuple (withdrawn-rows-of rows settings.node-name settings.principal))
  (setv current state)
  (for [row withdrawn]
    (setv job-id row.resource-id)
    (when (not-in job-id current.retired)
      (for [job (list current.jobs)]
        (when (= job.job-id job-id)
          (<- interrupted AgentdState (interrupt-job settings current job row now-ms))
          (setv current interrupted)))
      (setv current (replace current :retired (+ current.retired #(job-id))))))
  current)


(defk refresh-rows [state mode]
  {:pre [(: state AgentdState) (: mode str)]
   :post [(: % AgentdState)]}
  "知っている agent-job の行の cache を読み直す: full = 全量 list で置き換える / window =
   watch の since から続く event-window の post-image で差し替える(窓が読めなければ全量 list
   に落ちる・窓が尽きるまで続けて読む)。生まれの着地の時刻(generation 1 の image)は表に足す。"
  (if (= mode LIST-MODE-WINDOW)
      (do
        (setv after state.last-window-seq)
        (setv changed [])
        (setv retired [])
        (setv born [])
        (setv complete True)
        (setv exhausted False)
        (while (and complete (not exhausted))
          (<- window EventWindow (AcpEventWindow :after after :limit EVENT-WINDOW-LIMIT))
          (setv complete window.complete)
          (when complete
            (.extend changed window.rows)
            (.extend retired window.retired)
            (.extend born window.births)
            (setv exhausted (or window.exhausted (= window.through after)))
            (setv after window.through)))
        (if complete
            (do
              (<- job-rows tuple (rows-of-kind (tuple changed) AGENT-JOB-KIND))
              (<- merged tuple (merge-rows state.rows job-rows (tuple retired)))
              (<- births tuple (births-with state.births (tuple born)))
              (replace state :rows merged :births births :last-window-seq after))
            (do
              (<- listed tuple (AcpGet :kind AGENT-JOB-KIND))
              (<- pairs tuple (births-of-rows listed))
              (<- births tuple (births-with state.births pairs))
              (replace state :rows listed :births births :last-window-seq state.since))))
      (do
        (<- listed tuple (AcpGet :kind AGENT-JOB-KIND))
        (<- pairs tuple (births-of-rows listed))
        (<- births tuple (births-with state.births pairs))
        (replace state :rows listed :births births :last-window-seq state.since))))


(defk receive-bound-jobs [settings state mode now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: mode str) (: now-ms int)]
   :post [(: % AgentdState)]}
  "行の cache を読み直し(mode = full | window — judgment.list-mode-for の 1 点)、自分に結ばれた
   Bound の行と自分が持つ Running の行のうち、まだ memory に無い行を行の順に受ける(Bound =
   claim・Running = 行からの拾い直し)。取り下げられた行は先に割り込みの腕へ。claim を
   持ち越した job(defer)は拍ごとに読み直す。"
  (<- refreshed AgentdState (refresh-rows state mode))
  (setv rows refreshed.rows)
  (<- withdrawn-handled AgentdState (withdraw-jobs settings refreshed rows now-ms))
  (<- bound tuple (job-rows-bound-to rows settings.node-name))
  (<- running tuple (job-rows-running-on rows settings.node-name settings.principal))
  (<- known set (in-flight-ids withdrawn-handled))
  (setv previously-deferred withdrawn-handled.deferred)
  (setv current (replace withdrawn-handled :deferred #()))
  (for [row bound]
    (when (not-in row.resource-id known)
      (<- current AgentdState (claim-job settings current rows row previously-deferred now-ms))))
  (for [row running]
    (when (not-in row.resource-id known)
      (<- current AgentdState (recover-job settings current row now-ms))))
  (replace current :last-resync-ms now-ms))


(defk agentd-tick [settings state]
  {:pre [(: settings AgentdSettings) (: state AgentdState)]
   :post [(: % AgentdState)]}
  "1 拍: watch を待つ → 参加の heartbeat → 結ばれた job の受け → 走っている job の観測。
   3 つの腕は互いの I/O の失敗で止まらない(R9): 失敗は log して次の周期 / 次の拍へ持ち越す
   (heartbeat と受けは周期の刻印を進めて洪水を避ける)。I/O より広い例外(bug)は捕まえない。"
  (<- wait float (wait-seconds-for state settings))
  (<- signal WatchAdvance (AcpWatchSse :since state.since :wait-seconds wait))
  (<- now-ms int (ClockNowMs))
  (setv #^ AgentdState current (replace state :since signal.sequence))
  (<- heartbeat bool (due current.last-heartbeat-ms now-ms settings.node-heartbeat-seconds))
  (when heartbeat
    (try
      (<- joined AgentdState (join-tick settings current now-ms))
      (setv current joined)
      (except [e IO-FAILURES]
        (<- (LogLine :text f"agentd: heartbeat failed: {(. (type e) __name__)}: {e}"))
        (setv current (replace current :last-heartbeat-ms now-ms)))))
  (<- mode str (list-mode-for signal current now-ms settings))
  (when (!= mode LIST-MODE-NONE)
    (try
      (<- received AgentdState (receive-bound-jobs settings current mode now-ms))
      (setv current received)
      (except [e IO-FAILURES]
        (<- (LogLine :text f"agentd: receive failed: {(. (type e) __name__)}: {e}"))
        (setv current (replace current :last-resync-ms now-ms)))))
  (for [job (list current.jobs)]
    (try
      (<- observed AgentdState (observe-job settings current job now-ms))
      (setv current observed)
      (except [e IO-FAILURES]
        (<- (LogLine :text f"agentd: job {job.job-id} tick failed: {(. (type e) __name__)}: {e}")))))
  current)
