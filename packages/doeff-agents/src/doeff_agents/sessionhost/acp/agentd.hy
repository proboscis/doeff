;;; agentd の program — 参加(join)・agent-job の受け・記録(turn-record)・実況(TurnDelta)・
;;; 借用(custody)を effect の列として書く(段 2・agora-redesign #19 / #20)。
;;;
;;; ここは要求を並べるだけで、判断は judgment.hy の純関数(自分に結ばれた job か・
;;; capture の是非・待ちの長さ)、I/O は handlers.py(実)/ fake.py(test)。handler の
;;; 選択は runtime.py の composition root ちょうど。1 tick = agentd-tick で、状態
;;; (AgentdState)は値として出入りする(loop は runtime.py が回す)。
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
  AcpGet
  AcpGetRow
  AcpPutStatus
  AcpRow
  AcpStreamPush
  AcpWatchSse
  AgentdSettings
  AgentdState
  ClockNowMs
  Conflict
  CustodyLeaseBorrow
  CustodyLeaseRevoke
  DeltaBatch
  FsCanonicalPath
  FsFileSize
  FsWritePrivateText
  InFlightJob
  JobOutcome
  LaunchPlan
  LeaseGrant
  LeaseRefused
  LogLine
  MESSAGE-KIND
  MetricLine
  NODE-KIND
  Pushed
  Refused
  SessionCapture
  SessionGet
  SessionLaunch
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
  capture-verdict
  charter-with-grant
  condition-of
  deltas-of
  due
  ended-status-of
  frame-lines-of
  in-flight-ids
  inputs-of
  job-outcome-of
  job-rows-bound-to
  launch-plan-of
  lease-renew-due
  message-bodies-of
  node-row-named
  node-status-with-lease
  pane-frame
  resume-params-of
  resync-due
  running-status-of
  status-frame
  status-object-of
  transcript-path-of
  turn-record-ended-status
  turn-record-spec-of
  wait-seconds-for
  with-job
  without-job])


;; ---------------------------------------------------------------------------
;; 参加(join): Node の行は読むだけ・lease と観測を書く
;; ---------------------------------------------------------------------------

(defk join-tick [settings state now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: now-ms int)]
   :post [(: % AgentdState)]}
  "自分の Node の行を読み、在れば status.lease と status.observations を書く。無ければ
   1 行 log して次の周期に読み直す(行を作るのは acp-scheduling — 段 2 では lane 2c の
   道具 register-node)。"
  (<- rows tuple (AcpGet :kind NODE-KIND))
  (<- node (| AcpRow None) (node-row-named rows settings.node-name))
  (setv next (replace state :last-heartbeat-ms now-ms))
  (if (is node None)
      (do
        (when (not state.node-missing-logged)
          (<- (LogLine :text (+ f"agentd: node row {settings.node-name !r} is not in ACP "
                                     "yet (created by acp-scheduling / register-node); "
                                     "re-reading each heartbeat"))))
        (replace next :node-missing-logged True))
      (do
        (<- status dict (node-status-with-lease node settings now-ms (len state.jobs)))
        (<- outcome (| Written Conflict Refused) (AcpPutStatus :row node :status status))
        (when (isinstance outcome Refused)
          (<- (LogLine :text f"agentd: node lease refused ({outcome.status}): {outcome.error}")))
        (replace next :node-missing-logged False))))


;; ---------------------------------------------------------------------------
;; agent-job の受け: Bound → Running → session を起こす → inputs を送る
;; ---------------------------------------------------------------------------

