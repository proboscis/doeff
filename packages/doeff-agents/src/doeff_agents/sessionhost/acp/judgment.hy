;;; agentd の純粋な判断(defk の退化形 — bind ゼロ・I/O の import なし)。
;;;
;;; agentd は判断を持たない(設計 第 12.10 節・bounded context F)。ここに在るのは
;;;   * 「自分に結ばれた job か」の純関数 1 点(bound-to-me)— job を選ぶ唯一の判定。
;;;     選択も優先も無い(該当する行は行の順にすべて受ける)。
;;;   * 「自分が持つ Running か」(running-on-me)と、その行の次の 1 手(job-step-of:
;;;     器の現況 → observe | record-end | fail-missing)— job の進みは process の memory ではなく
;;;     行と器から毎拍導く(ADR-DOE-AGENTS-012 R7)。memory の InFlightJob は cache で、
;;;     再起動で消えても行から組み直せる(in-flight-job-of の 1 点)。
;;;   * 行の欄の写し(charter・inputs・affinity → 起こし方 / status の書き換え / node の
;;;     lease の欄)— 判断ではなく契約の綴りの変換。
;;;   * transcript の行 → TurnDelta の frame・本文(契約 record-service eventIn)・turn-record の entries
;;;     (契約 docs/contracts/turn-delta.json / agora-kinds.json の欄へ写す)。段 9f lane 9f-4: ACP の entry は
;;;     **見出しの閉じた欄**(TurnEntryHeadline — seq・at・kind・toolName・toolUseId・bytes・sha256・isError)で
;;;     本文を持たない。見出しを導く点は headline-of-body の 1 つ、JSON への写しは entry-json-of の 1 つ。
;;;     bytes / sha256 は service が冪等の判断に使う本文の同一性と同じ計算(record-body-bytes-of)。
;;;   * 温かい session(R10): 会話 → 生きている session の対応は行(agent-job の subject と
;;;     sessionHandle)から導き、Bound の job の起こし方は next-arm-for-job(launch | send |
;;;     resume | defer)の 1 点、手番の終わりは job-step-of の turn-end(host が刻んだ
;;;     turn_ended_at × 手番の始まりの下限 × 記録の進み)、idle の寿命は sessions-to-retire。
;;;   * 会話の引き継ぎ(段 8q・R20): session の会話・手番・家は起こす時に launch_attribution へ刻み
;;;     (session-attribution-of)、器の眺めから読む(attribution-of-view)— 回収される agent-job の行から
;;;     導かない。cache(温かい send / --resume)を保つのは同じ機体 ∧ 同じ家(account・binding・model の組 —
;;;     段 9o lane 9o-3)の時だけで、家か機体が違えば
;;;     cache の失効を受け入れ、ACP の記録を最初の本文に畳んで新しい session を起こす(operator 決定 #54・
;;;     rehydrate-history-of・上限は AgentdSettings の 1 点)。上限で落とした古い手番は黙って捨てず、落とした区間を
;;;     見出し 1 行(期間・kind ごとの件数・道具の名・全文の在処 — history-dropped-headline・綴りは turn-record の
;;;     見出しと同じ history-counts-note)に畳む(段 11 lane 11v・agora-redesign #55・R34。model は呼ばない)。落とす前に、
;;;     古い手番から道具の項(tool_use の入力・tool_result の本文)だけを先頭 budget / HISTORY_THIN_DIVISOR byte に薄くして
;;;     元の byte を名乗る(便 3・#225・R35 — 郵便・agent の text・user / system / error は 1 byte も変えない)。
;;;   * capture の是非(購読者の数 → continue | stop・issue #1 の決定)と待ちの長さ。
;;;   * profile の残量の観測 → status.observed の post-image(段 7 lane 7d-3・既知の形 = kubelet の
;;;     node status: 観測は runner が書き、判断〔枯渇〕は controller〔agora-budget〕): 窓の選び方
;;;     (observed-window-of)・percent の残量・resetAt = 窓の戻る時刻・断られた / 単位の違う profile は
;;;     書かない(profile-observed-of の閉語彙 ProfileVerdict)。会社境界の判定は持たない(agentcli の葉)。
;;; wire の綴り(kind 名・phase・route)は effects.py だけが持ち、ここは import する。
;;; I/O は 1 つも無い(handlers.py が持つ)— 法 (b)(針: この file に job を選ぶ第 2 の
;;; 判定が無い・binding を書かない)。

(require doeff-hy.macros [defk <-])

(import copy)
(import dataclasses [replace])
(import datetime [datetime timedelta timezone])
(import base64)
(import binascii)
(import hashlib)
(import json)
(import re)

(import doeff_agents.sessionhost.attachment [TurnAttachment attachment-wire])
;; 段 12 lane 12j(agora-redesign #320): 名前が指す「生きている行」を解く 3 値の判断は ACP の client library の写し
;; (live_row.hy・contracts.lock の kind = code)の 1 点 — ここに名前の索引を持たない。
(import doeff_agents.sessionhost.acp.live_row [resolve-live-row])
(import doeff_agents.sessionhost.acp.effects [
  CHARTER-KIND-KEY
  CHARTER-KIND-TURN
  CHARTER-KIND-VERIFY
  CHARTER-PLACE-KEY
  CHARTER-VERIFY-DEADLINE-KEY
  CHARTER-VERIFY-JOB-ID-KEY
  CHARTER-VERIFY-JOB-ID-PATTERN
  CHARTER-VERIFY-RUN-KEY-KEY
  CommandExited
  CommandGone
  CommandRunning
  InFlightCommand
  JOB-HANDLE-VERIFY-KEY
  VERIFY-RUNS-RELDIR
  VERIFY-SCRIPTS-RELDIR
  VERIFY-STEP-ENDED
  VERIFY-STEP-LOST
  VERIFY-STEP-OBSERVE
  VERIFY-STEP-TIMED-OUT
  VerifyPlan
  CHARTER-KIND-SUMMARIZE
  CHARTER-SUMMARIZE-REGION-BYTES-KEY
  CHARTER-SUMMARIZE-UNTIL-KEY
  InFlightSummarize
  JOB-HANDLE-SUMMARIZE-KEY
  RECORD-RAW-EVENT-KINDS
  RECORD-STREAM-SUMMARY
  RecordBatch
  RecordStream
  SUMMARY-EVENT-KIND
  SUMMARY-KIND
  SUMMARY-SPEC-CONVERSATION-KEY
  SUMMARY-SPEC-FROM-KEY
  SUMMARY-SPEC-RECORD-REF-KEY
  SUMMARY-SPEC-TO-KEY
  SUMMARY-STATE-CURRENT
  SUMMARY-STATE-SUPERSEDED
  SUMMARY-STREAM-KIND
  SUMMARY-STREAM-PREFIX
  SummarizePlan
  SummaryOutcome
  SummaryRegion
  HISTORY-SUMMARY-KIND
  HistorySummary
  SUMMARIZE-JOB-ID-PREFIX
  AGENT-ATTACHMENT-CAPABILITY
  AGENT-CAPABILITIES
  AGENT-INTERRUPT-CAPABILITY
  ATTACHMENT-BYTES-KEY
  ATTACHMENT-MIME-KEY
  ATTACHMENT-NAME-KEY
  ATTACHMENT-REF-KEY
  ATTACHMENT-SEQ-KEY
  ATTACHMENT-SHA256-KEY
  CONDITION-ATTACHMENT-IGNORED
  CAUSE-CATEGORY-RATE-LIMITED
  CONDITION-PROVIDER-LIMIT
  CONDITION-TURN-PRODUCED-NOTHING
  CONDITION-UNSCHEDULABLE
  MODEL-UNDECLARED
  REASON-RATE-LIMITED
  REASON-RETRY-BUDGET-EXHAUSTED
  PROVIDER-LIMIT-AT-KEY
  PROVIDER-LIMIT-ATTEMPT-KEY
  PROVIDER-LIMIT-PROFILE-KEY
  CHARTER-AUTO-COMPACT-WINDOW-KEY
  MESSAGE-ATTACHMENTS-KEY
  NODE-CAPABILITY-ATTACHMENTS-KEY
  RECORD-ATTACHMENT-EVENT-KIND
  CHARTER-INTERRUPT-ESCALATION-KEY
  CONDITION-INTERRUPT-ESCALATION-UNDECLARED
  AGENT-SETTINGS
  AGENT-TYPE-LEASE-KIND
  CHARTER-SETTING-KEYS
  CHARTER-WORK-DIR-KEY
  CHARTER-WORK-DIR-SCRATCH-KEY
  WORK-DIR-STEP-CREATE
  WORK-DIR-STEP-LAUNCH
  WORK-DIR-STEP-MISSING
  CONDITION-AGENT-SETTING-IGNORED
  AGENTD-PLACES
  NODE-CAPABILITIES-KEY
  NODE-LABEL-PLACES
  NODE-LABEL-PLACE-RETIRED
  NODE-SPEC-PLACES
  NODE-SPEC-PLACE-RETIRED
  PLACES-SEPARATOR
  NODE-SPEC-WORK-ROOTS
  NODE-SPEC-WORK-DIRS
  NODE-SPEC-WORK-DIR-ROOTS
  NODE-SPEC-CUSTODY-BORROWER
  PROFILE-KIND
  AGENTD-PRINCIPAL
  AGORA-KINDS-NAMESPACE
  ATTRIBUTION-AGENTD-KEY
  AgentdSettings
  AgentdState
  AcpRow
  ArmChoice
  BACKEND-HEADLESS
  CLAUDE-OAUTH-TOKEN-ENV
  CONDITION-AGENTD-RESTART
  CONDITION-INTERRUPTED
  CONDITION-RECORD-UNAVAILABLE
  CONDITION-SESSION-LOST
  CONVERSATION-ID-ENV
  CONVERSATION-KIND
  CREDENTIAL-SOURCE-HOME
  CREDENTIAL-SOURCE-LEASE
  CREDENTIAL-SOURCE-MISSING
  Conflict
  DELTA-CLIPPED-INPUT-ROOT
  DELTA-FRAME-MAX-BYTES
  DELTA-INPUT-STRING-LIMIT
  DeltaBatch
  ENTRY-KIND-ERROR
  ENTRY-KIND-SYSTEM
  ENTRY-KIND-TEXT
  ENTRY-KIND-TOOL-RESULT
  ENTRY-KIND-TOOL-USE
  TURN-OUTPUT-ENTRY-KINDS
  HISTORY-MAIL-KIND
  HISTORY-THIN-DIVISOR
  HeadlineCounts
  HeadlineTurns
  HistoryFold
  HistoryItem
  INTERRUPT-ARM-INTERRUPT
  INTERRUPT-ARM-NONE
  CANCEL-ACKNOWLEDGED-AT-KEY
  CANCEL-ARM-ACKNOWLEDGE
  CANCEL-ARM-FORCE
  CANCEL-ARM-NONE
  CANCEL-BY-KEY
  CANCEL-GRACE-SECONDS-KEY
  CANCEL-REASON-KEY
  CANCEL-REQUESTED-AT-KEY
  CANCEL-STAGE-GRACEFUL
  CANCEL-STAGE-KEY
  CAUSE-CATEGORY-CANCELLED
  CAUSE-CATEGORIES
  CAUSE-CATEGORY-KEY
  CAUSE-REASON-KEY
  CAUSE-CATEGORY-COMPLETED
  CAUSE-CATEGORY-FAILED
  CAUSE-CATEGORY-INTERRUPTED
  CAUSE-CATEGORY-AGENTD-STOPPED
  CAUSE-REASON-WITHDRAWN
  DEFAULT-CANCEL-GRACE-SECONDS
  JOB-SPEC-CANCEL-KEY
  JOB-STATUS-CANCEL-KEY
  JobCancel
  END-RETRY-DROP
  END-RETRY-WRITE
  PHASE-PENDING
  UnrecordedEnd
  RESULT-CAUSE-KEY
  InFlightJob
  InterruptRead
  OpenToolBlock
  BINDING-NODE-KEY
  BINDING-NODE-ROW-KEY
  JOB-INPUTS-DELIVERED-KEY
  JOB-INTERRUPTS-DELIVERED-KEY
  JOB-INTERRUPTS-ESCALATED-KEY
  JOB-INTERRUPTS-KEY
  JOB-INTERRUPTS-READ-KEY
  NODE-CAPABILITY-INTERRUPT-KEY
  JOB-STEP-FAIL-MISSING
  JOB-STEP-OBSERVE
  JOB-STEP-RECORD-END
  JOB-STEP-SESSION-LOST
  JOB-STEP-TURN-END
  JSONObject
  JobOutcome
  LIFECYCLE-MULTI-TURN
  EPOCH-VERDICT-ADOPT
  EPOCH-VERDICT-CONTINUE
  EPOCH-VERDICT-RELIST
  LIST-MODE-FULL
  LIST-MODE-NONE
  LIST-MODE-WINDOW
  MESSAGE-KIND
  LaunchPlan
  LeaseGrant
  NEXT-ARM-DEFER
  NEXT-ARM-LAUNCH
  NEXT-ARM-REHYDRATE
  NEXT-ARM-RESUME
  NEXT-ARM-SEND
  AGENTD-PROTOCOL
  NODE-JOINED
  NODE-SPEC-AGENTD-BUILD-KEY
  NODE-SPEC-AGENTD-KEY
  NODE-SPEC-AGENTD-PROTOCOL-KEY
  NODE-SPEC-AGENTD-REVISION-KEY
  OWNERSHIP-GRADE-COMPANY
  PHASE-BOUND
  PHASE-ENDED
  PHASE-RUNNING
  PHASE-WITHDRAWN
  PROFILE-BUDGET-UNIT-PERCENT
  PROFILE-OBSERVED-WINDOW-DEFAULT
  PROFILE-RETIRED
  ProfileHome
  PROFILE-USAGE-KIND
  PROFILE-STATUS-OBSERVED-BY-KEY
  PROFILE-STATUS-OBSERVED-KEY
  ProfileNotHeld
  ProfileObservation
  ProfileUnobserved
  ProfileUsage
  ProfileUsageUnavailable
  RECORD-APPEND-CONFLICT
  RECORD-APPEND-ERROR
  RECORD-APPEND-GIVEN-UP
  RECORD-APPEND-OK
  RECORD-BATCH-MAX-EVENTS
  RECORD-CREATE-CREATED
  RECORD-CREATE-GIVEN-UP
  RECORD-CREATE-PENDING
  RECORD-REF-PREFIX
  RECORD-BATCH-REFUSAL-STATUSES
  RECORD-STREAM-TURN
  RecordAppended
  RecordBatch
  RecordConflicted
  RECORD-MAIL-EVENT-KIND
  RecordEvent
  RecordPage
  RecordStream
  RecordUnread
  RecordUnsent
  RecordedTurns
  Refused
  SEAT-OPENER-ENV
  SESSION-OBSERVED-BUSY
  SESSION-OBSERVED-IDLE
  SESSION-TERMINAL-STATUSES
  STREAM-CAPABILITY-EVENTS
  STREAM-CAPABILITY-FRAMES
  STREAM-SOURCE-EVENTS
  STREAM-SOURCE-TRANSCRIPT
  SessionView
  TURN-RECORD-ENDED
  TURN-RECORD-RUNNING
  TURN-RECORD-SWEEP-END
  TURN-RECORD-SWEEP-SKIP
  TURN-RECORD-ENTRIES-BYTE-BUDGET
  TURN-RECORD-KIND
  TurnEntryDropMarker
  TurnEntryHeadline
  USAGE-WINDOW-FULL-PERCENT
  USAGE-WINDOW-SECONDS
  WatchAdvance
  Written])


;; ---------------------------------------------------------------------------
;; 自分に結ばれた job か — agentd が持つ唯一の判定
;; ---------------------------------------------------------------------------

(defk binding-names-me [binding node-name node-row-id]
  {:pre [(: binding (| dict None)) (: node-name str) (: node-row-id (| str None))]
   :post [(: % bool)]}
  "結び(status.binding)が自分を指すか(段 12 lane 12j・agora-redesign #321 = #317 の k8s 規則 1 後半・契約 scheduling.json
   binding.fields.nodeRow)— 判断はここ 1 点: 結びに nodeRow(結んだ node の行の id)が在れば、自分の生きている行の id
   (node-row-id・None = まだ参加していない → 受けない)と一致する時だけ。無い結び(この欄が生まれる前の書き)だけ node
   (機体の名前)に落ちる。同じ名の別の化身(退役した行・旧い agentd)に結ばれた手番は名前が同じでも自分ではない。"
  (when (not (isinstance binding dict))
    (return False))
  (setv node-row (.get binding BINDING-NODE-ROW-KEY))
  (if (and (isinstance node-row str) node-row)
      (and (is-not node-row-id None) (= node-row node-row-id))
      (= (.get binding BINDING-NODE-KEY) node-name)))


(defk bound-to-me [row node-name node-row-id]
  {:pre [(: row AcpRow) (: node-name str) (: node-row-id (| str None))]
   :post [(: % bool)]}
  "phase == Bound かつ結びが自分を指す(binding-names-me)。これ以外の条件で job を選ばない
   (法 agentd-holds-no-placement-judgment)。"
  (setv status row.status)
  (setv binding (if (isinstance status dict) (.get status "binding") None))
  (<- mine bool (binding-names-me binding node-name node-row-id))
  (and (isinstance status dict)
       (= (.get status "phase") PHASE-BOUND)
       mine))


(defk job-rows-bound-to [rows node-name node-row-id]
  {:pre [(: rows tuple) (: node-name str) (: node-row-id (| str None))]
   :post [(: % tuple)]}
  "list の行のうち自分に結ばれた行を、行の順のまま(優先も選択も無し)。"
  (setv out [])
  (for [row rows]
    (<- mine bool (bound-to-me row node-name node-row-id))
    (when mine
      (.append out row)))
  (tuple out))


;; ---------------------------------------------------------------------------
;; 自分が持つ Running か — 再起動後の続きを行から導く(R7)
;; ---------------------------------------------------------------------------

(defk session-id-of-handle [row]
  {:pre [(: row AcpRow)]
   :post [(: % (| str None))]}
  "agentd が claim の時に書いた sessionHandle.sessionId(無ければ None)。"
  (setv status row.status)
  (setv handle (if (isinstance status dict) (.get status "sessionHandle") None))
  (setv session-id (if (isinstance handle dict) (.get handle "sessionId") None))
  (if (isinstance session-id str) session-id None))


(defk running-on-me [row node-name node-row-id principal]
  {:pre [(: row AcpRow) (: node-name str) (: node-row-id (| str None)) (: principal str)]
   :post [(: % bool)]}
  "phase == Running かつ結びが自分を指す(binding-names-me)かつ sessionHandle.stream.owner == 自分の
   principal — 自分が claim した job(再起動で memory を失っても行が覚えている)。job を選ぶ
   判定はこれと bound-to-me の 2 つだけで、どちらも結びが自分を指す行に閉じる。"
  (setv status row.status)
  (setv binding (if (isinstance status dict) (.get status "binding") None))
  (setv handle (if (isinstance status dict) (.get status "sessionHandle") None))
  (setv stream (if (isinstance handle dict) (.get handle "stream") None))
  (<- mine bool (binding-names-me binding node-name node-row-id))
  (and (isinstance status dict)
       (= (.get status "phase") PHASE-RUNNING)
       mine
       (isinstance stream dict)
       (= (.get stream "owner") principal)))


(defk job-rows-running-on [rows node-name node-row-id principal]
  {:pre [(: rows tuple) (: node-name str) (: node-row-id (| str None)) (: principal str)]
   :post [(: % tuple)]}
  "list の行のうち自分が持つ Running の行を、行の順のまま。
   agora-redesign #519(段 12): **断られて置き直し待ちの行は拾い直さない** — 自分が書いた ProviderLimit の記録が
   いまの試み(binding.attempt)を名乗る Running の行は、器の session を片付けた後に配置(ACP Scheduling の
   supervision provider-refused)が Pending へ戻すのを待っている行。拾い直すと器に session が無いので
   fail-missing の腕が SessionFailed で Ended に書き、置き直しが撃たれなくなる(判断は attempt-refused? の 1 点)。
   古い attempt を名乗る記録だけの行(置き直しの後の次の試み)は今日どおり拾い直す。"
  (setv out [])
  (for [row rows]
    (<- mine bool (running-on-me row node-name node-row-id principal))
    (when mine
      (<- status dict (status-object-of row))
      (<- refused bool (attempt-refused? status))
      (when (not refused)
        (.append out row))))
  (tuple out))


(defk binding-attempt-of [status]
  {:pre [(: status dict)]
   :post [(: % int)]}
  "行の status.binding.attempt(配置が書く試みの回数・契約 scheduling.json binding.attempt)。欄の無い結び(段 6 より前の
   書き)は最初の試み = 1(配置の attemptsMadeOn と同じ読み)。bool は数でない・0 以下は無い。"
  (setv binding (.get status "binding"))
  (setv attempt (if (isinstance binding dict) (.get binding "attempt") None))
  (if (and (isinstance attempt int) (not (isinstance attempt bool)) (>= attempt 1))
      attempt
      1))


(defk attempt-refused? [status]
  {:pre [(: status dict)]
   :post [(: % bool)]}
  "agora-redesign #519: 行のいまの試み(binding.attempt)を名乗る ProviderLimit{status True} の記録が在るか =
   この試みは口座に断られ、配置の置き直しを待っている(契約 scheduling.json supervision provider-refused と同じ判定)。
   attempt を名乗らない記録(旧 agentd の書き — その手番は Ended)は当たらない。"
  (<- attempt int (binding-attempt-of status))
  (setv conditions (.get status "conditions"))
  (when (not (isinstance conditions list))
    (return False))
  (for [condition conditions]
    (when (and (isinstance condition dict)
               (= (.get condition "type") CONDITION-PROVIDER-LIMIT)
               (= (.get condition "status") "True")
               (= (.get condition PROVIDER-LIMIT-ATTEMPT-KEY) attempt))
      (return True)))
  False)


(defk refused-attempt-status-of [status conditions]
  {:pre [(: status dict) (: conditions tuple)]
   :post [(: % dict)]}
  "agora-redesign #519: 口座に断られた試みの手番の status — **phase はそのまま**(Ended にしない: 終端の巻き戻しは engine が
   断り、turn-record は 1 手番 1 行 — 置き直しは配置の supervision が Pending へ戻す)、sessionHandle・binding・result も
   そのまま、条件(手番の途中で判った事実 + ProviderLimit の記録〔profile / attempt / at を名乗る〕)を末尾に足すだけ。
   古い試みの記録は残る(予算の係の材料)。元の status は触らない。"
  (setv next (dict status))
  (setv existing (.get status "conditions"))
  (setv (get next "conditions") (+ (if (isinstance existing list) (list existing) []) (list conditions)))
  next)


(defk retired-rows-of [rows node-name node-row-id principal]
  {:pre [(: rows tuple) (: node-name str) (: node-row-id (| str None)) (: principal str)]
   :post [(: % tuple)]}
  "agora-redesign #519: 配置が退役させた行(Withdrawn + Unschedulable{status True, reason retry-budget-exhausted} —
   契約 scheduling.json retirement)のうち、最後の runner が自分(結びが自分を指し sessionHandle の owner が自分)の行。
   その turn-record を ended にするのは最後の runner(agentd.end-retired-records)— 記録は手番が本当に終わる時に ended
   (1 手番 1 行)。"
  (setv out [])
  (for [row rows]
    (setv status row.status)
    (when (and (isinstance status dict) (= (.get status "phase") PHASE-WITHDRAWN))
      (<- sid (| str None) (handle-owned-by row node-name node-row-id principal))
      (when (is-not sid None)
        (setv conditions (.get status "conditions"))
        (when (isinstance conditions list)
          (for [condition conditions]
            (when (and (isinstance condition dict)
                       (= (.get condition "type") CONDITION-UNSCHEDULABLE)
                       (= (.get condition "status") "True")
                       (= (.get condition "reason") REASON-RETRY-BUDGET-EXHAUSTED))
              (.append out row)
              (break)))))))
  (tuple out))


(defk job-step-of [view floor-ms progressed]
  {:pre [(: view (| SessionView None)) (: floor-ms int) (: progressed bool)]
   :post [(: % str)]}
  "自分の Running の行の次の 1 手(閉語彙 effects.JobStep)— 器の現況ちょうどから:
   器に session が無い → fail-missing(記録が在れば ended にし、SessionFailed で Ended)/
   器が終端 → record-end(記録の腕だけ: turn-record ended・result・phase Ended)/
   温かい session(multi_turn)で host が手番の終わり(turn_ended_at)をこの手番の始まりの
   下限(floor = 本文を送った時刻)より後に刻み、記録が進んでいる(送った本文が届いて手番が
   始まった証拠 = 前の手番の終わりの stale な観測と区別する)→ turn-end(記録の腕だけ・
   session は生かす)/ 行は非終端だが host の観測で backend が死んでいる(段 10 lane 10h —
   手番は終わらない)→ session-lost(記録の腕・job は SessionLost で Ended・session は host の
   monitor に任せる)/ それ以外 → observe。memory に在る job も無い job も同じ 1 点で決める。
   backend の生死は status の語から推測しない(judgment.backend-alive — 観測の無い眺めは生きて
   いると読む: 観測断 ≠ 死亡)。"
  (<- live-backend bool (backend-alive view))
  (cond
    (is view None) JOB-STEP-FAIL-MISSING
    (in view.status SESSION-TERMINAL-STATUSES) JOB-STEP-RECORD-END
    (and (= view.lifecycle LIFECYCLE-MULTI-TURN)
         (is-not view.turn-ended-at-ms None)
         (> view.turn-ended-at-ms floor-ms)
         progressed)
    JOB-STEP-TURN-END
    (not live-backend) JOB-STEP-SESSION-LOST
    True JOB-STEP-OBSERVE))


(defk restart-condition-of [job node-name reason now-ms]
  {:pre [(: job InFlightJob) (: node-name str) (: reason str) (: now-ms int)]
   :post [(: % dict)]}
  "agentd の停止で閉じた手番の条件(段 10 lane 10h 便 2): type AgentdRestart・reason に node・停止の理由(signal)・
   session・時刻(UTC)。手番の本文の結末は無い(result なし)— 次の手番は同じ会話の次の郵便が起こす。"
  (<- at str (history-time-of now-ms))
  (<- condition dict
      (condition-of CONDITION-AGENTD-RESTART
                    (+ f"agentd on node {node-name} stopped ({reason}) at {at} while the turn was running in "
                       f"session {job.session-id} — the headless process goes down with the host, so the turn is closed here")))
  condition)


(defk provider-limit-condition-of [cause model profile attempt at-ms]
  {:pre [(: cause (| dict None)) (: model (| str None)) (: profile (| str None)) (: attempt int) (: at-ms int)]
   :post [(: % (| dict None))]}
  "段 11 lane 11n 便 C(agora-redesign #179・依頼者の裁定 2026-09-15 案 c′): 器の終端の cause
   (SessionView.terminal-cause)→ provider の限度の条件 1 項(None = 限度の断りではない)。

   agora-redesign #519(段 12): 記録は**どの試みがいつどの口座で断られたか**を自分で名乗る —— profile(断られた口座 =
   この手番の binding.profile・空なら欄を落とす)・attempt(行の binding.attempt・binding-attempt-of)・at(断りの時刻 =
   記録を書く拍の時計・epoch ms)。契約 ACP scheduling.json profileExhaustion.providerRefusal.fields(additive)。配置は
   attempt = binding.attempt の記録を『この試みは断られた』と読んで置き直し、予算の係は profile / at を優先して読む。

   **族の表はここに無い**(ADR-DOE-AGENTS-008 R1: 観測形式のテキスト物理の家は impls/markers.hy
   ちょうど)。CLI の文に表を当てるのは器の側の 1 点 —— headless.hy の手番の腕が verdict の
   detail に markers.has-api-limit-marker を当て、当たった行を status failed + cause
   {category: rate_limited, reason: <CLI の文>} にする(pane の路の policy.hy と同じ表・同じ語彙)。
   制御面はその**欄**を読む(既知の形: runner が結末を書き、control plane が欄を読む)。

   model = **手番が走らせようとした model**(InFlightJob.model = charter.model)で、材料の中の
   message.model ではない: 限度の断りの拍の usage.model は `<synthetic>`(実測 2026-09-15 11:17)で、
   どの model が枯れたかを名乗らない。宣言が無い手番(effects.MODEL-UNDECLARED)は欄を落とす。

   until は書かない —— 窓(いつ戻るか)を知るのは予算の controller で、この条件は『断られた』の
   事実ちょうど。⚠ status.result には書かない(result が在ることは『手番が結果を報告した』の
   意味で、await の終端の別〔Acp.App.Agent.AgentJob.awaitOutcomeOf〕が反転する)。"
  (when (not (isinstance cause dict))
    (return None))
  (when (!= (.get cause "category") CAUSE-CATEGORY-RATE-LIMITED)
    (return None))
  (setv reason (.get cause "reason"))
  (setv line (if (and (isinstance reason str) (.strip reason))
                 (get (.splitlines (.strip reason)) 0)
                 CAUSE-CATEGORY-RATE-LIMITED))
  (setv condition {"type" CONDITION-PROVIDER-LIMIT "status" "True" "reason" REASON-RATE-LIMITED
                   "message" line
                   PROVIDER-LIMIT-ATTEMPT-KEY attempt
                   PROVIDER-LIMIT-AT-KEY at-ms})
  (when (and (isinstance model str) (.strip model) (!= model MODEL-UNDECLARED))
    (setv (get condition "model") model))
  (when (and (isinstance profile str) (.strip profile))
    (setv (get condition PROVIDER-LIMIT-PROFILE-KEY) profile))
  condition)


(defk session-lost-condition-of [view now-ms]
  {:pre [(: view SessionView) (: now-ms int)]
   :post [(: % dict)]}
  "session-lost の条件(段 10 lane 10h・agora-redesign #84): reason に session の id・backend の種類・
   pid(headless の backend_ref.pid — 無ければ none)・観測の時刻(UTC)。"
  (setv ref (or view.backend-ref {}))
  (setv pid (.get ref "pid"))
  (setv pid-text (if (and (isinstance pid int) (not (isinstance pid bool))) (str pid) "none"))
  (<- at str (history-time-of now-ms))
  (<- condition dict
      (condition-of CONDITION-SESSION-LOST
                    (+ f"session {view.session-id} ({view.backend-kind}, pid {pid-text}) has no live backend "
                       f"process at {at} while the turn was running — the turn cannot end")))
  condition)


;; ---------------------------------------------------------------------------
;; 温かい session — 会話の資源としての session と手番の起こし方(R10)
;; ---------------------------------------------------------------------------

(defk launch-lifecycle-of [charter]
  {:pre [(: charter dict)]
   :post [(: % str)]}
  "charter が lifecycle を名指せばそれ、無ければ agentd の既定 = multi_turn(session は会話の
   資源・job は手番 — 手番の終わりで片付けない)。"
  (setv declared (.get charter "lifecycle"))
  (if (and (isinstance declared str) declared) declared LIFECYCLE-MULTI-TURN))


(defk session-alive [view]
  {:pre [(: view (| SessionView None))]
   :post [(: % bool)]}
  "器に在り、終端でない(session = 会話の資源としての行の生死 — process の生死ではない)。"
  (and (is-not view None) (not-in view.status SESSION-TERMINAL-STATUSES)))


(defk backend-alive [view]
  {:pre [(: view (| SessionView None))]
   :post [(: % bool)]}
  "行の backend(headless の子 process / tmux の pane)が生きているかの host の観測(段 10 lane 10h・
   agora-redesign #84)。器に無い → 偽。観測が無い眺め(backend_alive = None — launch / resume の応答)は
   生きていると読む: 観測の無さは死亡の証拠ではない(ADR-DOE-AGENTS-009: 観測断 ≠ 死亡)。status の語は
   読まない — 死は明示の False だけ。"
  (and (is-not view None) (is-not view.backend-alive False)))


(defk session-idle [view]
  {:pre [(: view (| SessionView None))]
   :post [(: % bool)]}
  "温かい session が手番の間に居る(生きている ∧ multi_turn ∧ host が手番の終わりを刻んで
   いる)= 次の手番を send で受けられる。"
  (<- alive bool (session-alive view))
  (and alive
       (isinstance view SessionView)
       (= view.lifecycle LIFECYCLE-MULTI-TURN)
       (is-not view.turn-ended-at-ms None)))


(defk session-busy [view]
  {:pre [(: view (| SessionView None))]
   :post [(: % bool)]}
  "温かい session が手番の途中(生きている ∧ multi_turn ∧ 手番の終わりが刻まれていない)。"
  (<- alive bool (session-alive view))
  (and alive
       (isinstance view SessionView)
       (= view.lifecycle LIFECYCLE-MULTI-TURN)
       (is view.turn-ended-at-ms None)))


(defk handle-owned-by [row node-name node-row-id principal]
  {:pre [(: row AcpRow) (: node-name str) (: node-row-id (| str None)) (: principal str)]
   :post [(: % (| str None))]}
  "自分が claim した行(結びが自分を指す〔binding-names-me〕∧ sessionHandle.stream.owner == 自分)の
   sessionHandle.sessionId。phase は問わない(Running でも Ended でも会話の session の記録)。
   自分の行でなければ None。"
  (setv status row.status)
  (setv binding (if (isinstance status dict) (.get status "binding") None))
  (setv handle (if (isinstance status dict) (.get status "sessionHandle") None))
  (setv stream (if (isinstance handle dict) (.get handle "stream") None))
  (<- mine bool (binding-names-me binding node-name node-row-id))
  (if (and mine
           (isinstance stream dict)
           (= (.get stream "owner") principal))
      (do (<- sid (| str None) (session-id-of-handle row))
          sid)
      None))


(defk conversation-session-of [rows subject node-name node-row-id principal]
  {:pre [(: rows tuple) (: subject str) (: node-name str) (: node-row-id (| str None)) (: principal str)]
   :post [(: % (| str None))]}
  "会話(agent-job の spec.subject)→ その会話の最後の手番が使った session の id — 行から
   導く(memory を要らない): 自分が claim した同じ subject の行のうち createdAt が最新の
   sessionHandle.sessionId。無ければ None。"
  (setv found None)
  (setv found-at -1)
  (for [row rows]
    (when (= (.get row.spec "subject") subject)
      (<- sid (| str None) (handle-owned-by row node-name node-row-id principal))
      (when (and (is-not sid None) (> row.created-at-ms found-at))
        (setv found sid)
        (setv found-at row.created-at-ms))))
  found)


(defk warm-candidate-of [plan rows subject node-name node-row-id principal]
  {:pre [(: plan LaunchPlan) (: rows tuple) (: subject str) (: node-name str) (: node-row-id (| str None)) (: principal str)]
   :post [(: % (| str None))]}
  "次の手番を送れるかもしれない session の id: affinity.predecessor(scheduler の名指し)が
   在ればそれ、無ければ会話の最後の手番の session(行から)。None = 候補なし(launch)。"
  (if (is-not plan.predecessor None)
      plan.predecessor
      (do (<- sid (| str None) (conversation-session-of rows subject node-name node-row-id principal))
          sid)))


(defk session-affinity-key-of [plan]
  {:pre [(: plan LaunchPlan)]
   :post [(: % dict)]}
  "session を使い回す鍵(段 8q・R20・段 9o lane 9o-3・段 10c で session-affinity-key-of から改名 — 資格ではなく cache の同一性の鍵。
   手番の資格の出所は credential-source-of の 1 点): 預かり所の account(借りる時の家 = <homes-root>/<種類>/<account>)・
   charter の binding(借りない時の家・codex の profile_dir)・charter の model(plan.model — 無ければ走行器の既定を
   使う事実の名 \"default\")の組。同じ鍵 = 同じ器を使い回せる(homes-root は機体に 1 つ)。model を鍵に入れるのは、
   走っている CLI の session は起こした時の model のまま手番を回すから(session.send に model の欄は無く、headless の
   続きの process も器の行の model で起きる)— 宣言の model を変えた手番を温かい session へ送ると前の model で走る
   (実射 2026-09-14: charter は claude-opus-5・「session started: model claude-sonnet-5」)。既知の形 = virtual actor の
   器の再利用の鍵に宣言の欄を含める。欠けた欄は None のまま(発明しない)。
   段 10 lane 10e(agora-redesign #53・設計 第 9 節 問 5): この鍵の欄 = node が名乗る能力の表の restartOn(model・
   profile〔= account と binding の家〕)ちょうど(capabilities-of)。effort は鍵に入れない — 温かい process を
   起こし直す(--resume・cache は保つ)だけで同じ session のまま変えられる(next-arm-for-job の effort の腕)。"
  (setv binding (.get plan.charter "binding"))
  {"account" plan.account
   "binding" (if (isinstance binding dict) binding None)
   "model" plan.model})


