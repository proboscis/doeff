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
;;;     rehydrate-history-of・上限は AgentdSettings の 1 点)。
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
(import datetime [datetime timezone])
(import hashlib)
(import json)
(import re)

(import doeff_agents.sessionhost.acp.effects [
  AGENT-CAPABILITIES
  AGENT-INTERRUPT-CAPABILITY
  CHARTER-INTERRUPT-ESCALATION-KEY
  CONDITION-INTERRUPT-ESCALATION-UNDECLARED
  AGENT-SETTINGS
  AGENT-TYPE-LEASE-KIND
  CHARTER-SETTING-KEYS
  CONDITION-AGENT-SETTING-IGNORED
  NODE-CAPABILITIES-KEY
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
  HeadlineTurns
  HistoryFold
  INTERRUPT-ARM-INTERRUPT
  INTERRUPT-ARM-NONE
  InFlightJob
  InterruptRead
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
  NODE-GONE
  PHASE-BOUND
  PHASE-ENDED
  PHASE-RUNNING
  PHASE-WITHDRAWN
  PROFILE-BUDGET-UNIT-PERCENT
  PROFILE-OBSERVED-WINDOW-DEFAULT
  PROFILE-RETIRED
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
  RecordStream
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

(defk bound-to-me [row node-name]
  {:pre [(: row AcpRow) (: node-name str)]
   :post [(: % bool)]}
  "phase == Bound かつ status.binding.node == 自分。これ以外の条件で job を選ばない
   (法 agentd-holds-no-placement-judgment)。"
  (setv status row.status)
  (setv binding (if (isinstance status dict) (.get status "binding") None))
  (and (isinstance status dict)
       (= (.get status "phase") PHASE-BOUND)
       (isinstance binding dict)
       (= (.get binding "node") node-name)))


(defk job-rows-bound-to [rows node-name]
  {:pre [(: rows tuple) (: node-name str)]
   :post [(: % tuple)]}
  "list の行のうち自分に結ばれた行を、行の順のまま(優先も選択も無し)。"
  (setv out [])
  (for [row rows]
    (<- mine bool (bound-to-me row node-name))
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


(defk running-on-me [row node-name principal]
  {:pre [(: row AcpRow) (: node-name str) (: principal str)]
   :post [(: % bool)]}
  "phase == Running かつ status.binding.node == 自分かつ sessionHandle.stream.owner == 自分の
   principal — 自分が claim した job(再起動で memory を失っても行が覚えている)。job を選ぶ
   判定はこれと bound-to-me の 2 つだけで、どちらも binding.node == 自分の行に閉じる。"
  (setv status row.status)
  (setv binding (if (isinstance status dict) (.get status "binding") None))
  (setv handle (if (isinstance status dict) (.get status "sessionHandle") None))
  (setv stream (if (isinstance handle dict) (.get handle "stream") None))
  (and (isinstance status dict)
       (= (.get status "phase") PHASE-RUNNING)
       (isinstance binding dict)
       (= (.get binding "node") node-name)
       (isinstance stream dict)
       (= (.get stream "owner") principal)))


(defk job-rows-running-on [rows node-name principal]
  {:pre [(: rows tuple) (: node-name str) (: principal str)]
   :post [(: % tuple)]}
  "list の行のうち自分が持つ Running の行を、行の順のまま。"
  (setv out [])
  (for [row rows]
    (<- mine bool (running-on-me row node-name principal))
    (when mine
      (.append out row)))
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


(defk handle-owned-by [row node-name principal]
  {:pre [(: row AcpRow) (: node-name str) (: principal str)]
   :post [(: % (| str None))]}
  "自分が claim した行(binding.node == 自分 ∧ sessionHandle.stream.owner == 自分)の
   sessionHandle.sessionId。phase は問わない(Running でも Ended でも会話の session の記録)。
   自分の行でなければ None。"
  (setv status row.status)
  (setv binding (if (isinstance status dict) (.get status "binding") None))
  (setv handle (if (isinstance status dict) (.get status "sessionHandle") None))
  (setv stream (if (isinstance handle dict) (.get handle "stream") None))
  (if (and (isinstance binding dict)
           (= (.get binding "node") node-name)
           (isinstance stream dict)
           (= (.get stream "owner") principal))
      (do (<- sid (| str None) (session-id-of-handle row))
          sid)
      None))


(defk conversation-session-of [rows subject node-name principal]
  {:pre [(: rows tuple) (: subject str) (: node-name str) (: principal str)]
   :post [(: % (| str None))]}
  "会話(agent-job の spec.subject)→ その会話の最後の手番が使った session の id — 行から
   導く(memory を要らない): 自分が claim した同じ subject の行のうち createdAt が最新の
   sessionHandle.sessionId。無ければ None。"
  (setv found None)
  (setv found-at -1)
  (for [row rows]
    (when (= (.get row.spec "subject") subject)
      (<- sid (| str None) (handle-owned-by row node-name principal))
      (when (and (is-not sid None) (> row.created-at-ms found-at))
        (setv found sid)
        (setv found-at row.created-at-ms))))
  found)


(defk warm-candidate-of [plan rows subject node-name principal]
  {:pre [(: plan LaunchPlan) (: rows tuple) (: subject str) (: node-name str) (: principal str)]
   :post [(: % (| str None))]}
  "次の手番を送れるかもしれない session の id: affinity.predecessor(scheduler の名指し)が
   在ればそれ、無ければ会話の最後の手番の session(行から)。None = 候補なし(launch)。"
  (if (is-not plan.predecessor None)
      plan.predecessor
      (do (<- sid (| str None) (conversation-session-of rows subject node-name principal))
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
              NODE-CAPABILITY-INTERRUPT-KEY (get AGENT-INTERRUPT-CAPABILITY kind)}))


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
   候補が無い → launch /
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


(defk withdrawn-session-ids-of [rows node-name principal]
  {:pre [(: rows tuple) (: node-name str) (: principal str)]
   :post [(: % tuple)]}
  "Withdrawn の行のうち自分が claim していた行の session の id(手番の取り下げ — 走っている
   session を片付ける対象)。"
  (setv out [])
  (for [row rows]
    (setv status row.status)
    (when (and (isinstance status dict) (= (.get status "phase") PHASE-WITHDRAWN))
      (<- sid (| str None) (handle-owned-by row node-name principal))
      (when (and (is-not sid None) (not-in sid out))
        (.append out sid))))
  (tuple out))


(defk withdrawn-rows-of [rows node-name principal]
  {:pre [(: rows tuple) (: node-name str) (: principal str)]
   :post [(: % tuple)]}
  "Withdrawn の行のうち自分が claim していた行(行の順のまま)— 取り下げ = 走っている手番を
   止める合図(session は片付けない・idle の寿命は sessions-to-retire)。"
  (setv out [])
  (for [row rows]
    (setv status row.status)
    (when (and (isinstance status dict) (= (.get status "phase") PHASE-WITHDRAWN))
      (<- sid (| str None) (handle-owned-by row node-name principal))
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
   作った側)、conditions に Interrupted を 1 つ足す(既に在れば足さない)。"
  (setv next (dict status))
  (setv existing (.get status "conditions"))
  (setv conditions (if (isinstance existing list) (list existing) []))
  (when (not (any (gfor item conditions
                        (and (isinstance item dict) (= (.get item "type") CONDITION-INTERRUPTED)))))
    (<- condition dict (condition-of CONDITION-INTERRUPTED "agent-job withdrawn while the turn was running"))
    (.append conditions condition))
  (setv (get next "conditions") conditions)
  next)


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
   {sessionId, stream{owner, name}} だけ書く(binding は触らない — 書き手は scheduling)。"
  (<- next dict (status-object-of row))
  (setv (get next "phase") PHASE-RUNNING)
  (setv (get next "sessionHandle")
        {"sessionId" session-id
         "stream" {"owner" principal "name" session-id}})
  next)


(defk condition-of [condition-type reason]
  {:pre [(: condition-type str) (: reason str)]
   :post [(: % dict)]}
  "conditions の 1 項(改訂 R1-a の {type, status, reason})。"
  {"type" condition-type "status" "True" "reason" reason})


(defk ended-status-of [status result conditions]
  {:pre [(: status dict) (: result (| dict list str int float bool None)) (: conditions tuple)]
   :post [(: % dict)]}
  "手番の終わりの status: phase = Ended、result(あれば)、conditions は既存に足す。"
  (setv next (dict status))
  (setv (get next "phase") PHASE-ENDED)
  (when (is-not result None)
    (setv (get next "result") result))
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
    :model (if (and (isinstance model str) model) model "default")))


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


(defk history-event-line [event]
  {:pre [(: event RecordEvent)]
   :post [(: % (| str None))]}
  "会話の記録の service の出来事 1 つ → 「これまでの会話」の 1 項(kind ごとの畳み — 契約 record-service の
   eventKinds: text / tool_use / tool_result / system / error / user。frame は画面の断面で会話ではない・message は
   郵便で ACP の行から畳む〔service の mail の stream はまだ書き手が無い — 書き手が立つ便で郵便の畳みの座を移す〕
   = None)。本文が無い行(tombstone)は空の本文として畳む。"
  (<- stamp str (history-time-of event.at))
  (setv body (cond (isinstance event.text str) event.text
                   (isinstance event.summary str) event.summary
                   (isinstance event.input str) event.input
                   (is-not event.input None) (json.dumps event.input :ensure-ascii False)
                   (isinstance event.output str) event.output
                   (is-not event.output None) (json.dumps event.output :ensure-ascii False)
                   True ""))
  (setv tool (if (isinstance event.tool-name str) event.tool-name ""))
  (setv failed (if event.is-error "(誤り)" ""))
  (setv cut (if event.truncated "(切り詰め)" ""))
  (cond
    (= event.kind "text") f"[{stamp}] agent: {body}{cut}"
    (= event.kind "tool_use") f"[{stamp}] agent の道具 {tool}: {body}{cut}"
    (= event.kind "tool_result") f"[{stamp}] 道具の結果{failed}: {body}{cut}"
    (= event.kind "system") f"[{stamp}] system: {body}"
    (= event.kind "error") f"[{stamp}] 誤り: {body}"
    (= event.kind "user") f"[{stamp}] user: {body}{cut}"
    True None))


(defk history-headline-line [record]
  {:pre [(: record AcpRow)]
   :post [(: % (| str None))]}
  "ACP の turn-record の行 1 つ(見出しだけ・本文なし)→ 薄い再開の 1 項: 手番の出来事の数を kind ごとに数え、道具の名を
   並べる(本文の無い行を本文として扱わない — 中身は名乗れないので数と在処だけ)。見出しが無い行は None。"
  (setv status (if (isinstance record.status dict) record.status {}))
  (<- entries tuple (entries-of-status status))
  (setv counts {})
  (setv tools [])
  (setv newest 0)
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
    (setv at (.get entry "at"))
    (when (and (isinstance at int) (> at newest))
      (setv newest at)))
  (when (not counts)
    (return None))
  (<- stamp str (history-time-of (if (> newest 0) newest record.created-at-ms)))
  (setv parts (.join "・" (lfor [kind count] (.items counts) f"{kind} {count}")))
  (setv tool-names (.join ", " tools))
  (setv tool-note (if tools f"(道具: {tool-names})" ""))
  f"[{stamp}] 手番 {record.resource-id}(見出しだけ・本文は記録の service): {parts}{tool-note}")


(defk rehydrate-history-of [conversation-id messages source exclude budget fetched]
  {:pre [(: conversation-id str) (: messages tuple) (: source (| RecordedTurns HeadlineTurns)) (: exclude tuple)
         (: budget int) (: fetched dict)]
   :post [(: % HistoryFold)]}
  "会話の記録 → 履歴からの再開の手番の最初の本文に畳む「これまでの会話」(段 8q・R20・段 9f lane 9f-4)— 判断はここ 1 点:
   郵便(ACP の行 — spec.to か spec.from がこの会話・exclude = この手番の inputs は除く — 本文として別に届く)と手番の
   材料(source — 型で 2 つ: RecordedTurns = 会話の記録の service の本文〔設計 §2.4・before=latest から〕/ HeadlineTurns =
   ACP の見出しだけ〔service に届かない時の**薄い再開** — 本文は畳めないので手番ごとの数だけ・名乗る〕)を時刻順(同じ
   時刻は郵便が先)に並べ、会話へ届いた郵便(spec.to = この会話)ごとに手番に割る。UTF-8 で budget byte を超えたら
   **古い手番から要約せず落とし**、落とした手番と項の数と全文の在処を末尾に名乗る。最新の手番 1 つだけでも超えるなら
   その手番の先頭を落として末尾を残し、切った byte を名乗る。記録が無ければ text は空(薄い再開でも空)。"
  (setv thin (isinstance source HeadlineTurns))
  (setv items [])
  (setv order 0)
  (for [message messages]
    (setv spec message.spec)
    (setv message-id (.get spec "id" message.resource-id))
    (setv inbound (= (.get spec "to") conversation-id))
    (when (and (or inbound (= (.get spec "from") conversation-id)) (not-in message-id exclude))
      (setv at (.get spec "at"))
      (<- line str (history-message-line message (if (isinstance at int) at message.created-at-ms) fetched))
      (.append items #((if (isinstance at int) at message.created-at-ms) order inbound line))
      (setv order (+ order 1))))
  (if thin
      (for [record source.records]
        (when (= (.get record.spec "conversationId") conversation-id)
          (<- line (| str None) (history-headline-line record))
          (when (is-not line None)
            (setv status (if (isinstance record.status dict) record.status {}))
            (<- entries tuple (entries-of-status status))
            (setv first-at (next (gfor entry entries :if (isinstance (.get entry "at") int) (get entry "at"))
                                 record.created-at-ms))
            (.append items #(first-at order False line))
            (setv order (+ order 1)))))
      (for [event source.events]
        (<- line (| str None) (history-event-line event))
        (when (is-not line None)
          (.append items #(event.at order False line))
          (setv order (+ order 1)))))
  (setv groups [])
  (for [item (sorted items :key (fn [item] #((get item 0) (get item 1))))]
    (if (or (not groups) (get item 2))
        (.append groups [(get item 3)])
        (.append (get groups -1) (get item 3))))
  (when (not groups)
    (return (HistoryFold :text "" :kept-turns 0 :dropped-turns 0 :dropped-items 0 :size-bytes 0 :thin thin)))
  (setv header
        (if thin
            (+ f"これまでの会話(薄い再開・会話 {conversation-id}・古い順): 会話の記録の service に届かなかった"
               f"({source.reason})ため、手番の本文は無く ACP の見出し(出来事の数)だけです。郵便の本文は在ります。")
            (+ f"これまでの会話(会話の記録の service と ACP の郵便から組んだ写し・会話 {conversation-id}・古い順"
               (if source.complete "" "・会話の最初までは読んでいない") "):")))
  (setv where (+ f"全文は会話 {conversation-id} の記録 — 郵便は ACP の kind message(spec.to / spec.from = {conversation-id})"
                 f"の行・手番の本文は会話の記録の service(GET /v1/conversations/{conversation-id}/events)— にあります"))
  (setv blocks (lfor group groups (.join "\n" group)))
  (setv dropped-turns 0)
  (setv dropped-items 0)
  (setv footer "")
  (setv text (.join "\n\n" (+ [header] blocks)))
  (while (and (> (len (.encode text "utf-8")) budget) (> (- (len blocks) dropped-turns) 1))
    (setv dropped-items (+ dropped-items (len (get groups dropped-turns))))
    (setv dropped-turns (+ dropped-turns 1))
    (setv footer (+ f"(上限 {budget} byte を超えるため、古い手番 {dropped-turns} 件(出来事と郵便 {dropped-items} 件)を"
                    f"要約せずに落としました。{where})"))
    (setv text (.join "\n\n" (+ [header] (cut blocks dropped-turns None) [footer]))))
  (when (> (len (.encode text "utf-8")) budget)
    (setv newest (.encode (get blocks -1) "utf-8"))
    (setv notice (+ f"(上限 {budget} byte を超えるため、" (if (> dropped-turns 0) f"古い手番 {dropped-turns} 件(出来事と郵便 {dropped-items} 件)を落とし、" "")
                    f"最新の手番の先頭を落としました。{where})"))
    (setv fixed (+ (len (.encode header "utf-8")) (len (.encode notice "utf-8")) 4))
    (setv room (max 0 (- budget fixed)))
    (setv tail (.decode (cut newest (max 0 (- (len newest) room)) None) "utf-8" :errors "ignore"))
    (setv text (.join "\n\n" [header tail notice])))
  (HistoryFold :text text
               :kept-turns (- (len blocks) dropped-turns)
               :dropped-turns dropped-turns
               :dropped-items dropped-items
               :size-bytes (len (.encode text "utf-8"))
               :thin thin))


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
   (host.hy の session.resume の受理形 — resume 専用の欄はそのまま素通し)。lifecycle は
   運ばない — 新しい incarnation は蘇生元の行の lifecycle を継ぐ(launch.hy resume-session)。"
  (setv params {"session_id" predecessor
                "new_session_id" (.get charter "session_id")})
  (for [key ["prompt" "model" "effort" "mcp_servers" "session_env" "binding"
             "expected_result" "context_file" "launch_attribution"]]
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
   の二軸で host の fs-compose-home-view に家を組ませる。profile_dir は charter の binding
   の profile_dir(無ければ codex_home)— どちらも無ければ charter は変えない(借りた札を
   使う家が無い)。"
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
    (do
      (setv binding (.get charter "binding"))
      (setv binding (if (isinstance binding dict) binding {}))
      (setv profile-dir (or (.get binding "profile_dir") (.get binding "codex_home")))
      (if (or (is auth-json None) (not (isinstance profile-dir str)) (not profile-dir))
          #(next None)
          (do
            (setv auth-file f"{homes-root}/codex/{safe-account}/auth.json")
            (setv (get next "binding")
                  {"kind" "codex" "auth_file" auth-file "profile_dir" profile-dir})
            #(next auth-file))))
    True
    #(next None)))


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


(defk message-bodies-of [rows inputs fetched]
  {:pre [(: rows tuple) (: inputs tuple) (: fetched dict)]
   :post [(: % tuple)]}
  "inputs の id に対応する Message の本文(spec.body)を inputs の順に。戻り =
   #(bodies missing-ids)。鍵は契約の identityKey(spec.id)、無ければ行の resourceId。"
  (setv by-id {})
  (for [row rows]
    (setv spec-id (.get row.spec "id"))
    (setv (get by-id (if (isinstance spec-id str) spec-id row.resource-id)) row))
  (setv bodies [])
  (setv missing [])
  (for [input-id inputs]
    (setv row (.get by-id input-id))
    ;; 段 10f 便 1b: 本文を記録の service に置いた郵便は fetched(郵便 id → 本文)から引く。
    (setv body (cond (is row None) None
                     (isinstance (.get row.spec "body") str) (.get row.spec "body")
                     True (.get fetched input-id)))
    (if (isinstance body str)
        (.append bodies body)
        (.append missing input-id)))
  #((tuple bodies) (tuple missing)))


;; ---------------------------------------------------------------------------
;; 行の欄の写し(node の lease と観測)
;; ---------------------------------------------------------------------------

(defk node-row-named [rows name]
  {:pre [(: rows tuple) (: name str)]
   :post [(: % (| AcpRow None))]}
  "自分の名の生きた Node の行(gone は同じ名の生きた行ではない)。無ければ None。"
  (setv found None)
  (for [row rows]
    (setv state (if (isinstance row.status dict) (.get row.status "state") None))
    (when (and (is found None)
               (= (.get row.spec "name") name)
               (!= state NODE-GONE))
      (setv found row)))
  found)


(defk node-spec-of [settings]
  {:pre [(: settings AgentdSettings)]
   :post [(: % dict)]}
  "機体が名乗る自分の node の spec(R28・段 10 lane 10d・agora-redesign #85 — 既知の形 = kubelet の Node の自己登記):
   name = 機体の名・capacity = 宣言 file の [agentd].capacity・streamCapability = backend から導いた語・labels = 空
   (行を作る時 — 宣言は labels の表を持たない)。"
  {"name" settings.node-name
   "labels" {}
   "capacity" settings.node-capacity
   "streamCapability" settings.stream-capability})


(defk node-spec-declared [spec settings]
  {:pre [(: spec dict) (: settings AgentdSettings)]
   :post [(: % dict)]}
  "既に在る自分の node の行の spec を宣言へ揃えた形(R28): name・capacity・streamCapability は宣言から、labels は行の
   まま(行の labels は宣言の外の名乗り — 会社境界の boundary 等 — を運ぶので agentd は触らない・欠落 / 型違いは空)。
   宣言と一致していれば行の spec と等しい dict(呼び手は等しくない時だけ書く)。"
  (setv labels (.get spec "labels"))
  {"name" settings.node-name
   "labels" (if (isinstance labels dict) labels {})
   "capacity" settings.node-capacity
   "streamCapability" settings.stream-capability})


(defk node-status-with-lease [row settings now-ms sessions transcripts]
  {:pre [(: row AcpRow) (: settings AgentdSettings) (: now-ms int) (: sessions list) (: transcripts list)]
   :post [(: % dict)]}
  "agentd が書く欄だけを更新した node の status: lease{owner, heartbeatAt, expiresAt} と
   observations{streamCapability, sessions, transcripts, ownership?}(sessions = session-observations-of の列・
   transcripts = 終端の session のうち transcript がこの機体に残る会話の列〔段 8q〕・
   ownership = 起動の前に検めた所有の等級 {grade, proof} — 宣言が無ければ欄ごと書かない = 未観測・
   段 6 lane 6f)と capabilities(能力の表 — 段 10 lane 10e・capabilities-of)。state(scheduling の欄)は写すだけ。"
  (<- next dict (status-object-of row))
  (setv (get next "lease")
        {"owner" settings.principal
         "heartbeatAt" now-ms
         "expiresAt" (+ now-ms (* 1000 settings.node-lease-ttl-seconds))})
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


(defk profile-rows-held [active homes]
  {:pre [(: active tuple) (: homes tuple)]
   :post [(: % tuple)]}
  "生きている profile の行のうち、この機体に家(config dir)の在る profile の行 — 行の順のまま。
   判断はここ 1 点(段 8e lane 4j): 空なら観測の腕は usage を読まない(pool の pod は profile を
   1 つも持たない — 読み口が落ちる形で知るのではなく、家の在否で先に決める)。家は spec.name で
   引く(登録簿の名と契約の行の名は同じ綴り)。"
  (setv present (sfor home homes :if home.present home.name))
  (tuple (lfor row active :if (in (str (.get row.spec "name" row.resource-id)) present) row)))


(defk usage-by-profile [outcomes]
  {:pre [(: outcomes tuple)]
   :post [(: % dict)]}
  "usage の答えの列 → profile の名 → 答え(同じ名は最後の答え)。"
  (setv table {})
  (for [outcome outcomes]
    (setv (get table outcome.profile) outcome))
  table)


(defk observed-window-of [row]
  {:pre [(: row AcpRow)]
   :post [(: % str)]}
  "どの窓を観測するか — 判断はここ 1 点: spec.reset.everySeconds と周期が一致する provider の窓
   (effects.USAGE-WINDOW-SECONDS)、一致する窓が無ければ既定(5h)。"
  (setv reset (.get row.spec "reset"))
  (setv every (if (isinstance reset dict) (.get reset "everySeconds") None))
  (setv found None)
  (for [[name seconds] (.items USAGE-WINDOW-SECONDS)]
    (when (and (is found None) (isinstance every int) (= seconds every))
      (setv found name)))
  (if (is found None) PROFILE-OBSERVED-WINDOW-DEFAULT found))


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
      (<- window-name str (observed-window-of row))
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


(defk profile-observed-changed [row observed]
  {:pre [(: row AcpRow) (: observed dict)]
   :post [(: % bool)]}
  "committed の status.observed と違うか(同じなら書かない — 断面が同じ拍は書きを起こさない)。"
  (<- status dict (status-object-of row))
  (!= (.get status "observed") observed))


(defk profile-status-with-observed [row observed]
  {:pre [(: row AcpRow) (: observed dict)]
   :post [(: % dict)]}
  "agentd が書く欄だけを更新した profile の status: committed の status(state・conditions は
   他の書き手の欄 — 落とすと engine が断る)を写し、observed を据える。"
  (<- next dict (status-object-of row))
  (setv (get next "observed") observed)
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


(defk record-create-verdict [outcome started-ms now-ms deadline-seconds]
  {:pre [(: outcome (| Written Conflict Refused)) (: started-ms int) (: now-ms int)
         (: deadline-seconds (| int float))]
   :post [(: % str)]}
  "turn-record の create の結末 → 腕の状態(閉語彙 RecordCreateState・段 9p・agora-redesign #76)。
   Written = 作れた / Conflict = 既に在る(同じ鍵は冪等 — 拾い直し)→ created。Refused は 2 種:
   決定論的(record-refusal-deterministic)→ given-up / 頭が答えない → 期限(手番の始まり started-ms から
   deadline-seconds)の内なら pending・越えたら given-up。判断はこの 1 点(呼び手は結果の語で分岐しない)。"
  (if (or (isinstance outcome Written) (isinstance outcome Conflict))
      RECORD-CREATE-CREATED
      (do
        (<- deterministic bool (record-refusal-deterministic outcome.status))
        (cond
          deterministic RECORD-CREATE-GIVEN-UP
          (>= (- now-ms started-ms) (* 1000 deadline-seconds)) RECORD-CREATE-GIVEN-UP
          True RECORD-CREATE-PENDING))))


(defk record-create-applied [job outcome now-ms deadline-seconds]
  {:pre [(: job InFlightJob) (: outcome (| Written Conflict Refused)) (: now-ms int)
         (: deadline-seconds (| int float))]
   :post [(: % InFlightJob)]}
  "create の結末を job に写す(段 9p): 腕の状態(record-create-verdict)・最後に撃った拍・最後の断りの文。
   given-up になった拍は condition RecordUnavailable(理由 = 最後の断りと経過)を pending-conditions に足す
   (Ended の書きに乗る — 同じ型を二度足さない)。created は行の image を持たない(次の追記が鍵から読む)。"
  (<- verdict str (record-create-verdict outcome job.started-ms now-ms deadline-seconds))
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
   落とす … ただし必須 4 欄は素材が無ければ 0(素材の無い message は呼び手が渡さない)。"
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
  "usage の和(model は最後に見た綴り)。"
  (setv out (if (is total None) {} (dict total)))
  (for [key ["input" "output" "cacheWrite" "cacheRead" "cacheWrite5m" "cacheWrite1h"]]
    (when (in key part)
      (setv (get out key) (+ (int (.get out key 0)) (int (get part key))))))
  (when (in "model" part)
    (setv (get out "model") (get part "model")))
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


(defk claude-deltas-of [records job-id seq-start at streamed]
  {:pre [(: records tuple) (: job-id str) (: seq-start int) (: at int) (: streamed bool)]
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
   1 点で導く(段 9f lane 9f-4 — 本文は ACP へ写さない)。"
  (setv frames [])
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
        (setv seq (+ seq 1))))
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
  (<- entries tuple (entries-of-bodies (tuple bodies)))
  (DeltaBatch :frames (tuple frames) :entries entries :bodies (tuple bodies) :usage usage
              :next-seq seq :model model
              :context (if (is context-tokens None) None {"tokens" context-tokens "window" context-window})
              :interrupt-reads (tuple reads)))


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


(defk events-to-deltas [agent-type text job-id seq-start at]
  {:pre [(: agent-type str) (: text str) (: job-id str) (: seq-start int) (: at int)]
   :post [(: % DeltaBatch)]}
  "headless の events file の追記(stdout の行)→ TurnDelta の frame と entries(契約の
   種類の閉語彙 text / tool_use / tool_result / usage)。claude = stream-json の行、codex =
   app-server の JSON-RPC の通知。未知の kind は空。"
  (<- records tuple (parse-json-lines text))
  (cond
    (= agent-type "claude")
    (do (<- claude-batch DeltaBatch (claude-deltas-of records job-id seq-start at True))
        claude-batch)
    (= agent-type "codex")
    (do (<- codex-batch DeltaBatch (codex-event-deltas-of records job-id seq-start at))
        codex-batch)
    True (DeltaBatch :frames #() :entries #() :usage None :next-seq seq-start :model None)))


(defk deltas-of [agent-type source text job-id seq-start at]
  {:pre [(: agent-type str) (: source str) (: text str) (: job-id str) (: seq-start int)
         (: at int)]
   :post [(: % DeltaBatch)]}
  "実況の材料の追記(text)→ kind と材料の種類(閉語彙 effects.StreamSource)別の TurnDelta の
   frame と entries: events = headless の stdout の行(events-to-deltas)、transcript = tui の
   transcript の行。未知の kind は空。"
  (when (= source STREAM-SOURCE-EVENTS)
    (<- streamed DeltaBatch (events-to-deltas agent-type text job-id seq-start at))
    (return streamed))
  (<- records tuple (parse-json-lines text))
  (cond
    (= agent-type "claude")
    (do (<- claude-batch DeltaBatch (claude-deltas-of records job-id seq-start at False))
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
   window(変わった行だけ: event-window の post-image)/ idle → none。"
  (<- periodic bool (due state.last-resync-ms now-ms settings.watch-resync-seconds))
  (cond
    (or periodic (in signal.kind #{"gap" "closed"})) LIST-MODE-FULL
    (= signal.kind "changed") LIST-MODE-WINDOW
    True LIST-MODE-NONE))


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
      (JobOutcome :ended False :result None :conditions #())
      (do
        (setv conditions [])
        (when (!= view.status "done")
          (setv cause (or view.terminal-cause {}))
          (setv category (.get cause "category"))
          (setv reason (.get cause "reason"))
          (<- failed dict (condition-of "SessionFailed"
                                        (+ f"session {view.status}"
                                           (if (isinstance category str) f": {category}" "")
                                           (if (isinstance reason str) f" ({reason})" ""))))
          (.append conditions failed))
        (JobOutcome :ended True :result view.result-payload :conditions (tuple conditions)))))


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


(defk in-flight-ids [state]
  {:pre [(: state AgentdState)]
   :post [(: % set)]}
  (set (gfor job state.jobs job.job-id)))