(defk end-job-now [settings row reason-type reason now-ms]
  {:pre [(: settings AgentdSettings) (: row AcpRow) (: reason-type str) (: reason str) (: now-ms int)]
   :post [(: % bool)]}
  "起こせなかった job を Ended + condition で閉じる(fresh な行の generation で書く)。
   戻り = Ended の書きが着地したか。"
  (<- fresh (| AcpRow None) (AcpGetRow :key row.key))
  (setv target (if (is fresh None) row fresh))
  (<- status dict (status-object-of target))
  (<- condition dict (condition-of reason-type reason))
  (<- ended dict (ended-status-of status None #(condition)))
  (<- outcome (| Written Conflict Refused) (AcpPutStatus :row target :status ended))
  (when (not (isinstance outcome Written))
    (<- (LogLine :text f"agentd: could not end job {row.resource-id}: {outcome}")))
  (<- (LogLine :text f"agentd: job {row.resource-id} ended without a session: {reason-type} — {reason}"))
  (isinstance outcome Written))


(defk borrow-for [settings plan job-id]
  {:pre [(: settings AgentdSettings) (: plan LaunchPlan) (: job-id str)]
   :post [(: % tuple)]}
  "binding.account が在れば預かり所から借り、charter を組み直す。戻り =
   #(charter lease-grant-or-None refusal-or-None)。"
  (if (or (is plan.lease-kind None) (is plan.account None))
      #(plan.charter None None)
      (do
        (<- lease (| LeaseGrant LeaseRefused)
            (CustodyLeaseBorrow :kind plan.lease-kind :account plan.account
                                :purpose f"agent-job {job-id}"))
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


(defk claim-job [settings state row now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: row AcpRow) (: now-ms int)]
   :post [(: % AgentdState)]}
  "1 つの Bound の行を受ける: Running + sessionHandle を CAS で書き(負けたら次の list へ)、
   札を借り、charter で session を起こし(predecessor が在れば resume)、inputs の本文を送り、
   所要を計器に 1 行、turn-record を作り、status frame を 1 つ押す。"
  (<- plan LaunchPlan (launch-plan-of row))
  (setv job-id row.resource-id)
  (setv session-id (.get plan.charter "session_id"))
  (if (not (isinstance session-id str))
      (do
        (<- (end-job-now settings row "LaunchFailed" "charter carries no session_id" now-ms))
        state)
      (do
        (<- running dict (running-status-of row session-id settings.principal))
        (<- claimed (| Written Conflict Refused) (AcpPutStatus :row row :status running))
        (if (not (isinstance claimed Written))
            (do
              (<- (LogLine :text f"agentd: claim of job {job-id} did not land ({claimed}); will re-list"))
              state)
            (do
              (<- borrowed tuple (borrow-for settings plan job-id))
              (setv charter (get borrowed 0))
              (setv lease (get borrowed 1))
              (setv refusal (get borrowed 2))
              (if (is-not refusal None)
                  (do
                    (<- (end-job-now settings row "CredentialUnavailable"
                                            f"custody refused ({refusal.status}): {refusal.error}" now-ms))
                    state)
                  (do
                    (if (is plan.predecessor None)
                        (<- outcome (| SessionView SessionRefused) (SessionLaunch :params charter))
                        (do
                          (<- params dict (resume-params-of plan.predecessor charter))
                          (<- outcome (| SessionView SessionRefused) (SessionResume :params params))))
                    (if (isinstance outcome SessionRefused)
                        (do
                          (when (is-not lease None)
                            (<- (CustodyLeaseRevoke :lease-id lease.lease-id)))
                          (<- (end-job-now settings row "LaunchFailed" outcome.error now-ms))
                          state)
                        (do
                          (<- launched AgentdState
                              (after-launch settings state row plan charter outcome lease now-ms))
                          launched)))))))))


(defk after-launch [settings state row plan charter view lease now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: row AcpRow) (: plan LaunchPlan)
         (: charter dict) (: view SessionView) (: lease (| LeaseGrant None)) (: now-ms int)]
   :post [(: % AgentdState)]}
  "session が起きた後: inputs を送る・計器・turn-record・status frame・in-flight に登記。"
  (setv job-id row.resource-id)
  (setv pending [])
  (<- inputs tuple (inputs-of row))
  (when inputs
    (<- messages tuple (AcpGet :kind MESSAGE-KIND))
    (<- pair tuple (message-bodies-of messages inputs))
    (for [body (get pair 0)]
      (<- (SessionSend :session-id view.session-id :text body)))
    (when (get pair 1)
      (<- condition dict (condition-of "InputUnavailable"
                                       (+ "messages not found: " (.join ", " (get pair 1)))))
      (.append pending condition)))
  (<- sent-ms int (ClockNowMs))
  (<- (MetricLine :fields {"metric" "agent-job-to-send"
                                  "agentJobId" job-id
                                  "sessionId" view.session-id
                                  "createdAtMs" row.created-at-ms
                                  "sentAtMs" sent-ms
                                  "ms" (- sent-ms row.created-at-ms)}))
  ;; resume の手番は前の手番の行を entries に混ぜない — 今の file の大きさが始まり
  (<- canon str (FsCanonicalPath :path view.work-dir))
  (<- path (| str None) (transcript-path-of view canon))
  (setv start-offset 0)
  (when (and (is-not plan.predecessor None) (is-not path None))
    (<- size int (FsFileSize :path path))
    (setv start-offset size))
  (setv job (InFlightJob
              :job-key row.key
              :job-namespace row.namespace
              :job-id job-id
              :subject (str (.get row.spec "subject" job-id))
              :session-id view.session-id
              :agent-type view.agent-type
              :node settings.node-name
              :profile plan.profile
              :model plan.model
              :started-ms now-ms
              :start-offset start-offset
              :transcript-offset start-offset
              :delta-seq 0
              :lease-id (if (is lease None) None lease.lease-id)
              :lease-kind (if (is lease None) None lease.kind)
              :lease-account (if (is lease None) None plan.account)
              :lease-hold-ms (if (is lease None) None lease.hold-expires-at-ms)
              :capturing False
              :last-frame-ms 0
              :last-probe-ms 0
              :pending-conditions (tuple pending)))
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
               :capturing (= verdict "continue")))