(defk capabilities-of []
  {:pre []
   :post [(: % dict)]}
  "node の status.capabilities に名乗る能力の表(段 10 lane 10e・agora-redesign #53・契約 agora-kinds.json
   kinds.node.status.capabilities — 既知の形 = CI runner の label): agent の種類(charter.agent_type の語)ごとに
   settings(受ける欄)と restartOn(変えたら session を作り直す欄 — session-affinity-key-of の鍵の欄)と
   interrupt(割り込みの能力 — 段 10 lane 10n: steer-then-stop = 注入 → 期限で停止の合図 / stop = 即座に止めて渡す)。値は
   effects.AGENT-CAPABILITIES / AGENT-INTERRUPT-CAPABILITY の写し(list に直すだけ — JSON の形)。"
  (dfor [kind entry] (.items AGENT-CAPABILITIES)
        kind {"settings" (list (get entry "settings"))
              "restartOn" (list (get entry "restartOn"))
              ;; 段 10 lane 10n: 割り込みの能力(閉語彙 effects.InterruptCapability)— 面の文言はこれに従う。
              NODE-CAPABILITY-INTERRUPT-KEY (get AGENT-INTERRUPT-CAPABILITY kind)
              ;; 段 10 lane 10o(agora-redesign #96): 受ける添付の種類の語の列(閉語彙 effects.AttachmentKind)。
              ;; 欠落 = 何も受けない — だから名乗る種類は表に在る種類ちょうど(発明しない)。
              NODE-CAPABILITY-ATTACHMENTS-KEY (list (.get AGENT-ATTACHMENT-CAPABILITY kind #()))}))


(defk plan-with-node-home [plan home]
  {:pre [(: plan LaunchPlan) (: home str)]
   :post [(: % LaunchPlan)]}
  "charter の work_dir を node の家で展開した plan(段 10 lane 10y・agora-redesign #110・依頼者の裁定 2026-09-15 案 A): `~` と `~/…`
   だけを home で置き換える(それ以外 — 絶対 path・`~user`・相対 — は触らない)。home が空なら展開しない(`~` のままの path は
   work-dir-step-of で無い dir に落ちる)。作業場を機体に依らない綴りで宣言でき、会社 Mac の絶対 path に結ばれない。"
  (setv work-dir (.get plan.charter CHARTER-WORK-DIR-KEY))
  (when (or (not (isinstance work-dir str)) (not home) (not (or (= work-dir "~") (.startswith work-dir "~/"))))
    (return plan))
  (setv expanded (+ (.rstrip home "/") (cut work-dir 1 None)))
  (replace plan :charter (| plan.charter {CHARTER-WORK-DIR-KEY expanded})))


(defk work-dir-of [plan]
  {:pre [(: plan LaunchPlan)]
   :post [(: % (| str None))]}
  "手番の作業場(charter の work_dir・展開の後)。宣言が無い・空 = None(検める作業場が無い — 走行器の既定)。"
  (setv work-dir (.get plan.charter CHARTER-WORK-DIR-KEY))
  (if (and (isinstance work-dir str) work-dir) work-dir None))


(defk work-dir-step-of [plan exists]
  {:pre [(: plan LaunchPlan) (: exists bool)]
   :post [(: % str)]}
  "作業場の段の 1 点(段 10 lane 10y・閉語彙 effects.WorkDirStep): 在る(か宣言が無い)= launch / 無いが charter の
   work_dir_scratch が true = create(agentd が作ってよい scratch)/ 無い = missing(起こさずに条件 WorkDirMissing — repo を指す
   work_dir を空の dir で偽装しない)。印は bool の true ちょうど(文字列の true 等は印ではない — 発明しない)。"
  (<- work-dir (| str None) (work-dir-of plan))
  (cond (or (is work-dir None) exists) WORK-DIR-STEP-LAUNCH
        (is (.get plan.charter CHARTER-WORK-DIR-SCRATCH-KEY) True) WORK-DIR-STEP-CREATE
        True WORK-DIR-STEP-MISSING))


(defk effort-of-plan [plan]
  {:pre [(: plan LaunchPlan)]
   :post [(: % (| str None))]}
  "charter の effort(会話の宣言 → 配達の係が charter.effort に写した語)。無ければ None(走行器の既定)。"
  (setv effort (.get plan.charter "effort"))
  (if (and (isinstance effort str) effort) effort None))


(defk ignored-settings-of [plan view arm]
  {:pre [(: plan LaunchPlan) (: view (| SessionView None)) (: arm str)]
   :post [(: % tuple)]}
  "黙って落とさない(段 10 lane 10e・設計 第 9 節 問 3 / 問 4): charter に載った会話の宣言の欄のうち、この手番で効かない
   欄を条件 AgentSettingIgnored(1 欄 1 行・reason = <欄>=<値>: <理由>)にして返す。判断はここ 1 点:
   (1) charter.agent_type の種類が能力の表(AGENT-CAPABILITIES)に無い・その種類が受けない欄(settings に無い)—
       起こす前から分かる。
   (2) 温かい session へ送る手番(arm = send)で charter の work_dir が session の cwd と違う — cwd は起こした process の
       もので、send では変えられない(workDir は restartOn に無いので session は作り直さない・次に起こす時に効く)。
   model と profile は鍵(session-affinity-key-of)なので違えば send にならず、effort は違えば process を起こし直す
   (next-arm-for-job)— どちらもここには来ない。"
  (setv found [])
  (setv agent-type (.get plan.charter "agent_type"))
  (setv entry (if (isinstance agent-type str) (.get AGENT-CAPABILITIES agent-type) None))
  (setv accepted (if (is entry None) #() (get entry "settings")))
  (for [[charter-key setting] (.items CHARTER-SETTING-KEYS)]
    (setv value (.get plan.charter charter-key))
    (when (and (isinstance value str) value (not (in setting accepted)))
      (.append found
               {"type" CONDITION-AGENT-SETTING-IGNORED
                "status" "True"
                "reason" (+ f"{setting}={value}: agent kind "
                            (if (is entry None) f"{(repr agent-type)} names no capability table" f"{agent-type} does not accept {setting}"))})))
  (when (and (= arm NEXT-ARM-SEND) (isinstance view SessionView))
    (setv wanted (.get plan.charter "work_dir"))
    (when (and (isinstance wanted str) wanted (!= wanted view.work-dir) (in "workDir" accepted))
      (.append found
               {"type" CONDITION-AGENT-SETTING-IGNORED
                "status" "True"
                "reason" f"workDir={wanted}: the warm session keeps its cwd {view.work-dir} (applies when the session is next launched)"})))
  (tuple found))


(defk attribution-of-view [view]
  {:pre [(: view SessionView)]
   :post [(: % (| dict None))]}
  "session の行に agentd が刻んだ帰属(wire の launch_attribution の ATTRIBUTION-AGENTD-KEY の欄 —
   {conversationId, agentJobId, account, home, arm})。無ければ None(段 8q より前に起こした session・
   他の起こし手 — 発明しない)。"
  (setv attribution (or view.launch-attribution {}))
  (setv mine (.get attribution ATTRIBUTION-AGENTD-KEY))
  (if (and (isinstance mine dict) (isinstance (.get mine "conversationId") str)) mine None))


(defk session-attribution-of [plan job-id subject arm]
  {:pre [(: plan LaunchPlan) (: job-id str) (: subject str) (: arm str)]
   :post [(: % dict)]}
  "起こす session に刻む帰属(段 8q・R20): 会話・手番・家の account と鍵・起こし方。session の会話と家は
   session の行が覚える事実で、終端の後に回収される agent-job の行から導かない。"
  (<- home dict (session-affinity-key-of plan))
  (<- effort (| str None) (effort-of-plan plan))
  {"conversationId" subject
   "agentJobId" job-id
   "account" plan.account
   "home" home
   "arm" arm
   "effort" effort})


(defk charter-with-attribution [charter attribution]
  {:pre [(: charter dict) (: attribution dict)]
   :post [(: % dict)]}
  "charter の launch_attribution に agentd の欄を据える(作った側の欄は残す — host は opaque に保存して
   wire の眺めに返し、session.resume の params も同じ欄を運ぶ)。"
  (setv next (dict charter))
  (setv existing (.get charter "launch_attribution"))
  (setv merged (if (isinstance existing dict) (dict existing) {}))
  (setv (get merged ATTRIBUTION-AGENTD-KEY) attribution)
  (setv (get next "launch_attribution") merged)
  next)


(defk session-in-home [view home]
  {:pre [(: view SessionView) (: home dict)]
   :post [(: % bool)]}
  "session がその家で走っているか: 刻んだ帰属の home が同じ鍵。帰属の無い session は家が分からない =
   違う家と読む(cache の失効の側に倒す — 履歴からの再開は ACP の全史から会話を続ける)。"
  (<- mine (| dict None) (attribution-of-view view))
  (and (is-not mine None) (= (.get mine "home") home)))


(defk session-effort-of [view]
  {:pre [(: view SessionView)]
   :post [(: % (| str None))]}
  "session を起こした時の effort(帰属の effort の欄 — session-attribution-of が刻む)。欄が無い(段 10 lane 10e より前に
   起こした session)は None = 走行器の既定で起きた、と読む。"
  (<- mine (| dict None) (attribution-of-view view))
  (setv effort (if (is mine None) None (.get mine "effort")))
  (if (and (isinstance effort str) effort) effort None))


;; ---------------------------------------------------------------------------
;; 自己圧縮(段 10f 便 2・agora-redesign #82・operator 2026-09-14「that routing agent should compact itself with some
;; threshold」): 会話の宣言 status.agent.compactAt(文脈の使用率 % の閾値・任意)を、直前の手番の文脈の使用率(agentd の
;; 実測 — turn-record の usage に同等の欄が無い)が超えていたら、次の手番を履歴からの再開(rehydrate)で起こす。
;; 判断は next-arm-for-job の 1 点に条件 compact を足す形で、材料の読み(会話の行・実測の cache)は agentd.hy。
;; ---------------------------------------------------------------------------

(defk conversation-key-of [conversation-id]
  {:pre [(: conversation-id str)]
   :post [(: % str)]}
  "会話の行の鍵(identityKey = id・区画 = agora の kind の区画)— compactAt は鍵で 1 行読む(全量 list は撃たない)。"
  f"{AGORA-KINDS-NAMESPACE}:{CONVERSATION-KIND}:{conversation-id}")


(defk profile-key-of [profile]
  {:pre [(: profile str)]
   :post [(: % str)]}
  "profile の行の鍵(identityKey = 名・区画 = agora の kind の区画)— 置き場の名乗りは鍵で 1 行読む(全量 list は撃たない)。"
  f"{AGORA-KINDS-NAMESPACE}:{PROFILE-KIND}:{profile}")


(defk credential-place-of [row]
  {:pre [(: row (| AcpRow None))]
   :post [(: % (| str None))]}
  "結ばれた profile の行が名乗る置き場(spec.boundary — ACP の契約 agora-kinds.json の閉語彙 company | personal)。
   行が無い・欄が無い・語彙の外は None: **判らないものを食い違いと読まない**(封じた資格はその置き場の worker に
   しか無く、最後の門は預かり所の redeem が持つ — ここは走行係自身の前段の門・段 10 lane 10d 便 2 の I5)。"
  (when (is-not row None)
    (setv boundary (.get row.spec "boundary"))
    (when (and (isinstance boundary str) (in boundary AGENTD-PLACES))
      boundary)))


(defk credential-place-mismatch [places boundary]
  {:pre [(: places tuple) (: boundary (| str None))]
   :post [(: % bool)]}
  "自分が仕える置き場の集合と口座の置き場の食い違い(I5・段 11 lane 11u で集合へ)— 集合が名乗られていて、口座の
   置き場が判っていて、その語が集合に無い時だけ真(会社 Mac = company と personal の両方を名乗るので両方の口座を
   受ける・pool = personal だけなので会社の口座を断る)。名乗りの無い側が在る拍は偽(前段の門は判らないもので
   止めない — 止めるのは預かり所の側の構造)。"
  (and (bool places) (is-not boundary None) (not-in boundary places)))


(defk charter-place-of [row]
  {:pre [(: row AcpRow)]
   :post [(: % (| str None))]}
  "job の charter が要求する置き場(spec.charter.place の語・無い = None)。段 12(card acp:kanban-issue:ki-d13566f4d5eb・
   決定 案 A・2026-09-19): 要求の座は方策の 1 欄(delivery-policy の reception.rules[class=operate].open.place)で、
   ここは結ばれた行の綴りを写すだけ — 語彙の検は配置(ACP Inputs.jobViewOf)が結ぶ前に済ませている(判断の第 2 の点を
   作らない)。文字列でない・空は None(要求が無い = 今日どおりどの宿でも起きる)。"
  (setv charter (.get row.spec "charter"))
  (setv place (if (isinstance charter dict) (.get charter CHARTER-PLACE-KEY) None))
  (if (and (isinstance place str) place) place None))


(defk place-mismatch [places place]
  {:pre [(: places tuple) (: place (| str None))]
   :post [(: % bool)]}
  "charter が要求する置き場と自分の名乗りの食い違い(段 12・card acp:kanban-issue:ki-d13566f4d5eb の検 1 本の走行側)—
   **要求が在って、自分の集合にその語が無い**時だけ真。要求の無い手番(place = None)は今日どおり通る(欄を持たない
   charter の byte は不変 = overlay の identity)。集合の宣言が空(検体の断面 — 本番は参加しない)も止めない:
   判らないもので止めるのは前段の門の仕事ではない。口座の置き場の門(credential-place-mismatch)とは別の軸で、
   あちらは資格が宿の外へ出るか・こちらは宿が道具を持つか。比べる点はこの 1 つ。"
  (and (bool places) (is-not place None) (not-in place places)))


(defk compact-at-of [row]
  {:pre [(: row (| AcpRow None))]
   :post [(: % (| int None))]}
  "会話の行の status.agent.compactAt(契約 agora-kinds.json conversation.status.agent.compactAt — 0〜100 の整数)。
   行が無い・欄が無い・形が違う = None(圧縮しない — 発明しない)。"
  (when (is row None)
    (return None))
  (setv agent (if (isinstance row.status dict) (.get row.status "agent") None))
  (setv value (if (isinstance agent dict) (.get agent "compactAt") None))
  (if (and (isinstance value int) (not (isinstance value bool)) (<= 0 value 100)) value None))


(defk context-percent-of [context]
  {:pre [(: context (| dict None))]
   :post [(: % (| int None))]}
  "材料の末尾で測った文脈の大きさ(DeltaBatch.context = {tokens, window})→ 使用率(% の整数・切り捨て・上限 100)。
   window が無い(result の modelUsage が無い・codex が model_context_window を名乗らない)= None(比べない)。"
  (when (is context None)
    (return None))
  (setv tokens (.get context "tokens"))
  (setv window (.get context "window"))
  (if (and (isinstance tokens int) (isinstance window int) (> window 0))
      (min 100 (int (// (* 100 tokens) window)))
      None))


(defk compaction-due [compact-at percent]
  {:pre [(: compact-at (| int None)) (: percent (| int None))]
   :post [(: % bool)]}
  "この手番を圧縮して始めるか: 会話が閾値を宣言し(compactAt)、直前の手番の実測(percent)がそれ以上。
   どちらかが無ければ False(宣言の無い会話・測れていない session は圧縮しない)。"
  (and (is-not compact-at None) (is-not percent None) (>= percent compact-at)))


(defk context-percent-for [state session-id]
  {:pre [(: state AgentdState) (: session-id (| str None))]
   :post [(: % (| int None))]}
  "state の cache から session の直前の手番の文脈の使用率を引く(無ければ None)。"
  (when (is session-id None)
    (return None))
  (setv found None)
  (for [[sid percent] state.context-by-session]
    (when (= sid session-id)
      (setv found percent)))
  found)


(defk with-context-percent [state session-id percent]
  {:pre [(: state AgentdState) (: session-id str) (: percent (| int None))]
   :post [(: % AgentdState)]}
  "session の直前の手番の実測を state の cache に置く(同じ session は置き換え・None = 測れなかったので消す)。"
  (setv rest (tuple (lfor pair state.context-by-session :if (!= (get pair 0) session-id) pair)))
  (replace state :context-by-session (if (is percent None) rest (+ rest #(#(session-id percent))))))


(defk conversation-recorded-of [probe]
  {:pre [(: probe (| RecordPage RecordUnread None))]
   :post [(: % bool)]}
  "候補の無い job の会話に、記録された手番が在るか(段 12 lane 12j 追補 4・agora-redesign #233 / #176・問うのは claim が着いた後〔追補 7〕)。probe = 記録の service への
   1 読み(RecordReadSince since 0・limit 1・原文の kind)の答え: 原文の出来事が 1 つでも在れば真(履歴から再開する)/ 空の頁 = 偽
   (記録の無い会話 = 最初の手番 → launch)/ 読めなかった(RecordUnread)= 真(記録の在る会話の履歴を一過性の不達で失わない —
   再開の腕は届かない拍を薄い再開と名乗って ACP の見出しへ落ちる)/ None = 記録の service が配線されていない(問わない)= 偽(今日どおり
   launch — 見出しを読むのは headline-turns-for の 1 点で、第 2 の読み手を置かない〔段 9q〕)。"
  (cond
    (is probe None) False
    (isinstance probe RecordUnread) True
    True (bool probe.events)))


(defk next-arm-for-job [candidate view home effort compact]
  {:pre [(: candidate (| str None)) (: view (| SessionView None)) (: home dict) (: effort (| str None)) (: compact bool)]
   :post [(: % ArmChoice)]}
  "Bound の job の起こし方(閉語彙 effects.NextArm)— 判断はここ 1 点(R10 / R20)。candidate = 会話の前の
   session(affinity.predecessor か会話の最後の手番の session — warm-candidate-of)、view = その器の眺め、
   home = この job が走る家(session-affinity-key-of — account・binding・model の組)、effort = この job の charter の effort
   (段 10 lane 10e・None = 走行器の既定)、compact = この手番は文脈を縮めて始める(段 10f 便 2・agora-redesign #82:
   会話の宣言 compactAt を直前の手番の文脈の使用率が超えた — compaction-due の 1 点)。cache(温かい session / transcript)を
   保つのは同じ機体 ∧ 同じ家の時だけで、機体か家(profile の家)が変わる時は cache の失効を受け入れて 履歴から再開する
   (ACP の全史から)(operator 決定 2026-09-13 #54 逐語 \"i want cache kept when both machine and a profile is not changed. in
   other cases, i think i need to accept the fact that cache gets invalidated\"):
   候補が無い → launch = 新しい始まり(新しい id の session)。ただし候補の無さは『新しい会話』の証拠ではない(段 12 lane 12j 追補 4・
   agora-redesign #233 / #176: 宣言を変えた手番は Messaging の lineageFor〔段 12 lane 12k〕が predecessor を空にし、前の手番の agent-job の
   行は終了 300 s で回収される。実弾 2026-09-16 17:29 aj-545JP9E9ZMZHPM11ZW99KM51AC)— 記録の service に会話の原文が在れば launch は
   rehydrate に解ける。その問い(記録の 1 読み)と解きは **claim が着いた後**の fresh-start-arm-of の 1 点(追補 7・#233 の残債 a:
   claim の Conflict のたびに読みと log を繰り返さない — send / resume / defer と新しい id の鋳造は claim の前に要るので、ここは launch までで止める)/
   候補が生きていて idle でない ∧ backend が生きている → defer(手番の途中 — 圧縮も待つ)/
   compact ∧ 候補が在る → rehydrate(compacts — 温かい cache を捨てて記録の service の履歴から縮めて始めるのが圧縮の意味・
   生きている候補は片付ける。operator 2026-09-14 逐語 \"that routing agent should compact itself with some threshold\")/
   候補が生きて idle ∧ 同じ家 ∧ 同じ effort → send(温かい)/
   候補が生きて idle ∧ 同じ家 ∧ effort が違う → 候補を片付けて resume(段 10 lane 10e: effort は process の旗なので
   温かい process には届かない — 同じ session を新しい旗で --resume する。cache は保つ・session は作り直さない)/
   候補が生きて idle ∧ 家が違う → 候補を片付けて rehydrate(profile か model を変えた手番 — 失効した cache の器を残さない・
   新しい session は charter.model で起きる)/
   候補が生きていて idle でない ∧ backend が生きている(host の観測)→ defer(手番の途中 — 走っている手番に本文を
   積まない)/
   候補が生きていて idle でない ∧ backend が死んでいる(段 10 lane 10h・agora-redesign #84: 行は running のままだが
   process が無い — 手番は終わらないので待たない)→ 同じ家なら候補を片付けて resume(cache は保つ)、家が違えば
   候補を片付けて rehydrate(status の語で生死を推測しない — 観測は judgment.backend-alive の 1 点)/
   候補が器に登記されて終端 ∧ 同じ家 → resume(温かい session が片付いた後も cache を保つ --resume・effort は resume の
   params が運ぶ)/
   それ以外(候補が器に無い = 別の機体・器の行が消えた / 終端だが家が違う)→ rehydrate(ACP の会話の記録を
   最初の本文に畳む)。idle の温かい session は process が降りていても send(host の send が同じ session を --resume で
   起こし直す — backend の観測は手番の途中の判定にだけ効く)。"
  (<- alive bool (session-alive view))
  (<- idle bool (session-idle view))
  (<- live-backend bool (backend-alive view))
  (setv same False)
  (setv same-effort True)
  (when (isinstance view SessionView)
    (<- in-home bool (session-in-home view home))
    (setv same in-home)
    (<- launched-effort (| str None) (session-effort-of view))
    (setv same-effort (= launched-effort effort)))
  (cond
    (is candidate None) (ArmChoice :arm NEXT-ARM-LAUNCH :source None :retire None)
    (and alive (not idle) live-backend) (ArmChoice :arm NEXT-ARM-DEFER :source candidate :retire None)
    compact (ArmChoice :arm NEXT-ARM-REHYDRATE :source None :retire (if alive candidate None) :compacts True)
    (and idle same same-effort) (ArmChoice :arm NEXT-ARM-SEND :source candidate :retire None)
    (and idle same) (ArmChoice :arm NEXT-ARM-RESUME :source candidate :retire candidate)
    idle (ArmChoice :arm NEXT-ARM-REHYDRATE :source None :retire candidate)
    (and alive same) (ArmChoice :arm NEXT-ARM-RESUME :source candidate :retire candidate)
    alive (ArmChoice :arm NEXT-ARM-REHYDRATE :source None :retire candidate)
    same (ArmChoice :arm NEXT-ARM-RESUME :source candidate :retire None)
    True (ArmChoice :arm NEXT-ARM-REHYDRATE :source None :retire None)))


(defk fresh-start-asks-record [choice settings]
  {:pre [(: choice ArmChoice) (: settings AgentdSettings)]
   :post [(: % bool)]}
  "claim が着いた後に記録の service へ在否を問うか(段 12 lane 12j 追補 7): 腕が launch(候補なし = 新しい始まり)で、記録の service が
   配線されている時だけ。他の腕(send / resume / rehydrate)は問わない。判断はここ 1 点(agentd は腕の語を比べない — R10)。"
  (and (= choice.arm NEXT-ARM-LAUNCH) settings.record-enabled))


(defk fresh-start-arm-of [choice recorded]
  {:pre [(: choice ArmChoice) (: recorded bool)]
   :post [(: % ArmChoice)]}
  "claim が着いた後の腕の解き(段 12 lane 12j 追補 4 → 7・agora-redesign #233 / #176): launch(候補なし = 新しい始まり)は、記録の service に
   会話の原文が在れば(recorded = conversation-recorded-of)rehydrate に解ける — 履歴からの再開で始める。それ以外の腕(send / resume /
   rehydrate)はそのまま。判断はここ 1 点(claim の前の next-arm-for-job は launch で止める)。"
  (if (and (= choice.arm NEXT-ARM-LAUNCH) recorded)
      (replace choice :arm NEXT-ARM-REHYDRATE)
      choice))


(defk retire-reason-of [choice view job-id]
  {:pre [(: choice ArmChoice) (: view (| SessionView None)) (: job-id str)]
   :post [(: % str)]}
  "候補を片付ける理由の文(log の 1 行 — 判断は next-arm-for-job と同じ観測から): rehydrate = 家が違う /
   resume ∧ 候補が idle = effort が違う(温かい process を新しい旗で起こし直す)/ resume ∧ 候補が手番の途中 =
   backend が死んでいる(段 10 lane 10h — 手番は終わらないので待たない)。"
  (<- idle bool (session-idle view))
  (cond
    choice.compacts
    f"job {job-id} starts compacted — the conversation's context passed its compactAt threshold, so the warm session is dropped and the conversation is rehydrated from the record service (段 10f 便 2)"
    (= choice.arm NEXT-ARM-REHYDRATE)
    f"job {job-id} runs in another home (account, binding or model) — the session cache is dropped and the conversation is rehydrated"
    idle
    f"job {job-id} declares another effort — the warm process is replaced by a --resume of the same session with the new flags (cache kept)"
    True
    f"job {job-id} found the conversation's session mid-turn with a dead backend process — the row is retired and the same session is resumed (cache kept)"))


(defk fallback-arm-of [choice]
  {:pre [(: choice ArmChoice)]
   :post [(: % (| ArmChoice None))]}
  "起こす腕が器に断られた時の次の腕(R20): resume が断られた(transcript が見つからない・会話の identity が
   無い・work_dir が無い…)→ rehydrate(cache が使えない会話は ACP の記録から続ける)/ それ以外 → None(launch /
   rehydrate の断りは LaunchFailed)。断りの理由の語で分けない(器の admission の語彙に結ばない)。"
  (if (= choice.arm NEXT-ARM-RESUME)
      (ArmChoice :arm NEXT-ARM-REHYDRATE :source None :retire None)
      None))


(defk recovered-arm-of [plan view job-id]
  {:pre [(: plan LaunchPlan) (: view SessionView) (: job-id str)]
   :post [(: % str)]}
  "拾い直した Running の手番がどう始まっていたか(手番の始まりの offset の読み方): この job が起こした
   session なら刻んだ帰属の arm(launch / rehydrate = file の頭から・resume = 今の file の大きさ)、別の job が
   起こした session なら send(温かい手番 — 前の手番の行を混ぜない)、帰属が無ければ predecessor が在れば
   resume、無ければ launch。拾い直しの turn-floor は行の createdAt で、記録の進みは host の判定だけを信じる。"
  (<- mine (| dict None) (attribution-of-view view))
  (setv recorded (if (is mine None) None (.get mine "arm")))
  (cond
    (and (is-not mine None)
         (= (.get mine "agentJobId") job-id)
         (in recorded #{NEXT-ARM-LAUNCH NEXT-ARM-RESUME NEXT-ARM-REHYDRATE}))
    recorded
    (is-not mine None) NEXT-ARM-SEND
    (is plan.predecessor None) NEXT-ARM-LAUNCH
    True NEXT-ARM-RESUME))


(defk cleanup-after-end [view]
  {:pre [(: view SessionView)]
   :post [(: % bool)]}
  "終端の器を agentd が片付けるか: multi_turn は host の掃き取り(run_to_completion の
   cleanup)の対象外なので、agentd が起こした資源は agentd が session.cleanup で片付ける。"
  (= view.lifecycle LIFECYCLE-MULTI-TURN))


(defk retire-reason-after-job [job view step]
  {:pre [(: job InFlightJob) (: view SessionView) (: step str)]
   :post [(: % (| str None))]}
  "手番の終わりに agentd がその session を片付ける理由(None = 片付けない・温かいまま残す)。
   record-end で器が multi_turn(cleanup-after-end)= 終端の器の資源を agentd が片付ける。
   turn-end で取り消しの割り込みを実際に撃った job(cancel-interrupted・段 12 lane 12j・agora-redesign #422)= 割り込み(SIGINT)は器の
   transcript に『利用者が tool を拒んだ』印(Request interrupted by user for tool use)として残り、温かいまま次の手番が
   resume すると agent が再実行を断って確認待ちにする。取り消しは手番を捨てる決定で、文脈の値打ちは記録(turn-record の
   rehydrate)が持つ — session は片付け、次の手番は記録から新しい session を起こす。cancel-forced は force-cancel が
   既に片付けている(None)。session-lost は host の monitor が終端に倒す(None)。手番が既に終わっていて割り込まなかった取り消しは
   印が無いので温かいまま(None)。

   ⚠ cleanup-after-end は defk(Program を返す)なので、`and` に渡す前に `<-` で値へ解く — 直に置くと Program は常に
   truthy で、run_to_completion の器まで record-end で片付けてしまう(実弾 = 277ae0f6 で赤くなった
   test-charter-lifecycle-is-respected-when-declared)。"
  (<- retire bool (cleanup-after-end view))
  (when (and (= step JOB-STEP-RECORD-END) retire)
    (return f"session {view.status} at the end of job {job.job-id}"))
  (when (and (= step JOB-STEP-TURN-END) job.cancel-interrupted)
    (return (+ f"cancelled turn of job {job.job-id} leaves the interrupt in the transcript — "
               "the next turn rehydrates from the record (#422)")))
  None)


(defk sessions-to-retire [views now-ms ttl-seconds]
  {:pre [(: views tuple) (: now-ms int) (: ttl-seconds int)]
   :post [(: % tuple)]}
  "idle が TTL を過ぎた温かい session の id(片付ける対象)。時計は引数(effect は呼び手)。"
  (setv out [])
  (for [view views]
    (<- idle bool (session-idle view))
    (when (and idle
               (is-not view.turn-ended-at-ms None)
               (>= now-ms (+ view.turn-ended-at-ms (* 1000 ttl-seconds))))
      (.append out view.session-id)))
  (tuple out))


(defk withdrawn-session-ids-of [rows node-name node-row-id principal]
  {:pre [(: rows tuple) (: node-name str) (: node-row-id (| str None)) (: principal str)]
   :post [(: % tuple)]}
  "Withdrawn の行のうち自分が claim していた行の session の id(手番の取り下げ — 走っている
   session を片付ける対象)。"
  (setv out [])
  (for [row rows]
    (setv status row.status)
    (when (and (isinstance status dict) (= (.get status "phase") PHASE-WITHDRAWN))
      (<- sid (| str None) (handle-owned-by row node-name node-row-id principal))
      (when (and (is-not sid None) (not-in sid out))
        (.append out sid))))
  (tuple out))


(defk withdrawn-rows-of [rows node-name node-row-id principal]
  {:pre [(: rows tuple) (: node-name str) (: node-row-id (| str None)) (: principal str)]
   :post [(: % tuple)]}
  "Withdrawn の行のうち自分が claim していた行(行の順のまま)— 取り下げ = 走っている手番を
   止める合図(session は片付けない・idle の寿命は sessions-to-retire)。"
  (setv out [])
  (for [row rows]
    (setv status row.status)
    (when (and (isinstance status dict) (= (.get status "phase") PHASE-WITHDRAWN))
      (<- sid (| str None) (handle-owned-by row node-name node-row-id principal))
      (when (is-not sid None)
        (.append out row))))
  (tuple out))


(defk interrupt-arm-for [job view]
  {:pre [(: job InFlightJob) (: view (| SessionView None))]
   :post [(: % str)]}
  "取り下げ(Withdrawn)を受けた自分の job の腕(閉語彙 effects.InterruptArm)— 判断はここ
   1 点: 器が生きていて、この手番がまだ終わっていない(turn_ended_at が無いか、この手番の
   始まりの下限より前 = 前の手番の終わり)→ interrupt(session.interrupt を撃つ・session は
   残す)/ それ以外(器が無い・終端・手番は既に終わっている)→ none。"
  (<- alive bool (session-alive view))
  (if (and alive
           (isinstance view SessionView)
           (or (is view.turn-ended-at-ms None)
               (<= view.turn-ended-at-ms job.turn-floor-ms)))
      INTERRUPT-ARM-INTERRUPT
      INTERRUPT-ARM-NONE))


(defk interrupted-status-of [status]
  {:pre [(: status dict)]
   :post [(: % dict)]}
  "取り下げで止めた手番の agent-job の status: phase は書かない(Withdrawn のまま — 書き手は
   作った側)、conditions に Interrupted を 1 つ足す(既に在れば足さない)。段 12 lane 12k(agora-redesign #349 行 3 粒 3a):
   終端の理由も result.cause {category: interrupted, reason: withdrawn} に載せる(Withdrawn の行の result は agentd の欄 — 既に
   在る結末は保つ・cause は同じ値なので書き直しは冪等)。"
  (setv next (dict status))
  (setv existing (.get status "conditions"))
  (setv conditions (if (isinstance existing list) (list existing) []))
  (when (not (any (gfor item conditions
                        (and (isinstance item dict) (= (.get item "type") CONDITION-INTERRUPTED)))))
    (<- condition dict (condition-of CONDITION-INTERRUPTED "agent-job withdrawn while the turn was running"))
    (.append conditions condition))
  (setv (get next "conditions") conditions)
  (<- cause dict (terminal-cause-of CAUSE-CATEGORY-INTERRUPTED CAUSE-REASON-WITHDRAWN))
  (<- carried dict (result-with-cause (.get status "result") cause))
  (setv (get next "result") carried)
  next)


;; ---------------------------------------------------------------------------
;; 取り消しの 3 段(段 12 lane 12j・agora-redesign #367・契約 scheduling.json の cancel の節): 合図 → 猶予 → 強制
;; ---------------------------------------------------------------------------

(defk job-cancel-of [row]
  {:pre [(: row AcpRow)]
   :post [(: % (| JobCancel None))]}
  "行の spec.cancel(段 1 の合図)→ JobCancel。無い・壊れた形(requestedAt が整数でない・reason が空か文字列でない・
   graceSeconds が整数でないか負・by が文字列でない)= None — 壊れた合図で手番を止めない(engine の intent cancel-job
   が形を検めるので、壊れた形は写しの欠陥の印)。graceSeconds の欠落 = 契約の既定(DEFAULT-CANCEL-GRACE-SECONDS)。"
  (setv raw (.get row.spec JOB-SPEC-CANCEL-KEY))
  (when (not (isinstance raw dict))
    (return None))
  (setv requested (.get raw CANCEL-REQUESTED-AT-KEY))
  (setv reason (.get raw CANCEL-REASON-KEY))
  (setv grace (.get raw CANCEL-GRACE-SECONDS-KEY DEFAULT-CANCEL-GRACE-SECONDS))
  (setv by (.get raw CANCEL-BY-KEY ""))
  (when (or (not (isinstance requested int)) (isinstance requested bool)
            (not (isinstance reason str)) (= reason "")
            (not (isinstance grace int)) (isinstance grace bool) (< grace 0)
            (not (isinstance by str)))
    (return None))
  (JobCancel :requested-at-ms requested :grace-seconds grace :reason reason :by by))


(defk cancel-deadline-ms [cancel]
  {:pre [(: cancel JobCancel)]
   :post [(: % int)]}
  "猶予の期限(ms)= requestedAt + graceSeconds × 1000(ACP の Acp.App.Agent.AgentJob.cancelDeadline と同じ算)。"
  (+ cancel.requested-at-ms (* 1000 cancel.grace-seconds)))


(defk cancel-arm-for [job view cancel now-ms]
  {:pre [(: job InFlightJob) (: view (| SessionView None)) (: cancel JobCancel) (: now-ms int)]
   :post [(: % str)]}
  "取り消しの合図を受けた自分の job の腕(閉語彙 effects.CancelArm)— 判断はここ 1 点: まだ見届けていなければ
   acknowledge(手番の途中なら割り込み・status.cancel を書く — 手番が既に終わっていても見届けは書く)/ 見届け済みで
   猶予の期限を過ぎ、器が生きていてこの手番がまだ終わっていなければ force / それ以外は none(手番の終わりを待つ —
   終われば finalize が result.cause {graceful} を書く)。手番が走っているかは取り下げと同じ 1 点 interrupt-arm-for。"
  (<- running str (interrupt-arm-for job view))
  (<- deadline int (cancel-deadline-ms cancel))
  (cond
    (is job.cancel-acknowledged-at-ms None) CANCEL-ARM-ACKNOWLEDGE
    (and (>= now-ms deadline) (= running INTERRUPT-ARM-INTERRUPT)) CANCEL-ARM-FORCE
    True CANCEL-ARM-NONE))


(defk cancel-acknowledged-status-of [status now-ms]
  {:pre [(: status dict) (: now-ms int)]
   :post [(: % dict)]}
  "見届けの status(段 2): status.cancel = {acknowledgedAt: now, stage: graceful}(既に在れば変えない — 見届けは 1 度)。
   phase は書かない(Running のまま — 終端の書きは最後)。"
  (setv next (dict status))
  (setv existing (.get status JOB-STATUS-CANCEL-KEY))
  (when (not (isinstance existing dict))
    (setv (get next JOB-STATUS-CANCEL-KEY)
          {CANCEL-ACKNOWLEDGED-AT-KEY now-ms CANCEL-STAGE-KEY CANCEL-STAGE-GRACEFUL}))
  next)


(defk cancelled-cause-of [cancel stage]
  {:pre [(: cancel JobCancel) (: stage str)]
   :post [(: % dict)]}
  "Ended の result.cause(契約 scheduling.json cancel.result.cause): {category: cancelled, stage, reason}。"
  {"category" CAUSE-CATEGORY-CANCELLED CANCEL-STAGE-KEY stage CANCEL-REASON-KEY cancel.reason})


(defk terminal-cause-of [category reason]
  {:pre [(: category str) (: reason (| str None))]
   :post [(: % dict)]}
  "終端の result.cause(契約 scheduling.json resultCause・段 12 lane 12k・agora-redesign #349 行 3 粒 3a): {category, reason?}。
   category は effects.CauseCategory の閉語彙ちょうど — 表の外は断る(契約に無い語を agentd が書かない)。
   取り消しの cause(stage つき)は cancelled-cause-of。"
  (when (not-in category CAUSE-CATEGORIES)
    (raise (ValueError f"result.cause.category {(repr category)} is outside {CAUSE-CATEGORIES}")))
  (setv cause {CAUSE-CATEGORY-KEY category})
  (when (isinstance reason str)
    (setv (get cause CAUSE-REASON-KEY) reason))
  cause)


(defk command-cause-of [conditions]
  {:pre [(: conditions tuple)]
   :post [(: % dict)]}
  "命令の job(verify / summarize — agent を起こさない)の終端の cause: 条件が 1 つも無ければ completed(赤の rc も結末であって
   条件ではない)、在れば failed で reason = 先頭の条件の型(この族の条件は全部が失敗の印 — Verify* / Summarize*)。"
  (if conditions
      (do
        (setv first (get conditions 0))
        (<- failed dict (terminal-cause-of CAUSE-CATEGORY-FAILED (str (.get first "type"))))
        failed)
      (do
        (<- completed dict (terminal-cause-of CAUSE-CATEGORY-COMPLETED None))
        completed)))


(defk result-with-cause [result cause]
  {:pre [(: result (| dict list str int float bool None)) (: cause dict)]
   :post [(: % dict)]}
  "手番の結末に取り消しの cause を載せる: dict の結末はその欄 cause に(器の cause より取り消しが正 — 合図が先に在った)、
   None は cause だけ、dict でない結末は {value, cause} に包む(器の答えを落とさない)。"
  (cond
    (isinstance result dict) (do (setv next (dict result))
                                 (setv (get next RESULT-CAUSE-KEY) cause)
                                 next)
    (is result None) {RESULT-CAUSE-KEY cause}
    True {"value" result RESULT-CAUSE-KEY cause}))


(defk outcome-with-cancel [outcome cancel stage]
  {:pre [(: outcome JobOutcome) (: cancel (| JobCancel None)) (: stage str)]
   :post [(: % JobOutcome)]}
  "取り消された job の結末に cause を載せる(cancel が None = 取り消されていない → 不変)。取り消しの cause は器の結末の cause より正
   (合図が先に在った)。result は触らない — cause を result に載せるのは ended-status-of の 1 点(#349 行 3 粒 3a)。"
  (when (is cancel None)
    (return outcome))
  (<- cause dict (cancelled-cause-of cancel stage))
  (replace outcome :cause cause))


(defk outcome-with-limit [outcome limit]
  {:pre [(: outcome JobOutcome) (: limit (| dict None))]
   :post [(: % JobOutcome)]}
  "provider の限度の条件(provider-limit-condition-of・None = 断りではない)を結末の cause に写す(段 12 lane 12k・agora-redesign
   #349 行 3 粒 3a): cause が completed / failed の時だけ {category: failed, reason: ProviderLimit} に置き換える(取り消し・停止の
   cause は上書きしない — 合図が先に在った)。value は書かない(value は手番が報告した結果)。"
  (when (is limit None)
    (return outcome))
  (setv category (if (isinstance outcome.cause dict) (.get outcome.cause CAUSE-CATEGORY-KEY) None))
  (when (not-in category #(CAUSE-CATEGORY-COMPLETED CAUSE-CATEGORY-FAILED None))
    (return outcome))
  (<- refused dict (terminal-cause-of CAUSE-CATEGORY-FAILED CONDITION-PROVIDER-LIMIT))
  (replace outcome :cause refused))


;; 依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB(D1): 手番の終わりの結末は、その手番が**何か出したか**から導く — 温かい手番の終わりを
;; 無条件に completed と名乗らない(旧形: finalize-job の turn-end の腕が completed を貼り直し、出力 0 件の手番と働いた手番が
;; 結末の型で区別できなかった)。


(defk turn-produced-nothing-condition-of [step source path batch turn-error]
  {:pre [(: step str) (: source (| str None)) (: path (| str None)) (: batch DeltaBatch) (: turn-error (| str None))]
   :post [(: % (| dict None))]}
  "温かい手番の終わり(turn-end)で本文のための model の出力が 1 本も無かったか —— **判断の 1 点**。None = 何か出した /
   温かい手番の終わりではない / 材料が読めない器(stream の path が無い — 読めないことは「何も出していない」の証拠ではない)。

   材料 = 手番の始まりから読み直した batch(turn-batch-of): assistant の見出し(text / tool_use / tool_result)が 0 本で、
   usage も無い(usage は assistant の message からだけ組む — result の行の usage は数えない。thinking だけの手番も usage は
   在るので当たらない)。= model が本文のために 1 度も呼ばれていない = 手番が走らなかった事実。条件の文には根拠を残す
   (system の見出しの数・usage の無さ・器が名乗った手番の失敗の文 turn-error — D2)。"
  (when (!= step JOB-STEP-TURN-END)
    (return None))
  (when (or (is source None) (is path None))
    (return None))
  (when (is-not batch.usage None)
    (return None))
  (setv kinds (lfor entry batch.entries :if (isinstance entry TurnEntryHeadline) entry.kind))
  (when (any (gfor kind kinds (in kind TURN-OUTPUT-ENTRY-KINDS)))
    (return None))
  (setv system-count (len (lfor kind kinds :if (= kind ENTRY-KIND-SYSTEM) kind)))
  (setv said (if (and (isinstance turn-error str) (.strip turn-error))
                 f"; the runner said the turn failed: {(.strip turn-error)}"
                 "; the runner reported the turn as ended without an error"))
  (<- condition dict
      (condition-of CONDITION-TURN-PRODUCED-NOTHING
                    (+ f"the turn ended with no model output for its input ({(len kinds)} headlines, "
                       f"{system-count} system, 0 text / tool_use / tool_result, no usage){said} — "
                       "the model was never called, so the turn did not run")))
  condition)


(defk outcome-with-nothing [outcome nothing]
  {:pre [(: outcome JobOutcome) (: nothing (| dict None))]
   :post [(: % JobOutcome)]}
  "出力 0 件の条件(turn-produced-nothing-condition-of・None = 何か出した)を結末に写す(依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB・D1):
   cause が completed の時だけ {category: failed, reason: TurnProducedNothing} に置き換え、条件を 1 項足す。取り消し・停止・
   限度・器の失敗の cause は上書きしない(合図・決定的な理由が先に在った — 手番が走らなかった側へ畳まない・D3)。"
  (when (is nothing None)
    (return outcome))
  (setv category (if (isinstance outcome.cause dict) (.get outcome.cause CAUSE-CATEGORY-KEY) None))
  (when (!= category CAUSE-CATEGORY-COMPLETED)
    (return outcome))
  (<- failed dict (terminal-cause-of CAUSE-CATEGORY-FAILED CONDITION-TURN-PRODUCED-NOTHING))
  (replace outcome :cause failed :conditions (+ outcome.conditions #(nothing))))


(defk recovered-cancel-of [job row]
  {:pre [(: job InFlightJob) (: row AcpRow)]
   :post [(: % InFlightJob)]}
  "拾い直し(再起動後)の job に行の取り消しを写す: spec.cancel → cancel、status.cancel.acknowledgedAt → 見届けの拍
   (無ければ None = 次の拍に見届け直す — 割り込みは改めて撃つ・見届けの書きは既に在れば変えない)。"
  (<- cancel (| JobCancel None) (job-cancel-of row))
  (<- status dict (status-object-of row))
  (setv existing (.get status JOB-STATUS-CANCEL-KEY))
  (setv acknowledged (if (isinstance existing dict) (.get existing CANCEL-ACKNOWLEDGED-AT-KEY) None))
  (setv acknowledged-ms (if (and (isinstance acknowledged int) (not (isinstance acknowledged bool))) acknowledged None))
  ;; #422: 行に見届けが在れば割り込みは撃たれたとみなす(印の有無は読めない — 片付ける側に倒す)
  (replace job :cancel cancel
               :cancel-acknowledged-at-ms acknowledged-ms
               :cancel-interrupted (is-not acknowledged-ms None)))


;; ---------------------------------------------------------------------------
;; 着かなかった Ended と置き直された試み(段 12 lane 12j・agora-redesign #402): 同じ手番を別の session で走らせない
;; ---------------------------------------------------------------------------

(defk unrecorded-end-of [job result cause conditions now-ms]
  {:pre [(: job InFlightJob) (: result (| dict list str int float bool None)) (: cause dict) (: conditions tuple) (: now-ms int)]
   :post [(: % UnrecordedEnd)]}
  "着かなかった Ended の持ち越しの材料(結末・cause・条件はそのまま・at = 手番の終わりの拍)。"
  (UnrecordedEnd :job-key job.job-key :job-id job.job-id :session-id job.session-id
                 :result result :cause cause :conditions conditions :at-ms now-ms))


(defk end-retry-verdict [row session-id principal at-ms now-ms ttl-ms]
  {:pre [(: row (| AcpRow None)) (: session-id str) (: principal str) (: at-ms int) (: now-ms int) (: ttl-ms int)]
   :post [(: % str)]}
  "着かなかった Ended を行に書き直すか(閉語彙 effects.EndRetryVerdict)— 判断はここ 1 点: 行が無い・終端(Ended / Withdrawn)・
   別の session が走らせている Running(sessionHandle が自分の session でない)・持ち越しの上限を過ぎた → drop。
   Pending(監督が解いた)・Bound(置き直しの試み attempt N)・自分の session の Running → write(手番は終わっている —
   同じ手番を別の session で走らせない・監督の置き直しはこの Ended で閉じる)。"
  (when (or (is row None) (> (- now-ms at-ms) ttl-ms))
    (return END-RETRY-DROP))
  (setv status (if (isinstance row.status dict) row.status {}))
  (setv phase (.get status "phase"))
  (setv handle (.get status "sessionHandle"))
  (setv stream (if (isinstance handle dict) (.get handle "stream") None))
  (setv mine (and (isinstance handle dict)
                  (= (.get handle "sessionId") session-id)
                  (isinstance stream dict)
                  (= (.get stream "owner") principal)))
  (cond
    (in phase #{PHASE-PENDING PHASE-BOUND}) END-RETRY-WRITE
    (and (= phase PHASE-RUNNING) mine) END-RETRY-WRITE
    True END-RETRY-DROP))


(defk with-unrecorded-end [state end]
  {:pre [(: state AgentdState) (: end UnrecordedEnd)]
   :post [(: % AgentdState)]}
  "持ち越しを置く(同じ job は 1 つ — 後の方で置き換える)。"
  (setv kept (lfor existing state.unrecorded-ends :if (!= existing.job-id end.job-id) existing))
  (replace state :unrecorded-ends (tuple (+ kept [end]))))


(defk without-unrecorded-end [state job-id]
  {:pre [(: state AgentdState) (: job-id str)]
   :post [(: % AgentdState)]}
  (replace state :unrecorded-ends (tuple (lfor existing state.unrecorded-ends :if (!= existing.job-id job-id) existing))))


(defk unrecorded-end-ids [state]
  {:pre [(: state AgentdState)]
   :post [(: % set)]}
  "持ち越している job の id(claim の門の材料 — この id の Bound の行は受けない)。"
  (set (gfor end state.unrecorded-ends end.job-id)))


(defk rebound-rows-of [rows jobs]
  {:pre [(: rows tuple) (: jobs tuple)]
   :post [(: % tuple)]}
  "自分に結ばれた Bound の行のうち、自分がいま走らせている job(memory の InFlightJob)と同じ id のもの = 監督が置き直した試み
   (attempt N)。新しい session を起こさず、走っている session を名乗って Running に戻す(引き継ぐ)相手。"
  (setv mine (sfor job jobs job.job-id))
  (setv out [])
  (for [row rows]
    (setv status row.status)
    (when (and (isinstance status dict) (= (.get status "phase") PHASE-BOUND) (in row.resource-id mine))
      (.append out row)))
  (tuple out))


;; ---------------------------------------------------------------------------
;; 割り込みの本文(段 8 lane 4x・agora-redesign #56): 行の interrupts を器へ渡す
;; ---------------------------------------------------------------------------

(defk string-list-of [status key]
  {:pre [(: status dict) (: key str)]
   :post [(: % tuple)]}
  "status の欄 key の文字の列(無い・list でない・文字でない項は空 / 落とす — 発明しない)。"
  (setv raw (.get status key))
  (if (isinstance raw list)
      (tuple (lfor item raw :if (isinstance item str) item))
      #()))


(defk pending-interrupts-of [row sent]
  {:pre [(: row AcpRow) (: sent tuple)]
   :post [(: % tuple)]}
  "行の status.interrupts のうち、まだ器へ渡していない Message の id(載せた順): 行の
   interruptsDelivered に無く、memory の sent(CAS が着地するまでの写し)にも無いもの。
   level-triggered — 判断はこの行と memo だけで、前の拍の何も要らない。"
  (<- status dict (status-object-of row))
  (<- pending tuple (string-list-of status JOB-INTERRUPTS-KEY))
  (<- delivered tuple (string-list-of status JOB-INTERRUPTS-DELIVERED-KEY))
  (setv seen (set))
  (setv out [])
  (for [message-id pending]
    (when (and (not-in message-id delivered) (not-in message-id sent) (not-in message-id seen))
      (.add seen message-id)
      (.append out message-id)))
  (tuple out))


(defk interrupts-delivered-status-of [status ids]
  {:pre [(: status dict) (: ids tuple)]
   :post [(: % dict)]}
  "渡した割り込みを行に写した status(同じ 1 回の書き): interruptsDelivered の末尾に ids を足し
   (既に在る id は足さない・順は保つ)、interrupts から渡した id を全部消す(ids と、既に
   delivered に在った id — 載せ直された id を二度渡さない)。他の欄は写す。"
  (setv next (dict status))
  (<- pending tuple (string-list-of status JOB-INTERRUPTS-KEY))
  (<- delivered tuple (string-list-of status JOB-INTERRUPTS-DELIVERED-KEY))
  (setv all-delivered (+ (list delivered) (lfor message-id ids :if (not-in message-id delivered) message-id)))
  (setv (get next JOB-INTERRUPTS-KEY) (lfor message-id pending :if (not-in message-id all-delivered) message-id))
  (setv (get next JOB-INTERRUPTS-DELIVERED-KEY) all-delivered)
  next)


(defk inputs-delivered-status-of [status ids]
  {:pre [(: status dict) (: ids tuple)]
   :post [(: % dict)]}
  "器へ渡せた inputs の郵便を行に写した status(card acp:kanban-issue:ki-3149aebbf675 A・同じ 1 回の書き):
   inputsDelivered の末尾に ids を足す(既に在る id は足さない・順は保つ)。他の欄は写す。
   append-only ちょうど — 消す腕は無い(割り込みの interrupts → interruptsDelivered のような移し替えも無い:
   inputs は spec の欄で agentd は書かない)。"
  (setv next (dict status))
  (<- delivered tuple (string-list-of status JOB-INPUTS-DELIVERED-KEY))
  (setv (get next JOB-INPUTS-DELIVERED-KEY)
        (+ (list delivered) (lfor message-id ids :if (not-in message-id delivered) message-id)))
  next)


(defk escalation-seconds-of-charter [charter]
  {:pre [(: charter dict)]
   :post [(: % (| int None))]}
  "期限(秒)= charter.interruptEscalationSeconds(段 10 lane 10n・依頼者の追補 2026-09-14: 方策の行の値を Messaging の
   Plan.charterFor が会話の宣言で重ねて charter に写す)。agentd はこの欄だけを読む — 方策も会話も読まず、code に既定を
   置かない。無い・整数でない・負 = None(宣言なし → 注入だけ + 条件 InterruptEscalationUndeclared)。"
  (setv raw (.get charter CHARTER-INTERRUPT-ESCALATION-KEY))
  (if (and (isinstance raw int) (not (isinstance raw bool)) (>= raw 0)) raw None))


(defk with-injected-interrupts [job ids now-ms]
  {:pre [(: job InFlightJob) (: ids tuple) (: now-ms int)]
   :post [(: % InFlightJob)]}
  "注入した割り込みを memory に積む(id → 注入した時刻・既に在る id は積まない)。"
  (setv known (sfor [message-id _] job.interrupts-injected message-id))
  (setv added (tuple (lfor message-id ids :if (not-in message-id known) #(message-id now-ms))))
  (replace job :interrupts-injected (+ job.interrupts-injected added)))


(defk unread-interrupts-of [job]
  {:pre [(: job InFlightJob)]
   :post [(: % tuple)]}
  "注入したが model が読んだ証拠のまだ無い割り込みの id(注入した順)。"
  (setv read-ids (sfor [message-id _] job.interrupts-read message-id))
  (tuple (lfor [message-id _] job.interrupts-injected :if (not-in message-id read-ids) message-id)))


(defk interrupt-reads-of [job reads]
  {:pre [(: job InFlightJob) (: reads tuple)]
   :post [(: % tuple)]}
  "材料の証拠(DeltaBatch.interrupt-reads)→ 新しく読んだと判る (id, seq) の列(判断はここ 1 点): 名の在る証拠
   (claude の command_lifecycle started の command_uuid = Message の id)はその id だけ、名の無い証拠(codex の
   turn/started — 止めた後の手番は積んであった注入を全部読む)は未読の id を全部、その証拠の seq で。同じ id は
   最初の証拠だけ(順は注入した順)。"
  (<- unread-ids tuple (unread-interrupts-of job))
  (setv unread (list unread-ids))
  (setv out [])
  (for [evidence reads]
    (if (is evidence.ref None)
        (do
          (for [message-id unread]
            (.append out #(message-id evidence.seq)))
          (setv unread []))
        (when (in evidence.ref unread)
          (.append out #(evidence.ref evidence.seq))
          (.remove unread evidence.ref))))
  (tuple out))


(defk interrupts-due-for-escalation [job now-ms]
  {:pre [(: job InFlightJob) (: now-ms int)]
   :post [(: % tuple)]}
  "停止の合図を出す拍か(判断はここ 1 点): 期限が宣言されていて(charter の値・None = 出さない)、注入から期限の秒が
   経ち、読んだ証拠も出した印も無い id(注入した順)。1 つでも在れば呼び手が合図を 1 度出し、未読の id 全部に印を付ける
   (合図は session に 1 つ — CLI は queued の注入を全部次の手番に運ぶ)。"
  (setv seconds job.interrupt-escalation-seconds)
  (if (is seconds None)
      #()
      (do
        (setv read-ids (sfor [message-id _] job.interrupts-read message-id))
        (setv escalated-ids (sfor [message-id _] job.interrupts-escalated message-id))
        (tuple (lfor [message-id at-ms] job.interrupts-injected
                     :if (and (not-in message-id read-ids)
                              (not-in message-id escalated-ids)
                              (>= (- now-ms at-ms) (* 1000 seconds)))
                     message-id)))))


(defk interrupt-marks-status-of [status read escalated]
  {:pre [(: status dict) (: read tuple) (: escalated tuple)]
   :post [(: % dict)]}
  "読んだ / 止めた印を行の status に写す(同じ 1 回の書き・additive): interruptsRead は {id: seq}・interruptsEscalated は
   {id: ms} の map に足す(既に在る id は変えない — append-only)。他の欄は写す。"
  (setv next (dict status))
  (for [[key marks] [#(JOB-INTERRUPTS-READ-KEY read) #(JOB-INTERRUPTS-ESCALATED-KEY escalated)]]
    (setv existing (.get status key))
    (setv table (if (isinstance existing dict) (dict existing) {}))
    (for [[message-id value] marks]
      (when (not-in message-id table)
        (setv (get table message-id) value)))
    (when (or table (isinstance existing dict))
      (setv (get next key) table)))
  next)


(defk status-with-condition [status condition-type reason]
  {:pre [(: status dict) (: condition-type str) (: reason str)]
   :post [(: % dict)]}
  "status の conditions に type の条件を 1 つ足す(既に在れば足さない・他の欄は写す)。"
  (setv next (dict status))
  (setv existing (.get status "conditions"))
  (setv conditions (if (isinstance existing list) (list existing) []))
  (when (not (any (gfor item conditions
                        (and (isinstance item dict) (= (.get item "type") condition-type)))))
    (<- condition dict (condition-of condition-type reason))
    (.append conditions condition))
  (setv (get next "conditions") conditions)
  next)


(defk interrupt-escalation-undeclared-reason [job]
  {:pre [(: job InFlightJob)]
   :post [(: % str)]}
  "条件 InterruptEscalationUndeclared の理由の文(charter に期限が無い job に割り込みを注入した)。"
  (+ f"charter of agent-job {job.job-id} declares no {CHARTER-INTERRUPT-ESCALATION-KEY}; "
     "the interrupt was injected but no stop signal will follow"))


(defk recovered-interrupts-of [job row now-ms]
  {:pre [(: job InFlightJob) (: row AcpRow) (: now-ms int)]
   :post [(: % InFlightJob)]}
  "拾い直し(再起動後)の割り込みの memory: 行の interruptsDelivered(渡した id)のうち、行の interruptsRead にも
   interruptsEscalated にも無い id を、拾い直した時刻で注入したものとして積む(期限はそこから数える — 注入の時刻は
   行に無い)。読んだ / 止めた印は行の写し。"
  (<- status dict (status-object-of row))
  (<- delivered tuple (string-list-of status JOB-INTERRUPTS-DELIVERED-KEY))
  (setv read-table (.get status JOB-INTERRUPTS-READ-KEY))
  (setv escalated-table (.get status JOB-INTERRUPTS-ESCALATED-KEY))
  (setv read (tuple (lfor [message-id seq] (.items (if (isinstance read-table dict) read-table {}))
                          :if (and (isinstance message-id str) (isinstance seq int) (not (isinstance seq bool)))
                          #(message-id seq))))
  (setv escalated (tuple (lfor [message-id at-ms] (.items (if (isinstance escalated-table dict) escalated-table {}))
                               :if (and (isinstance message-id str) (isinstance at-ms int) (not (isinstance at-ms bool)))
                               #(message-id at-ms))))
  (setv settled (| (sfor [message-id _] read message-id) (sfor [message-id _] escalated message-id)))
  (setv injected (tuple (lfor message-id delivered :if (not-in message-id settled) #(message-id now-ms))))
  (replace job :interrupts-injected injected :interrupts-read read :interrupts-escalated escalated
               :interrupts-sent (+ job.interrupts-sent delivered)))


(defk job-row-keyed [rows job-key]
  {:pre [(: rows tuple) (: job-key str)]
   :post [(: % (| AcpRow None))]}
  "cache の行を鍵で(無ければ None)。"
  (setv found None)
  (for [row rows]
    (when (and (is found None) (= row.key job-key))
      (setv found row)))
  found)


(defk stream-capability-of-backend [backend]
  {:pre [(: backend str)]
   :post [(: % str)]}
  "node の observations.streamCapability は backend から導く(契約 turn-delta.json capability):
   headless = events(stdout の行の増分)・tmux / herdr = frames(pane の断面)。"
  (if (= backend BACKEND-HEADLESS) STREAM-CAPABILITY-EVENTS STREAM-CAPABILITY-FRAMES))


(defk session-observations-of [views]
  {:pre [(: views tuple)]
   :post [(: % list)]}
  "node の status.observations.sessions — 器の眺めと session に刻んだ帰属から導いた
   [{conversationId, sessionId, state, account}](生きている温かい session だけ・state は idle | busy・
   account = 帰属の account(null = 借りていない))。帰属の無い session(段 8q より前に起こした・他の
   起こし手)は載せない(発明しない)。会話の対応は session の行が覚える事実で、回収される agent-job の
   行に頼らない(R20)。"
  (setv out [])
  (for [view views]
    (<- alive bool (session-alive view))
    (when (and alive (= view.lifecycle LIFECYCLE-MULTI-TURN))
      (<- mine (| dict None) (attribution-of-view view))
      (when (is-not mine None)
        (<- idle bool (session-idle view))
        (.append out {"conversationId" (get mine "conversationId")
                      "sessionId" view.session-id
                      "state" (if idle SESSION-OBSERVED-IDLE SESSION-OBSERVED-BUSY)
                      "account" (.get mine "account")}))))
  out)


(defk stale-conversation-sessions-of [views subject home keep-session-id]
  {:pre [(: views tuple) (: subject str) (: home dict) (: keep-session-id str)]
   :post [(: % tuple)]}
  "会話の**他の家**に残る温かい session(段 12 lane 12j・agora-redesign #379 受入 2 = 1 会話 1 温かい session): 生きている
   multi_turn の session のうち、帰属の conversationId == subject ∧ 家がこの手番の家(home = session-affinity-key-of)と違う ∧
   この手番が使う session(keep-session-id)ではないものの id(views の順)。同じ家の session は触らない(候補 = send / resume の
   相手)。行(agent-job)ではなく器の帰属から読む — 前の手番の行は終了 300 s で回収され、候補の無い手番が別の家で起きた拍に
   古い家の session が残って配置の親和の材料(node の observations.sessions)に化けた(#352 受入 1 の実弾・#379)。"
  (setv out [])
  (for [view views]
    (<- alive bool (session-alive view))
    (when (and alive (= view.lifecycle LIFECYCLE-MULTI-TURN) (!= view.session-id keep-session-id))
      (<- mine (| dict None) (attribution-of-view view))
      (when (and (is-not mine None) (= (.get mine "conversationId") subject))
        (<- in-home bool (session-in-home view home))
        (when (not in-home)
          (.append out view.session-id)))))
  (tuple out))


(defk profile-accounts-of [rows]
  {:pre [(: rows tuple)]
   :post [(: % dict)]}
  "生きている profile の行 → {profile の名: 預かり所の account}(段 12・agora-redesign #577)。

   pane の席の口座を解く目録は**手番の資格と同じ 1 点**(profile の行の spec.account)で、第 2 の
   対応表を作らない。account を持たない行・退いた行(state retired)は載せない — 名の無い家を推し量らないのは
   配置の係と同じ作法(ACP の seatOccupancy も『account を宣言しない profile に pane の半分は無い』)。"
  (setv out {})
  (<- active tuple (profile-rows-active rows))
  (for [row active]
    (setv name (str (.get row.spec "name" row.resource-id)))
    (setv account (.get row.spec "account"))
    (when (and name (isinstance account str) account)
      (setv (get out name) account)))
  out)


(defk pane-observations-of [seats accounts sessions]
  {:pre [(: seats tuple) (: accounts dict) (: sessions list)]
   :post [(: % list)]}
  "pane の席 → node の status.observations.sessions に足す要素の列(段 12・agora-redesign #577)。

   数える側(ACP の paneSeatsByAccount)が読むのは『生きた lease の node の busy な session で、家が名の在る
   account で、会話の担い手が pane の行』ちょうど。ここが供給するのはその材料で、判断は 3 つだけ:
     * **自分の session と二重に載せない** — 同じ会話の agent-job の session が既に列に在る席は落とす
       (担い手の路で半分は分かれるが、1 つの会話を 2 度載せない side を機体でも閉じる)。
     * **口座は目録で解く**(profile-accounts-of)。解けない席(profile を実測できない・行が account を
       持たない)は **account の欄ごと載せない** = 契約の『欠落 = 不明』で、数える側は不明な家を占有に
       数えない(推し量らない)。
     * **state は席が測った語をそのまま運ぶ**(busy = 手番を走らせている / idle = 入力待ち)。毎観測で
       測り直すので、終わった手番は次の観測で idle に落ちる(席を解放する通知は要らない)。"
  (setv mine (set (gfor item sessions (.get item "conversationId"))))
  (setv out [])
  (for [seat seats]
    (when (not-in seat.conversation-id mine)
      (setv item {"conversationId" seat.conversation-id
                  "sessionId" seat.session-id
                  "state" seat.state})
      (setv account (.get accounts seat.profile))
      (when (isinstance account str)
        (setv (get item "account") account))
      (.append out item)))
  out)


(defk transcript-candidates-of [views sessions limit]
  {:pre [(: views tuple) (: sessions list) (: limit int)]
   :post [(: % tuple)]}
  "node の observations.transcripts の候補(段 8q・R20): 終端の session のうち agentd の帰属と会話の
   identity を持ち、同じ会話の生きた session が sessions に無いもの — 会話ごとに最新(started_at)の 1 つを
   新しい順に limit 件。transcript の在否は読まない(呼び手が FsFileSize で確かめる — I/O は handler)。"
  (setv live (set (gfor item sessions (.get item "conversationId"))))
  (setv newest {})
  (for [view views]
    (<- alive bool (session-alive view))
    (when (and (not alive) (is-not view.conversation None))
      (<- mine (| dict None) (attribution-of-view view))
      (when (and (is-not mine None) (not-in (get mine "conversationId") live))
        (setv conversation-id (get mine "conversationId"))
        (setv held (.get newest conversation-id))
        (when (or (is held None) (> (or view.started-at-ms 0) (or held.started-at-ms 0)))
          (setv (get newest conversation-id) view)))))
  (setv ordered (sorted (.values newest)
                        :key (fn [view] #((- (or view.started-at-ms 0)) view.session-id))))
  (tuple (cut ordered 0 limit)))


(defk transcript-observation-of [view]
  {:pre [(: view SessionView)]
   :post [(: % dict)]}
  "node の observations.transcripts の 1 項 {conversationId, sessionId, account}(帰属から)。"
  (<- mine (| dict None) (attribution-of-view view))
  (setv known (if (is mine None) {} mine))
  {"conversationId" (.get known "conversationId")
   "sessionId" view.session-id
   "account" (.get known "account")})


;; ---------------------------------------------------------------------------
;; 行の欄の写し(agent-job)
;; ---------------------------------------------------------------------------

(defk status-object-of [row]
  {:pre [(: row AcpRow)]
   :post [(: % dict)]}
  "行の status(無ければ空 — 生まれの状態は engine が刻むので agentd が発明しない)。"
  (if (isinstance row.status dict) (dict row.status) {}))


(defk running-status-of [row session-id principal]
  {:pre [(: row AcpRow) (: session-id str) (: principal str)]
   :post [(: % dict)]}
  "受けた job の status: committed の欄を写し、phase = Running と sessionHandle
   {sessionId, stream{owner, name}} と inputsDelivered だけ書く(binding は触らない — 書き手は scheduling)。
   card acp:kanban-issue:ki-3149aebbf675 A: inputsDelivered は**受けた拍に空で宣言する** —
   「この agentd は郵便を渡せた id だけをこの欄に足す」の名乗りで、以後この行の配達は欄ちょうどで判じられる
   (欄が現れるのを送りの着地まで待つと、claim から送りまでの間〔器を起こす数秒〕に配達の拍が走り、
   phase = Running の推定で handedAt が先に立つ — 直そうとしている取り違えがその窓に残る)。
   既に在る値は写す(拾い直し・置き直しの Running の書きが渡した id を消さない — append-only)。"
  (<- next dict (status-object-of row))
  (setv (get next "phase") PHASE-RUNNING)
  (setv (get next "sessionHandle")
        {"sessionId" session-id
         "stream" {"owner" principal "name" session-id}})
  (when (not (isinstance (.get next JOB-INPUTS-DELIVERED-KEY) list))
    (setv (get next JOB-INPUTS-DELIVERED-KEY) []))
  next)


(defk condition-of [condition-type reason]
  {:pre [(: condition-type str) (: reason str)]
   :post [(: % dict)]}
  "conditions の 1 項(改訂 R1-a の {type, status, reason})。"
  {"type" condition-type "status" "True" "reason" reason})


(defk ended-status-of [status result cause conditions]
  {:pre [(: status dict) (: result (| dict list str int float bool None)) (: cause dict) (: conditions tuple)]
   :post [(: % dict)]}
  "手番の終わりの status(Ended の書きの 1 点 — 段 12 lane 12k・agora-redesign #349 行 3 粒 3a): phase = Ended、result は**必ず**
   cause を運ぶ(result-with-cause: dict の結末はその欄 cause に・None は cause だけ・dict でない結末は {value, cause})、conditions は
   既存に足す。cause の無い Ended は書けない(引数で強いる・category は閉語彙の外を断る)— 読み手 ACP awaitOutcomeOf は cause で
   終端の意味を読み、result の有無や conditions 頼みにしない。"
  (when (not-in (.get cause CAUSE-CATEGORY-KEY) CAUSE-CATEGORIES)
    (raise (ValueError f"Ended without a contract cause: {(repr cause)} (category must be one of {CAUSE-CATEGORIES})")))
  (setv next (dict status))
  (setv (get next "phase") PHASE-ENDED)
  (<- carried dict (result-with-cause result cause))
  (setv (get next "result") carried)
  (when conditions
    (setv existing (.get status "conditions"))
    (setv (get next "conditions")
          (+ (if (isinstance existing list) (list existing) []) (list conditions))))
  next)


(defk launch-plan-of [row]
  {:pre [(: row AcpRow)]
   :post [(: % LaunchPlan)]}
  "Bound の行から起こし方を写す: charter(= session.launch の params そのもの)・
   affinity.predecessor(あれば resume)・binding.account(あれば預かり所から借りる —
   種類は charter の agent_type)・binding.profile と charter.model は turn-record の欄。
   欠けている欄は発明しない: profile は binding に無ければ \"unbound\"(結ばれた profile が
   無い事実の名)、model は charter に無ければ \"default\"(走行器の既定を使う事実の名)。
   charter の session_id / session_name は**読まない**(在っても落とす): session の id は agentd が
   鋳造する(effect MintId → charter-with-session-id)— 作った側の固定の id は片付いた session の
   行と衝突する(実弾 2026-09-12 `session is already registered`)。"
  (setv spec row.spec)
  (setv charter (.get spec "charter"))
  (when (not (isinstance charter dict))
    (raise (ValueError f"agent-job {row.key}: spec.charter is not an object")))
  (setv affinity (.get spec "affinity"))
  (setv predecessor (if (isinstance affinity dict) (.get affinity "predecessor") None))
  (<- status dict (status-object-of row))
  (setv binding (.get status "binding"))
  (setv binding (if (isinstance binding dict) binding {}))
  (setv account (.get binding "account"))
  (setv agent-type (.get charter "agent_type"))
  (setv lease-kind (if (and (isinstance account str) account (isinstance agent-type str))
                       (.get AGENT-TYPE-LEASE-KIND agent-type)
                       None))
  (setv profile (.get binding "profile"))
  (setv model (.get charter "model"))
  (setv charter-out (dict charter))
  (.pop charter-out "session_id" None)
  (.pop charter-out "session_name" None)
  (<- lifecycle str (launch-lifecycle-of charter))
  (setv (get charter-out "lifecycle") lifecycle)
  (LaunchPlan
    :charter charter-out
    :predecessor (if (isinstance predecessor str) predecessor None)
    :lease-kind lease-kind
    :account (if (is lease-kind None) None account)
    :profile (if (and (isinstance profile str) profile) profile "unbound")
    :model (if (and (isinstance model str) model) model MODEL-UNDECLARED)))


(defk credential-source-of [plan custody-declared]
  {:pre [(: plan LaunchPlan) (: custody-declared bool)]
   :post [(: % str)]}
  "手番の資格の出所(段 10c・agora-redesign #80・operator 決定 2026-09-14 \"access token is to be fetched from k3s\")—
   判断はここ 1 点(effects.TurnCredentialSource の閉語彙):
   lease = binding.account が在り、charter の agent_type に貸与の種類がある(launch-plan-of が plan.account を据えた)—
   預かり所から借りる / missing = それが無く、この node は預かり所を宣言している — 起こさない(claim も書かず、
   条件 CredentialSourceMissing で閉じる: 手番の資格は貸与ちょうどで、機体の profile の家へ黙って落ちない)/
   home = それが無く、預かり所を宣言していない node(移行前の機体)— charter の binding で起こす今日の経路。"
  (cond
    (and (is-not plan.lease-kind None) (is-not plan.account None)) CREDENTIAL-SOURCE-LEASE
    custody-declared CREDENTIAL-SOURCE-MISSING
    True CREDENTIAL-SOURCE-HOME))


(defk credential-from-custody [source]
  {:pre [(: source str)]
   :post [(: % bool)]}
  "この手番の資格が預かり所の貸与か(段 10 lane 10d 便 2)。出所の語を比べるのは judgment の中だけ —
   呼び手(claim の腕)は真偽だけを読む(R23: 出所の判断点を増やさない)。"
  (= source CREDENTIAL-SOURCE-LEASE))


(defk charter-with-session-id [charter session-id]
  {:pre [(: charter dict) (: session-id str)]
   :post [(: % dict)]}
  "鋳造した session の id を charter に据える(session_id と session_name の両方 — sessionhost の
   器の名も同じ綴り)。sessionHandle.sessionId と stream の name はこの id。"
  (setv next (dict charter))
  (setv (get next "session_id") session-id)
  (setv (get next "session_name") session-id)
  next)


(defk first-turn-carries-inputs [backend-kind arm]
  {:pre [(: backend-kind str) (: arm str)]
   :post [(: % bool)]}
  "起こす手番の本文に inputs の郵便を畳むか — 判定はここ 1 点(R16): host の backend が headless
   ∧ 腕が起こす腕(launch / resume / rehydrate)。headless の器は 1 手番 = 1 prompt(claude は 1 手番 1 process・
   codex は turn/start が手番)で、走っている手番の途中に次の本文を積めない(実弾 2026-09-12:
   launch の直後の send が同じ名で --resume を spawn し `headless session already exists`)。
   send の腕(温かい session)は起こさないので畳む先が無い(郵便の本文だけを send)。tui(tmux /
   herdr)は launch の後に send(pane の paste は手番の途中でも積める)で今日どおり。"
  (and (= backend-kind BACKEND-HEADLESS) (in arm #{NEXT-ARM-LAUNCH NEXT-ARM-RESUME NEXT-ARM-REHYDRATE})))


(defk send-folds-bodies [backend-kind]
  {:pre [(: backend-kind str)]
   :post [(: % bool)]}
  "送る腕(温かい session)でも郵便の本文を 1 本に畳むか — 判定はここ 1 点(R16): host の backend が
   headless なら畳む。headless の器は 1 手番 = 1 prompt(claude は 1 手番 1 process・codex は turn/start が
   手番)で、走っている手番の途中に次の本文を積めない — 相乗りした N 通を N 回 session.send すると
   先頭 1 通しか agent に届かない(実測 2026-09-18: log 全体 168 job)。**腕では分岐しない**: headless なら
   after-start に来た bodies は腕を問わず畳む。tui(tmux / herdr)は pane の paste が手番の途中でも積めるので
   1 通 1 送りのまま。
   ⚠ first-turn-carries-inputs(charter に畳むか)とは別の述語: send の腕は incarnate が早戻りして charter を
   組まないので、あちらを send で True にすると charter にも畳まれず after-start にも空の bodies が渡り、
   郵便が 1 通も届かなくなる。畳む場所は charter ではなく after-start。"
  (= backend-kind BACKEND-HEADLESS))


(defk first-turn-prompt-of [charter-prompt bodies]
  {:pre [(: charter-prompt str) (: bodies tuple)]
   :post [(: % str)]}
  "1 手番目の本文: charter の prompt(前置き)と郵便の本文(inputs の順)を空行で区切って 1 つに。
   郵便が無ければ charter だけ・空白だけの部分は入れない。"
  (.join "\n\n" (lfor part (+ [charter-prompt] (list bodies)) :if (.strip part) part)))


(defk charter-with-first-turn [charter bodies]
  {:pre [(: charter dict) (: bodies tuple)]
   :post [(: % dict)]}
  "charter の prompt を 1 手番目の本文(first-turn-prompt-of)に据える。launch も resume
   (resume-params-of が charter の prompt を運ぶ)も同じ 1 点を通る。"
  (setv next (dict charter))
  (setv prompt (.get charter "prompt"))
  (<- folded str (first-turn-prompt-of (if (isinstance prompt str) prompt "") bodies))
  (setv (get next "prompt") folded)
  next)


(defk charter-with-history [charter history]
  {:pre [(: charter dict) (: history str)]
   :post [(: % dict)]}
  "履歴からの再開の手番の charter(段 8q・R20): prompt(前置き)の後に「これまでの会話」を空行で足す(記録が無ければ
   変えない)。郵便の本文はこの後に charter-with-first-turn が畳む(headless)か send で届く(tui)。"
  (setv next (dict charter))
  (when (.strip history)
    (setv prompt (.get charter "prompt"))
    (setv (get next "prompt")
          (.join "\n\n" (lfor part [(if (isinstance prompt str) prompt "") history] :if (.strip part) part))))
  next)


(defk conversation-opener-of [row]
  {:pre [(: row (| AcpRow None))]
   :post [(: % (| str None))]}
  "会話の行の spec.opener の逐語(契約 agora-kinds.json conversation.spec.opener — operator / machine / system)。行が無い・欄が
   文字列でない = None(発明しない — env には置かない)。"
  (when (is row None)
    (return None))
  (setv opener (.get row.spec "opener"))
  (if (and (isinstance opener str) opener) opener None))


(defk charter-with-conversation-env [charter conversation-id opener]
  {:pre [(: charter dict) (: conversation-id str) (: opener (| str None))]
   :post [(: % dict)]}
  "段 10f 便 2 追補 3(agora-redesign #82): 手番の process の env(charter.session_env — 非 auth の overlay)に会話の身元を置く 1 点:
   AGORA_CONVERSATION_ID = 会話の id・AGORA_SEAT_OPENER = 会話の行の opener の逐語(読めなければ置かない)。呼び手が置いた
   他の session_env の欄は残す。"
  (setv next (dict charter))
  (setv env (dict (or (.get charter "session_env") {})))
  (setv (get env CONVERSATION-ID-ENV) conversation-id)
  (when (is-not opener None)
    (setv (get env SEAT-OPENER-ENV) opener))
  (setv (get next "session_env") env)
  next)


(defk incarnation-charter-of [plan choice session-id bodies history attribution backend-kind lease homes-root opener]
  {:pre [(: plan LaunchPlan) (: choice ArmChoice) (: session-id str) (: bodies tuple) (: history str)
         (: attribution dict) (: backend-kind str) (: lease (| LeaseGrant None)) (: homes-root str) (: opener (| str None))]
   :post [(: % tuple)]}
  "起こす session の charter を組む 1 点(launch / resume / rehydrate — send は起こさない): 鋳造した id →
   会話の身元の env(段 10f 便 2 追補 3 — 会話の id は帰属の conversationId・opener は会話の行から)→
   (rehydrate)これまでの会話 → (headless の起こす腕)郵便の本文 → 借りた札の家 → 帰属。戻り =
   #(charter auth-file-or-None)(codex の借りた auth.json の置き場 — 書くのは呼び手の effect)。"
  (<- with-id dict (charter-with-session-id plan.charter session-id))
  (<- with-env dict (charter-with-conversation-env with-id (str (get attribution "conversationId")) opener))
  (setv charter with-env)
  (when (= choice.arm NEXT-ARM-REHYDRATE)
    (<- with-history dict (charter-with-history charter history))
    (setv charter with-history))
  (<- folds bool (first-turn-carries-inputs backend-kind choice.arm))
  (when folds
    (<- folded dict (charter-with-first-turn charter bodies))
    (setv charter folded))
  (setv auth-file None)
  (when (and (is-not lease None) (is-not plan.lease-kind None) (is-not plan.account None))
    (<- granted tuple (charter-with-grant charter plan.lease-kind plan.account
                                          lease.access-token lease.auth-json homes-root))
    (setv charter (get granted 0))
    (setv auth-file (get granted 1)))
  (<- stamped dict (charter-with-attribution charter attribution))
  #(stamped auth-file))


;; ---------------------------------------------------------------------------
;; 履歴からの再開(段 8q・R20・段 9f lane 9f-4): 郵便(ACP)+ 手番の本文(会話の記録の service)→ 「これまでの会話」
;; ---------------------------------------------------------------------------

(defk history-time-of [at]
  {:pre [(: at int)]
   :post [(: % str)]}
  "記録の時刻(epoch ms)の人の読む綴り(UTC・秒)。"
  (.strftime (datetime.fromtimestamp (/ at 1000) :tz timezone.utc) "%Y-%m-%dT%H:%M:%SZ"))


(defk history-message-line [message at fetched]
  {:pre [(: message AcpRow) (: at int) (: fetched dict)]
   :post [(: % str)]}
  "郵便 1 通 → 「これまでの会話」の 1 項(差出人 → 宛先(種類): 本文)。段 10f 便 1b: 本文を記録の service に置いた郵便は
   fetched(郵便 id → 本文 — agentd の mail-bodies-by-ref が読み集めた表)から引く。"
  (<- stamp str (history-time-of at))
  (setv spec message.spec)
  (setv sender (.get spec "from" "?"))
  (setv to (.get spec "to" "?"))
  (setv kind (.get spec "kind" "note"))
  (setv body (if (isinstance (.get spec "body") str) (.get spec "body") (.get fetched (.get spec "id" message.resource-id))))
  (setv text (if (isinstance body str) body ""))
  f"[{stamp}] {sender} → {to}({kind}): {text}")


(defk history-event-body [event]
  {:pre [(: event RecordEvent)]
   :post [(: % str)]}
  "会話の記録の service の出来事 1 つ → 畳む本文(text / summary / input / output の順に最初に在る欄・object は JSON・本文が無い行
   〔tombstone〕は空)。"
  (cond (isinstance event.text str) event.text
        (isinstance event.summary str) event.summary
        (isinstance event.input str) event.input
        (is-not event.input None) (json.dumps event.input :ensure-ascii False)
        (isinstance event.output str) event.output
        (is-not event.output None) (json.dumps event.output :ensure-ascii False)
        True ""))


;; 消えた本文の印(段 12 lane 12l・agora-redesign #383 粒 2)— 綴りは 1 点。
(setv HISTORY-ERASED-MARK "(本文は消去済み)")


(defk history-event-line-of [event body]
  {:pre [(: event RecordEvent) (: body str)]
   :post [(: % (| str None))]}
  "出来事 1 つと畳む本文 → 「これまでの会話」の 1 項(kind ごとの畳み — 契約 record-service の eventKinds: text / tool_use /
   tool_result / system / error / user。frame は画面の断面で会話ではない・message は郵便で ACP の行から畳む〔service の mail の
   stream はまだ書き手が無い — 書き手が立つ便で郵便の畳みの座を移す〕= None)。"
  (<- stamp str (history-time-of event.at))
  (setv tool (if (isinstance event.tool-name str) event.tool-name ""))
  (setv failed (if event.is-error "(誤り)" ""))
  (setv cut (if event.truncated "(切り詰め)" ""))
  ;; 段 12 lane 12l(agora-redesign #383 粒 2): 本文が消された行(保存期間の係 retention か手の tombstone)は空の本文に印を
  ;; 付ける — 「何も言わなかった」と「言ったが消えた」を agent が見分ける(本文は発明しない)。
  (setv gone (if (isinstance event.tombstoned-at int) HISTORY-ERASED-MARK ""))
  (cond
    (= event.kind "text") f"[{stamp}] agent: {body}{cut}{gone}"
    (= event.kind "tool_use") f"[{stamp}] agent の道具 {tool}: {body}{cut}{gone}"
    (= event.kind "tool_result") f"[{stamp}] 道具の結果{failed}: {body}{cut}{gone}"
    (= event.kind "system") f"[{stamp}] system: {body}{gone}"
    (= event.kind "error") f"[{stamp}] 誤り: {body}{gone}"
    (= event.kind "user") f"[{stamp}] user: {body}{cut}{gone}"
    True None))


(defk history-event-line [event]
  {:pre [(: event RecordEvent)]
   :post [(: % (| str None))]}
  "会話の記録の service の出来事 1 つ → 全文の 1 項(history-event-body + history-event-line-of)。本文が無い行(tombstone)は
   空の本文として畳む。"
  (<- body str (history-event-body event))
  (<- line (| str None) (history-event-line-of event body))
  line)


(defk history-thin-body [body head-bytes]
  {:pre [(: body str) (: head-bytes int)]
   :post [(: % (| str None))]}
  "本文を先頭 head-bytes byte(UTF-8 の境で切る)に薄くし、元の大きさを名乗る(段 11 lane 11v 便 3・agora-redesign #225・R35)。
   head-bytes 以下の本文は薄くならない(None)。"
  (setv raw (.encode body "utf-8"))
  (when (<= (len raw) head-bytes)
    (return None))
  (setv head (.decode (cut raw 0 head-bytes) "utf-8" :errors "ignore"))
  f"{head}(先頭 {head-bytes} byte だけ・元 {(len raw)} byte)")


(defk history-event-thin-line [event head-bytes]
  {:pre [(: event RecordEvent) (: head-bytes int)]
   :post [(: % (| str None))]}
  "薄くした 1 項(R35): 薄くするのは道具の項(tool_use の入力・tool_result の本文)だけ。他の kind(agent の text・user / system /
   error = 会話の結論と文脈)と、head-bytes 以下で薄くならない本文は None(全文の項のまま)。綴りは全文の項と同じ history-event-line-of。"
  (when (not-in event.kind #("tool_use" "tool_result"))
    (return None))
  (<- body str (history-event-body event))
  (<- thin (| str None) (history-thin-body body head-bytes))
  (when (is thin None)
    (return None))
  (<- line (| str None) (history-event-line-of event thin))
  line)


(defk headline-counts-of-entries [entries at]
  {:pre [(: entries tuple) (: at int)]
   :post [(: % HeadlineCounts)]}
  "turn-record の entries(見出しの列)→ 見出しの数: kind ごとの件数(初出の順)・道具の名(初出の順)・期間(entries の at の
   最小と最大・時刻を持つ entry が無ければ at = 行の時刻)。落とした印(drop marker)は数えない。"
  (setv counts {})
  (setv tools [])
  (setv first-at None)
  (setv last-at None)
  (for [entry entries]
    (<- marker bool (is-drop-marker entry))
    (when marker
      (continue))
    (setv kind (.get entry "kind"))
    (when (isinstance kind str)
      (setv (get counts kind) (+ (.get counts kind 0) 1)))
    (setv tool (.get entry "toolName"))
    (when (and (isinstance tool str) (not-in tool tools))
      (.append tools tool))
    (setv entry-at (.get entry "at"))
    (when (isinstance entry-at int)
      (when (or (is first-at None) (< entry-at first-at))
        (setv first-at entry-at))
      (when (or (is last-at None) (> entry-at last-at))
        (setv last-at entry-at))))
  (HeadlineCounts :counts (tuple (.items counts)) :tools (tuple tools)
                  :first-at (if (is first-at None) at first-at)
                  :last-at (if (is last-at None) at last-at)))


(defk headline-counts-of-items [items]
  {:pre [(: items tuple)]
   :post [(: % HeadlineCounts)]}
  "「これまでの会話」の項の列(空でない)→ 1 つの見出しの数: kind ごとの件数を足し合わせ(初出の順)・道具の名は初出の順・
   期間 = 項の at の最小と until の最大。上限で落とした区間の見出し(history-dropped-headline)の材料。"
  (setv counts {})
  (setv tools [])
  (for [item items]
    (for [[kind count] item.counts.counts]
      (setv (get counts kind) (+ (.get counts kind 0) count)))
    (for [tool item.counts.tools]
      (when (not-in tool tools)
        (.append tools tool))))
  (HeadlineCounts :counts (tuple (.items counts)) :tools (tuple tools)
                  :first-at (min (gfor item items item.at))
                  :last-at (max (gfor item items item.until))))


(defk history-counts-note [counts]
  {:pre [(: counts HeadlineCounts)]
   :post [(: % str)]}
  "見出しの数の綴り(1 点 — turn-record の 1 行の見出しも、落とした区間の見出しも、ここから同じ形で組む):
   「text 2・tool_use 1・tool_result 1(道具: Read)」。件数が無ければ空。"
  (setv parts (.join "・" (lfor [kind count] counts.counts f"{kind} {count}")))
  (setv tool-names (.join ", " counts.tools))
  (setv tool-note (if counts.tools f"(道具: {tool-names})" ""))
  (+ parts tool-note))


(defk history-headline-line [record counts]
  {:pre [(: record AcpRow) (: counts HeadlineCounts)]
   :post [(: % (| str None))]}
  "ACP の turn-record の行 1 つ(見出しだけ・本文なし)→ 薄い再開の 1 項: 手番の出来事の数を kind ごとに数え、道具の名を
   並べる(本文の無い行を本文として扱わない — 中身は名乗れないので数と在処だけ)。counts = headline-counts-of-entries の
   答え・綴りは history-counts-note の 1 点。見出しが無い行(件数が空)は None。"
  (when (not counts.counts)
    (return None))
  (<- note str (history-counts-note counts))
  (<- stamp str (history-time-of counts.last-at))
  f"[{stamp}] 手番 {record.resource-id}(見出しだけ・本文は記録の service): {note}")


(defk history-dropped-headline [counts turns items budget where]
  {:pre [(: counts HeadlineCounts) (: turns int) (: items int) (: budget int) (: where str)]
   :post [(: % str)]}
  "上限で落とした区間(古い手番の連なり)の見出し 1 行(段 11 lane 11v・agora-redesign #55・R34): 期間・落とした手番と項の数・
   kind ごとの件数と道具の名(history-counts-note の 1 点)・全文の在処。本文は要約しない(model を呼ばない・決定的)。"
  (<- note str (history-counts-note counts))
  (<- first-stamp str (history-time-of counts.first-at))
  (<- last-stamp str (history-time-of counts.last-at))
  (+ f"[{first-stamp}〜{last-stamp}] 古い手番 {turns} 件(出来事と郵便 {items} 件)は上限 {budget} byte を超えるため"
     f"本文を畳まず、見出しだけ残します: {note}。{where}"))


(defk history-cut-notice [budget cut-bytes where]
  {:pre [(: budget int) (: cut-bytes int) (: where str)]
   :post [(: % str)]}
  "最新の手番 1 つだけでも上限を超える時の断り(その先頭から切った byte を名乗る)。where = 全文の在処(落とした区間の見出しが
   既に名乗っている時は空)。"
  (+ f"(上限 {budget} byte を超えるため、最新の手番の先頭 {cut-bytes} byte を落としました。" where ")"))


(defk history-header-of [conversation-id source kept-summaries]
  {:pre [(: conversation-id str) (: source (| RecordedTurns HeadlineTurns)) (: kept-summaries int)]
   :post [(: % str)]}
  "「これまでの会話」の頭の 1 行(段 12 lane 12j 追補 5 で 1 点に): 薄い再開は届かなかった理由を名乗り、要約が残っていればその数
   (上限で落とした要約は数えない)を名乗る。"
  (setv summarized (if (> kept-summaries 0) f"・古い区間 {kept-summaries} つは要約(記録の service の recordSeq の区間を名乗る)で、原文はその後" ""))
  (if (isinstance source HeadlineTurns)
      (+ f"これまでの会話(薄い再開・会話 {conversation-id}・古い順{summarized}): 会話の記録の service に届かなかった"
         f"({source.reason})ため、手番の本文は無く ACP の見出し(出来事の数)だけです。郵便の本文は在ります。")
      (+ f"これまでの会話(会話の記録の service と ACP の郵便から組んだ写し・会話 {conversation-id}・古い順{summarized}"
         (if source.complete "" "・会話の最初までは読んでいない") "):")))


(defk dropped-headline-counts [items summaries]
  {:pre [(: items tuple) (: summaries tuple)]
   :post [(: % HeadlineCounts)]}
  "上限で落とした区間の見出しの数(段 12 lane 12j 追補 5): 原文の項(郵便と出来事)の件数・道具・期間は headline-counts-of-items の
   1 点から、落とした要約(HistorySummary)は kind 要約 の件数として先頭に足す。期間は原文の項だけで数える(要約の区間の recordSeq を
   時刻に読まない)— 原文を 1 つも落としていない(要約だけ落ちた)拍だけ、要約を書いた時刻を期間にする。どちらかは空でない。"
  (setv summary-count (len summaries))
  (when (not items)
    (setv ats (lfor summary summaries summary.at))
    (return (HeadlineCounts :counts #(#(HISTORY-SUMMARY-KIND summary-count)) :tools #()
                            :first-at (min ats) :last-at (max ats))))
  (<- base HeadlineCounts (headline-counts-of-items items))
  (if (= summary-count 0)
      base
      (replace base :counts (tuple (+ [#(HISTORY-SUMMARY-KIND summary-count)] (list base.counts))))))


(defk summary-floor-at-of [page floor]
  {:pre [(: page (| RecordPage RecordUnread)) (: floor int)]
   :post [(: % (| int None))]}
  "要約が覆う記録の終わりの時刻(段 12 lane 12j 追補 6): recordSeq = floor の出来事(agentd.history-for が RecordReadSince since floor−1・
   limit 1 で 1 読み)の at。読めない・空・recordSeq が floor でない(欠番)= None(郵便を絞らない — 発明しない)。"
  (when (isinstance page RecordUnread)
    (return None))
  (when (not page.events)
    (return None))
  (setv edge (get page.events 0))
  (if (= edge.record-seq floor) edge.at None))


(defk rehydrate-history-of [conversation-id messages source exclude budget fetched summaries floor-at]
  {:pre [(: conversation-id str) (: messages tuple) (: source (| RecordedTurns HeadlineTurns)) (: exclude tuple)
         (: budget int) (: fetched dict) (: summaries tuple) (: floor-at (| int None))]
   :post [(: % HistoryFold)]}
  "会話の記録 → 履歴からの再開の手番の最初の本文に畳む「これまでの会話」(段 8q・R20・段 9f lane 9f-4・段 11 lane 11v R34 / R35)—
   判断はここ 1 点: 郵便(ACP の行 — spec.to か spec.from がこの会話・exclude = この手番の inputs は除く — 本文として別に届く)と
   手番の材料(source — 型で 2 つ: RecordedTurns = 会話の記録の service の本文〔設計 §2.4・before=latest から〕/ HeadlineTurns =
   ACP の見出しだけ〔service に届かない時の**薄い再開** — 本文は畳めないので手番ごとの数だけ・名乗る〕)を時刻順(同じ
   時刻は郵便が先)に並べ、会話へ届いた郵便(spec.to = この会話)ごとに手番に割る。UTF-8 で budget byte を超えたら 3 段:
   段 1(R35)= 古い手番から新しい手番へ、道具の項(tool_use の入力・tool_result の本文)だけを先頭 budget / HISTORY_THIN_DIVISOR
   byte に薄くして元の byte を名乗る(郵便・agent の text・user / system / error は 1 byte も変えない — 会話の意図と結論)。
   段 2(R34)= 全部を薄くしても超える間、**古い手番から要約せず落とし**、落とした区間(古い手番の連なり)を**見出し 1 行**
   (期間・落とした手番と項の数・kind ごとの件数・道具の名・全文の在処 — history-dropped-headline)に畳んで残した手番の前に置く
   (黙って捨てない・model は呼ばない — agora-redesign #55 便 1)。段 3 = 最新の手番 1 つだけでも超えるならその手番の先頭を
   落として末尾を残し、切った byte を名乗る。記録が無ければ text は空(薄い再開でも空)。
   段 12 lane 12j 便 3(agora-redesign #233・#55 案 D): summaries = kind summary の行の要約(古い順・recordSeq の閉区間)を**原文の前**に
   1 区間 1 段として置く(history-summary-line・道具の項ではないので薄くならない)。追補 5(便 4 の実射 2026-09-16 18:08・aj-88JXQX…): 要約は
   原文と**別の前置き**で、時刻の並びには入れない(recordSeq を時刻に読まない)。上限では原文の手番を先に落とし(最新の 1 手番は残す)、
   それでも超える時だけ古い要約から落として見出し〔kind 要約〕に数える — 要約は既に圧縮された履歴で byte あたりの価値が原文より高い。
   旧の順(要約が最も古い項として先に落ちる)では上限 65,536 byte・原文 1,500 出来事・要約 4 本のとき本文に要約が 1 本も残らず、見出しの
   期間は recordSeq 0 を時刻に読んで 1970 年から始まった。見出しの期間は落とした原文の時刻だけで数える(要約は件数)。
   原文は呼び手が最大の to より新しい出来事だけを渡す(agentd.record-turns-for の floor)。summaries が空なら今日どおり。
   追補 6(実射 2026-09-16 18:45: 落ちた 77 手番の大半が郵便 105 通で上限を食っていた): floor-at = 要約が覆う記録の終わり(recordSeq = floor
   の出来事の at・summary-floor-at-of・None = 要約なし / 読めない)。それ以前の郵便は要約が担う(要約はその期間の郵便も読んで書いている)ので
   畳まず、HistoryFold.summarized_mails に数える。"
  (setv thin (isinstance source HeadlineTurns))
  (setv thin-k (// budget HISTORY-THIN-DIVISOR))
  ;; 追補 5(便 4 の実射 2026-09-16 18:08): 要約は原文と別の前置き — recordSeq の区間の順に 1 区間 1 段で、時刻の並びには入れない
  ;; (recordSeq を時刻に読まない)。落とすのは原文の手番(最新の 1 つを除く)を全部落としても超える時だけ・古い要約から(段 2b)。
  (setv ordered-summaries (list (sorted summaries :key (fn [summary] summary.from-seq))))
  (setv summary-lines [])
  (for [summary ordered-summaries]
    (<- summary-line str (history-summary-line summary))
    (.append summary-lines summary-line))
  (setv items [])
  (setv order 0)
  (setv summarized-mails 0)
  (for [message messages]
    (setv spec message.spec)
    (setv message-id (.get spec "id" message.resource-id))
    (setv inbound (= (.get spec "to") conversation-id))
    (when (and (or inbound (= (.get spec "from") conversation-id)) (not-in message-id exclude))
      (setv at (.get spec "at"))
      (setv mail-at (if (isinstance at int) at message.created-at-ms))
      ;; 追補 6: 要約が覆う記録の終わり以前の郵便は要約が担う — 畳まず数えるだけ(上限を食わせない)。
      (setv covered (and (is-not floor-at None) (<= mail-at floor-at)))
      (when covered
        (setv summarized-mails (+ summarized-mails 1)))
      (when (not covered)
        (<- line str (history-message-line message mail-at fetched))
        (.append items (HistoryItem :at mail-at :until mail-at :order order :inbound inbound :line line
                                    :counts (HeadlineCounts :counts #(#(HISTORY-MAIL-KIND 1)) :tools #()
                                                            :first-at mail-at :last-at mail-at)
                                    :thin-line None))
        (setv order (+ order 1)))))
  (if thin
      (for [record source.records]
        (when (= (.get record.spec "conversationId") conversation-id)
          (setv status (if (isinstance record.status dict) record.status {}))
          (<- entries tuple (entries-of-status status))
          (<- counts HeadlineCounts (headline-counts-of-entries entries record.created-at-ms))
          (<- line (| str None) (history-headline-line record counts))
          (when (is-not line None)
            (.append items (HistoryItem :at counts.first-at :until counts.last-at :order order :inbound False
                                        :line line :counts counts :thin-line None))
            (setv order (+ order 1)))))
      (for [event source.events]
        (<- line (| str None) (history-event-line event))
        (when (is-not line None)
          (<- thin-line (| str None) (history-event-thin-line event thin-k))
          (.append items (HistoryItem :at event.at :until event.at :order order :inbound False :line line
                                      :counts (HeadlineCounts :counts #(#(event.kind 1))
                                                              :tools (if (isinstance event.tool-name str) #(event.tool-name) #())
                                                              :first-at event.at :last-at event.at)
                                      :thin-line thin-line))
          (setv order (+ order 1)))))
  (setv groups [])
  (for [item (sorted items :key (fn [item] #(item.at item.order)))]
    (if (or (not groups) item.inbound)
        (.append groups [item])
        (.append (get groups -1) item)))
  (when (and (not groups) (not summary-lines))
    (return (HistoryFold :text "" :kept-turns 0 :dropped-turns 0 :dropped-items 0 :thinned-turns 0 :dropped-headline None
                         :cut-bytes 0 :size-bytes 0 :thin thin :summary-regions 0 :dropped-summaries 0
                         :summarized-mails summarized-mails)))
  (setv where (+ f"全文は会話 {conversation-id} の記録 — 郵便は ACP の kind message(spec.to / spec.from = {conversation-id})"
                 f"の行・手番の本文は会話の記録の service(GET /v1/conversations/{conversation-id}/events)— にあります"))
  (setv full-blocks (lfor group groups (.join "\n" (lfor item group item.line))))
  (setv thin-blocks (lfor group groups (.join "\n" (lfor item group (if (is item.thin-line None) item.line item.thin-line)))))
  (setv thinnable (lfor group groups (any (gfor item group (is-not item.thin-line None)))))
  (setv blocks (list full-blocks))
  (setv kept-summaries (list summary-lines))
  (setv thinned-turns 0)
  (setv dropped-turns 0)
  (setv dropped-items 0)
  (setv dropped-summaries 0)
  (setv headline None)
  (<- header str (history-header-of conversation-id source (len kept-summaries)))
  (setv text (.join "\n\n" (+ [header] kept-summaries blocks)))
  ;; 段 1(R35): 落とす前に薄くする — 古い手番から新しい手番へ、道具の項だけ。薄くなる項の無い手番は数えない。
  (setv reach 0)
  (while (and (> (len (.encode text "utf-8")) budget) (< reach (len groups)))
    (when (get thinnable reach)
      (setv (get blocks reach) (get thin-blocks reach))
      (setv thinned-turns (+ thinned-turns 1))
      (setv text (.join "\n\n" (+ [header] kept-summaries blocks))))
    (setv reach (+ reach 1)))
  ;; 段 2(R34)+ 段 2b(追補 5): 全部を薄くしても超える間、まず**原文の**古い手番から落とし(最新の 1 手番は残す)、それでも超える時だけ
  ;; 古い要約から落とす(頭の要約の数も減る)。落とした区間(原文の手番と要約)は見出し 1 行に畳んで頭の直後(残した要約と手番の前)に
  ;; 置く(黙って捨てない)。見出しの点は 1 つ(history-dropped-headline)。
  (while (and (> (len (.encode text "utf-8")) budget)
              (or (> (- (len blocks) dropped-turns) 1) (< dropped-summaries (len summary-lines))))
    (if (> (- (len blocks) dropped-turns) 1)
        (setv dropped-turns (+ dropped-turns 1))
        (setv dropped-summaries (+ dropped-summaries 1)))
    (setv kept-summaries (cut summary-lines dropped-summaries None))
    (<- header str (history-header-of conversation-id source (len kept-summaries)))
    (setv dropped (tuple (gfor group (cut groups 0 dropped-turns) item group item)))
    (setv dropped-items (len dropped))
    (<- counts HeadlineCounts (dropped-headline-counts dropped (tuple (cut ordered-summaries 0 dropped-summaries))))
    (<- headline str (history-dropped-headline counts dropped-turns dropped-items budget where))
    (setv text (.join "\n\n" (+ [header headline] kept-summaries (cut blocks dropped-turns None)))))
  ;; 段 3: 最新の手番 1 つ(と頭・見出し・残した要約)だけでも超える: その手番の先頭を切って末尾を残し、切った byte を名乗る。
  (setv cut-bytes 0)
  (when (and blocks (> (len (.encode text "utf-8")) budget))
    (setv newest (.encode (get blocks -1) "utf-8"))
    (setv lead (+ [header] (if (is headline None) [] [headline]) kept-summaries))
    (setv notice-where (if (is headline None) where ""))
    (<- probe str (history-cut-notice budget (len newest) notice-where))
    (setv fixed (len (.encode (.join "\n\n" (+ lead ["" probe])) "utf-8")))
    (setv room (max 0 (- budget fixed)))
    (setv tail (.decode (cut newest (max 0 (- (len newest) room)) None) "utf-8" :errors "ignore"))
    (setv cut-bytes (- (len newest) (len (.encode tail "utf-8"))))
    (<- notice str (history-cut-notice budget cut-bytes notice-where))
    (setv text (.join "\n\n" (+ lead [tail notice]))))
  (HistoryFold :text text
               :kept-turns (- (len blocks) dropped-turns)
               :dropped-turns dropped-turns
               :dropped-items dropped-items
               :thinned-turns thinned-turns
               :dropped-headline headline
               :cut-bytes cut-bytes
               :size-bytes (len (.encode text "utf-8"))
               :thin thin
               :summary-regions (- (len summary-lines) dropped-summaries)
               :dropped-summaries dropped-summaries
               :summarized-mails summarized-mails))


(defk history-summary-line [summary]
  {:pre [(: summary HistorySummary)]
   :post [(: % str)]}
  "要約 1 区間 → 「これまでの会話」の 1 段(区間の recordSeq と model を名乗り、本文はそのまま — 段 12 lane 12j 便 3)。"
  f"[要約 recordSeq {summary.from-seq}〜{summary.to-seq}・{summary.model}] {summary.text}")


(defk record-history-satisfied [events budget]
  {:pre [(: events tuple) (: budget int)]
   :post [(: % bool)]}
  "履歴からの再開の後向きの読みを止めてよいか: 読めた出来事の本文の bytes(切る前の大きさ・契約 storedEvent.bytes)の
   合計が畳みの上限(budget)に届いた(これより古い頁は畳みが落とす)。"
  (>= (sum (gfor event events event.bytes)) budget))


(defk record-page-advances [before next]
  {:pre [(: before (| int None)) (: next (| int None))]
   :post [(: % bool)]}
  "次の頁を読んでよいか: cursor.next が在り(None = 会話の最初まで読めた)、今の before より小さい(後向きに進む)。進まない
   答え(同じか大きい cursor)は契約違反なのでそこで止める — 読みを無限に繰り返さない。"
  (and (is-not next None) (or (is before None) (< next before))))


(defk inputs-of [row]
  {:pre [(: row AcpRow)]
   :post [(: % tuple)]}
  "spec.inputs(Message の id の列)。無ければ空。"
  (setv inputs (.get row.spec "inputs"))
  (if (isinstance inputs list)
      (tuple (lfor item inputs :if (isinstance item str) item))
      #()))


(defk resume-params-of [predecessor charter]
  {:pre [(: predecessor str) (: charter dict)]
   :post [(: % dict)]}
  "affinity.predecessor が在る job の session.resume の params: 前の incarnation の
   session_id を名指し、新しい session_id と launch の意図(charter)を運ぶ
   (host.hy の session.resume の受理形 — resume 専用の欄はそのまま素通し)。添付(段 10 lane 10o)は
   起こす腕が 1 手番目に畳む郵便の物で、launch と同じ項の綴りで運ぶ。lifecycle は
   運ばない — 新しい incarnation は蘇生元の行の lifecycle を継ぐ(launch.hy resume-session)。"
  (setv params {"session_id" predecessor
                "new_session_id" (.get charter "session_id")})
  ;; ⚠ ここは charter の欄を**名簿で**写す(素通しではない)— 足した欄は名簿にも足す。
  ;; 実弾 2026-09-15 09:5x(operator): 段 10 lane 10o の attachments を名簿に入れ忘れたので、
  ;; **腕が resume の手番だけ**画像が黙って落ちていた(誤りも条件も出ないまま model が画像を見ない)。
  ;; 起こす腕は launch / resume / rehydrate の 3 つ — 検が launch しか通っていなかったのが見落としの根。
  (for [key ["prompt" "model" "effort" "mcp_servers" "session_env" "binding"
             "expected_result" "context_file" "launch_attribution"
             ;; 圧縮の閾値(設計記録 docs/design/auto-compact-window): launch は charter を丸ごと
             ;; params にするので素通しだが、resume は名簿の写し — ここに無いと
             ;; **蘇生の手番だけ**閾値が落ちて窓の上限任せに戻る(上の傷跡と同じ形)。
             CHARTER-AUTO-COMPACT-WINDOW-KEY
             MESSAGE-ATTACHMENTS-KEY]]
    (when (in key charter)
      (setv (get params key) (get charter key))))
  params)


(defk charter-with-grant [charter lease-kind account grant-token auth-json homes-root]
  {:pre [(: charter dict) (: lease-kind str) (: account str)
         (: grant-token (| str None)) (: auth-json (| str None)) (: homes-root str)]
   :post [(: % tuple)]}
  "借りた札で charter を組み直す。戻り = #(charter' auth-file-path)。
   claude: 札は env CLAUDE_CODE_OAUTH_TOKEN(custodian の契約 — 資格 file は書かない)、
   家 = <homes-root>/claude/<account> を binding {kind claude-code, config_dir} で渡す
   (transcript の家 = この config_dir の projects/ 配下)。
   codex: 札 = auth.json の中身。<homes-root>/codex/<account>/auth.json へ置き(家の中の
   auth file — 法 (e) の唯一の許された平文)、binding {kind codex, auth_file, profile_dir}
   の二軸で host の fs-compose-home-view に家を組ませる。profile_dir は claude の config_dir と
   同じく <homes-root>/codex/<account> ちょうど(段 10 lane 10r・agora-redesign #99 — ACP は段 10c
   から charter に binding を書かないので、charter の binding の profile_dir / codex_home を読むと
   家が無く札が使われない。読みもしない: 機体の家を借りた札の家にしない)。"
  (setv next (dict charter))
  (setv safe-account (re.sub r"[^A-Za-z0-9._-]" "_" account))
  (cond
    (= lease-kind "claude")
    (do
      (setv env (dict (or (.get charter "session_env") {})))
      (when (is-not grant-token None)
        (setv (get env CLAUDE-OAUTH-TOKEN-ENV) grant-token))
      (setv (get next "session_env") env)
      (setv (get next "binding")
            {"kind" "claude-code" "config_dir" f"{homes-root}/claude/{safe-account}"})
      #(next None))
    (= lease-kind "codex")
    (if (is auth-json None)
        #(next None)
        (do
          (setv profile-dir f"{homes-root}/codex/{safe-account}")
          (setv auth-file f"{profile-dir}/auth.json")
          (setv (get next "binding")
                {"kind" "codex" "auth_file" auth-file "profile_dir" profile-dir})
          #(next auth-file)))
    True
    #(next None)))


(defk turn-session-env-of [lease]
  {:pre [(: lease (| LeaseGrant None))]
   :post [(: % dict)]}
  "手番ごとの資格の env(段 10 lane 10d 便 2 の追補 2・実弾 #92 = 預かり所が口座を更新した後、
   誕生の札で再開した温かい手番が 401 を食った)。温かい session への送りは、降りた process を
   `--resume` で起こし直すことがある — その起こしに **この手番で借りた札** を載せる。
   claude の綴りは charter-with-grant と同じ 1 点(CLAUDE-OAUTH-TOKEN-ENV)。codex の札は
   家の中の auth file が運ぶので env は空(値は log にも行にも出さない)。"
  (if (or (is lease None) (is lease.access-token None))
      {}
      {CLAUDE-OAUTH-TOKEN-ENV lease.access-token}))


(defk message-body-ref-of [spec]
  {:pre [(: spec dict)]
   :post [(: % (| tuple None))]}
  "段 10f 便 1b(agora-redesign #82・契約 agora-kinds.json message.spec.bodyRef): 本文を記録の service に置いた郵便の
   在処 #(conversation stream)。本文(spec.body)を行に持つ郵便・bodyRef の形が合わない郵便は None(読みに行かない)。"
  (setv ref (.get spec "bodyRef"))
  (if (and (not (isinstance (.get spec "body") str))
           (isinstance ref dict)
           (isinstance (.get ref "conversation") str)
           (isinstance (.get ref "stream") str))
      #((get ref "conversation") (get ref "stream"))
      None))


(defk message-attachments-of [spec]
  {:pre [(: spec dict)]
   :post [(: % tuple)]}
  "段 10 lane 10o(agora-redesign #96・契約 agora-kinds.json message.spec.attachments): 郵便の行が運ぶ添付の
   見出しの列 → #(#(stream seq) …) の並び(読む順)。形の合わない項は落とす(発明しない)。見出しは本文と
   同じ stream(郵便の id)を名指すので、ここは stream と作り手の序数だけを取り出す — 中身は記録の service。
   ⚠ 種類(mime)の判断はここに置かない: 受ける種類は ACP の契約と画面の糊が決め、agentd は運ぶだけ。"
  (setv found [])
  (setv headlines (.get spec MESSAGE-ATTACHMENTS-KEY))
  (when (isinstance headlines list)
    (for [headline headlines]
      (when (isinstance headline dict)
        (setv ref (.get headline ATTACHMENT-REF-KEY))
        (setv seq (.get headline ATTACHMENT-SEQ-KEY))
        (when (and (isinstance ref dict)
                   (isinstance (.get ref "conversation") str)
                   (isinstance (.get ref "stream") str)
                   (isinstance seq int)
                   (not (isinstance seq bool))
                   (>= seq 0))
          (.append found #((get ref "conversation") (get ref "stream") seq
                           (.get headline ATTACHMENT-MIME-KEY)
                           (.get headline ATTACHMENT-BYTES-KEY)
                           (.get headline ATTACHMENT-SHA256-KEY)
                           (.get headline ATTACHMENT-NAME-KEY)))))))
  (tuple found))


(defk attachment-of [events headline]
  {:pre [(: events tuple) (: headline tuple)]
   :post [(: % (| TurnAttachment None))]}
  "見出し 1 つ + その stream の出来事の列 → 器へ渡す型つきの添付。名指した作り手の序数の kind attachment の
   出来事に中身(data)が在り、見出しの mime / bytes / sha256 と食い違わない時だけ値を返す。無い・欠けた・
   食い違う時は None(呼び手が条件 AttachmentIgnored に写す — 黙って落とさない・中身を発明しない)。

   ⚠ **見出しの bytes / sha256 は画像の生の byte**(差出人の client の 1 点 attachments-plan が測る材料 —
   ACP の法 89ce1f)。記録の service の出来事が名乗る bytes / sha256 は**本文の欄の compact JSON**を測った
   別の値なので、そのまま比べてはならない。⇒ base64 を解いた**生の byte**で比べる。
   実弾 2026-09-15 02:39(本番の e2e chat.send-image): 出来事の値と見出しを比べていたので必ず食い違い、
   本番の log に『attachment 1 of message … could not be read from the record service』が出て、
   画像が 1 枚も CLI へ渡らなかった(手番は条件なしで終わるので、黙って画像だけが落ちていた)。"
  (setv seq (get headline 2))
  (setv mime (get headline 3))
  (setv size (get headline 4))
  (setv digest (get headline 5))
  (setv name (get headline 6))
  (setv found None)
  (for [event events]
    (when (and (is found None)
               (= event.producer-seq seq)
               (= event.kind RECORD-ATTACHMENT-EVENT-KIND)
               (isinstance event.data str)
               (isinstance event.mime str)
               (or (not (isinstance mime str)) (= event.mime mime)))
      ;; 生の byte(見出しが名乗る材料)。解けない綴りは値にしない — 中身を発明しない。
      (setv raw (try (base64.b64decode event.data :validate True)
                     (except [[binascii.Error ValueError]] None)))
      (when (and (is-not raw None)
                 (or (not (isinstance size int)) (isinstance size bool) (= (len raw) size))
                 (or (not (isinstance digest str))
                     (= (.hexdigest (hashlib.sha256 raw)) digest)))
        (setv found (TurnAttachment :mime event.mime
                                    :data event.data
                                    :bytes (len raw)
                                    :sha256 (.hexdigest (hashlib.sha256 raw))
                                    :name (if (isinstance name str) name ""))))))
  found)


(defk mail-text-of [events]
  {:pre [(: events tuple)]
   :post [(: % (| str None))]}
  "段 10f 便 1b: 郵便の stream の出来事の列(1 郵便 = 1 出来事 kind message)→ 本文。本文を持つ kind message の出来事が
   無ければ None(読めない郵便 — 呼び手が missing と名乗る・本文を発明しない)。"
  (setv found None)
  (for [event events]
    (when (and (is found None) (= event.kind RECORD-MAIL-EVENT-KIND) (isinstance event.text str))
      (setv found event.text)))
  found)


(defk first-turn-attachments-of [carried]
  {:pre [(: carried tuple)]
   :post [(: % tuple)]}
  "段 10 lane 10o(agora-redesign #96): 起こす腕が 1 手番目に畳む郵便の添付 = 畳む郵便すべての添付を順に
   1 本に並べたもの(本文が 1 つの prompt に畳まれるので、添付も同じ 1 手番に載る)。"
  (setv flat [])
  (for [one carried]
    (for [attachment one]
      (.append flat attachment)))
  (tuple flat))


(defk launch-charter-with-attachments [charter attachments]
  {:pre [(: charter dict) (: attachments tuple)]
   :post [(: % dict)]}
  "起こす charter に 1 手番目の添付を載せる(添付が無ければ charter は 1 byte も変えない — 欄を作らない)。
   ⚠ charter は **RPC へ出る object** なので、型つきの値のままでは JSON にできない — 項の綴りの 1 点
   (sessionhost.attachment.attachment-wire)で書く。host の口が同じ 1 点で型つきに戻す。
   ⚠ 画像の CLI の綴りはここに無い: 器が型つきで受け取り、kind ごとの Dialogue が組む(法 012 R21)。
   実弾 2026-09-15 03:08: 型つきのまま入れていたので RPC へ出す拍に
   `TypeError: Object of type TurnAttachment is not JSON serializable` で tick ごと落ち、
   手番が SessionFailed で終わっていた(画像も本文も届かない)。"
  (if attachments
      (dict charter #** {MESSAGE-ATTACHMENTS-KEY (lfor one attachments (attachment-wire one))})
      charter))


(defk mail-heading-of [message-id spec]
  {:pre [(: message-id str) (: spec dict)]
   :post [(: % str)]}
  "郵便の見出し 1 行(段 10 lane 10r 追補・agora-redesign #99・依頼者の裁定 2026-09-15 案 A): 郵便の身元は配達の封筒の一部で、
   CLI へ渡す係 = agentd が本文の前に付ける — 手番の agent は郵便を id で名指せる(受付の `ai forward <id> <担い手>` は
   id が要る)。綴りはこの 1 点: `[郵便 <id>・kind=<kind>・class=<class か 無し>・from=<会話 id か operator>・
   parent=<id か 無し>・at=<JST>]`。欠けた欄は「無し」(発明しない)。at は契約の時計(epoch ms)を JST で。"
  (setv none "無し")
  (setv words {})
  (for [key ["kind" "class" "from" "parent"]]
    (setv value (.get spec key))
    (setv (get words key) (if (and (isinstance value str) (.strip value)) value none)))
  (setv at (.get spec "at"))
  (setv at-text (if (and (isinstance at int) (not (isinstance at bool)))
                    (.strftime (datetime.fromtimestamp (/ at 1000) :tz (timezone (timedelta :hours 9) "JST")) "%Y-%m-%d %H:%M:%S JST")
                    none))
  (+ "[郵便 " message-id
     "・kind=" (get words "kind")
     "・class=" (get words "class")
     "・from=" (get words "from")
     "・parent=" (get words "parent")
     "・at=" at-text "]"))


(defk mail-input-ids-of [asked missing]
  {:pre [(: asked tuple) (: missing tuple)]
   :post [(: % tuple)]}
  "この手番が運ぶ郵便の id(行の inputs の順): 行が頼んだ id のうち、本文を読めなかった id(missing —
   条件 InputUnavailable の座)を除いたもの。message-bodies-of の bodies / attachments と同じ並びで、
   i 番の本文は i 番の id の郵便(card acp:kanban-issue:ki-3149aebbf675 A: 『渡せた』を id で記帳するには
   本文の並びと id の並びが 1 点で対応していなければならない)。"
  (tuple (lfor input-id asked :if (not-in input-id missing) input-id)))


(defk send-parcels-of [ids bodies carried folds]
  {:pre [(: ids tuple) (: bodies tuple) (: carried tuple) (: folds bool)]
   :post [(: % tuple)]}
  "after-start が撃つ送りの束(card acp:kanban-issue:ki-3149aebbf675 B / A の 1 点): 各項は
   #(本文 添付 その送りが運ぶ郵便の id の組)。畳む(headless)なら 1 通の束 1 つ — 1 手番 = 1 prompt なので
   相乗りした N 通は 1 回の send に畳み、その 1 回の成否が N 通ぜんぶの成否(判断は send-folds-bodies)。
   畳まない(tui)なら 1 通 1 束で、i 番の束は i 番の郵便ちょうど。bodies が空なら束も空(起こす腕は
   1 手番目の prompt に畳んであるので送りは無い)。⚠ 本文と id の対応を知るのはこの 1 点 — 呼び手が
   並びを組み直すと、届いた id と記帳する id がずれる。"
  (when (not bodies)
    (return #()))
  (if folds
      (do
        (<- text str (first-turn-prompt-of "" bodies))
        (<- attachments tuple (first-turn-attachments-of carried))
        #(#(text attachments ids)))
      (tuple (lfor [index body] (enumerate bodies)
                   #(body
                     (if (< index (len carried)) (get carried index) #())
                     (if (< index (len ids)) #((get ids index)) #()))))))


(defk mail-turn-text-of [message-id spec body]
  {:pre [(: message-id str) (: spec dict) (: body str)]
   :post [(: % str)]}
  "手番へ渡す郵便の文 = 見出し(mail-heading-of)1 行 + 本文。1 手番目に畳む腕(first-turn-prompt-of)・温かい session への
   send・割り込みの注入の 3 つの路が同じ文を運ぶ(郵便の手番の文を組む点はここだけ — message-bodies-of と割り込みの腕が呼ぶ)。"
  (<- heading str (mail-heading-of message-id spec))
  (+ heading "\n" body))


(defk message-bodies-of [rows inputs fetched carried]
  {:pre [(: rows tuple) (: inputs tuple) (: fetched dict) (: carried dict)]
   :post [(: % tuple)]}
  "inputs の id に対応する Message の本文(spec.body)と添付を inputs の順に。戻り =
   #(bodies attachments missing-ids)。鍵は契約の identityKey(spec.id)、無ければ行の resourceId。
   段 10 lane 10o(agora-redesign #96): 本文と添付は**同じ 1 つの述語**でここ 1 点で並べる —
   bodies の i 番と attachments の i 番は同じ郵便(2 つの関数に分けると並びがずれる)。本文の無い
   郵便は missing で、その添付も並びに載らない(本文と添付は同じ郵便の同じ 1 手番)。"
  (setv by-id {})
  (for [row rows]
    (setv spec-id (.get row.spec "id"))
    (setv (get by-id (if (isinstance spec-id str) spec-id row.resource-id)) row))
  (setv bodies [])
  (setv attachments [])
  (setv missing [])
  (for [input-id inputs]
    (setv row (.get by-id input-id))
    ;; 段 10f 便 1b: 本文を記録の service に置いた郵便は fetched(郵便 id → 本文)から引く。
    (setv body (cond (is row None) None
                     (isinstance (.get row.spec "body") str) (.get row.spec "body")
                     True (.get fetched input-id)))
    (if (and (is-not row None) (isinstance body str))
        (do
          ;; 段 10 lane 10r 追補: 本文の前に郵便の見出し(1 手番目の畳みと温かい send は同じ bodies を読む)。
          (<- text str (mail-turn-text-of input-id row.spec body))
          (.append bodies text)
          ;; 読めなかった添付は carried に載っていない = 空(呼び手が条件 AttachmentIgnored に写す)。
          (.append attachments (.get carried input-id #())))
        (.append missing input-id)))
  #((tuple bodies) (tuple attachments) (tuple missing)))


;; ---------------------------------------------------------------------------
;; 行の欄の写し(node の lease と観測)
;; ---------------------------------------------------------------------------

(defk node-row-entry-of [row]
  {:pre [(: row AcpRow)]
   :post [(: % dict)]}
  "AcpRow → library の項(契約 scheduling.json liveRow.nodeRow の綴り — name = spec.name・alive = status.state == joined・
   lease = status.lease.expiresAt〔epoch ms〕・row = この行)。綴りを知るのはここ 1 点で、判断(resolve-live-row)は綴りを知らない。"
  (setv status (if (isinstance row.status dict) row.status {}))
  (setv lease (.get status "lease"))
  (setv expires (if (isinstance lease dict) (.get lease "expiresAt") None))
  {"name" (str (.get row.spec "name" ""))
   "alive" (= (.get status "state") NODE-JOINED)
   "lease" (if (and (isinstance expires int) (not (isinstance expires bool))) expires None)
   "row" row})


(defk node-row-named [rows name]
  {:pre [(: rows tuple) (: name str)]
   :post [(: % (| AcpRow None))]}
  "自分の名が指す**生きている** Node の行(段 12 lane 12j・agora-redesign #320・#317 規則 1): 判断は ACP の client library の
   写し live_row.resolve-live-row の 1 点(終端 = gone の行は候補にしない・生きている行は lease の新しい順)。one = その行・
   many = 先頭(lease の最も新しい行 — 動き続けねばならない呼び手の規則 preferred-live-row と同じ・同じ名の 2 本は再起動の
   直後の旧い化身が lease を残している拍)・none = None(join が新しい化身を作る)。名前の索引をここに持たない。"
  (setv entries [])
  (for [row rows]
    (<- entry dict (node-row-entry-of row))
    (when (= (get entry "name") name)
      (.append entries entry)))
  (<- resolved dict (resolve-live-row entries))
  (setv live (get resolved "live"))
  (if (= (len live) 0) None (get live 0)))


(defk live-node-names-of [rows]
  {:pre [(: rows tuple)]
   :post [(: % frozenset)]}
  "kind node の行のうち**生きている**(status.state == joined)行の spec.name の集合(段 12・agora-redesign #537 便 1)。
   綴りを知るのは node-row-entry-of の 1 点のままで、ここはその alive を集めるだけ。走っている turn-record の巡回が
   『この記録を名乗る機体はまだ居るか』を引く材料 — 居ない機体(再配備で名前ごと消えた pool の pod)の記録は誰が
   閉じてもよい。"
  (setv names [])
  (for [row rows]
    (<- entry dict (node-row-entry-of row))
    (when (get entry "alive")
      (.append names (get entry "name"))))
  (frozenset names))


(defk node-resource-id-of [rows name]
  {:pre [(: rows tuple) (: name str)]
   :post [(: % str)]}
  "自分の node の行を**作る**時の resource-id(段 10 lane 10d 便 4・agora-redesign #107 の (3))。

   身元は spec.name の 1 点(契約 node の identityKey)で、resource-id は行の器の名にすぎない。engine は
   『**生きた**行が身元を持っている』時だけ身元の衝突を断るので、配車から外された(gone の)行は身元を
   占めない — が、その行が name の**鍵**を占めているので、同じ鍵での create は永久に conflict になる
   (本番の実弾 2026-09-14〜15: agentd の入れ替えのたびに行が gone になり、再参加が 409 で回り続け、
   依頼者が status を手で書いて戻していた)。⇒ 鍵が空いていなければ、同じ身元の**新しい incarnation**
   として空いている `<name>-<n>`(n は 2 から)を選ぶ。読み手は spec.name で引くので面は変わらない。"
  (setv taken #{})
  (for [row rows]
    (setv key (str row.key))
    (setv at (.rfind key ":"))
    (.add taken (if (= at -1) key (cut key (+ at 1) None))))
  (if (not-in name taken)
      name
      (do
        (setv n 2)
        (while (in f"{name}-{n}" taken)
          (setv n (+ n 1)))
        f"{name}-{n}")))


(defk node-labels-of [settings labels]
  {:pre [(: settings AgentdSettings) (: labels dict)]
   :post [(: % dict)]}
  "行の labels に、宣言から名乗る置き場の集合の写し(labels.places = , 区切りの 1 文字列・deprecated・読み手が残る間の面)
   を重ねた形(段 10 lane 10d 便 2・agora-redesign #85・段 11 lane 11u・#224 で集合へ)。退役した 1 値の写し labels.place
   は落とす(旧い agentd が書いた行を揃える時 — 配車は読まないが、2 つの綴りを並べない)。行の他の名乗り(会社境界の
   boundary 等・宣言の外のもの)は触らない。集合の宣言が空の断面(検体の既定 — 本番は composition root が参加を断る)では
   足さない: 嘘の名乗りを書かない。"
  (setv next (dict labels))
  (.pop next NODE-LABEL-PLACE-RETIRED None)
  (when settings.places
    (setv (get next NODE-LABEL-PLACES) (.join PLACES-SEPARATOR settings.places)))
  next)


(defk node-places-of [settings spec]
  {:pre [(: settings AgentdSettings) (: spec dict)]
   :post [(: % dict)]}
  "宣言から名乗る置き場の集合を spec の欄 places(語の list・宣言の順)に置いた写し(段 11 lane 11u・agora-redesign #224・
   依頼者の裁定 2026-09-16)。**名乗りの座はこの型つきの欄 1 つ** — 配車の絞り(ACP の nodeAcceptsProfile)はここだけを
   読み、profile の boundary が集合に含まれる node にだけ結ぶ。退役した 1 値の欄 place(契約 v3)は落とす — 配車は place を
   持つ行を行の誤りとして断るので、旧い行を揃える写しに残さない。集合の宣言が空の断面(検体の既定 — 本番は composition
   root が参加を断る)では足さない: 嘘の名乗りを書かない。"
  (setv next (dict spec))
  (.pop next NODE-SPEC-PLACE-RETIRED None)
  (when settings.places
    (setv (get next NODE-SPEC-PLACES) (list settings.places)))
  next)


(defk node-work-roots-of [settings spec]
  {:pre [(: settings AgentdSettings) (: spec dict)]
   :post [(: % dict)]}
  "宣言から名乗る作業場の根を spec の欄 workRoots(文字列の list・宣言の順)に置いた写し(段 10 lane 10y・agora-redesign #110・依頼者の
   裁定 2026-09-15 案 C)。宣言が無い(None)なら足さない — 名乗らない node の行に欄は無く、読み方は配車の側が決める。"
  (setv next (dict spec))
  (when (is-not settings.work-roots None)
    (setv (get next NODE-SPEC-WORK-ROOTS) (list settings.work-roots)))
  next)


(defk node-work-dirs-of [settings spec]
  {:pre [(: settings AgentdSettings) (: spec dict)]
   :post [(: % dict)]}
  "join が家から導いた「持つ作業場」を spec の欄 workDirs(文字列の list・綴りの順)に置いた写し(段 12 lane 12j・agora-redesign #575
   便 2・#557 案 A の後半)。None(導いていない)なら足さない — 欄の無い node は配車が篩わない。空の list は「何も持たない」の宣言
   (checkout の無い pod — 配車はその node に区画の手番を結ばない)。"
  (setv next (dict spec))
  (when (is-not settings.work-dirs None)
    (setv (get next NODE-SPEC-WORK-DIRS) (list settings.work-dirs)))
  next)


(defk node-work-dir-roots-of [settings spec]
  {:pre [(: settings AgentdSettings) (: spec dict)]
   :post [(: % dict)]}
  "join が実勢から導いた「持っている根」を spec の欄 workDirRoots(文字列の list・宣言の順)に置いた写し(段 12 lane 12j 追補・
   card acp:kanban-issue:ki-3bfe48a9d5dc)。None(導いていない)なら足さない — 欄の無い node の判定は今日どおり(名簿だけ)。
   空の list は「候補の根がどれも無い」の宣言。名簿(workDirs)と違って根の下は列挙しない。"
  (setv next (dict spec))
  (when (is-not settings.work-dir-roots None)
    (setv (get next NODE-SPEC-WORK-DIR-ROOTS) (list settings.work-dir-roots)))
  next)


(defk node-custody-borrower-of [settings spec]
  {:pre [(: settings AgentdSettings) (: spec dict)]
   :post [(: % dict)]}
  "composition root が導いた「預かり所へ名乗る借り手の等価鍵」を spec の欄 custodyBorrower に置いた写し
   (card acp:kanban-issue:ki-40021864e62f・ACP 側の依頼 lt-FMEPYFTCRQSKV4V8V0A82VQQFC)。
   None(名乗らない)なら**欄ごと足さない** —— 配車(ACP Decide.nodeCustodyKey)は欄の無い node を node 名で
   束ね、この軸が無かった時と 1 bit も変わらない(版が混ざる艦隊と、ACP 先 / doeff 後で片側だけ着地した
   断面の排水路)。判断は composition root(runtime._custody_borrower_of_env → join.custody-borrower-of)の
   1 点で、ここは写すだけ。"
  (setv next (dict spec))
  (when (is-not settings.custody-borrower None)
    (setv (get next NODE-SPEC-CUSTODY-BORROWER) settings.custody-borrower))
  next)


(defk declared-capacity-of [settings]
  {:pre [(: settings AgentdSettings)]
   :post [(: % int)]}
  "node が名乗る capacity(段 12 lane 12j・agora-redesign #304 便 2): 排水の最中(settings.draining)は 0 — 配車は新しい手番を
   この node に結ばず、走っている手番は最後まで観測される。それ以外は宣言 file の [agentd].capacity。判断はここ 1 点。"
  (if settings.draining 0 settings.node-capacity))


(defk agentd-version-of [settings]
  {:pre [(: settings AgentdSettings)]
   :post [(: % dict)]}
  "参加時に node の spec.agentd へ名乗る自分の版(段 12 lane 12j・agora-redesign #367・既知の形 行 3 (h)): protocol = effects.AGENTD-PROTOCOL
   の 1 点(ACP との wire の版・配置の床が比べる)・revision = 据え付けの刻印(無ければ unstamped — 嘘の sha を書かない)・build = image の
   tag か local。node-spec-of と node-spec-declared の両方がこの 1 点を読む。"
  {NODE-SPEC-AGENTD-PROTOCOL-KEY AGENTD-PROTOCOL
   NODE-SPEC-AGENTD-REVISION-KEY settings.agentd-revision
   NODE-SPEC-AGENTD-BUILD-KEY settings.agentd-build})


(defk node-spec-of [settings]
  {:pre [(: settings AgentdSettings)]
   :post [(: % dict)]}
  "機体が名乗る自分の node の spec(R28・段 10 lane 10d・agora-redesign #85 — 既知の形 = kubelet の Node の自己登記):
   name = 機体の名・places = 宣言 file の [agentd].places の集合(**配車の絞りが読む 1 点**・段 11 lane 11u)・
   capacity = 宣言 file の [agentd].capacity・streamCapability = backend から導いた語・
   labels = 同じ集合の写し(labels.places — 読み手が残る間の deprecated の面。配車は読まない)。"
  (<- labels dict (node-labels-of settings {}))
  (<- capacity int (declared-capacity-of settings))
  (<- version dict (agentd-version-of settings))
  (<- placed dict (node-places-of settings {"name" settings.node-name
                                           "labels" labels
                                           "capacity" capacity
                                           "streamCapability" settings.stream-capability
                                           NODE-SPEC-AGENTD-KEY version}))
  (<- rooted dict (node-work-roots-of settings placed))
  (<- held dict (node-work-dirs-of settings rooted))
  (<- under dict (node-work-dir-roots-of settings held))
  ;; card acp:kanban-issue:ki-40021864e62f: 預かり所へ名乗る借り手の等価鍵(名乗らない機体は欄ごと無い)。
  (<- borrowing dict (node-custody-borrower-of settings under))
  borrowing)


(defk node-spec-declared [spec settings]
  {:pre [(: spec dict) (: settings AgentdSettings)]
   :post [(: % dict)]}
  "既に在る自分の node の行の spec を宣言へ揃えた形(R28): name・places・capacity・streamCapability・labels.places は
   宣言から、labels の他の名乗り(会社境界の boundary 等 — 宣言の外)は行のまま(agentd は触らない・欠落 / 型違いは空)。
   退役した 1 値の place / labels.place(契約 v3)は落とす(段 11 lane 11u — 配車は place を持つ行を断る)。
   宣言と一致していれば行の spec と等しい dict(呼び手は等しくない時だけ書く)。"
  (setv labels (.get spec "labels"))
  (<- declared dict (node-labels-of settings (if (isinstance labels dict) labels {})))
  (<- capacity int (declared-capacity-of settings))
  (<- version dict (agentd-version-of settings))
  (<- placed dict (node-places-of settings {"name" settings.node-name
                                           "labels" declared
                                           "capacity" capacity
                                           "streamCapability" settings.stream-capability
                                           NODE-SPEC-AGENTD-KEY version}))
  (<- rooted dict (node-work-roots-of settings placed))
  (<- held dict (node-work-dirs-of settings rooted))
  (<- under dict (node-work-dir-roots-of settings held))
  ;; card acp:kanban-issue:ki-40021864e62f: 預かり所へ名乗る借り手の等価鍵(名乗らない機体は欄ごと無い)。
  (<- borrowing dict (node-custody-borrower-of settings under))
  borrowing)


(defk node-lease-of [settings now-ms]
  {:pre [(: settings AgentdSettings) (: now-ms int)]
   :post [(: % dict)]}
  "node の lease{owner, heartbeatAt, expiresAt}。expiresAt = now + TTL(周期と TTL の定義点は effects.AgentdSettings の
   node-heartbeat-seconds / node-lease-ttl-seconds の 1 点)。"
  {"owner" settings.principal
   "heartbeatAt" now-ms
   "expiresAt" (+ now-ms (* 1000 settings.node-lease-ttl-seconds))})


(defk node-status-with-renewed-lease [row settings now-ms]
  {:pre [(: row AcpRow) (: settings AgentdSettings) (: now-ms int)]
   :post [(: % dict)]}
  "lease の heartbeat が書く node の status(段 10 lane 10ba・agora-redesign #115): committed の status を写し、lease だけを
   新しくする(observations・capabilities・state には触らない — lease の書き手は heartbeat の thread の 1 つ)。"
  (<- next dict (status-object-of row))
  (<- lease dict (node-lease-of settings now-ms))
  (setv (get next "lease") lease)
  next)


(defk node-status-with-observations [row settings sessions transcripts]
  {:pre [(: row AcpRow) (: settings AgentdSettings) (: sessions list) (: transcripts list)]
   :post [(: % dict)]}
  "tick の参加の腕が書く node の status: committed の status を写し(lease は写すだけ — 書くのは heartbeat の thread・
   段 10 lane 10ba)、observations{streamCapability, sessions, transcripts, ownership?}(sessions = session-observations-of の列・
   transcripts = 終端の session のうち transcript がこの機体に残る会話の列〔段 8q〕・
   ownership = 起動の前に検めた所有の等級 {grade, proof} — 宣言が無ければ欄ごと書かない = 未観測・
   段 6 lane 6f)と capabilities(能力の表 — 段 10 lane 10e・capabilities-of)を差し替える。state(scheduling の欄)は写すだけ。"
  (<- next dict (status-object-of row))
  (setv observations
        {"streamCapability" settings.stream-capability
         "sessions" sessions
         "transcripts" transcripts})
  (when (is-not settings.ownership None)
    (setv (get observations "ownership")
          {"grade" settings.ownership.grade "proof" settings.ownership.proof}))
  (setv (get next "observations") observations)
  ;; 段 10 lane 10e: 能力の表(受ける欄 / 作り直す欄)を lease と同じ拍に名乗る(契約の書き手 = agentd)。
  (<- table dict (capabilities-of))
  (setv (get next NODE-CAPABILITIES-KEY) table)
  next)


;; ---------------------------------------------------------------------------
;; 行の欄の写し(profile の残量の観測 — 段 7 lane 7d-3)
;; ---------------------------------------------------------------------------

(defk profile-rows-active [rows]
  {:pre [(: rows tuple)]
   :post [(: % tuple)]}
  "profile の行のうち観測する行 = 生きている(state ≠ retired)行、行の順のまま。"
  (setv out [])
  (for [row rows]
    (setv state (if (isinstance row.status dict) (.get row.status "state") None))
    (when (!= state PROFILE-RETIRED)
      (.append out row)))
  (tuple out))


(defk profile-kind-of [row]
  {:pre [(: row AcpRow)]
   :post [(: % str)]}
  "行の口座の種類 = spec.kind(claude / codex — 配置の観測の腕が預かり所の在庫から写す)。欄の無い行は
   PROFILE-USAGE-KIND(claude)と読む(段 12 lane 12c・agora-redesign #479 より前の行は全部 claude)。"
  (setv kind (.get row.spec "kind"))
  (if (and (isinstance kind str) kind) kind PROFILE-USAGE-KIND))


(defk profile-rows-of-kind [active kind]
  {:pre [(: active tuple) (: kind str)]
   :post [(: % tuple)]}
  "生きている行のうち口座の種類が kind の行 — 行の順のまま。観測の腕は名簿の種類ごと(PROFILE-USAGE-KINDS)に
   家と残量を読むので、その種類の行が 1 つも無ければ家も usage も読まない(#479)。"
  (setv out [])
  (for [row active]
    (when (= (profile-kind-of-plain row) kind)
      (.append out row)))
  (tuple out))


(defn #^ str profile-kind-of-plain [#^ AcpRow row]
  "profile-kind-of と同じ答えの純関数(lfor / for の中から呼ぶ形 — 判断は 1 つ・綴りは同じ既定)。"
  (setv kind (.get row.spec "kind"))
  (if (and (isinstance kind str) kind) kind PROFILE-USAGE-KIND))


(defn #^ bool home-carries-name [#^ ProfileHome home #^ str name]
  "家が行の名を名乗るか = 名簿の名か別名に一致(#479 D-479-2: ACP の行の名 codex-personal ↔ 名簿の家 personal)。"
  (or (= home.name name) (in name home.aliases)))


(defk profile-rows-held [active homes settings]
  {:pre [(: active tuple) (: homes tuple) (: settings AgentdSettings)]
   :post [(: % tuple)]}
  "生きている profile の行のうち、この機体に家(config dir)の在る profile の行 — 行の順のまま。
   判断はここ 1 点(段 8e lane 4j): 空なら観測の腕は usage を読まない(pool の pod は profile を
   1 つも持たない — 読み口が落ちる形で知るのではなく、家の在否で先に決める)。家は spec.name で
   引く — 登録簿の名か**別名**に一致する家(段 12 lane 12c・agora-redesign #479: codex の行の名は預かり所の
   別名 codex-personal で名簿の名 personal と違う。claude は両者が同じ綴り)。呼び手は種類ごとの行と
   その種類の家を渡す(profile-rows-of-kind)— 種類をまたいで同じ名の家に結ばない。
   段 10 lane 10y(agora-redesign #110・operator 指示 2026-09-09「会社 profile の API 呼び出しは会社所有の機体だけ」):
   宣言の所有の等級(join で検めた settings.ownership)が company でない機体(personal・未宣言)は、spec.boundary = company の
   行を家が在っても持たない — usage を読む列にも log にも会社の口座が現れない。軸は機体の所有で、置き場(place)では
   ない(会社 Mac は place personal でも所有は company で、会社の口座を今日どおり観測する)。provider を呼んでよいかの
   最後の判定は今日どおり agentcli の葉(ReadProfileUsage の断り)が持つ。"
  (setv present (tuple (gfor home homes :if home.present home)))
  (setv company-owned (and (is-not settings.ownership None) (= settings.ownership.grade OWNERSHIP-GRADE-COMPANY)))
  (tuple (lfor row active
               :if (and (any (gfor home present (home-carries-name home (str (.get row.spec "name" row.resource-id)))))
                        (or company-owned (!= (.get row.spec "boundary") OWNERSHIP-GRADE-COMPANY)))
               row)))


(defk usage-by-profile [outcomes]
  {:pre [(: outcomes tuple)]
   :post [(: % dict)]}
  "usage の答えの列 → profile の名 → 答え(同じ名は最後の答え)。"
  (setv table {})
  (for [outcome outcomes]
    (setv (get table outcome.profile) outcome))
  table)


(defk usage-by-row-name [rows homes by-name]
  {:pre [(: rows tuple) (: homes tuple) (: by-name dict)]
   :post [(: % dict)]}
  "行の名 → その行の家の**正名**で引いた usage の答え(#479 D-479-2)。usage の記録は名簿の名(personal)で
   立つが、行の名は別名(codex-personal)でもよいので、家(名か別名が一致・profile-rows-held と同じ規則)を
   経て引く。家が無い・答えが無い行は載せない(profile-observed-of が『持たない』と読む)。"
  (setv table {})
  (for [row rows]
    (setv name (str (.get row.spec "name" row.resource-id)))
    (for [home homes]
      (when (and (not-in name table) (home-carries-name home name))
        (setv outcome (.get by-name home.name))
        (when (is-not outcome None)
          (setv (get table name) outcome)))))
  table)


(defk observed-window-of [row windows]
  {:pre [(: row AcpRow) (: windows tuple)]
   :post [(: % str)]}
  "どの窓を観測するか — 判断はここ 1 点: spec.reset.everySeconds と周期が一致する provider の窓
   (effects.USAGE-WINDOW-SECONDS)、一致する窓が無ければ既定(5h)= 宣言の窓。
   宣言の窓が答え(windows)に無く、答えに別の窓が在れば、答えの窓のうち周期の最も長いものを観測する
   (段 12 lane 12c・agora-redesign #479 D-479-3: codex の pro plan は 7d の窓しか返さず 5h は null —
   宣言の窓を待つと永久に未観測)。値は発明しない(答えに在る窓の名を observed.window に名乗る)。
   答えが空なら宣言の窓(呼び手が『答えに無い』と名乗る)。"
  (setv reset (.get row.spec "reset"))
  (setv every (if (isinstance reset dict) (.get reset "everySeconds") None))
  (setv found None)
  (for [[name seconds] (.items USAGE-WINDOW-SECONDS)]
    (when (and (is found None) (isinstance every int) (= seconds every))
      (setv found name)))
  (setv declared (if (is found None) PROFILE-OBSERVED-WINDOW-DEFAULT found))
  (setv names (sfor window windows window.name))
  (if (or (in declared names) (not names))
      declared
      (do
        (setv longest None)
        (for [window windows]
          (when (or (is longest None)
                    (> (.get USAGE-WINDOW-SECONDS window.name 0) (.get USAGE-WINDOW-SECONDS longest 0)))
            (setv longest window.name)))
        longest)))


(defk profile-observed-of [row usage node-name]
  {:pre [(: row AcpRow) (: usage (| ProfileUsage ProfileUsageUnavailable None)) (: node-name str)]
   :post [(: % (| ProfileObservation ProfileUnobserved ProfileNotHeld))]}
  "1 つの profile の行と、この機体の usage の答えから、書く観測(閉語彙 effects.ProfileVerdict)を
   決める 1 点: 答えが無い → 持たない(書かず log もしない)/ 読めなかった(会社境界の断り・
   provider の失敗 — 判定は agentcli の葉)→ 書かない(理由を log)/ budget.unit が percent でない →
   書かない(remaining の単位が無い)/ 選んだ窓が答えに無い → 書かない / それ以外 → observed
   {window, remaining = 満量 - 使用(percent・0 未満は 0), resetAt = 窓の戻る時刻(窓が空で無ければ
   観測の時刻 = 待つ窓が無い), observedAt = 断面の時刻, node = 自分}。値は発明しない。"
  (setv budget (.get row.spec "budget"))
  (setv unit (if (isinstance budget dict) (.get budget "unit") None))
  (cond
    (is usage None) (ProfileNotHeld)
    (isinstance usage ProfileUsageUnavailable) (ProfileUnobserved :reason usage.reason)
    (!= unit PROFILE-BUDGET-UNIT-PERCENT)
    (ProfileUnobserved :reason f"budget.unit {unit !r} is not {PROFILE-BUDGET-UNIT-PERCENT} — remaining has no unit to report")
    True
    (do
      (<- window-name str (observed-window-of row usage.windows))
      (setv window None)
      (for [candidate usage.windows]
        (when (and (is window None) (= candidate.name window-name))
          (setv window candidate)))
      (if (is window None)
          (ProfileUnobserved :reason f"usage of {usage.profile} carries no {window-name} window")
          (ProfileObservation
            :observed {"window" window-name
                       "remaining" (max 0.0 (- USAGE-WINDOW-FULL-PERCENT window.used-percent))
                       "resetAt" (if (is window.resets-at-ms None) usage.captured-at-ms window.resets-at-ms)
                       "observedAt" usage.captured-at-ms
                       "node" node-name})))))


(defk profile-observed-changed [row observed node-name]
  {:pre [(: row AcpRow) (: observed dict) (: node-name str)]
   :post [(: % bool)]}
  "自分の枡(committed の status.observedBy[node])と違うか(同じなら書かない — 断面が同じ拍は書きを
   起こさない)。段 12 lane 12j(agora-redesign #351・依頼者の裁定 (B)): 比べるのは自分の枡で、最新の
   1 枡(observed)は比べない — 他の機体が書き換える枡を比べると、2 台が互いの拍を『変化』と読んで
   毎周期書き合う(実測 2026-09-16: personal の generation 1436・btc 1568・observed.node は数秒で入れ替わる)。"
  (<- status dict (status-object-of row))
  (setv slots (.get status PROFILE-STATUS-OBSERVED-BY-KEY))
  (setv mine (if (isinstance slots dict) (.get slots node-name) None))
  (!= mine observed))


(defk profile-latest-should-replace [current observed period-ms]
  {:pre [(: current (| dict None)) (: observed dict) (: period-ms int)]
   :post [(: % bool)]}
  "最新の 1 枡(status.observed)を自分の観測で置き換えるか — 枡が無い / 値(window・remaining・resetAt)が
   違う / 載っている観測が自分の周期より古い(observedAt の差 ≥ period)時だけ。同じ値の新しい拍では
   置き換えない(2 台の書き合いを止める — #351)。observedAt を読めない枡は古いと読む。"
  (cond
    (is current None) True
    (any (gfor key ["window" "remaining" "resetAt"] (!= (.get current key) (.get observed key)))) True
    True
    (do
      (setv current-at (.get current "observedAt"))
      (setv mine-at (.get observed "observedAt"))
      (or (not (isinstance current-at int))
          (not (isinstance mine-at int))
          (>= (- mine-at current-at) period-ms)))))


(defk profile-status-with-observed [row observed node-name period-ms]
  {:pre [(: row AcpRow) (: observed dict) (: node-name str) (: period-ms int)]
   :post [(: % dict)]}
  "agentd が書く欄だけを更新した profile の status: committed の status(state・conditions は
   他の書き手の欄 — 落とすと engine が断る)を写し、自分の枡 observedBy[node] に観測を据え、最新の
   1 枡 observed は profile-latest-should-replace が真の時だけ置き換える(他の node の枡は行のまま写す)。"
  (<- next dict (status-object-of row))
  (setv slots (.get next PROFILE-STATUS-OBSERVED-BY-KEY))
  (setv slots (if (isinstance slots dict) (dict slots) {}))
  (setv (get slots node-name) observed)
  (setv (get next PROFILE-STATUS-OBSERVED-BY-KEY) slots)
  (setv current (.get next PROFILE-STATUS-OBSERVED-KEY))
  (<- replace-latest bool (profile-latest-should-replace (if (isinstance current dict) current None) observed period-ms))
  (when replace-latest
    (setv (get next PROFILE-STATUS-OBSERVED-KEY) observed))
  next)


;; ---------------------------------------------------------------------------
;; 手番の記録(turn-record)
;; ---------------------------------------------------------------------------

(defk turn-record-key-of [job-id]
  {:pre [(: job-id str)]
   :post [(: % str)]}
  "turn-record の行の鍵(identityKey = agentJobId・区画 = agora の kind の区画)。"
  f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:{job-id}")


(defk in-flight-job-of [row plan view node-name started-ms turn-floor-ms start-offset lease pending]
  {:pre [(: row AcpRow) (: plan LaunchPlan) (: view SessionView) (: node-name str)
         (: started-ms int) (: turn-floor-ms int) (: start-offset int)
         (: lease (| LeaseGrant None)) (: pending tuple)]
   :post [(: % InFlightJob)]}
  "agent-job の行 + 起こし方の写し + 器の眺めから、観測に要る memory の状態を組む 1 点。
   受けた直後(after-start)も再起動後の拾い直し(adopt)も同じ形 — 行と器に無い欄
   (offset・seq・capture の可否)は始まりの値で、発明しない。turn-floor-ms = この手番の
   始まりの下限(送った時刻・拾い直しは行の createdAt)。期限(interrupt-escalation-seconds)は charter の値ちょうど
   (段 10 lane 10n・None = 宣言なし)。"
  (<- escalation-seconds (| int None) (escalation-seconds-of-charter plan.charter))
  (InFlightJob
    :job-key row.key
    :job-namespace row.namespace
    :job-id row.resource-id
    :subject (str (.get row.spec "subject" row.resource-id))
    :session-id view.session-id
    :agent-type view.agent-type
    :node node-name
    :profile plan.profile
    :model plan.model
    :started-ms started-ms
    :turn-floor-ms turn-floor-ms
    :start-offset start-offset
    :transcript-offset start-offset
    :delta-seq 0
    :lease-id (if (is lease None) None lease.lease-id)
    :lease-kind (if (is lease None) None lease.kind)
    :lease-account (if (is lease None) None plan.account)
    :lease-hold-ms (if (is lease None) None lease.hold-expires-at-ms)
    :capturing False
    :stream-gone False
    :last-frame-ms 0
    :last-probe-ms 0
    :pending-conditions pending
    :interrupt-escalation-seconds escalation-seconds))


(defk turn-record-spec-of [job]
  {:pre [(: job InFlightJob)]
   :post [(: % dict)]}
  "契約 turn-record の spec(conversationId・agentJobId・node・profile・model・sessionId)。sessionId = この手番を
   走らせた session(段 8q — Messaging が次の手番の affinity.predecessor に名指す綴り。書かないと会話の前の
   session が名指されず、温かい session が片付いた次の手番は文脈なしで起きる)。"
  {"conversationId" job.subject
   "agentJobId" job.job-id
   "node" job.node
   "profile" job.profile
   "model" job.model
   "sessionId" job.session-id})


(defk entries-of-status [status]
  {:pre [(: status dict)]
   :post [(: % tuple)]}
  "行の status が持つ entries(無ければ空・object でない要素は落とす — 発明しない)。"
  (setv entries (.get status "entries"))
  (if (isinstance entries list)
      (tuple (lfor entry entries :if (isinstance entry dict) entry))
      #()))


(defk next-seq-after [entries floor]
  {:pre [(: entries tuple) (: floor int)]
   :post [(: % int)]}
  "行の entries の seq の次(既に在る seq と衝突しない採番の下限)と floor の大きい方。
   拾い直し(再起動)の job は seq 0 から数え直すので、行の seq を越えた所から続ける。"
  (setv top -1)
  (for [entry entries]
    (setv seq (.get entry "seq"))
    (when (and (isinstance seq int) (> seq top))
      (setv top seq)))
  (max floor (+ top 1)))


(defk renumbered-entries [entries floor]
  {:pre [(: entries tuple) (: floor int)]
   :post [(: % tuple)]}
  "新しい見出し(TurnEntryHeadline の列)の seq が行の採番(floor = 行の次の seq)より小さければ、floor から順に
   振り直す(拾い直した job は 0 から数え直すので行の seq と衝突する)。衝突しなければそのまま。"
  (when (not entries)
    (return entries))
  (setv first (get entries 0))
  (when (not (isinstance first TurnEntryHeadline))
    (raise (TypeError f"renumbered-entries: entries must be TurnEntryHeadline, got {(. (type first) __name__)}")))
  (when (>= first.seq floor)
    (return entries))
  (setv out [])
  (setv seq floor)
  (for [entry entries]
    (.append out (replace entry :seq seq))
    (setv seq (+ seq 1)))
  (tuple out))


(defk entry-bytes [entry]
  {:pre [(: entry dict)]
   :post [(: % int)]}
  "entry 1 つの JSON(compact・UTF-8)の byte(行の上限の物差し — 契約 conventions.turnRecordEntries)。"
  (len (.encode (json.dumps entry :ensure-ascii False :separators #("," ":")) "utf-8")))


(defk drop-marker [dropped seq at]
  {:pre [(: dropped int) (: seq int) (: at int)]
   :post [(: % dict)]}
  "行の上限で古い見出しを落とした印(kind system・truncated・dropped — 本文を持たないので text も bytes / sha256 も
   無い)の JSON — 落とした最古の seq と最新の at を持ち、列の先頭に立つ。写しは entry-json-of の 1 点。"
  (<- marker dict (entry-json-of (TurnEntryDropMarker :seq seq :at at :dropped dropped)))
  marker)


(defk is-drop-marker [entry]
  {:pre [(: entry dict)]
   :post [(: % bool)]}
  "行の上限の印(drop-marker)か(kind system で dropped を持つ)。"
  (and (= (.get entry "kind") ENTRY-KIND-SYSTEM) (isinstance (.get entry "dropped") int)))


(defk marker-bytes [dropped seq at]
  {:pre [(: dropped int) (: seq int) (: at int)]
   :post [(: % int)]}
  "印(drop-marker)の byte(dropped の桁で伸びるので都度数える)。"
  (<- marker dict (drop-marker (max dropped 1) seq at))
  (<- size int (entry-bytes marker))
  size)


(defk entries-within-budget [entries budget]
  {:pre [(: entries tuple) (: budget int)]
   :post [(: % tuple)]}
  "entries を行の上限(byte)に収める: 超えたら**古い出来事から**落とし、先頭に印(drop-marker)を
   残す。既に印が先頭に在れば(前の拍で落としている)その dropped・最古の seq・最新の at を
   引き継いで数える。収まっていればそのまま(印も足さない)。新しい出来事は必ず残る(印 + 新しい
   側だけが列)。物差し = 各 entry の compact JSON の UTF-8 byte + 列の括弧と区切り。"
  (setv rows (list entries))
  (setv dropped 0)
  (setv oldest-seq 0)
  (setv newest-at 0)
  (setv has-marker False)
  (when rows
    (<- flagged bool (is-drop-marker (get rows 0)))
    (setv has-marker flagged))
  (when has-marker
    (setv marker (get rows 0))
    (setv dropped (int (get marker "dropped")))
    (setv oldest-seq (int (.get marker "seq" 0)))
    (setv newest-at (int (.get marker "at" 0)))
    (setv rows (cut rows 1 None)))
  (setv sizes [])
  (for [row rows]
    (<- size int (entry-bytes row))
    (.append sizes size))
  (setv total (+ 2 (sum sizes) (len sizes)))
  (when (and (= dropped 0) (<= total budget))
    (return entries))
  (setv index 0)
  (while True
    (<- marker-size int (marker-bytes dropped oldest-seq newest-at))
    (when (or (not rows) (<= (+ total marker-size 1) budget))
      (break))
    (setv victim (get rows 0))
    (setv rows (cut rows 1 None))
    (setv total (- total (get sizes index) 1))
    (setv index (+ index 1))
    (when (= dropped 0)
      (setv oldest-seq (int (.get victim "seq" 0))))
    (setv dropped (+ dropped 1))
    (setv newest-at (max newest-at (int (.get victim "at" 0)))))
  (when (= dropped 0)
    (return entries))
  (<- marker dict (drop-marker dropped oldest-seq newest-at))
  (tuple (+ [marker] rows)))


(defk turn-record-appended-status [status new-entries]
  {:pre [(: status dict) (: new-entries tuple)]
   :post [(: % dict)]}
  "行の status に見出しを追記した status(post-image): entries = 行の entries + new-entries(TurnEntryHeadline の列 —
   JSON への写しは entry-json-of の 1 点・本文の欄は型に無い)を行の上限(TURN-RECORD-ENTRIES-BYTE-BUDGET)に収めたもの。
   他の欄は写す。"
  (setv next (dict status))
  (<- existing tuple (entries-of-status status))
  (setv rendered [])
  (for [entry new-entries]
    (when (not (isinstance entry TurnEntryHeadline))
      (raise (TypeError f"turn-record entries must be TurnEntryHeadline, got {(. (type entry) __name__)}")))
    (<- item dict (entry-json-of entry))
    (.append rendered item))
  (<- bounded tuple (entries-within-budget (+ existing (tuple rendered)) TURN-RECORD-ENTRIES-BYTE-BUDGET))
  (setv (get next "entries") (list bounded))
  next)


(defk turn-record-ended-status [status usage entries]
  {:pre [(: status dict) (: usage (| dict None)) (: entries tuple)]
   :post [(: % dict)]}
  "手番の終わりの turn-record の status: 残りの見出し(entries — TurnEntryHeadline の列)を行の entries に**追記**
   した上で state = ended・usage(素材があれば)。行の entries は落とさない(手番の間に追記した見出しが正本)。"
  (<- next dict (turn-record-appended-status status entries))
  (setv (get next "state") TURN-RECORD-ENDED)
  (when (is-not usage None)
    (setv (get next "usage") usage))
  next)


(defk turn-record-sweep-verdict [record pair node-name live-nodes in-flight-ids]
  {:pre [(: record AcpRow) (: pair (| AcpRow None)) (: node-name str)
         (: live-nodes frozenset) (: in-flight-ids set)]
   :post [(: % str)]}
  "走っている turn-record を閉じるか(段 12・agora-redesign #537 便 1・閉語彙 TurnRecordSweepVerdict)。

   既知の形 = k8s の controller の reconcile: 手番の終わりの**出来事**(1 度の書き)に記録の終わりを預けず、
   行の**終状態**を読んで取り残しを level-triggered に閉じる。根 = end-turn-record の書きが断られた拍に log 1 行で
   終わり、settle-record はその戻りを見ずに agent-job を Ended にして memory から外していたので、記録は永久に
   running のまま残った(会話は Dormant=False{turn-record-running}・実弾 38 本)。

   end = 4 つが揃った時ちょうど:
     1. 記録が running(ended の行は触らない — 冪等)
     2. その手番が自分の memory に無い(走らせている手番・持ち越しの Ended〔#402〕を切らない)
     3. 対の agent-job が終端(Ended / Withdrawn)か、行ごと無い
        (Pending / Bound / Running は走っている手番か置き直し待ち〔#519〕— 切ると次の試みの記録を失う)
     4. 記録の名乗る node が自分か、生きている node の集合に無い
        (pool の pod は再配備のたびに名前が変わるので『自分の行だけ』では死んだ pod の記録を誰も閉じない。
         生きている別の機体の手番はその機体に任せる — 書き手を 1 つに保つ)
   どれか 1 つでも欠ければ skip。判断はこの 1 点で、腕(agentd.sweep-turn-records)は語で分岐する以上のことをしない。"
  (<- status dict (status-object-of record))
  (when (!= (.get status "state") TURN-RECORD-RUNNING)
    (return TURN-RECORD-SWEEP-SKIP))
  (setv job-id (.get record.spec "agentJobId"))
  (when (in job-id in-flight-ids)
    (return TURN-RECORD-SWEEP-SKIP))
  (when (isinstance pair AcpRow)
    (<- pair-status dict (status-object-of pair))
    (when (not-in (.get pair-status "phase") #(PHASE-ENDED PHASE-WITHDRAWN))
      (return TURN-RECORD-SWEEP-SKIP)))
  (setv node (.get record.spec "node"))
  (if (or (= node node-name) (not-in node live-nodes))
      TURN-RECORD-SWEEP-END
      TURN-RECORD-SWEEP-SKIP))


;; ---------------------------------------------------------------------------
;; 会話の記録の service への二重書き(段 9f lane 9f-2・agora-redesign #59・設計 §2.4)
;; ---------------------------------------------------------------------------
;; 既知の形 = runner の transactional outbox(spool に書いてから送る・受理で消す・冪等の再送)。判断はここ: batch の
;; 組み立て・stream id・spool の鍵・結末の語・消し込みの可否・再送の周期・追いつきの差・拾い直しの番。採番(producerSeq)は
;; 本文を組む時の seq = InFlightJob の delta-seq の 1 点で、ACP の見出しの seq と同じ値。

(defk record-stream-id-of [job-id attempt]
  {:pre [(: job-id str) (: attempt int)]
   :post [(: % str)]}
  "手番の stream の id(契約 streamRef.id・path の {streamId}): agent-job の id に拾い直しの番を含める(`<jobId>#a<attempt>`)。"
  f"{job-id}#a{attempt}")


(defk record-stream-of [job]
  {:pre [(: job InFlightJob)]
   :post [(: % RecordStream)]}
  "走っている手番の本文の stream(kind turn・順の物差し = 手番の始まりの時刻・node・profile・拾い直しの番)。"
  (<- stream-id str (record-stream-id-of job.job-id job.record-attempt))
  (RecordStream :kind RECORD-STREAM-TURN :stream-id stream-id :started-at-ms job.started-ms
                :node job.node :profile job.profile :attempt job.record-attempt))


(defk record-ref-of [conversation-id stream-id]
  {:pre [(: conversation-id str) (: stream-id str)]
   :post [(: % str)]}
  "turn-record の status.recordRef の綴り(設計 §2.2): `record:<cid>/<streamId>` — 本文の在処の参照(claim check の札)。"
  f"{RECORD-REF-PREFIX}{conversation-id}/{stream-id}")


(defk record-stream-job-of [stream-id]
  {:pre [(: stream-id str)]
   :post [(: % str)]}
  "stream id(record-stream-id-of の `<jobId>#a<attempt>`)→ agent-job の id(逆写像 — 拾い直しの番は最後の `#a` の後)。
   受理の答えから turn-record の行(鍵 = job id)を引くのに使う。"
  (setv parts (.rpartition stream-id "#a"))
  (if (get parts 1) (get parts 0) stream-id))


(defk turn-record-recorded-status [status record-ref highest]
  {:pre [(: status dict) (: record-ref str) (: highest int)]
   :post [(: % (| dict None))]}
  "service が本文を受理した答え(highestProducerSeq)を turn-record の行へ写す status(post-image・設計 §2.2):
   recordedSeq = 受理済みの本文の最大 producerSeq(後ろへ戻さない — 古い stream の遅れた再送は小さい値を持つ)・
   recordRef = 本文の在処。進まない答え(既に同じか大きい値)は None(書かない)。他の欄は写す。"
  (setv current (.get status "recordedSeq"))
  (when (and (isinstance current int) (not (isinstance current bool)) (>= current highest))
    (return None))
  (setv next (dict status))
  (setv (get next "recordRef") record-ref)
  (setv (get next "recordedSeq") highest)
  next)


(defk record-spool-key-of [stream-id first-seq last-seq]
  {:pre [(: stream-id str) (: first-seq int) (: last-seq int)]
   :post [(: % str)]}
  "spool の file の鍵(1 batch 1 file・辞書順 = 送る順): stream id の file に使えない字を `_` に、seq は 0 詰め 12 桁。"
  (setv safe (re.sub r"[^A-Za-z0-9._-]" "_" stream-id))
  (+ safe "." (.zfill (str first-seq) 12) "-" (.zfill (str last-seq) 12)))


(defk record-batches-of [job bodies]
  {:pre [(: job InFlightJob) (: bodies tuple)]
   :post [(: % tuple)]}
  "拍で読んだ本文の列 → batch の列(契約 limits.batchMaxEvents ごとに分ける)。会話 = job の subject・stream =
   record-stream-of・events = 本文そのまま(producerSeq は組んだ時の seq・切らない)。同じ拍の再送は同じ鍵と本文。"
  (when (not bodies)
    (return #()))
  (<- stream RecordStream (record-stream-of job))
  (setv batches [])
  (setv start 0)
  (while (< start (len bodies))
    (setv chunk (tuple (cut bodies start (+ start RECORD-BATCH-MAX-EVENTS))))
    (<- key str (record-spool-key-of stream.stream-id (get (get chunk 0) "producerSeq")
                                     (get (get chunk -1) "producerSeq")))
    (.append batches (RecordBatch :spool-key key :conversation-id job.subject :stream stream :events chunk))
    (setv start (+ start RECORD-BATCH-MAX-EVENTS)))
  (tuple batches))


(defk record-append-word-of [outcome]
  {:pre [(: outcome (| RecordAppended RecordConflicted RecordUnsent))]
   :post [(: % str)]}
  "追記の結末 → 語(effects.RecordAppendWord の閉語彙)— 計器の outcome と spool の扱いを**この 1 点**で決める(段 9f lane 9f-8):
   ok = 受理(消す)/ conflict = 409(同じ鍵で違う本文 — 再送しても積めない・消して赤の計器)/ given-up = この batch だけの
   決まった断り(RECORD-BATCH-REFUSAL-STATUSES = 400 malformed・422 unstorable — 撃ち直しても通らない〔法 7〕: 隔離して理由を
   名乗り、後ろの batch へ進む — 断られた 1 つの batch で spool の先頭を塞がない)/ error = 系の側の送れなさ(届かない・5xx・
   札 401 / 403・窓 429 — 残して backoff・後ろの batch も同じ理由で送れないのでこの拍の残りも撃たない)。
   札を given-up にしないのは、断りが batch ではなく機体の設定の性質だから(設定を直せば残した batch が自動で送れる —
   turn-record の create の record-refusal-deterministic とは扱う物の単位が違う)。"
  (cond
    (isinstance outcome RecordAppended) RECORD-APPEND-OK
    (isinstance outcome RecordConflicted) RECORD-APPEND-CONFLICT
    (in outcome.status RECORD-BATCH-REFUSAL-STATUSES) RECORD-APPEND-GIVEN-UP
    True RECORD-APPEND-ERROR))


(defk record-unavailable-noted [job reason]
  {:pre [(: job InFlightJob) (: reason str)]
   :post [(: % InFlightJob)]}
  "手番の記録が欠けた理由を condition RecordUnavailable として job の pending-conditions に足す(Ended の書きに乗る — 同じ型を
   二度足さない・最初の理由を残す)。turn-record の行を作れない(record-create-applied)と、本文の batch を service が決まった
   断りで断った(record-given-up-noted)の 2 つの口が同じこの点を通る。"
  (when (any (gfor c job.pending-conditions (= (.get c "type") CONDITION-RECORD-UNAVAILABLE)))
    (return job))
  (<- condition dict (condition-of CONDITION-RECORD-UNAVAILABLE reason))
  (replace job :pending-conditions (+ job.pending-conditions #(condition))))


(defk record-given-up-noted [state stream-id reason]
  {:pre [(: state AgentdState) (: stream-id str) (: reason str)]
   :post [(: % AgentdState)]}
  "決まった断りで隔離した本文の batch の理由を、その stream の手番が memory に居れば condition RecordUnavailable に写す
   (段 9f lane 9f-8 — record-unavailable-noted)。手番が既に memory に無ければ state のまま(理由は log と隔離の置き場の
   理由の file が名乗る)。"
  (for [job state.jobs]
    (<- job-stream str (record-stream-id-of job.job-id job.record-attempt))
    (when (= job-stream stream-id)
      (<- noted InFlightJob (record-unavailable-noted job reason))
      (<- next AgentdState (with-job state noted))
      (return next)))
  state)


(defk record-flush-due [state now-ms settings]
  {:pre [(: state AgentdState) (: now-ms int) (: settings AgentdSettings)]
   :post [(: % bool)]}
  "spool を読んで送る拍か: 送れている間(backoff なし)は毎拍 — 出来事を読んだ拍の終わりに送る。送れなかった後は
   record_retry_seconds の周期(届かない service へ拍ごとに撃って loop を塞がない)。"
  (<- period-passed bool (due state.record-backoff-ms now-ms settings.record-retry-seconds))
  period-passed)


(defk record-refusal-deterministic [status]
  {:pre [(: status int)]
   :post [(: % bool)]}
  "turn-record の create の断りが決定論的か(段 9p): 4xx(408 / 429 を除く)は契約・札・区画の不備で、撃ち直しても
   同じ答え(法 7: 決定論的な失敗は撃ち直さない)。0(到達不能)・5xx・408・429 は頭が答えていない — 撃ち直す。"
  (and (>= status 400) (< status 500) (not-in status #{408 429})))


(defk record-create-verdict [outcome started-ms now-ms deadline-seconds final]
  {:pre [(: outcome (| Written Conflict Refused)) (: started-ms int) (: now-ms int)
         (: deadline-seconds (| int float)) (: final bool)]
   :post [(: % str)]}
  "turn-record の create の結末 → 腕の状態(閉語彙 RecordCreateState・段 9p・agora-redesign #76)。
   Written = 作れた / Conflict = 既に在る(同じ鍵は冪等 — 拾い直し)→ created。Refused は 2 種:
   決定論的(record-refusal-deterministic)→ given-up / 頭が答えない → 期限(手番の始まり started-ms から
   deadline-seconds)の内なら pending・越えたら given-up。判断はこの 1 点(呼び手は結果の語で分岐しない)。

   final(agora-redesign #537 H3)= この create が手番の**最後の 1 度**(終わりの拍の force)か。真なら期限の内でも
   pending にしない — 次の拍はもう来ないので、pending のままだと条件が 1 つも乗らず『Ended・turn-record なし・
   理由なし』の行になり、郵便の側(ACP Messaging の turnlessOf)は『手番が 1 度も始まらなかった』と読んで
   agent-job-ended-without-a-turn で failed にする(実弾 2026-09-18: 20 秒の手番が頭の答えない拍に当たった)。"
  (if (or (isinstance outcome Written) (isinstance outcome Conflict))
      RECORD-CREATE-CREATED
      (do
        (<- deterministic bool (record-refusal-deterministic outcome.status))
        (cond
          deterministic RECORD-CREATE-GIVEN-UP
          (>= (- now-ms started-ms) (* 1000 deadline-seconds)) RECORD-CREATE-GIVEN-UP
          final RECORD-CREATE-GIVEN-UP
          True RECORD-CREATE-PENDING))))


(defk record-create-applied [job outcome now-ms deadline-seconds final]
  {:pre [(: job InFlightJob) (: outcome (| Written Conflict Refused)) (: now-ms int)
         (: deadline-seconds (| int float)) (: final bool)]
   :post [(: % InFlightJob)]}
  "create の結末を job に写す(段 9p): 腕の状態(record-create-verdict)・最後に撃った拍・最後の断りの文。
   given-up になった拍は condition RecordUnavailable(理由 = 最後の断りと経過)を pending-conditions に足す
   (Ended の書きに乗る — 同じ型を二度足さない)。created は行の image を持たない(次の追記が鍵から読む)。
   final = 手番の終わりの最後の 1 度(#537 H3 — 期限の内でも pending にしない)。"
  (<- verdict str (record-create-verdict outcome job.started-ms now-ms deadline-seconds final))
  (setv refusal (if (isinstance outcome Refused) f"{outcome.status}: {outcome.error}" ""))
  (setv next (replace job :record-create verdict :record-create-last-ms now-ms :record-create-refusal refusal))
  (when (= verdict RECORD-CREATE-GIVEN-UP)
    (setv elapsed-s (// (- now-ms job.started-ms) 1000))
    (<- noted InFlightJob
        (record-unavailable-noted next f"turn-record could not be created ({refusal}) after {elapsed-s} s; events of this turn were not recorded"))
    (setv next noted))
  next)


(defk record-create-due [job now-ms settings]
  {:pre [(: job InFlightJob) (: now-ms int) (: settings AgentdSettings)]
   :post [(: % bool)]}
  "この拍に turn-record を作り直すか(段 9p): 腕が pending で、最後に撃ってから record_retry_seconds(spool の再送と
   同じ弁)が過ぎた時。created / given-up は撃たない。"
  (if (!= job.record-create RECORD-CREATE-PENDING)
      False
      (do
        (<- period-passed bool (due job.record-create-last-ms now-ms settings.record-retry-seconds))
        period-passed)))


(defk record-lag-of [jobs stream-id highest]
  {:pre [(: jobs tuple) (: stream-id str) (: highest int)]
   :post [(: % (| int None))]}
  "見出しと本文の追いつきの差(計器 agentd_record_lag_seq): stream が一致する走っている手番の ACP の見出しの最大 seq
   (最後に書けた turn-record の image の entries)− service の答えの highestProducerSeq。本文が先(0 未満)は 0。
   手番が memory に無い・見出しがまだ無い = None(名乗らない)。"
  (for [job jobs]
    (<- job-stream str (record-stream-id-of job.job-id job.record-attempt))
    (when (and (= job-stream stream-id) (is-not job.record None))
      (<- record-status dict (status-object-of job.record))
      (<- existing tuple (entries-of-status record-status))
      (<- head int (next-seq-after existing 0))
      (when (> head 0)
        (return (max 0 (- (- head 1) highest))))))
  None)


(defk recovered-record-of [record]
  {:pre [(: record (| AcpRow None))]
   :post [(: % tuple)]}
  "拾い直した手番(recover-job)の本文の stream の番と採番の下限: #(attempt floor)。行が無ければ #(1 0)(最初の受けと
   同じ)、在れば attempt = 行の generation + 1(最初の受け = 1 より大きく、行が進むごとに単調 — ACP に新しい欄を
   書かない)・floor = 行の entries の seq の次(拾い直しの採番を見出しの seq の続きから — service の producerSeq と
   ACP の見出しの seq を同じ値に保つ)。"
  (when (is record None)
    (return #(1 0)))
  (<- record-status dict (status-object-of record))
  (<- existing tuple (entries-of-status record-status))
  (<- floor int (next-seq-after existing 0))
  #((+ record.generation 1) floor))


;; ---------------------------------------------------------------------------
;; transcript → TurnDelta / entries
;; ---------------------------------------------------------------------------

(defk transcript-path-of [view canonical-work-dir]
  {:pre [(: view SessionView) (: canonical-work-dir str)]
   :post [(: % (| str None))]}
  "器の眺めから transcript の file を引く。claude =
   <CLAUDE_CONFIG_DIR>/projects/<mangled canonical work_dir>/<sid>.jsonl(mangle = 非英数字を
   '-' に — impls/claude_code.hy の会話 identity の家と同じ物理)、codex = conversation の
   rollout_path。材料が欠ければ None(実況は無し・記録は空 — 発明しない)。"
  (setv conv (or view.conversation {}))
  (setv identity (or view.effective-identity {}))
  (cond
    (= view.agent-type "codex")
    (do (setv rollout (.get conv "rollout_path"))
        (if (isinstance rollout str) rollout None))
    (= view.agent-type "claude")
    (do (setv config-dir (.get identity "CLAUDE_CONFIG_DIR"))
        (setv sid (.get conv "session_id"))
        (if (and (isinstance config-dir str) (isinstance sid str) canonical-work-dir)
            (do (setv mangled (re.sub "[^A-Za-z0-9]" "-" canonical-work-dir))
                f"{config-dir}/projects/{mangled}/{sid}.jsonl")
            None))
    True None))


(defk stream-source-of [view canonical-work-dir]
  {:pre [(: view SessionView) (: canonical-work-dir str)]
   :post [(: % tuple)]}
  "実況と記録の材料の在処(閉語彙 effects.StreamSource): headless の器は events file
   (backend_ref.events_path — host が stdout の行を追記する正本)、tui の器は transcript。
   戻り = #(source path)。材料が欠ければ #(None None)(発明しない)。"
  (if (= view.backend-kind BACKEND-HEADLESS)
      (do
        (setv ref (or view.backend-ref {}))
        (setv path (.get ref "events_path"))
        (if (isinstance path str) #(STREAM-SOURCE-EVENTS path) #(None None)))
      (do
        (<- path (| str None) (transcript-path-of view canonical-work-dir))
        (if (is path None) #(None None) #(STREAM-SOURCE-TRANSCRIPT path)))))


(defk usage-of-claude-message [usage model]
  {:pre [(: usage dict) (: model (| str None))]
   :post [(: % dict)]}
  "claude の message.usage → 契約 usage(token の 4 欄 + 内訳)。欄の欠落は 0 を発明せず
   落とす … ただし必須 4 欄は素材が無ければ 0(素材の無い message は呼び手が渡さない)。
   model を名乗るのは **message 1 つの usage**(実況の usage frame — 契約 turn-delta.json の frame の usage が model を
   宣言している)ちょうどで、手番の和(add-usage — 行の status.usage)は運ばない。"
  (setv cache (or (.get usage "cache_creation") {}))
  (setv #^ JSONObject out {"input" (int (.get usage "input_tokens" 0))
                           "output" (int (.get usage "output_tokens" 0))
                           "cacheWrite" (int (.get usage "cache_creation_input_tokens" 0))
                           "cacheRead" (int (.get usage "cache_read_input_tokens" 0))})
  (when (isinstance cache dict)
    (when (in "ephemeral_5m_input_tokens" cache)
      (setv (get out "cacheWrite5m") (int (get cache "ephemeral_5m_input_tokens"))))
    (when (in "ephemeral_1h_input_tokens" cache)
      (setv (get out "cacheWrite1h") (int (get cache "ephemeral_1h_input_tokens")))))
  (when (isinstance model str)
    (setv (get out "model") model))
  out)


(defk add-usage [total part]
  {:pre [(: total (| dict None)) (: part dict)]
   :post [(: % dict)]}
  "usage の和 = 手番の消費(turn-record の status.usage へ行く値)。欄は token の 6 つちょうどで、model は運ばない —
   契約 agora-kinds.json の turn-record.status.usage は additionalProperties: false で model を宣言していない(手番の model の
   正本は行の spec.model・message ごとの model は実況の usage frame・最後に見た綴りは DeltaBatch.model)。和が model を
   運んでいた間、ACP が書きを登録した schema に照らし始めた拍(#493)から手番の終わりの書きが 400 で断られ、終わった手番の
   行が running のまま残った(agora-redesign #526・実弾 2026-09-17)。"
  (setv out (if (is total None) {} (dict total)))
  (for [key ["input" "output" "cacheWrite" "cacheRead" "cacheWrite5m" "cacheWrite1h"]]
    (when (in key part)
      (setv (get out key) (+ (int (.get out key 0)) (int (get part key))))))
  out)


(defk parse-json-lines [text]
  {:pre [(: text str)]
   :post [(: % tuple)]}
  "JSON 行の列 → dict の列(壊れた行・object でない行は飛ばす)。"
  (setv out [])
  (for [line (.splitlines text)]
    (setv stripped (.strip line))
    (when stripped
      (try
        (setv value (json.loads stripped))
        (except [ValueError]
          (setv value None)))
      (when (isinstance value dict)
        (.append out value))))
  (tuple out))


(defk summary-of [value limit]
  {:pre [(: value (| dict list str int float bool None)) (: limit int)]
   :post [(: % str)]}
  "表示用の要約(JSON の compact な綴りを limit 字で切る)。"
  (setv text (if (isinstance value str) value (json.dumps value :ensure-ascii False)))
  (if (> (len text) limit) (cut text 0 limit) text))


(defk record-body-of [body]
  {:pre [(: body dict)]
   :post [(: % dict)]}
  "本文(契約 eventIn)の同一性の材料 = text / summary / input / output の在る欄だけ(None は無いのと同じ)— service の
   judgment.body-of と同じ形。"
  (setv out {})
  (for [name ["text" "summary" "input" "output"]]
    (setv field (.get body name))
    (when (is-not field None)
      (setv (get out name) field)))
  out)


(defk record-body-bytes-of [body]
  {:pre [(: body dict)]
   :post [(: % bytes)]}
  "本文の同一性の綴り = 材料(record-body-of)の compact JSON(鍵は sort・ASCII に逃がさない)の UTF-8 — service の
   judgment.body-bytes-of と同じ 1 点(bytes と sha256 はこの綴りから)。"
  (<- material dict (record-body-of body))
  (.encode (json.dumps material :sort-keys True :separators #("," ":") :ensure-ascii False) "utf-8"))


(defk text-body [seq at text model]
  {:pre [(: seq int) (: at int) (: text str) (: model (| str None))]
   :post [(: % dict)]}
  "assistant の本文の 1 block → 本文(契約 record-service eventIn・kind text・切らない — 段 9f lane 9f-2)。"
  (setv body {"producerSeq" seq "at" at "kind" ENTRY-KIND-TEXT "text" text})
  (when (isinstance model str)
    (setv (get body "model") model))
  body)


(defk tool-use-body [seq at tool-id name input]
  {:pre [(: seq int) (: at int) (: tool-id str) (: name str)
         (: input (| dict list str int float bool None))]
   :post [(: % dict)]}
  "道具の呼び出し → 本文(kind tool_use・toolName・toolUseId・input = 入力そのもの)。"
  (setv body {"producerSeq" seq "at" at "kind" ENTRY-KIND-TOOL-USE "toolName" name "input" input})
  (when tool-id
    (setv (get body "toolUseId") tool-id))
  body)


(defk tool-result-body [seq at tool-id output is-error]
  {:pre [(: seq int) (: at int) (: tool-id str)
         (: output (| dict list str int float bool None)) (: is-error bool)]
   :post [(: % dict)]}
  "道具の結果 → 本文(kind tool_result・toolUseId・output = 出力そのもの・isError は誤りの時だけ名乗る)。"
  (setv body {"producerSeq" seq "at" at "kind" ENTRY-KIND-TOOL-RESULT "output" output})
  (when tool-id
    (setv (get body "toolUseId") tool-id))
  (when is-error
    (setv (get body "isError") True))
  body)


(defk note-body [seq at kind text]
  {:pre [(: seq int) (: at int) (: kind str) (: text str)]
   :post [(: % dict)]}
  "器の出来事(kind system)と手番の誤り(kind error)→ 本文(text)。"
  {"producerSeq" seq "at" at "kind" kind "text" text})


(defk headline-of-body [body]
  {:pre [(: body dict)]
   :post [(: % TurnEntryHeadline)]}
  "本文(契約 record-service eventIn の形 — 会話の記録の service へ切らずに運ぶ出来事)→ ACP の turn-record の見出し
   (TurnEntryHeadline — 設計 §2.2 の閉じた欄)。**見出しを導く点はここ 1 つ**: seq = 本文の producerSeq(採番は 1 点)・
   at・kind・toolName / toolUseId(道具)・isError・bytes / sha256 = 本文の同一性(record-body-bytes-of — service が
   冪等の判断に使う値と同じ)。本文の欄(text / summary / input / output / model)は型に無い — 写さない・切らない。"
  (<- material bytes (record-body-bytes-of body))
  (setv tool-name (.get body "toolName"))
  (setv tool-use-id (.get body "toolUseId"))
  (TurnEntryHeadline :seq (get body "producerSeq")
                     :at (get body "at")
                     :kind (get body "kind")
                     :bytes (len material)
                     :sha256 (.hexdigest (hashlib.sha256 material))
                     :tool-name (if (isinstance tool-name str) tool-name None)
                     :tool-use-id (if (isinstance tool-use-id str) tool-use-id None)
                     :is-error (is (.get body "isError") True)))


(defk entry-json-of [entry]
  {:pre [(: entry (| TurnEntryHeadline TurnEntryDropMarker))]
   :post [(: % dict)]}
  "見出し(か行の上限の印)→ turn-record の status.entries の item の JSON(契約 agora-kinds.json §2.2 の閉じた欄)。
   写す点はここ 1 つ: 見出し = {seq, at, kind, toolName?, toolUseId?, bytes, sha256, isError?}・印 = {seq, at, kind system,
   truncated, dropped}。無い欄は書かない(null を発明しない)。本文の欄はどちらの型にも無い。"
  (if (isinstance entry TurnEntryDropMarker)
      {"seq" entry.seq "at" entry.at "kind" ENTRY-KIND-SYSTEM "truncated" True "dropped" entry.dropped}
      (do
        (setv item {"seq" entry.seq "at" entry.at "kind" entry.kind})
        (when (is-not entry.tool-name None)
          (setv (get item "toolName") entry.tool-name))
        (when (is-not entry.tool-use-id None)
          (setv (get item "toolUseId") entry.tool-use-id))
        (setv (get item "bytes") entry.bytes)
        (setv (get item "sha256") entry.sha256)
        (when entry.is-error
          (setv (get item "isError") True))
        item)))


(defk entries-of-bodies [bodies]
  {:pre [(: bodies tuple)]
   :post [(: % tuple)]}
  "本文の列 → turn-record の見出しの列(TurnEntryHeadline・順と seq はそのまま・導く点は headline-of-body の 1 つ)。"
  (setv out [])
  (for [body bodies]
    (<- headline TurnEntryHeadline (headline-of-body body))
    (.append out headline))
  (tuple out))


(defk claude-system-note [record]
  {:pre [(: record dict)]
   :post [(: % (| str None))]}
  "claude の stream-json の system の行 → 人の読む 1 行(None = 記録しない出来事)。
   init = session の始まり(model・permissionMode・cwd・tools の数)/ api_retry = API の再試行 /
   hook_response = hook の失敗(exit ≠ 0 か outcome ≠ success)だけ。status / hook_started /
   thinking_tokens / rate_limit は実況の雑音なので残さない。"
  (setv subtype (.get record "subtype"))
  (setv model (.get record "model"))
  (setv permission (.get record "permissionMode"))
  (setv cwd (.get record "cwd"))
  (setv tools (.get record "tools"))
  (setv attempt (.get record "attempt" "?"))
  (setv max-retries (.get record "max_retries" "?"))
  (setv error-status (.get record "error_status"))
  (setv error (.get record "error"))
  (setv hook-name (.get record "hook_name" "?"))
  (setv exit-code (.get record "exit_code"))
  (setv outcome (.get record "outcome"))
  (setv stderr (.get record "stderr"))
  (cond
    (= subtype "init")
    (+ "session started"
       (if (isinstance model str) f": model {model}" "")
       (if (isinstance permission str) f" · permission {permission}" "")
       (if (isinstance cwd str) f" · cwd {cwd}" "")
       (if (isinstance tools list) f" · tools {(len tools)}" ""))
    (= subtype "api_retry")
    (+ f"API retry {attempt}/{max-retries}"
       (if (is-not error-status None) f": {error-status}" "")
       (if (isinstance error str) f" {error}" ""))
    (= subtype "hook_response")
    (if (or (and (isinstance exit-code int) (!= exit-code 0))
            (and (isinstance outcome str) (!= outcome "success")))
        (+ f"hook {hook-name} failed"
           (if (isinstance exit-code int) f" (exit {exit-code})" "")
           (if (and (isinstance stderr str) (.strip stderr)) f": {(.strip stderr)}" ""))
        None)
    True None))


(defk claude-result-error [record]
  {:pre [(: record dict)]
   :post [(: % (| str None))]}
  "claude の stream-json の result の行 → 誤りの理由(None = 成功 — 記録しない)。"
  (setv is-error (= (.get record "is_error") True))
  (setv subtype (.get record "subtype"))
  (if (or is-error (and (isinstance subtype str) (!= subtype "success")))
      (do (setv body (.get record "result"))
          (setv errors (.get record "errors"))
          (cond
            (and (isinstance body str) (.strip body)) body
            (and (isinstance errors list) errors) (.join "\n" (lfor item errors (str item)))
            (isinstance subtype str) subtype
            True "error"))
      None))


(defk delta-frame [job-id seq at kind payload]
  {:pre [(: job-id str) (: seq int) (: at int) (: kind str) (: payload dict)]
   :post [(: % dict)]}
  "契約 turn-delta.json の frame 1 つ。"
  {"agentJobId" job-id "seq" seq "at" at "kind" kind "payload" payload})


(defk clip-input [value root limit]
  {:pre [(: value (| dict list str int float bool None)) (: root str) (: limit int)]
   :post [(: % tuple)]}
  "実況に載せる入力 → #(載せる値 切った所の path の列)。入れ子の中の文字列を 1 つ limit 字で切り、切った所の path を
   root から . で繋いで名乗る(object は key・array は添字 — 例 input.edits.0.old_string)。契約 turn-delta.json の
   tool_use.input / clipped の規則で、agora の画面の糊が記録の行を切る規則(controllers/screen/runtime/protocol.hy の
   _clip-json)と同じ綴り。切るものが無ければ値をそのまま返す(写しを作らない)。"
  (setv paths []
        stack [#(value root)])
  (while stack
    (setv [item path] (.pop stack))
    (cond
      (isinstance item str)
      (when (> (len item) limit)
        (.append paths path))
      (isinstance item dict)
      (for [[name child] (.items item)]
        (.append stack #(child (+ path "." (str name)))))
      (isinstance item list)
      (for [[index child] (enumerate item)]
        (.append stack #(child (+ path "." (str index)))))))
  (when (not paths)
    (return #(value [])))
  (setv holder {root (copy.deepcopy value)}
        places [#(holder root)])
  (while places
    (setv [container key] (.pop places)
          item (get container key))
    (cond
      (isinstance item str)
      (when (> (len item) limit)
        (setv (get container key) (cut item 0 limit)))
      (isinstance item dict)
      (for [name (list item)]
        (.append places #(item name)))
      (isinstance item list)
      (for [index (range (len item))]
        (.append places #(item index)))))
  #((get holder root) (sorted paths)))


(defk tool-use-frame [job-id seq at tool-id name summary given]
  {:pre [(: job-id str) (: seq int) (: at int) (: tool-id str) (: name str) (: summary str)
         (: given (| dict list str int float bool None))]
   :post [(: % dict)]}
  "道具の呼び出しの実況の frame(契約 turn-delta.json の tool_use — 段 10 lane 10j・agora-redesign #87 の裁定 問 7 / 8)。
   入力が object の時だけ input を載せ(文字列は DELTA-INPUT-STRING-LIMIT 字で切り、切った所を clipped が名乗る)、
   encode した frame が DELTA-FRAME-MAX-BYTES を超える時は input を落として clipped を根の 1 語にする。object でない
   入力(codex の function_call.arguments = JSON の文字列)は名乗らない —— 面は summary で描く。"
  (setv payload {"toolUseId" tool-id "name" name "summary" summary})
  (when (isinstance given dict)
    (<- clip tuple (clip-input given DELTA-CLIPPED-INPUT-ROOT DELTA-INPUT-STRING-LIMIT))
    (setv [carried marks] clip)
    (setv (get payload "input") carried)
    (when marks
      (setv (get payload "clipped") (list marks))))
  (<- frame dict (delta-frame job-id seq at "tool_use" payload))
  (when (<= (len (.encode (json.dumps frame :ensure-ascii False) "utf-8")) DELTA-FRAME-MAX-BYTES)
    (return frame))
  (<- shrunk dict (delta-frame job-id seq at "tool_use"
                               {"toolUseId" tool-id "name" name "summary" summary
                                "clipped" [DELTA-CLIPPED-INPUT-ROOT]}))
  shrunk)


(defk tool-input-chunks [text limit]
  {:pre [(: text str) (: limit int)]
   :post [(: % tuple)]}
  "書きかけの引数の続き(1 回の読みの中で連結した partial_json)→ frame ごとの chunk の列。limit 字を超える連結は続きの
   chunk に分ける(字は落とさない — 読み手は総字数を chunk の長さの和で数える)。空の連結は空の chunk 1 つ(道具の開始の
   拍に走行器が送る空の差分 — 名前だけのカードが開始の拍に出る)。"
  (if (<= (len text) limit)
      #(text)
      (tuple (lfor start (range 0 (len text) limit) (cut text start (+ start limit))))))


(defk claude-deltas-of [records job-id seq-start at streamed open-blocks]
  {:pre [(: records tuple) (: job-id str) (: seq-start int) (: at int) (: streamed bool) (: open-blocks tuple)]
   :post [(: % DeltaBatch)]}
  "claude の行(transcript の jsonl も stream-json の stdout も同じ形: assistant の content
   block・user の tool_result)→ frame と entries。usage は message.id ごとに 1 度だけ数える
   (1 message が block ごとの行に割れる)。streamed(headless の events): 本文の chunk は
   stream_event の text_delta を text frame に写し、完成した assistant の text block は
   entries だけ(同じ本文を frame で二度流さない)。transcript(streamed = False)は完成した
   block を text frame に。streamed では system の行(init / API の retry / hook の失敗 —
   claude-system-note)を kind system の entry に、result の行の誤り(claude-result-error)を
   kind error の entry に写す(手番の終わりの判定は host が読む — ここは記録だけ)。本文は
   text-body 等で切らずに組み(DeltaBatch.bodies — 段 9f lane 9f-2)、entries(見出し)はそこから headline-of-body の
   1 点で導く(段 9f lane 9f-4 — 本文は ACP へ写さない)。
   道具の呼び出しの書きかけの引数(2026-09-19・card acp:kanban-issue:ki-0d0bcd1e81d9): streamed では stream_event の
   content_block_start の tool_use で block を開き(open-blocks = 前の読みからまだ開いている block の表・OpenToolBlock の列)、
   input_json_delta の partial_json を**この読みの中で同じ block ごとに連結して** tool_input_delta の frame 1 つに写す
   (束ねる粒は読みの周期そのもの — 時間の定数を足さない)。chunk は文字列のまま運び、JSON として解釈しない。frame は
   その block の最初の差分の位置に置く(完成の tool_use の frame より必ず前)。message_start でその message の表を空に
   戻し、content_block_stop で block を閉じる。開始を見ていない差分は frame にせず数える(orphan-input-deltas — id も
   名前も発明しない)。書きかけは frame だけで、bodies / entries には 1 字も書かない(記録は完成した tool_use だけ)。
   読み終えてまだ開いている block は DeltaBatch.open-tool-blocks で返す(次の読みの入力)。"
  (setv frames [])
  ;; 開いている道具の block: (parent, index) → OpenToolBlock。drafts = この読みで差分を見た block → 連結中の chunk と
  ;; frames の中の置き場(最初の差分の位置 — 順を保つ)。
  (setv opened (dfor block open-blocks #(block.parent block.index) block))
  (setv drafts {})
  (setv orphan-input-deltas 0)
  (setv bodies [])
  (setv usage None)
  (setv seen-messages (set))
  (setv model None)
  (setv seq seq-start)
  ;; 段 10f 便 2: 文脈の大きさ = 最後の assistant の message の usage(入力側 + 出力)・window = result の
  ;; modelUsage[model].contextWindow(CLI 2.x の result 行の欄・model = 最後に見た message の model)。材料の末尾の値が勝つ。
  (setv context-tokens None)
  (setv context-window None)
  ;; 段 10 lane 10n: 割り込みの証拠(command_lifecycle started = model が読む拍)と停止の合図の答え(control_response の
  ;; still_queued)— どちらも kind system の entry にして seq を持たせる。合図の答えの後の result(is_error)は「止めた段の
  ;; 終わり」で誤りではない(同じ材料の中で読めた時 — 実測 4 ms 差)。
  (setv reads [])
  (setv stopped-by-signal False)
  (for [record records]
    (setv kind (.get record "type"))
    (setv message (.get record "message"))
    (when (and streamed (= kind "command_lifecycle") (= (.get record "state") "started")
               (isinstance (.get record "command_uuid") str))
      (setv command-uuid (get record "command_uuid"))
      (<- read-body dict (note-body seq at ENTRY-KIND-SYSTEM f"interrupt read by the model: {command-uuid}"))
      (.append bodies read-body)
      (.append reads (InterruptRead :ref command-uuid :seq seq))
      (setv seq (+ seq 1)))
    (when (and streamed (= kind "control_response"))
      (setv response (.get record "response"))
      (setv payload (if (isinstance response dict) (.get response "response") None))
      (setv still (if (isinstance payload dict) (.get payload "still_queued") None))
      (when (and (isinstance response dict) (= (.get response "subtype") "success") (isinstance still list))
        (setv stopped-by-signal True)
        (setv names (.join ", " (lfor item still :if (isinstance item str) item)))
        (<- stop-body dict (note-body seq at ENTRY-KIND-SYSTEM
                                      (+ "interrupt escalated: the running turn was stopped; "
                                         (if names f"queued for the next turn: {names}" "nothing queued for the next turn"))))
        (.append bodies stop-body)
        (setv seq (+ seq 1))))
    (when (and streamed (= kind "result"))
      (setv model-usage (.get record "modelUsage"))
      (when (and (isinstance model-usage dict) (isinstance model str))
        (setv entry (.get model-usage model))
        (setv window (if (isinstance entry dict) (.get entry "contextWindow") None))
        (when (and (isinstance window int) (not (isinstance window bool)) (> window 0))
          (setv context-window window))))
    (when (and streamed (= kind "system"))
      (<- note (| str None) (claude-system-note record))
      (when (is-not note None)
        (<- system-body dict (note-body seq at ENTRY-KIND-SYSTEM note))
        (.append bodies system-body)
        (setv seq (+ seq 1))))
    (when (and streamed (= kind "result"))
      (<- failure (| str None) (claude-result-error record))
      (when (is-not failure None)
        (<- error-body dict (note-body seq at (if stopped-by-signal ENTRY-KIND-SYSTEM ENTRY-KIND-ERROR)
                                       (if stopped-by-signal f"turn stopped by the interrupt signal ({failure})" failure)))
        (.append bodies error-body)
        (setv seq (+ seq 1)))
      (setv stopped-by-signal False))
    (when (and streamed (= kind "stream_event"))
      (setv event (.get record "event"))
      (setv delta (if (isinstance event dict) (.get event "delta") None))
      (when (and (isinstance event dict)
                 (= (.get event "type") "content_block_delta")
                 (isinstance delta dict)
                 (= (.get delta "type") "text_delta")
                 (isinstance (.get delta "text") str))
        (<- chunk-frame dict (delta-frame job-id seq at "text" {"text" (get delta "text")}))
        (.append frames chunk-frame)
        (setv seq (+ seq 1)))
      ;; 道具の呼び出しの書きかけの引数。block の名指しは (parent_tool_use_id, index) — index は message ごとの番号。
      (when (isinstance event dict)
        (setv event-type (.get event "type"))
        (setv parent (.get record "parent_tool_use_id"))
        (setv parent-key (if (isinstance parent str) parent ""))
        (setv block-index (.get event "index"))
        (setv indexed (and (isinstance block-index int) (not (isinstance block-index bool))))
        (cond
          (= event-type "message_start")
          (for [key (list opened)]
            (when (= (get key 0) parent-key)
              (.pop opened key)))
          (and (= event-type "content_block_start") indexed)
          (do
            (setv started (.get event "content_block"))
            (setv started-id (if (isinstance started dict) (.get started "id") None))
            (setv started-name (if (isinstance started dict) (.get started "name") None))
            ;; 同じ番号の前の block は終わっている(番号の使い回し)— 道具でない block の開始でも表から外す。
            (.pop opened #(parent-key block-index) None)
            (when (and (isinstance started dict) (= (.get started "type") "tool_use")
                       (isinstance started-id str) started-id (isinstance started-name str) started-name)
              (setv (get opened #(parent-key block-index))
                    (OpenToolBlock :parent parent-key :index block-index :tool-use-id started-id :name started-name))))
          (and (= event-type "content_block_stop") indexed)
          (.pop opened #(parent-key block-index) None)
          (and (= event-type "content_block_delta") indexed (isinstance delta dict)
               (= (.get delta "type") "input_json_delta") (isinstance (.get delta "partial_json") str))
          (do
            (setv open-block (.get opened #(parent-key block-index)))
            (cond
              (is open-block None)
              (setv orphan-input-deltas (+ orphan-input-deltas 1))
              (in open-block.tool-use-id drafts)
              (.append (get (get drafts open-block.tool-use-id) "parts") (get delta "partial_json"))
              True
              (do
                ;; 最初の差分の位置に frame の置き場を取る(seq もここで振る)。chunk は読み終えてから連結して入れる。
                (<- draft-frame dict (delta-frame job-id seq at "tool_input_delta"
                                                  {"toolUseId" open-block.tool-use-id "name" open-block.name "chunk" ""}))
                (.append frames draft-frame)
                (setv (get drafts open-block.tool-use-id) {"frame" draft-frame "parts" [(get delta "partial_json")]})
                (setv seq (+ seq 1)))))
          True None)))
    (when (and (in kind #{"assistant" "user"}) (isinstance message dict))
      (setv content (.get message "content"))
      (setv message-model (.get message "model"))
      (when (isinstance message-model str)
        (setv model message-model))
      (when (= kind "assistant")
        (setv message-id (.get message "id"))
        (setv message-usage (.get message "usage"))
        (when (and (isinstance message-usage dict) (not-in message-id seen-messages))
          (.add seen-messages message-id)
          (<- part dict (usage-of-claude-message message-usage message-model))
          (<- usage dict (add-usage usage part))
          (setv context-tokens (+ (get part "input") (get part "cacheRead") (get part "cacheWrite") (get part "output")))
          (<- usage-frame dict (delta-frame job-id seq at "usage" part))
          (.append frames usage-frame)
          (setv seq (+ seq 1))))
      (when (isinstance content list)
        (for [block content]
          (when (isinstance block dict)
            (setv block-type (.get block "type"))
            (cond
              (and (= kind "assistant") (= block-type "text") (isinstance (.get block "text") str))
              (do
                (setv payload {"text" (get block "text")})
                (when (isinstance message-model str)
                  (setv (get payload "model") message-model))
                (when (not streamed)
                  (<- text-frame dict (delta-frame job-id seq at "text" payload))
                  (.append frames text-frame))
                (<- block-body dict (text-body seq at (get block "text")
                                                (if (isinstance message-model str) message-model None)))
                (.append bodies block-body)
                (setv seq (+ seq 1)))
              (and (= kind "assistant") (= block-type "tool_use"))
              (do
                (setv tool-id (str (.get block "id" "")))
                (setv name (str (.get block "name" "")))
                (<- summary str (summary-of (.get block "input") 4000))
                (<- use-frame dict (tool-use-frame job-id seq at tool-id name summary (.get block "input")))
                (.append frames use-frame)
                (<- use-body dict (tool-use-body seq at tool-id name (.get block "input")))
                (.append bodies use-body)
                (setv seq (+ seq 1)))
              (and (= kind "user") (= block-type "tool_result"))
              (do
                (setv tool-id (str (.get block "tool_use_id" "")))
                (setv result (.get block "content"))
                (setv is-error (bool (.get block "is_error" False)))
                (<- summary str (summary-of result 4000))
                (setv size (len (.encode (json.dumps result :ensure-ascii False) "utf-8")))
                (<- result-frame dict (delta-frame job-id seq at "tool_result"
                                                   {"toolUseId" tool-id "summary" summary
                                                    "bytes" size
                                                    "isError" is-error}))
                (.append frames result-frame)
                (<- result-body dict (tool-result-body seq at tool-id result is-error))
                (.append bodies result-body)
                (setv seq (+ seq 1)))
              True None))))))
  ;; 書きかけの連結を frame の置き場へ入れる。上限(tool_use.input の文字列を切るのと同じ DELTA-INPUT-STRING-LIMIT 字)を
  ;; 超える連結は続きの frame に分け、置き場の直後へ差す(順を保つ・字は落とさない)。分けた分の seq は読みの末尾から振る
  ;; (seq は frame の名で、並びの鍵は中継の連番 — 完成の tool_use より前に並ぶことは置き場が保つ)。
  (for [draft (.values drafts)]
    (<- pieces tuple (tool-input-chunks (.join "" (get draft "parts")) DELTA-INPUT-STRING-LIMIT))
    (setv head-frame (get draft "frame"))
    (setv (get (get head-frame "payload") "chunk") (get pieces 0))
    ;; 置き場を探すのは分ける時だけ(ふつうの読みは 1 frame — frame の seq は読みの中で一意なので等値で 1 つに決まる)。
    (setv place (if (> (len pieces) 1) (+ (.index frames head-frame) 1) 0))
    (for [piece (cut pieces 1 None)]
      (<- more-frame dict (delta-frame job-id seq at "tool_input_delta"
                                       {"toolUseId" (get (get head-frame "payload") "toolUseId")
                                        "name" (get (get head-frame "payload") "name")
                                        "chunk" piece}))
      (.insert frames place more-frame)
      (setv place (+ place 1))
      (setv seq (+ seq 1))))
  (<- entries tuple (entries-of-bodies (tuple bodies)))
  (DeltaBatch :frames (tuple frames) :entries entries :bodies (tuple bodies) :usage usage
              :next-seq seq :model model
              :context (if (is context-tokens None) None {"tokens" context-tokens "window" context-window})
              :interrupt-reads (tuple reads)
              :open-tool-blocks (tuple (.values opened))
              :orphan-input-deltas orphan-input-deltas))


(defk codex-context-of [last window]
  {:pre [(: last (| dict None)) (: window (| int None))]
   :post [(: % (| dict None))]}
  "codex の直近の応答の token(rollout の last_token_usage / app-server の tokenUsage.last)と model の窓 → 文脈の大きさ
   {tokens, window}(段 10f 便 2)。last が無ければ None。tokens = 入力(cache を含む)+ 出力。"
  (when (is last None)
    (return None))
  (setv input-tokens (or (.get last "input_tokens") (.get last "inputTokens") 0))
  (setv output-tokens (or (.get last "output_tokens") (.get last "outputTokens") 0))
  {"tokens" (+ (int input-tokens) (int output-tokens))
   "window" (if (and (isinstance window int) (not (isinstance window bool)) (> window 0)) window None)})


(defk codex-deltas-of [records job-id seq-start at]
  {:pre [(: records tuple) (: job-id str) (: seq-start int) (: at int)]
   :post [(: % DeltaBatch)]}
  "codex の rollout の行(response_item の message / function_call /
   function_call_output・event_msg の token_count)→ frame と entries。読めない形は飛ばす。"
  (setv frames [])
  (setv bodies [])
  (setv usage None)
  (setv context None)
  (setv seq seq-start)
  (for [record records]
    (setv kind (.get record "type"))
    (setv payload (.get record "payload"))
    (when (isinstance payload dict)
      (setv ptype (.get payload "type"))
      (cond
        (and (= kind "response_item") (= ptype "message") (= (.get payload "role") "assistant"))
        (for [block (or (.get payload "content") [])]
          (when (and (isinstance block dict) (isinstance (.get block "text") str))
            (<- text-frame dict (delta-frame job-id seq at "text" {"text" (get block "text")}))
            (.append frames text-frame)
            (<- block-body dict (text-body seq at (get block "text") None))
            (.append bodies block-body)
            (setv seq (+ seq 1))))
        (and (= kind "response_item") (= ptype "function_call"))
        (do
          (setv name (str (.get payload "name" "")))
          (setv call-id (str (.get payload "call_id" "")))
          (<- summary str (summary-of (.get payload "arguments") 4000))
          ;; codex の arguments は JSON の**文字列**(object ではない)。同じ 1 点を通すことで「object でない入力は
          ;; 名乗らない」の規則を走行器ごとに書き分けない(契約 turn-delta.json の tool_use.input)。
          (<- use-frame dict (tool-use-frame job-id seq at call-id name summary (.get payload "arguments")))
          (.append frames use-frame)
          (<- use-body dict (tool-use-body seq at call-id name (.get payload "arguments")))
          (.append bodies use-body)
          (setv seq (+ seq 1)))
        (and (= kind "response_item") (= ptype "function_call_output"))
        (do
          (setv output (.get payload "output"))
          (setv call-id (str (.get payload "call_id" "")))
          (<- summary str (summary-of output 4000))
          (<- whole str (summary-of output 1000000000))
          (<- result-frame dict (delta-frame job-id seq at "tool_result"
                                             {"toolUseId" call-id
                                              "summary" summary
                                              "bytes" (len (.encode whole "utf-8"))}))
          (.append frames result-frame)
          (<- result-body dict (tool-result-body seq at call-id output False))
          (.append bodies result-body)
          (setv seq (+ seq 1)))
        (and (= kind "event_msg") (= ptype "token_count"))
        (do
          (setv info (.get payload "info"))
          (setv total (if (isinstance info dict) (.get info "total_token_usage") None))
          (when (isinstance total dict)
            (setv #^ JSONObject part {"input" (int (.get total "input_tokens" 0))
                                      "output" (int (.get total "output_tokens" 0))
                                      "cacheWrite" 0
                                      "cacheRead" (int (.get total "cached_input_tokens" 0))})
            ;; token_count は累計なので和ではなく最新で置き換える
            (setv usage part)
            (<- usage-frame dict (delta-frame job-id seq at "usage" part))
            (.append frames usage-frame)
            (setv seq (+ seq 1)))
          ;; 段 10f 便 2: 文脈の大きさ = 直近の応答(last_token_usage)と model の窓(最新が勝つ)。
          (when (isinstance info dict)
            (setv last-usage (.get info "last_token_usage"))
            (setv model-window (.get info "model_context_window"))
            (<- measured (| dict None) (codex-context-of (if (isinstance last-usage dict) last-usage None)
                                                         (if (and (isinstance model-window int) (not (isinstance model-window bool))) model-window None)))
            (when (is-not measured None)
              (setv context measured))))
        True None)))
  (<- entries tuple (entries-of-bodies (tuple bodies)))
  (DeltaBatch :frames (tuple frames) :entries entries :bodies (tuple bodies) :usage usage
              :next-seq seq :model None :context context))


(defk codex-event-deltas-of [records job-id seq-start at]
  {:pre [(: records tuple) (: job-id str) (: seq-start int) (: at int)]
   :post [(: % DeltaBatch)]}
  "codex の app-server の通知(JSON-RPC・schema v2)→ frame と entries:
   item/agentMessage/delta → text frame(本文の chunk)/ item/completed の agentMessage →
   text の entry(完成した本文 — frame では二度流さない)/ item/completed の commandExecution →
   tool_use(name = command_execution・summary = command)と tool_result(aggregatedOutput・
   isError = exitCode ≠ 0)の frame と entries / thread/tokenUsage/updated → usage frame
   (累計なので最新で置き換える)。応答・他の通知は読まない。"
  (setv frames [])
  (setv bodies [])
  (setv usage None)
  (setv context None)
  (setv seq seq-start)
  ;; 段 10 lane 10n: codex に注入の段は無い(inject = turn/interrupt → 同じ thread へ turn/start)。止めた後の
  ;; turn/started が「積んであった注入を model が読む拍」— 名を運ぶ欄が無いので ref = None(未読を全部)。
  (setv reads [])
  (for [record records]
    (setv method (.get record "method"))
    (setv params (.get record "params"))
    (when (and (isinstance method str) (isinstance params dict))
      (setv item (.get params "item"))
      (cond
        (= method "turn/started")
        (do
          (<- started-body dict (note-body seq at ENTRY-KIND-SYSTEM "turn started"))
          (.append bodies started-body)
          (.append reads (InterruptRead :ref None :seq seq))
          (setv seq (+ seq 1)))
        (and (= method "item/agentMessage/delta") (isinstance (.get params "delta") str))
        (do
          (<- chunk-frame dict (delta-frame job-id seq at "text" {"text" (get params "delta")}))
          (.append frames chunk-frame)
          (setv seq (+ seq 1)))
        (and (= method "item/completed") (isinstance item dict)
             (= (.get item "type") "agentMessage") (isinstance (.get item "text") str))
        (do
          (<- item-body dict (text-body seq at (get item "text") None))
          (.append bodies item-body)
          (setv seq (+ seq 1)))
        (and (= method "item/completed") (isinstance item dict)
             (= (.get item "type") "commandExecution") (isinstance (.get item "command") str))
        (do
          (setv tool-id (str (.get item "id" "")))
          (<- summary str (summary-of (get item "command") 4000))
          ;; codex の command は**文字列**(object ではない)ので input は載らない — 組む点は 1 つ(tool-use-frame)。
          (<- use-frame dict (tool-use-frame job-id seq at tool-id "command_execution" summary (get item "command")))
          (.append frames use-frame)
          (<- use-body dict (tool-use-body seq at tool-id "command_execution" (get item "command")))
          (.append bodies use-body)
          (setv seq (+ seq 1))
          (setv output (.get item "aggregatedOutput"))
          (when (isinstance output str)
            (<- result-summary str (summary-of output 4000))
            (setv exit-code (.get item "exitCode"))
            (setv is-error (and (isinstance exit-code int) (!= exit-code 0)))
            (<- result-frame dict (delta-frame job-id seq at "tool_result"
                                               {"toolUseId" tool-id "summary" result-summary
                                                "bytes" (len (.encode output "utf-8"))
                                                "isError" is-error}))
            (.append frames result-frame)
            (<- result-body dict (tool-result-body seq at tool-id output is-error))
            (.append bodies result-body)
            (setv seq (+ seq 1))))
        (= method "turn/completed")
        (do
          ;; 手番の終わりの誤り(status ≠ completed)を kind error の entry に(判定は host — ここは記録だけ)。
          ;; 段 10 lane 10n: interrupted は止めた段の終わり(割り込みの本文を渡すため・取り下げ)で誤りではない → kind system。
          (setv turn (.get params "turn"))
          (when (isinstance turn dict)
            (setv turn-status (.get turn "status"))
            (when (and (isinstance turn-status str) (!= turn-status "completed"))
              (setv error (.get turn "error"))
              (setv message (if (isinstance error dict) (.get error "message") None))
              (setv interrupted (= turn-status "interrupted"))
              (<- error-body dict (note-body seq at (if interrupted ENTRY-KIND-SYSTEM ENTRY-KIND-ERROR)
                                             (cond
                                               interrupted "turn stopped by the interrupt signal"
                                               (isinstance message str) message
                                               True f"turn-{turn-status}")))
              (.append bodies error-body)
              (setv seq (+ seq 1)))))
        (= method "thread/tokenUsage/updated")
        (do
          (setv token-usage (.get params "tokenUsage"))
          (setv total (if (isinstance token-usage dict) (.get token-usage "total") None))
          (when (isinstance total dict)
            (setv #^ JSONObject part {"input" (int (or (.get total "inputTokens") 0))
                                      "output" (int (or (.get total "outputTokens") 0))
                                      "cacheWrite" (int (or (.get total "cacheWriteInputTokens") 0))
                                      "cacheRead" (int (or (.get total "cachedInputTokens") 0))})
            (setv usage part)
            (<- usage-frame dict (delta-frame job-id seq at "usage" part))
            (.append frames usage-frame)
            (setv seq (+ seq 1)))
          ;; 段 10f 便 2: 文脈の大きさ = 直近の応答(tokenUsage.last)と model の窓(modelContextWindow・最新が勝つ)。
          (when (isinstance token-usage dict)
            (setv last-usage (.get token-usage "last"))
            (setv model-window (.get token-usage "modelContextWindow"))
            (<- measured (| dict None) (codex-context-of (if (isinstance last-usage dict) last-usage None)
                                                         (if (and (isinstance model-window int) (not (isinstance model-window bool))) model-window None)))
            (when (is-not measured None)
              (setv context measured))))
        True None)))
  (<- entries tuple (entries-of-bodies (tuple bodies)))
  (DeltaBatch :frames (tuple frames) :entries entries :bodies (tuple bodies) :usage usage
              :next-seq seq :model None :context context :interrupt-reads (tuple reads)))


(defk events-to-deltas [agent-type text job-id seq-start at open-blocks]
  {:pre [(: agent-type str) (: text str) (: job-id str) (: seq-start int) (: at int) (: open-blocks tuple)]
   :post [(: % DeltaBatch)]}
  "headless の events file の追記(stdout の行)→ TurnDelta の frame と entries(契約の
   種類の閉語彙 text / tool_use / tool_result / usage)。claude = stream-json の行、codex =
   app-server の JSON-RPC の通知。未知の kind は空。"
  (<- records tuple (parse-json-lines text))
  (cond
    (= agent-type "claude")
    (do (<- claude-batch DeltaBatch (claude-deltas-of records job-id seq-start at True open-blocks))
        claude-batch)
    (= agent-type "codex")
    (do (<- codex-batch DeltaBatch (codex-event-deltas-of records job-id seq-start at))
        codex-batch)
    True (DeltaBatch :frames #() :entries #() :usage None :next-seq seq-start :model None)))


(defk deltas-of [agent-type source text job-id seq-start at open-blocks]
  {:pre [(: agent-type str) (: source str) (: text str) (: job-id str) (: seq-start int)
         (: at int) (: open-blocks tuple)]
   :post [(: % DeltaBatch)]}
  "実況の材料の追記(text)→ kind と材料の種類(閉語彙 effects.StreamSource)別の TurnDelta の
   frame と entries: events = headless の stdout の行(events-to-deltas)、transcript = tui の
   transcript の行。未知の kind は空。"
  (when (= source STREAM-SOURCE-EVENTS)
    (<- streamed DeltaBatch (events-to-deltas agent-type text job-id seq-start at open-blocks))
    (return streamed))
  (<- records tuple (parse-json-lines text))
  (cond
    (= agent-type "claude")
    ;; transcript(tui)は完成した block の行だけで、引数の差分を運ばない — 開いた block の表は空のまま。
    (do (<- claude-batch DeltaBatch (claude-deltas-of records job-id seq-start at False #()))
        claude-batch)
    (= agent-type "codex")
    (do (<- codex-batch DeltaBatch (codex-deltas-of records job-id seq-start at))
        codex-batch)
    True (DeltaBatch :frames #() :entries #() :usage None :next-seq seq-start :model None)))


(defk pane-frame [job-id seq at lines]
  {:pre [(: job-id str) (: seq int) (: at int) (: lines tuple)]
   :post [(: % dict)]}
  "pane の断面の frame(mode = full)。"
  (<- frame dict (delta-frame job-id seq at "frame"
                              {"mode" "full" "rows" (len lines) "lines" (list lines)}))
  frame)


(defk status-frame [job-id seq at phase]
  {:pre [(: job-id str) (: seq int) (: at int) (: phase str)]
   :post [(: % dict)]}
  "手番の進みの frame(running / waiting / ended)。"
  (<- frame dict (delta-frame job-id seq at "status" {"phase" phase}))
  frame)


(defk frame-lines-of [text]
  {:pre [(: text str)]
   :post [(: % tuple)]}
  "capture の text → 行の列(末尾の空行は落とす)。"
  (setv lines (list (.splitlines text)))
  (while (and lines (not (.strip (get lines -1))))
    (.pop lines))
  (tuple lines))


;; ---------------------------------------------------------------------------
;; capture の是非・待ちの長さ・周期(判断ではなく規則の写し)
;; ---------------------------------------------------------------------------

(defk capture-verdict [subscribers]
  {:pre [(: subscribers (| int None))]
   :post [(: % str)]}
  "購読者が居る時だけ capture する(issue #1 の決定 1 と 4)。0 = stop、正 = continue。
   None(中継が数を返さない)は「居る」と読まない = stop。"
  (if (and (isinstance subscribers int) (> subscribers 0)) "continue" "stop"))


(defk wait-seconds-for [state settings]
  {:pre [(: state AgentdState) (: settings AgentdSettings)]
   :post [(: % float)]}
  "次の watch の待ちの上限: 購読者が居る(capturing)間は、この器の実況が events(headless —
   file の追記の読み)なら events の周期(≤ 50 ms・段 8 lane 4aa)、frames(tui の pane の断面)なら
   frame の間隔。手番が走っていれば transcript の周期、何も無ければ idle の上限。"
  (cond
    (any (gfor job state.jobs job.capturing))
    (if (= settings.stream-capability STREAM-CAPABILITY-EVENTS)
        (float settings.events-poll-seconds)
        (float settings.frame-interval-seconds))
    state.jobs (float settings.transcript-poll-seconds)
    True (float settings.idle-wait-seconds)))


(defk record-due [job now-ms settings]
  {:pre [(: job InFlightJob) (: now-ms int) (: settings AgentdSettings)]
   :post [(: % bool)]}
  "手番の記録(turn-record)へ出来事を追記する拍か(段 8 lane 4aa): 最後の追記から transcript の
   周期が経った(まだ 1 度も = 今)。push の周期(events_poll_seconds)には追随しない — 記録の書きは
   ACP の event 1 つで、拍ごとに書くと journal と画面の糊の watch の拍が飽和する。"
  (<- period-passed bool (due (if (= job.last-record-ms 0) None job.last-record-ms) now-ms
                              settings.transcript-poll-seconds))
  period-passed)


(defk due [last-ms now-ms period-seconds]
  {:pre [(: last-ms (| int None)) (: now-ms int) (: period-seconds (| int float))]
   :post [(: % bool)]}
  "周期の拍か(last が None = まだ 1 度も = 今)。"
  (or (is last-ms None)
      (>= (- now-ms last-ms) (* 1000 period-seconds))))


(defk resync-due [signal state now-ms settings]
  {:pre [(: signal WatchAdvance) (: state AgentdState) (: now-ms int) (: settings AgentdSettings)]
   :post [(: % bool)]}
  "list を読み直す拍: sequence が進んだ・gap・接続の張り直し・周期の保険。"
  (<- periodic bool (due state.last-resync-ms now-ms settings.watch-resync-seconds))
  (or (in signal.kind #{"changed" "gap" "closed"}) periodic))


(defk list-mode-for [signal state now-ms settings]
  {:pre [(: signal WatchAdvance) (: state AgentdState) (: now-ms int) (: settings AgentdSettings)]
   :post [(: % str)]}
  "行をどう読み直すか(閉語彙 effects.ListMode)— 判断はここ 1 点: 周期の保険が来た・gap・
   接続の張り直し・まだ 1 度も読んでいない → full(全量 list)/ watch で起きた(changed)→
   window(変わった行だけ: event-window の post-image)/ idle・session(器の出来事で起きた拍 —
   ACP の sequence は進んでいない・段 12 lane 12b)→ none。"
  (<- periodic bool (due state.last-resync-ms now-ms settings.watch-resync-seconds))
  (cond
    (or periodic (in signal.kind #{"gap" "closed"})) LIST-MODE-FULL
    (= signal.kind "changed") LIST-MODE-WINDOW
    True LIST-MODE-NONE))


(defk window-epoch-verdict [known announced]
  {:pre [(: known (| str None)) (: announced (| str None))]
   :post [(: % str)]}
  "窓の答えが名乗る store の版(契約 read-freshness.json の storeEpoch)をどう扱うか(閉語彙 effects.EpochVerdict)—
   判断はここ 1 点(SDK python / Hy runtime の informer と同じ 1 つの規則): announced が無い → continue(この契約より前の
   engine・版の判断を持たない)/ 覚えた版が無い → adopt(初めて名乗られた版を採る — 変わったではない)/ 同じ → continue /
   違う → relist(cursor は別の出来事を指し得るので全量 list へ・新しい版を覚える)。"
  (cond
    (is announced None) EPOCH-VERDICT-CONTINUE
    (is known None) EPOCH-VERDICT-ADOPT
    (= known announced) EPOCH-VERDICT-CONTINUE
    True EPOCH-VERDICT-RELIST))


(defk rows-of-kind [rows kind]
  {:pre [(: rows tuple) (: kind str)]
   :post [(: % tuple)]}
  "行の列のうち kind の行(行の順のまま)。"
  (tuple (lfor row rows :if (= row.kind kind) row)))


(defk merge-rows [known changed retired]
  {:pre [(: known tuple) (: changed tuple) (: retired tuple)]
   :post [(: % tuple)]}
  "知っている行(鍵ごとの最新の image)に窓の差分を重ねる: changed は鍵で置き換え(無ければ
   足す)、retired の鍵は消す。順序は鍵の順(決定的)。"
  (setv by-key {})
  (for [row known]
    (setv (get by-key row.key) row))
  (for [row changed]
    (setv (get by-key row.key) row))
  (for [key retired]
    (.pop by-key key None))
  (tuple (lfor key (sorted by-key) (get by-key key))))


(defk births-of-rows [rows]
  {:pre [(: rows tuple)]
   :post [(: % tuple)]}
  "行の列のうち generation 1 の image(SpecApplied の post-image)の id → 着地の時刻(ms)の対。"
  (tuple (sorted (lfor row rows
                       :if (and (= row.generation 1) (is-not row.landed-at-ms None))
                       #(row.resource-id row.landed-at-ms)))))


(defk births-with [births pairs]
  {:pre [(: births tuple) (: pairs tuple)]
   :post [(: % tuple)]}
  "生まれの着地の時刻の表(job の id → ms)に対を足す(既に在る id は変えない — 生まれは 1 度)。"
  (setv table (dict births))
  (for [[job-id at-ms] pairs]
    (when (not-in job-id table)
      (setv (get table job-id) at-ms)))
  (tuple (sorted (.items table))))


(defk birth-ms-of [row births]
  {:pre [(: row AcpRow) (: births tuple)]
   :post [(: % int)]}
  "計器 agent-job-to-send の始点 = 行の生まれの着地(ns 精度): 生まれの表に在ればそれ、行自身が
   generation 1 で landed_at_ms を持てばそれ、無ければ今日の値(秒の粒度の createdAt)。"
  (setv table (dict births))
  (setv known (.get table row.resource-id))
  (cond
    (isinstance known int) known
    (and (= row.generation 1) (is-not row.landed-at-ms None)) row.landed-at-ms
    True row.created-at-ms))


(defk message-key-of [message-id]
  {:pre [(: message-id str)]
   :post [(: % str)]}
  "Message の行の鍵(identityKey = id・区画 = agora の kind の区画)— inputs の本文は全量の
   list ではなく鍵で 1 行ずつ読む。"
  f"{AGORA-KINDS-NAMESPACE}:{MESSAGE-KIND}:{message-id}")


(defk lease-renew-due [job now-ms settings]
  {:pre [(: job InFlightJob) (: now-ms int) (: settings AgentdSettings)]
   :post [(: % bool)]}
  "借りた札の錠の期限の margin 秒前に借り直す。"
  (and (is-not job.lease-hold-ms None)
       (>= (+ now-ms (* 1000 settings.lease-renew-margin-seconds)) job.lease-hold-ms)))


(defk job-outcome-of [view]
  {:pre [(: view SessionView)]
   :post [(: % JobOutcome)]}
  "器の眺めから手番の結末を読む(既存の turn-end の意味論 = 行の status が終端 —
   policy.hy の monitor が turn-end で done へ倒す。語彙は effects.SESSION-TERMINAL-STATUSES)。done 以外の終端は SessionFailed の
   condition(理由 = terminal_cause の category と reason)。"
  (if (not-in view.status SESSION-TERMINAL-STATUSES)
      (JobOutcome :ended False :result None :cause None :conditions #())
      (do
        (setv conditions [])
        (when (!= view.status "done")
          (setv session-cause (or view.terminal-cause {}))
          (setv category (.get session-cause "category"))
          (setv reason (.get session-cause "reason"))
          (<- failed dict (condition-of "SessionFailed"
                                        (+ f"session {view.status}"
                                           (if (isinstance category str) f": {category}" "")
                                           (if (isinstance reason str) f" ({reason})" ""))))
          (.append conditions failed))
        ;; #349 行 3 粒 3a: 自然に終わった手番 = completed(value は result に)・done 以外の終端 = failed / SessionFailed
        (setv done (= view.status "done"))
        (<- cause dict (terminal-cause-of (if done CAUSE-CATEGORY-COMPLETED CAUSE-CATEGORY-FAILED)
                                          (if done None "SessionFailed")))
        (JobOutcome :ended True :result view.result-payload :cause cause :conditions (tuple conditions)))))


(defk without-job [state job-id]
  {:pre [(: state AgentdState) (: job-id str)]
   :post [(: % AgentdState)]}
  (replace state :jobs (tuple (lfor job state.jobs :if (!= job.job-id job-id) job))))


(defk with-job [state job]
  {:pre [(: state AgentdState) (: job InFlightJob)]
   :post [(: % AgentdState)]}
  "同じ job_id の行を置き換える(無ければ足す)。"
  (setv kept (lfor existing state.jobs :if (!= existing.job-id job.job-id) existing))
  (replace state :jobs (tuple (+ kept [job]))))


(defk job-in-flight [state job-id]
  {:pre [(: state AgentdState) (: job-id str)]
   :post [(: % (| InFlightJob None))]}
  "memory の手番を id で引く(無ければ None)。拍の 2 周目が 1 周目の後の姿を読む 1 点 —— 1 周目で閉じた
  (without-job された)job は None で、2 周目は飛ばす(card acp:kanban-issue:ki-6eb745f6d528)。"
  (setv found None)
  (for [job state.jobs]
    (when (= job.job-id job-id)
      (setv found job)))
  found)


(defk in-flight-ids [state]
  {:pre [(: state AgentdState)]
   :post [(: % set)]}
  (set (gfor job state.jobs job.job-id)))


;; ---------------------------------------------------------------------------
;; verify の命令(段 12 lane 12a・agora-redesign #230)— 会話の手番ではない job の判断
;; ---------------------------------------------------------------------------
;;
;; charter.kind = verify の job は定期便の検証の命令 1 つ(会社 repo の日次の全体検証)。契機は k3s の
;; CronJob、配置は charter.place を spec.places に名乗る node(この agentd の機体)、実行はこの agentd が
;; 機体自身の資格で **script を 1 つ走らせる**こと — claude / codex を起こさず、預かり所から札も借りない。
;; 命令の文字列は行から運ばない(herdr-hud D0626 決定 2): 走らせるのは機体の家の
;; VERIFY-SCRIPTS-RELDIR/<jobId>.sh ちょうどで、知らない id・綴りの外・無い script は条件で loud に落とす。
;; 結末は file(log / rc / pid)に残し、agentd が再起動しても行(sessionHandle.verify)と file から組み直す(R7)。

(defk job-kind-of [row]
  {:pre [(: row AcpRow)]
   :post [(: % str)]}
  "job の種類(charter.kind の語・無い = turn)。agentd が読むのはこの 1 語で、語彙の検は配置(ACP Inputs.jobViewOf)が
   結ぶ前に済ませている — ここは結ばれた行の綴りを写すだけ(判断の第 2 の点を作らない)。文字列でない・空は turn。"
  (setv charter (.get row.spec "charter"))
  (setv kind (if (isinstance charter dict) (.get charter CHARTER-KIND-KEY) None))
  (if (and (isinstance kind str) kind) kind CHARTER-KIND-TURN))


(defk verify-plan-of [row home runs-dir]
  {:pre [(: row AcpRow) (: home str) (: runs-dir str)]
   :post [(: % (| VerifyPlan str))]}
  "Bound の verify の行から走らせ方を写す(判断ではなく欄の写しと置き場の導出): charter.jobId(綴りは
   CHARTER-VERIFY-JOB-ID-PATTERN ちょうど — path の要素にそのまま使う)・runKey(無ければ空)・deadlineSeconds
   (正の整数・無ければ 0 = 期限なし)。script = home/VERIFY-SCRIPTS-RELDIR/<jobId>.sh、結末の 3 file = runs-dir の
   下の <job id>.{log,rc,pid}。読めない行は理由の文(呼び手が条件 VerifyScriptMissing で閉じる)。"
  (setv charter (.get row.spec "charter"))
  (when (not (isinstance charter dict))
    (return f"agent-job {row.resource-id}: spec.charter is not an object"))
  (setv verify-id (.get charter CHARTER-VERIFY-JOB-ID-KEY))
  (when (not (and (isinstance verify-id str) (re.match CHARTER-VERIFY-JOB-ID-PATTERN verify-id)))
    (return (+ f"agent-job {row.resource-id}: charter.{CHARTER-VERIFY-JOB-ID-KEY} {verify-id !r} is not a verify id "
               f"(pattern {CHARTER-VERIFY-JOB-ID-PATTERN})")))
  (setv run-key (.get charter CHARTER-VERIFY-RUN-KEY-KEY))
  (setv deadline (.get charter CHARTER-VERIFY-DEADLINE-KEY))
  (setv deadline-seconds (if (and (isinstance deadline int) (not (isinstance deadline bool)) (> deadline 0)) deadline 0))
  (when (not home)
    (return f"agent-job {row.resource-id}: this node declares no home (AgentdSettings.home) to find {VERIFY-SCRIPTS-RELDIR} under"))
  (VerifyPlan
    :job-id row.resource-id
    :verify-id verify-id
    :run-key (if (isinstance run-key str) run-key "")
    :deadline-seconds deadline-seconds
    :script-path f"{home}/{VERIFY-SCRIPTS-RELDIR}/{verify-id}.sh"
    :log-path f"{runs-dir}/{row.resource-id}.log"
    :rc-path f"{runs-dir}/{row.resource-id}.rc"
    :pid-path f"{runs-dir}/{row.resource-id}.pid"))


(defk verify-argv-of [plan]
  {:pre [(: plan VerifyPlan)]
   :post [(: % tuple)]}
  "verify の命令の起こし方の 1 点: sh の 1 行が自分の pid を書き、script を走らせ(stdout / stderr は log の file へ追記)、
   終了コードを rc の file に書く。agentd はこの process を待たない(自分の session で起き、再起動しても残る)。
   引用は sh の位置引数($0〜$3)で運ぶ — path を文字列に埋めない。"
  #("/bin/sh" "-c"
    "echo $$ > \"$0\" && \"$1\" >> \"$2\" 2>&1; echo $? > \"$3\""
    plan.pid-path plan.script-path plan.log-path plan.rc-path))


(defk verify-handle-of [plan principal started-ms]
  {:pre [(: plan VerifyPlan) (: principal str) (: started-ms int)]
   :post [(: % dict)]}
  "verify の job の sessionHandle: stream{owner, name} は手番と同じ形(running-on-me の判定が自分の Running を
   見つける鍵 — 中継へ frame は押さない)、verify{jobId, runKey, startedAtMs, scriptPath, logPath, rcPath, pidPath} は
   拾い直しの材料(R7: 正本は行)。"
  {"stream" {"owner" principal "name" plan.job-id}
   JOB-HANDLE-VERIFY-KEY {"jobId" plan.verify-id
                          "runKey" plan.run-key
                          "startedAtMs" started-ms
                          "deadlineSeconds" plan.deadline-seconds
                          "scriptPath" plan.script-path
                          "logPath" plan.log-path
                          "rcPath" plan.rc-path
                          "pidPath" plan.pid-path}})


(defk verify-running-status-of [row handle]
  {:pre [(: row AcpRow) (: handle dict)]
   :post [(: % dict)]}
  "受けた verify の status: committed の欄を写し、phase = Running と sessionHandle だけ書く(binding は触らない)。"
  (<- next dict (status-object-of row))
  (setv (get next "phase") PHASE-RUNNING)
  (setv (get next "sessionHandle") handle)
  next)


(defk verify-plan-of-handle [row]
  {:pre [(: row AcpRow)]
   :post [(: % (| VerifyPlan None))]}
  "自分の Running の verify の行から走らせ方を組み直す(再起動後の拾い直し — R7): sessionHandle.verify の欄ちょうど。
   欄が無い・形が違う = None(拾い直せない — 呼び手が結末なしで閉じる)。"
  (setv status row.status)
  (setv handle (if (isinstance status dict) (.get status "sessionHandle") None))
  (setv verify (if (isinstance handle dict) (.get handle JOB-HANDLE-VERIFY-KEY) None))
  (when (not (isinstance verify dict))
    (return None))
  (setv verify-id (.get verify "jobId"))
  (setv run-key (.get verify "runKey"))
  (setv deadline (.get verify "deadlineSeconds"))
  (setv script-path (.get verify "scriptPath"))
  (setv log-path (.get verify "logPath"))
  (setv rc-path (.get verify "rcPath"))
  (setv pid-path (.get verify "pidPath"))
  (when (not (and (isinstance verify-id str) (isinstance script-path str) (isinstance log-path str)
                  (isinstance rc-path str) (isinstance pid-path str)))
    (return None))
  (VerifyPlan
    :job-id row.resource-id
    :verify-id verify-id
    :run-key (if (isinstance run-key str) run-key "")
    :deadline-seconds (if (and (isinstance deadline int) (not (isinstance deadline bool)) (> deadline 0)) deadline 0)
    :script-path script-path
    :log-path log-path
    :rc-path rc-path
    :pid-path pid-path))


(defk verify-started-ms-of-handle [row fallback-ms]
  {:pre [(: row AcpRow) (: fallback-ms int)]
   :post [(: % int)]}
  "拾い直した verify の起こした時刻(sessionHandle.verify.startedAtMs・無ければ fallback = 行の createdAt)— 期限の起点。"
  (setv status row.status)
  (setv handle (if (isinstance status dict) (.get status "sessionHandle") None))
  (setv verify (if (isinstance handle dict) (.get handle JOB-HANDLE-VERIFY-KEY) None))
  (setv started (if (isinstance verify dict) (.get verify "startedAtMs") None))
  (if (and (isinstance started int) (not (isinstance started bool))) started fallback-ms))


(defk in-flight-command-of [row plan pid started-ms]
  {:pre [(: row AcpRow) (: plan VerifyPlan) (: pid (| int None)) (: started-ms int)]
   :post [(: % InFlightCommand)]}
  "走らせている verify の命令の memory の状態を組む 1 点(受けた直後も拾い直しも同じ形)。"
  (InFlightCommand
    :job-key row.key
    :job-namespace row.namespace
    :job-id row.resource-id
    :verify-id plan.verify-id
    :run-key plan.run-key
    :started-ms started-ms
    :deadline-seconds plan.deadline-seconds
    :pid pid
    :script-path plan.script-path
    :log-path plan.log-path
    :rc-path plan.rc-path
    :pid-path plan.pid-path))


(defk pid-of-text [text]
  {:pre [(: text (| str None))]
   :post [(: % (| int None))]}
  "pid の file の中身 → pid(数字の行 1 つ・それ以外は None)。"
  (if (and (isinstance text str) (re.match r"^\s*\d+\s*$" text))
      (int (.strip text))
      None))


(defk rc-of-text [text]
  {:pre [(: text (| str None))]
   :post [(: % (| int None))]}
  "rc の file の中身 → 終了コード(数字の行 1 つ・それ以外は None = まだ書かれていない / 壊れている)。"
  (if (and (isinstance text str) (re.match r"^\s*\d+\s*$" text))
      (int (.strip text))
      None))


(defk verify-step-of [probe started-ms now-ms deadline-seconds]
  {:pre [(: probe (| CommandRunning CommandExited CommandGone)) (: started-ms int) (: now-ms int) (: deadline-seconds int)]
   :post [(: % str)]}
  "verify の命令の次の 1 手(閉語彙 effects.VerifyStep): rc の file が在る → ended / 消えた → lost / 走っていて期限
   (deadlineSeconds > 0)を越えた → timed-out(止める)/ それ以外 → observe。"
  (cond
    (isinstance probe CommandExited) VERIFY-STEP-ENDED
    (isinstance probe CommandGone) VERIFY-STEP-LOST
    (and (> deadline-seconds 0) (>= (- now-ms started-ms) (* deadline-seconds 1000))) VERIFY-STEP-TIMED-OUT
    True VERIFY-STEP-OBSERVE))


(defk verify-result-of [command rc ended-ms]
  {:pre [(: command InFlightCommand) (: rc int) (: ended-ms int)]
   :post [(: % dict)]}
  "verify の結末(agent-job の status.result): kind・便の id・発火の鍵・rc・始まり / 終わり / 所要・log の path。
   赤(rc != 0)も結末であって条件ではない — 日次の検証の赤は台帳(ai land verify が land-partition に記帳)の側の事実。"
  {"kind" CHARTER-KIND-VERIFY
   "jobId" command.verify-id
   "runKey" command.run-key
   "rc" rc
   "startedAtMs" command.started-ms
   "endedAtMs" ended-ms
   "durationMs" (- ended-ms command.started-ms)
   "log" command.log-path})


(defk withdrawn-command-rows-of [rows node-name node-row-id principal]
  {:pre [(: rows tuple) (: node-name str) (: node-row-id (| str None)) (: principal str)]
   :post [(: % tuple)]}
  "Withdrawn の行のうち自分が受けた verify の命令の行(行の順のまま): 結びが自分を指す(binding-names-me)∧ sessionHandle.stream.owner ==
   自分 ∧ sessionHandle.verify が在る。手番の行(sessionId を持つ)は withdrawn-rows-of の持ち分で、ここには来ない。"
  (setv out [])
  (for [row rows]
    (setv status row.status)
    (when (and (isinstance status dict) (= (.get status "phase") PHASE-WITHDRAWN))
      (setv binding (.get status "binding"))
      (setv handle (.get status "sessionHandle"))
      (setv stream (if (isinstance handle dict) (.get handle "stream") None))
      (<- mine bool (binding-names-me binding node-name node-row-id))
      (when (and mine
                 (isinstance stream dict)
                 (= (.get stream "owner") principal)
                 (isinstance handle dict)
                 (isinstance (.get handle JOB-HANDLE-VERIFY-KEY) dict))
        (.append out row))))
  (tuple out))


(defk without-command [state job-id]
  {:pre [(: state AgentdState) (: job-id str)]
   :post [(: % AgentdState)]}
  (replace state :commands (tuple (lfor command state.commands :if (!= command.job-id job-id) command))))


(defk with-command [state command]
  {:pre [(: state AgentdState) (: command InFlightCommand)]
   :post [(: % AgentdState)]}
  "同じ job_id の命令を置き換える(無ければ足す)。"
  (setv kept (lfor existing state.commands :if (!= existing.job-id command.job-id) existing))
  (replace state :commands (tuple (+ kept [command]))))


(defk in-flight-command-ids [state]
  {:pre [(: state AgentdState)]
   :post [(: % set)]}
  (set (gfor command state.commands command.job-id)))


;; ---------------------------------------------------------------------------
;; 会話の履歴の段階つき要約(段 12 lane 12j・agora-redesign #233・#55 案 D)— charter.kind = summarize の判断
;; ---------------------------------------------------------------------------
;;
;; operator 2026-09-16 "lets see if 1 will work" = 方法 1(段階つきの要約): 文脈が閾値を超えた会話の古い区間を、会話と同じ profile の
;; Claude Code(Opus 5)で 1 段落に縮め、区間ごとに agora の kind summary の行(本文は記録の service の claim check)を書く。
;; ここに在るのは判断の純関数だけ: 行の欄の写し(summarize-plan-of)・区間の切り方(summary-region-of)・prompt の 1 点
;; (summarize-prompt-of — 残す情報と落とす情報の定義点)・起こし方の 1 点(summarize-argv-of)・答えの読み(summarize-output-of)・
;; 行と出来事の綴り(summary-spec-of / summary-status-of / summary-body-of)。I/O は agentd.hy の腕。

(defk summarize-plan-of [row default-region-bytes default-deadline-seconds]
  {:pre [(: row AcpRow) (: default-region-bytes int) (: default-deadline-seconds int)]
   :post [(: % (| SummarizePlan str))]}
  "Bound の summarize の行から何を要約するかを写す(判断ではなく欄の写し): 会話 = spec.subject・区間の上端 = charter.until
   (0 以上の整数・recordSeq・含む)・1 区間の上限 = charter.regionByteBudget(正の整数・無ければ宣言の値)・model = charter.model・
   資格 = status.binding.profile / account(配置が結んだ会話の profile — 無い行は起こさない)・期限 = 宣言の値。読めない行は理由の文。"
  (setv charter (.get row.spec "charter"))
  (when (not (isinstance charter dict))
    (return f"agent-job {row.resource-id}: spec.charter is not an object"))
  (setv subject (.get row.spec "subject"))
  (when (not (and (isinstance subject str) subject))
    (return f"agent-job {row.resource-id}: spec.subject (the conversation) is missing"))
  (setv until (.get charter CHARTER-SUMMARIZE-UNTIL-KEY))
  (when (not (and (isinstance until int) (not (isinstance until bool)) (>= until 0)))
    (return f"agent-job {row.resource-id}: charter.{CHARTER-SUMMARIZE-UNTIL-KEY} {until !r} is not a recordSeq (a non-negative integer)"))
  (setv region (.get charter CHARTER-SUMMARIZE-REGION-BYTES-KEY))
  (setv region-bytes (if (and (isinstance region int) (not (isinstance region bool)) (> region 0)) region default-region-bytes))
  (setv model (.get charter "model"))
  (when (not (and (isinstance model str) model))
    (return f"agent-job {row.resource-id}: charter.model is missing — a summarize names the model it runs"))
  (setv status (if (isinstance row.status dict) row.status {}))
  (setv binding (.get status "binding"))
  (setv profile (if (isinstance binding dict) (.get binding "profile") None))
  (setv account (if (isinstance binding dict) (.get binding "account") None))
  (when (not (and (isinstance profile str) profile (isinstance account str) account))
    (return f"agent-job {row.resource-id}: status.binding carries no profile / account — a summarize runs under the conversation's custody lease"))
  (SummarizePlan :job-id row.resource-id :conversation-id subject :until until :region-byte-budget region-bytes
                 :model model :profile profile :account account :deadline-seconds default-deadline-seconds))


(defk summary-rows-covered-to [rows conversation-id]
  {:pre [(: rows tuple) (: conversation-id str)]
   :post [(: % (| int None))]}
  "この会話の生きた summary の行(state が superseded でない)の spec.to の最大 = 要約済みの区間の終わり。無ければ None。
   要約済みの区間は 2 度要約しない(次の区間はこの値 + 1 から)。"
  (setv best None)
  (for [row rows]
    (when (!= (.get row.spec SUMMARY-SPEC-CONVERSATION-KEY) conversation-id)
      (continue))
    (setv status (if (isinstance row.status dict) row.status {}))
    (when (= (.get status "state") SUMMARY-STATE-SUPERSEDED)
      (continue))
    (setv to (.get row.spec SUMMARY-SPEC-TO-KEY))
    (when (and (isinstance to int) (not (isinstance to bool)) (or (is best None) (> to best)))
      (setv best to)))
  best)


(defk summary-region-of [events from-seq until budget]
  {:pre [(: events tuple) (: from-seq int) (: until int) (: budget int)]
   :post [(: % (| SummaryRegion None))]}
  "次に要約する 1 区間: recordSeq が [from-seq, until] の原文の出来事(RECORD-RAW-EVENT-KINDS)を昇順に取り、bytes の和が budget に
   届いた出来事で区間を閉じる(少なくとも 1 つは入れる — 1 つで budget を超える出来事も 1 区間)。区間の to = 最後に入れた出来事の
   recordSeq。原文が 1 つも無ければ None(要約するものが無い)。"
  (setv taken [])
  (setv total 0)
  (for [event (sorted events :key (fn [event] event.record-seq))]
    (when (or (< event.record-seq from-seq) (> event.record-seq until) (not-in event.kind RECORD-RAW-EVENT-KINDS))
      (continue))
    (.append taken event)
    (setv total (+ total event.bytes))
    (when (>= total budget)
      (break)))
  (if taken
      (SummaryRegion :from-seq from-seq :to-seq (. (get taken -1) record-seq) :events (tuple taken) :source-bytes total)
      None))


(defk summary-region-text [region]
  {:pre [(: region SummaryRegion)]
   :post [(: % str)]}
  "区間の原文 — 履歴からの再開の畳みと同じ綴り(history-event-line・時刻の印つき・recordSeq の順)。"
  (setv lines [])
  (for [event region.events]
    (<- line (| str None) (history-event-line event))
    (when (is-not line None)
      (.append lines line)))
  (.join "\n" lines))


(defk summarize-prompt-of [conversation-id region text]
  {:pre [(: conversation-id str) (: region SummaryRegion) (: text str)]
   :post [(: % str)]}
  "要約の指示 — **何を残し何を落とすかの定義点はここ 1 つ**(ACP agora-kinds.json conventions.stagedSummaries.keep はこの写し)。
   答えは日本語の散文 1 段落で、前置き・見出し・箇条書き・code block を含めない。長さの上限は置かない(要点が尽きたら終える)。"
  (+ f"あなたは会話 {conversation-id} の記録の一部(記録の service の recordSeq {region.from-seq}〜{region.to-seq}・出来事 {(len region.events)} 件)を、"
     "後の手番の agent が文脈として読む 1 段落の要約に縮める係です。\n\n"
     "残す情報: 決定(何を決めたか・理由)・進行中の仕事(何をどこまで進めたか・次の一手)・未解決の問い・道具の結果の要点"
     "(数値・file の path・commit の sha・id)・作った成果物の在処・失敗と回避。\n"
     "落とす情報: 道具の生の出力・繰り返しの経過・挨拶・推論の途中の言い直し。\n\n"
     "書式: 日本語の散文 1 段落。見出し・箇条書き・code block・前置き・後書きを付けない。固有の識別子(path・sha・id・URL)は逐語で残す。"
     "長さの上限は無いが、要点が尽きたら終える。\n\n"
     "--- 記録(古い順) ---\n"
     text
     "\n--- 記録の終わり ---\n"))


(defk summarize-paths-of [runs-dir job-id region started-ms]
  {:pre [(: runs-dir str) (: job-id str) (: region SummaryRegion) (: started-ms int)]
   :post [(: % dict)]}
  "1 区間の結末の 5 file: prompt・答え(JSON)・log・rc・pid。名は job・区間・**起こした時刻**で一意 — 区間ごとに別(前の区間の rc を次の区間の
   probe が読まない)で、同じ job の id が GC の後に再び走る時(便 4 の実弾 2026-09-16 16:23: 前の走の rc の file が残っていて probe が即 Exited と読み、
   まだ空の out を『答えが無い』と断った)も前の走の file を読まない。"
  (setv stem f"{runs-dir}/{job-id}-{region.from-seq}-{region.to-seq}-{started-ms}")
  {"prompt" f"{stem}.prompt.txt" "out" f"{stem}.out.json" "log" f"{stem}.log" "rc" f"{stem}.rc" "pid" f"{stem}.pid"})


(defk summarize-argv-of [claude-binary model paths]
  {:pre [(: claude-binary str) (: model str) (: paths dict)]
   :post [(: % tuple)]}
  "要約の起こし方の 1 点: sh の 1 行が自分の pid を書き、Claude Code を print mode(-p・答えは JSON 1 つ・session を残さない・
   道具なし・skill なし)で prompt の file から起こし、答えを out の file へ、stderr を log へ、終了コードを rc の file へ書く。
   binary・model・path は位置引数($0〜$6)で運び文字列に埋めない。agentd はこの process を待たない(verify と同じ形)。"
  #("/bin/sh" "-c"
    "echo $$ > \"$0\" && \"$1\" -p --model \"$2\" --output-format json --no-session-persistence --tools \"\" --disable-slash-commands < \"$3\" > \"$4\" 2>> \"$5\"; echo $? > \"$6\""
    (get paths "pid") claude-binary model (get paths "prompt") (get paths "out") (get paths "log") (get paths "rc")))


(defk claude-home-of [homes-root account]
  {:pre [(: homes-root str) (: account str)]
   :post [(: % str)]}
  "借りた札の claude の家 = <homes-root>/claude/<account の安全な綴り>(charter-with-grant が binding.config_dir に書く綴りと同じ)。"
  (setv safe-account (re.sub r"[^A-Za-z0-9._-]" "_" account))
  f"{homes-root}/claude/{safe-account}")


(defk summarize-env-of [token config-dir]
  {:pre [(: token str) (: config-dir str)]
   :post [(: % tuple)]}
  "要約の process に足す env: 借りた札(CLAUDE_CODE_OAUTH_TOKEN — charter-with-grant と同じ綴り)と家(CLAUDE_CONFIG_DIR)。"
  #(#(CLAUDE-OAUTH-TOKEN-ENV token) #("CLAUDE_CONFIG_DIR" config-dir)))


(defk summarize-handle-of [plan region paths principal started-ms regions-done]
  {:pre [(: plan SummarizePlan) (: region SummaryRegion) (: paths dict) (: principal str) (: started-ms int) (: regions-done int)]
   :post [(: % dict)]}
  "summarize の job の sessionHandle: stream{owner, name} は手番と同じ形(running-on-me の鍵)、summarize{…} は拾い直しの材料
   (R7: 正本は行 — 走っている区間・上端・model・資格・結末の file・済んだ区間の数)。札は載せない(秘密・再起動は借り直す)。"
  {"stream" {"owner" principal "name" plan.job-id}
   JOB-HANDLE-SUMMARIZE-KEY {"conversationId" plan.conversation-id
                             "until" plan.until
                             "from" region.from-seq
                             "to" region.to-seq
                             "sourceEvents" (len region.events)
                             "sourceBytes" region.source-bytes
                             "startedAtMs" started-ms
                             "deadlineSeconds" plan.deadline-seconds
                             "model" plan.model
                             "profile" plan.profile
                             "account" plan.account
                             "regionByteBudget" plan.region-byte-budget
                             "promptPath" (get paths "prompt")
                             "outPath" (get paths "out")
                             "logPath" (get paths "log")
                             "rcPath" (get paths "rc")
                             "pidPath" (get paths "pid")
                             "regionsDone" regions-done}})


(defk summarize-running-status-of [row handle]
  {:pre [(: row AcpRow) (: handle dict)]
   :post [(: % dict)]}
  "受けた summarize の status: committed の欄を写し、phase = Running と sessionHandle だけ書く(binding は触らない)。"
  (<- next dict (status-object-of row))
  (setv (get next "phase") PHASE-RUNNING)
  (setv (get next "sessionHandle") handle)
  next)


(defk in-flight-summarize-of [job-key job-namespace plan region paths pid lease-id started-ms regions-done]
  {:pre [(: job-key str) (: job-namespace str) (: plan SummarizePlan) (: region SummaryRegion) (: paths dict)
         (: pid (| int None)) (: lease-id (| str None)) (: started-ms int) (: regions-done int)]
   :post [(: % InFlightSummarize)]}
  "走らせている summarize の memory の状態を組む 1 点(受けた直後・次の区間・拾い直しも同じ形)。"
  (InFlightSummarize
    :job-key job-key :job-namespace job-namespace :job-id plan.job-id
    :conversation-id plan.conversation-id :until plan.until
    :from-seq region.from-seq :to-seq region.to-seq
    :source-events (len region.events) :source-bytes region.source-bytes
    :model plan.model :profile plan.profile :account plan.account :region-byte-budget plan.region-byte-budget
    :started-ms started-ms :deadline-seconds plan.deadline-seconds
    :pid pid :lease-id lease-id
    :prompt-path (get paths "prompt") :out-path (get paths "out") :log-path (get paths "log")
    :rc-path (get paths "rc") :pid-path (get paths "pid")
    :regions-done regions-done))


(defk summarize-plan-of-command [command]
  {:pre [(: command InFlightSummarize)]
   :post [(: % SummarizePlan)]}
  "走っている summarize から次の区間の plan(同じ会話・上端・上限・model・資格・期限)。"
  (SummarizePlan :job-id command.job-id :conversation-id command.conversation-id :until command.until
                 :region-byte-budget command.region-byte-budget :model command.model
                 :profile command.profile :account command.account :deadline-seconds command.deadline-seconds))


(defk summarize-of-handle [row default-deadline-seconds]
  {:pre [(: row AcpRow) (: default-deadline-seconds int)]
   :post [(: % (| InFlightSummarize None))]}
  "自分の Running の summarize の行から memory の状態を組み直す(再起動後の拾い直し — R7): sessionHandle.summarize の欄ちょうど。
   pid は file から(呼び手)・札は None(次の区間で借り直す)。欄が無い・形が違う = None(拾い直せない — 呼び手が結末なしで閉じる)。"
  (setv status row.status)
  (setv handle (if (isinstance status dict) (.get status "sessionHandle") None))
  (setv summarize (if (isinstance handle dict) (.get handle JOB-HANDLE-SUMMARIZE-KEY) None))
  (when (not (isinstance summarize dict))
    (return None))
  (setv conversation-id (.get summarize "conversationId"))
  (setv until (.get summarize "until"))
  (setv from-seq (.get summarize "from"))
  (setv to-seq (.get summarize "to"))
  (setv model (.get summarize "model"))
  (setv profile (.get summarize "profile"))
  (setv account (.get summarize "account"))
  (setv budget (.get summarize "regionByteBudget"))
  (setv prompt-path (.get summarize "promptPath"))
  (setv out-path (.get summarize "outPath"))
  (setv log-path (.get summarize "logPath"))
  (setv rc-path (.get summarize "rcPath"))
  (setv pid-path (.get summarize "pidPath"))
  (when (not (and (isinstance conversation-id str) (isinstance until int) (isinstance from-seq int) (isinstance to-seq int)
                  (isinstance model str) (isinstance profile str) (isinstance account str) (isinstance budget int)
                  (isinstance prompt-path str) (isinstance out-path str) (isinstance log-path str)
                  (isinstance rc-path str) (isinstance pid-path str)))
    (return None))
  (setv started (.get summarize "startedAtMs"))
  (setv deadline (.get summarize "deadlineSeconds"))
  (setv done (.get summarize "regionsDone"))
  (setv source-events (.get summarize "sourceEvents"))
  (setv source-bytes (.get summarize "sourceBytes"))
  (InFlightSummarize
    :job-key row.key :job-namespace row.namespace :job-id row.resource-id
    :conversation-id conversation-id :until until :from-seq from-seq :to-seq to-seq
    :source-events (if (and (isinstance source-events int) (not (isinstance source-events bool))) source-events 0)
    :source-bytes (if (and (isinstance source-bytes int) (not (isinstance source-bytes bool))) source-bytes 0)
    :model model :profile profile :account account :region-byte-budget budget
    :started-ms (if (and (isinstance started int) (not (isinstance started bool))) started row.created-at-ms)
    :deadline-seconds (if (and (isinstance deadline int) (not (isinstance deadline bool)) (> deadline 0)) deadline default-deadline-seconds)
    :pid None :lease-id None
    :prompt-path prompt-path :out-path out-path :log-path log-path :rc-path rc-path :pid-path pid-path
    :regions-done (if (and (isinstance done int) (not (isinstance done bool))) done 0)))


(defk summarize-output-of [text]
  {:pre [(: text (| str None))]
   :post [(: % (| SummaryOutcome str))]}
  "claude の print モード(--output-format json)の答え(result の 1 object)を読む: subtype が success で result が空でない文字列なら要約の本文・
   usage(input_tokens / output_tokens / cache_creation_input_tokens / cache_read_input_tokens → 契約 turn-record の usage の 4 欄)・
   modelUsage の鍵の model。JSON でない・object でない・誤り(is_error / subtype != success)・本文が空 = 理由の文。"
  (when (or (is text None) (= (.strip text) ""))
    (return "claude in print mode wrote no answer (the out file is empty)"))
  (setv document None)
  (try
    (setv document (json.loads text))
    (except [ValueError]
      (return f"claude in print mode answered non-JSON: {(cut (.strip text) 0 200) !r}")))
  (when (not (isinstance document dict))
    (return f"claude in print mode answered a JSON {(. (type document) __name__)}, not the result object"))
  (setv result (.get document "result"))
  (when (or (is (.get document "is_error") True) (and (in "subtype" document) (!= (.get document "subtype") "success")))
    (return f"claude in print mode answered an error ({(.get document "subtype")}): {(if (isinstance result str) (cut result 0 400) result) !r}"))
  (when (not (and (isinstance result str) (!= (.strip result) "")))
    (return "claude in print mode answered success without a result text"))
  (setv usage-raw (.get document "usage"))
  (setv usage None)
  (when (isinstance usage-raw dict)
    (setv pairs [#("input" "input_tokens") #("output" "output_tokens") #("cacheWrite" "cache_creation_input_tokens") #("cacheRead" "cache_read_input_tokens")])
    (setv built {})
    (for [[ours theirs] pairs]
      (setv value (.get usage-raw theirs))
      (when (and (isinstance value int) (not (isinstance value bool)) (>= value 0))
        (setv (get built ours) value)))
    (when (= (len built) 4)
      (setv usage built)))
  ;; 答えの modelUsage には要約を書いた model の他に CLI の下読み(haiku・出力 数十 token)も並ぶ(実弾 2026-09-16 16:36: 最初の鍵を採って
  ;; 行の model が claude-haiku-4-5 になった)— 要約を書いた model = **出力 token が最も多い model**。
  (setv model-usage (.get document "modelUsage"))
  (setv model None)
  (setv most -1)
  (when (isinstance model-usage dict)
    (for [[name entry] (.items model-usage)]
      (setv out-tokens (if (isinstance entry dict) (.get entry "outputTokens") None))
      (when (and (isinstance name str) (isinstance out-tokens int) (not (isinstance out-tokens bool)) (> out-tokens most))
        (setv most out-tokens)
        (setv model name))))
  (SummaryOutcome :text (.strip result) :usage usage :model model))


(defk summary-stream-id-of [from-seq to-seq]
  {:pre [(: from-seq int) (: to-seq int)]
   :post [(: % str)]}
  "要約の本文の stream の id(記録の service・streamKind summary)= summary#<from>-<to>(ACP agora-kinds.json kinds.summary の recordRef の綴り)。"
  f"{SUMMARY-STREAM-PREFIX}{from-seq}-{to-seq}")


(defk summary-body-of [text at model]
  {:pre [(: text str) (: at int) (: model str)]
   :post [(: % dict)]}
  "要約の本文の出来事(契約 record-service eventIn・kind summary・producerSeq 0・本文は text・model は見出しの欄)。"
  {"producerSeq" 0 "at" at "kind" SUMMARY-EVENT-KIND "text" text "model" model})


(defk summary-batch-of [conversation-id stream-id body started-at-ms node profile]
  {:pre [(: conversation-id str) (: stream-id str) (: body dict) (: started-at-ms int) (: node str) (: profile str)]
   :post [(: % RecordBatch)]}
  "要約の本文 1 つの appendEvents の要求(stream = streamKind summary・出来事は 1 つ・spool の鍵 = summary-<会話>-<stream>)。"
  (RecordBatch :spool-key f"summary-{conversation-id}-{stream-id}"
               :conversation-id conversation-id
               :stream (RecordStream :kind RECORD-STREAM-SUMMARY :stream-id stream-id :started-at-ms started-at-ms :node node :profile profile :attempt 1)
               :events #(body)))


(defk summary-row-id-of [conversation-id to-seq]
  {:pre [(: conversation-id str) (: to-seq int)]
   :post [(: % str)]}
  "kind summary の行の id(identityKey は [conversationId, to] — id はその写し・1 区間 1 行)。"
  f"sum-{conversation-id}-{to-seq}")


(defk summary-spec-of [command record-ref body-bytes sha256]
  {:pre [(: command InFlightSummarize) (: record-ref str) (: body-bytes int) (: sha256 str)]
   :post [(: % dict)]}
  "kind summary の spec(ACP agora-kinds.json kinds.summary の schema の写し): 会話・区間・本文の claim check・原文の数と byte・書いた job。"
  {SUMMARY-SPEC-CONVERSATION-KEY command.conversation-id
   SUMMARY-SPEC-FROM-KEY command.from-seq
   SUMMARY-SPEC-TO-KEY command.to-seq
   SUMMARY-SPEC-RECORD-REF-KEY record-ref
   "bytes" body-bytes
   "sha256" sha256
   "sourceEvents" command.source-events
   "sourceBytes" command.source-bytes
   "agentJobId" command.job-id})


(defk summary-status-of [model at usage]
  {:pre [(: model str) (: at int) (: usage (| dict None))]
   :post [(: % dict)]}
  "kind summary の status(state = current・書いた model・時刻・消費があれば usage)。"
  (setv status {"state" SUMMARY-STATE-CURRENT "model" model "at" at})
  (when (is-not usage None)
    (setv (get status "usage") usage))
  status)


(defk summarize-result-of [command regions-done ended-ms]
  {:pre [(: command InFlightSummarize) (: regions-done int) (: ended-ms int)]
   :post [(: % dict)]}
  "summarize の結末(agent-job の status.result): kind・会話・上端・書いた区間の数・最後の区間の to・始まり / 終わり。"
  {"kind" CHARTER-KIND-SUMMARIZE
   "conversationId" command.conversation-id
   "until" command.until
   "regions" regions-done
   "lastTo" command.to-seq
   "startedAtMs" command.started-ms
   "endedAtMs" ended-ms})


(defk summarize-empty-result-of [plan from-seq ended-ms]
  {:pre [(: plan SummarizePlan) (: from-seq int) (: ended-ms int)]
   :post [(: % dict)]}
  "要約する原文が無かった summarize の結末(regions 0 — 全部が要約済み・作り手の冪等の答え・条件ではない)。"
  {"kind" CHARTER-KIND-SUMMARIZE
   "conversationId" plan.conversation-id
   "until" plan.until
   "regions" 0
   "from" from-seq
   "endedAtMs" ended-ms})


(defk withdrawn-summarize-rows-of [rows node-name node-row-id principal]
  {:pre [(: rows tuple) (: node-name str) (: node-row-id (| str None)) (: principal str)]
   :post [(: % tuple)]}
  "Withdrawn の行のうち自分が受けた summarize の行(行の順のまま): 結びが自分を指す(binding-names-me)∧ sessionHandle.stream.owner == 自分 ∧
   sessionHandle.summarize が在る。手番の行(sessionId)は withdrawn-rows-of・verify は withdrawn-command-rows-of の持ち分。"
  (setv out [])
  (for [row rows]
    (setv status row.status)
    (when (and (isinstance status dict) (= (.get status "phase") PHASE-WITHDRAWN))
      (setv binding (.get status "binding"))
      (setv handle (.get status "sessionHandle"))
      (setv stream (if (isinstance handle dict) (.get handle "stream") None))
      (<- mine bool (binding-names-me binding node-name node-row-id))
      (when (and mine
                 (isinstance stream dict)
                 (= (.get stream "owner") principal)
                 (isinstance handle dict)
                 (isinstance (.get handle JOB-HANDLE-SUMMARIZE-KEY) dict))
        (.append out row))))
  (tuple out))


(defk without-summarize [state job-id]
  {:pre [(: state AgentdState) (: job-id str)]
   :post [(: % AgentdState)]}
  (replace state :summaries (tuple (lfor command state.summaries :if (!= command.job-id job-id) command))))


(defk with-summarize [state command]
  {:pre [(: state AgentdState) (: command InFlightSummarize)]
   :post [(: % AgentdState)]}
  "同じ job_id の summarize を置き換える(無ければ足す)。"
  (setv kept (lfor existing state.summaries :if (!= existing.job-id command.job-id) existing))
  (replace state :summaries (tuple (+ kept [command]))))


(defk in-flight-summarize-ids [state]
  {:pre [(: state AgentdState)]
   :post [(: % set)]}
  (set (lfor command state.summaries command.job-id)))


;; ---------------------------------------------------------------------------
;; 段 12 lane 12j 便 3(agora-redesign #233): 要約の契機と、再開が読む要約の判断
;; ---------------------------------------------------------------------------

(defk summarize-due [context trigger-tokens]
  {:pre [(: context (| dict None)) (: trigger-tokens int)]
   :post [(: % bool)]}
  "手番の終わりに要約の job を書くか: 材料の末尾で測った文脈の大きさ(DeltaBatch.context.tokens — 窓は見ない・operator の言葉は
   『0.5M token』)が宣言 summarize_trigger_tokens を**超えた**時だけ。宣言 0 = 契機を置かない・測れない手番 = 書かない。"
  (when (or (<= trigger-tokens 0) (is context None))
    (return False))
  (setv tokens (.get context "tokens"))
  (and (isinstance tokens int) (not (isinstance tokens bool)) (> tokens trigger-tokens)))


(defk turn-floor-of [events]
  {:pre [(: events tuple)]
   :post [(: % (| int None))]}
  "『今の手番より前』の区間の上端 = この手番の stream の最初の出来事の recordSeq − 1。stream にまだ出来事が無い(spool が届いていない)
   = None(この拍は契機を見送る — 次の手番の終わりに測り直す)。"
  (setv seqs (lfor event events event.record-seq))
  (if seqs (- (min seqs) 1) None))


(defk summarize-job-id-of [conversation-id until]
  {:pre [(: conversation-id str) (: until int)]
   :post [(: % str)]}
  "契機が書く summarize の agent-job の id(記録の綴り — 冪等の鍵は engine の identity (subject, inputs=[]))。"
  f"{SUMMARIZE-JOB-ID-PREFIX}{conversation-id}-{until}")


(defk summarize-job-spec-of [conversation-id until model]
  {:pre [(: conversation-id str) (: until int) (: model str)]
   :post [(: % dict)]}
  "summarize の agent-job の spec(ACP scheduling.json charterKind.summarize の runnerCharter): subject = 会話・inputs = []・
   charter = {kind summarize・agent_type claude・model・until}・reason = summarize。regionByteBudget は書かない(走行係の宣言の値)。"
  {"subject" conversation-id
   "inputs" []
   "charter" {CHARTER-KIND-KEY CHARTER-KIND-SUMMARIZE "agent_type" "claude" "model" model CHARTER-SUMMARIZE-UNTIL-KEY until}
   "reason" CHARTER-KIND-SUMMARIZE})


(defk summary-stream-id-of-ref [record-ref]
  {:pre [(: record-ref (| str None))]
   :post [(: % (| str None))]}
  "kind summary の行の spec.recordRef(record:<cid>/<streamId>)→ stream の id。形が違えば None。"
  (when (not (and (isinstance record-ref str) (.startswith record-ref RECORD-REF-PREFIX) (in "/" record-ref)))
    (return None))
  (setv stream-id (get (.split record-ref "/" 1) 1))
  (if stream-id stream-id None))


(defk history-summary-of [row text]
  {:pre [(: row AcpRow) (: text str)]
   :post [(: % (| HistorySummary None))]}
  "kind summary の行 + 記録の service から読んだ本文 → 再開に畳む要約。spec.from / to が整数でない・state が superseded・本文が空 = None。"
  (setv status (if (isinstance row.status dict) row.status {}))
  (when (= (.get status "state") SUMMARY-STATE-SUPERSEDED)
    (return None))
  (setv from-seq (.get row.spec SUMMARY-SPEC-FROM-KEY))
  (setv to-seq (.get row.spec SUMMARY-SPEC-TO-KEY))
  (when (not (and (isinstance from-seq int) (not (isinstance from-seq bool)) (isinstance to-seq int) (not (isinstance to-seq bool))
                  (!= (.strip text) "")))
    (return None))
  (setv model (.get status "model"))
  (setv at (.get status "at"))
  (HistorySummary :from-seq from-seq :to-seq to-seq
                  :at (if (and (isinstance at int) (not (isinstance at bool))) at row.created-at-ms)
                  :model (if (and (isinstance model str) model) model "?")
                  :text (.strip text)))


(defk summary-floor-of [summaries]
  {:pre [(: summaries tuple)]
   :post [(: % (| int None))]}
  "要約が覆う区間の終わり(to の最大)= 原文を読む下限。要約が無ければ None(今日どおり全部を原文で)。"
  (setv tos (lfor summary summaries summary.to-seq))
  (if tos (max tos) None))