(defk stream-transcript [settings job path now-ms]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: path str) (: now-ms int)]
   :post [(: % InFlightJob)]}
  "transcript の追記を読み、TurnDelta(text / tool_use / tool_result / usage)を押す。"
  (<- chunk TranscriptChunk (SessionTranscript :path path :offset job.transcript-offset))
  (<- batch DeltaBatch (deltas-of job.agent-type chunk.text job.job-id job.delta-seq now-ms))
  (setv next (replace job :transcript-offset chunk.offset :delta-seq batch.next-seq))
  (if batch.frames
      (do
        (<- subscribers (| int None) (push-frames settings next batch.frames))
        (<- verdict str (capture-verdict subscribers))
        (replace next :capturing (= verdict "continue")))
      next))


(defk capture-frame [settings job now-ms]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: now-ms int)]
   :post [(: % InFlightJob)]}
  "pane の断面を 1 枚押す(購読者が居る間だけ呼ばれる)。"
  (<- text str (SessionCapture :session-id job.session-id :lines settings.frame-lines))
  (<- lines tuple (frame-lines-of text))
  (<- frame dict (pane-frame job.job-id job.delta-seq now-ms lines))
  (<- subscribers (| int None) (push-frames settings job #(frame)))
  (<- verdict str (capture-verdict subscribers))
  (replace job :delta-seq (+ job.delta-seq 1)
               :last-frame-ms now-ms
               :capturing (= verdict "continue")))


;; ---------------------------------------------------------------------------
;; 記録(turn-record)と手番の終わり
;; ---------------------------------------------------------------------------

(defk finalize-job [settings state job view path now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: job InFlightJob)
         (: view SessionView) (: path (| str None)) (: now-ms int)]
   :post [(: % AgentdState)]}
  "手番の終わり: transcript から entries と usage を組み turn-record を ended に、agent-job を
   Ended(result / conditions)に、status frame ended を押し、札を返す。"
  (<- outcome JobOutcome (job-outcome-of view))
  (if (is path None)
      (setv batch (DeltaBatch :frames #() :entries #() :usage None :next-seq 0 :model None))
      (do
        (<- chunk TranscriptChunk (SessionTranscript :path path :offset job.start-offset))
        (<- whole DeltaBatch (deltas-of job.agent-type chunk.text job.job-id 0 now-ms))
        (setv batch whole)))
  ;; turn-record → ended
  (<- record (| AcpRow None)
      (AcpGetRow :key f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:{job.job-id}"))
  (if (is record None)
      (<- (LogLine :text f"agentd: turn-record for job {job.job-id} is missing at turn end"))
      (do
        (<- record-status dict (status-object-of record))
        (<- ended-record dict (turn-record-ended-status record-status batch.usage batch.entries))
        (<- wrote (| Written Conflict Refused) (AcpPutStatus :row record :status ended-record))
        (when (not (isinstance wrote Written))
          (<- (LogLine :text f"agentd: turn-record of job {job.job-id} not ended ({wrote})")))))
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
                                  "ms" (- now-ms job.started-ms)}))
  (<- next AgentdState (without-job state job.job-id))
  next)


(defk observe-job [settings state job now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: job InFlightJob) (: now-ms int)]
   :post [(: % AgentdState)]}
  "走っている 1 つの job の拍: 器の眺め → 実況(transcript / frame)→ 札の延長 → 終端なら記録。"
  (<- view (| SessionView None) (SessionGet :session-id job.session-id))
  (if (is view None)
      (do
        (<- (LogLine :text f"agentd: session {job.session-id} of job {job.job-id} is not registered any more"))
        (<- fresh (| AcpRow None) (AcpGetRow :key job.job-key))
        (when (is-not fresh None)
          (<- (end-job-now settings fresh "SessionFailed" "session row vanished" now-ms)))
        (<- dropped AgentdState (without-job state job.job-id))
        dropped)
      (do
        (<- canon str (FsCanonicalPath :path view.work-dir))
        (<- path (| str None) (transcript-path-of view canon))
        (setv current job)
        (when (is-not path None)
          (<- current InFlightJob (stream-transcript settings current path now-ms)))
        (<- frame-due bool (due current.last-frame-ms now-ms settings.frame-interval-seconds))
        (when (and current.capturing frame-due)
          (<- current InFlightJob (capture-frame settings current now-ms)))
        (<- probe-due bool (due current.last-probe-ms now-ms settings.subscriber-recheck-seconds))
        (when (and (not current.capturing) probe-due)
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
        (<- outcome JobOutcome (job-outcome-of view))
        (if outcome.ended
            (<- next AgentdState (finalize-job settings state current view path now-ms))
            (<- next AgentdState (with-job state current)))
        next)))


;; ---------------------------------------------------------------------------
;; 1 tick
;; ---------------------------------------------------------------------------

(defk receive-bound-jobs [settings state now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: now-ms int)]
   :post [(: % AgentdState)]}
  "list で自分に結ばれた Bound の行を読み、まだ受けていない行を行の順に受ける。"
  (<- rows tuple (AcpGet :kind AGENT-JOB-KIND))
  (<- bound tuple (job-rows-bound-to rows settings.node-name))
  (<- known set (in-flight-ids state))
  (setv current state)
  (for [row bound]
    (when (not-in row.resource-id known)
      (<- current AgentdState (claim-job settings current row now-ms))))
  (replace current :last-resync-ms now-ms))


(defk agentd-tick [settings state]
  {:pre [(: settings AgentdSettings) (: state AgentdState)]
   :post [(: % AgentdState)]}
  "1 拍: watch を待つ → 参加の heartbeat → 結ばれた job の受け → 走っている job の観測。"
  (<- wait float (wait-seconds-for state settings))
  (<- signal WatchAdvance (AcpWatchSse :since state.since :wait-seconds wait))
  (<- now-ms int (ClockNowMs))
  (setv current (replace state :since signal.sequence))
  (<- heartbeat bool (due current.last-heartbeat-ms now-ms settings.node-heartbeat-seconds))
  (when heartbeat
    (<- current AgentdState (join-tick settings current now-ms)))
  (<- resync bool (resync-due signal current now-ms settings))
  (when resync
    (<- current AgentdState (receive-bound-jobs settings current now-ms)))
  (for [job (list current.jobs)]
    (<- current AgentdState (observe-job settings current job now-ms)))
  current)
