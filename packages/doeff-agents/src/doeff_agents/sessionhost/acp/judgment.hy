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
;;;   * transcript の行 → TurnDelta の frame と turn-record の entries(契約
;;;     docs/contracts/turn-delta.json / agora-kinds.json の欄へ写す)。
;;;   * 温かい session(R10): 会話 → 生きている session の対応は行(agent-job の subject と
;;;     sessionHandle)から導き、Bound の job の起こし方は next-arm-for-job(launch | send |
;;;     resume | defer)の 1 点、手番の終わりは job-step-of の turn-end(host が刻んだ
;;;     turn_ended_at × 手番の始まりの下限 × 記録の進み)、idle の寿命は sessions-to-retire。
;;;   * 会話の引き継ぎ(段 8q・R20): session の会話・手番・家は起こす時に launch_attribution へ刻み
;;;     (session-attribution-of)、器の眺めから読む(attribution-of-view)— 回収される agent-job の行から
;;;     導かない。cache(温かい send / --resume)を保つのは同じ機体 ∧ 同じ家の時だけで、家か機体が違えば
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

(import dataclasses [replace])
(import datetime [datetime timezone])
(import json)
(import re)

(import doeff_agents.sessionhost.acp.effects [
  AGENT-TYPE-LEASE-KIND
  AGENTD-PRINCIPAL
  AGORA-KINDS-NAMESPACE
  ATTRIBUTION-AGENTD-KEY
  AgentdSettings
  AgentdState
  AcpRow
  ArmChoice
  BACKEND-HEADLESS
  CLAUDE-OAUTH-TOKEN-ENV
  CONDITION-INTERRUPTED
  DeltaBatch
  ENTRY-KIND-ERROR
  ENTRY-KIND-SYSTEM
  ENTRY-KIND-TEXT
  ENTRY-KIND-TOOL-RESULT
  ENTRY-KIND-TOOL-USE
  ENTRY-SUMMARY-MAX-CHARS
  ENTRY-TEXT-MAX-CHARS
  HistoryFold
  INTERRUPT-ARM-INTERRUPT
  INTERRUPT-ARM-NONE
  InFlightJob
  JOB-INTERRUPTS-DELIVERED-KEY
  JOB-INTERRUPTS-KEY
  JOB-STEP-FAIL-MISSING
  JOB-STEP-OBSERVE
  JOB-STEP-RECORD-END
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
  RECORD-APPEND-OK
  RECORD-BATCH-MAX-EVENTS
  RECORD-STATUS-MALFORMED
  RECORD-STREAM-TURN
  RecordAppended
  RecordBatch
  RecordConflicted
  RecordStream
  RecordUnsent
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
  USAGE-WINDOW-FULL-PERCENT
  USAGE-WINDOW-SECONDS
  WatchAdvance])


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
   session は生かす)/ それ以外 → observe。memory に在る job も無い job も同じ 1 点で決める。"
  (cond
    (is view None) JOB-STEP-FAIL-MISSING
    (in view.status SESSION-TERMINAL-STATUSES) JOB-STEP-RECORD-END
    (and (= view.lifecycle LIFECYCLE-MULTI-TURN)
         (is-not view.turn-ended-at-ms None)
         (> view.turn-ended-at-ms floor-ms)
         progressed)
    JOB-STEP-TURN-END
    True JOB-STEP-OBSERVE))


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
  "器に在り、終端でない。"
  (and (is-not view None) (not-in view.status SESSION-TERMINAL-STATUSES)))


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


(defk home-key-of [plan]
  {:pre [(: plan LaunchPlan)]
   :post [(: % dict)]}
  "job が走る家の鍵(段 8q・R20): 預かり所の account(借りる時の家 = <homes-root>/<種類>/<account>)と
   charter の binding(借りない時の家・codex の profile_dir)の対。同じ鍵 = 同じ家(homes-root は機体に
   1 つ)。欠けた欄は None のまま(発明しない)。"
  (setv binding (.get plan.charter "binding"))
  {"account" plan.account
   "binding" (if (isinstance binding dict) binding None)})


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
  (<- home dict (home-key-of plan))
  {"conversationId" subject
   "agentJobId" job-id
   "account" plan.account
   "home" home
   "arm" arm})


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


(defk next-arm-for-job [candidate view home]
  {:pre [(: candidate (| str None)) (: view (| SessionView None)) (: home dict)]
   :post [(: % ArmChoice)]}
  "Bound の job の起こし方(閉語彙 effects.NextArm)— 判断はここ 1 点(R10 / R20)。candidate = 会話の前の
   session(affinity.predecessor か会話の最後の手番の session — warm-candidate-of)、view = その器の眺め、
   home = この job が走る家(home-key-of)。cache(温かい session / transcript)を保つのは同じ機体 ∧ 同じ家の
   時だけで、機体か家(profile の家)が変わる時は cache の失効を受け入れて 履歴から再開する(ACP の全史から)
   (operator 決定 2026-09-13 #54 逐語 \"i want cache kept when both machine and a profile is not changed. in
   other cases, i think i need to accept the fact that cache gets invalidated\"):
   候補が無い → launch /
   候補が生きて idle ∧ 同じ家 → send(温かい)/
   候補が生きて idle ∧ 家が違う → 候補を片付けて rehydrate(profile を変えた手番 — 失効した cache の器を残さない)/
   候補が生きていて idle でない → defer(手番の途中 — 走っている手番に本文を積まない)/
   候補が器に登記されて終端 ∧ 同じ家 → resume(温かい session が片付いた後も cache を保つ --resume)/
   それ以外(候補が器に無い = 別の機体・器の行が消えた / 終端だが家が違う)→ rehydrate(ACP の会話の記録を
   最初の本文に畳む)。"
  (<- alive bool (session-alive view))
  (<- idle bool (session-idle view))
  (setv same False)
  (when (isinstance view SessionView)
    (<- in-home bool (session-in-home view home))
    (setv same in-home))
  (cond
    (is candidate None) (ArmChoice :arm NEXT-ARM-LAUNCH :source None :retire None)
    (and idle same) (ArmChoice :arm NEXT-ARM-SEND :source candidate :retire None)
    idle (ArmChoice :arm NEXT-ARM-REHYDRATE :source None :retire candidate)
    alive (ArmChoice :arm NEXT-ARM-DEFER :source candidate :retire None)
    same (ArmChoice :arm NEXT-ARM-RESUME :source candidate :retire None)
    True (ArmChoice :arm NEXT-ARM-REHYDRATE :source None :retire None)))


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


(defk incarnation-charter-of [plan choice session-id bodies history attribution backend-kind lease homes-root]
  {:pre [(: plan LaunchPlan) (: choice ArmChoice) (: session-id str) (: bodies tuple) (: history str)
         (: attribution dict) (: backend-kind str) (: lease (| LeaseGrant None)) (: homes-root str)]
   :post [(: % tuple)]}
  "起こす session の charter を組む 1 点(launch / resume / rehydrate — send は起こさない): 鋳造した id →
   (rehydrate)これまでの会話 → (headless の起こす腕)郵便の本文 → 借りた札の家 → 帰属。戻り =
   #(charter auth-file-or-None)(codex の借りた auth.json の置き場 — 書くのは呼び手の effect)。"
  (<- with-id dict (charter-with-session-id plan.charter session-id))
  (setv charter with-id)
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
;; 履歴からの再開(段 8q・R20): ACP の会話の記録 → 「これまでの会話」
;; ---------------------------------------------------------------------------

(defk history-time-of [at]
  {:pre [(: at int)]
   :post [(: % str)]}
  "記録の時刻(epoch ms)の人の読む綴り(UTC・秒)。"
  (.strftime (datetime.fromtimestamp (/ at 1000) :tz timezone.utc) "%Y-%m-%dT%H:%M:%SZ"))


(defk history-message-line [message at]
  {:pre [(: message AcpRow) (: at int)]
   :post [(: % str)]}
  "郵便 1 通 → 「これまでの会話」の 1 項(差出人 → 宛先(種類): 本文)。"
  (<- stamp str (history-time-of at))
  (setv spec message.spec)
  (setv sender (.get spec "from" "?"))
  (setv to (.get spec "to" "?"))
  (setv kind (.get spec "kind" "note"))
  (setv body (.get spec "body"))
  (setv text (if (isinstance body str) body ""))
  f"[{stamp}] {sender} → {to}({kind}): {text}")


(defk history-entry-line [entry at]
  {:pre [(: entry dict) (: at int)]
   :post [(: % (| str None))]}
  "turn-record の出来事 1 つ → 「これまでの会話」の 1 項(kind ごとの畳み — 契約 turn-record の entries の
   閉語彙: text / tool_use / tool_result / system / error。frame は tui の画面の断面で会話ではないので畳まない
   = None)。"
  (<- stamp str (history-time-of at))
  (setv kind (.get entry "kind"))
  (setv text (.get entry "text"))
  (setv summary (.get entry "summary"))
  (setv body (cond (isinstance text str) text (isinstance summary str) summary True ""))
  (setv tool (.get entry "toolName" ""))
  (setv failed (if (.get entry "isError") "(誤り)" ""))
  (cond
    (= kind "text") f"[{stamp}] agent: {body}"
    (= kind "tool_use") f"[{stamp}] agent の道具 {tool}: {body}"
    (= kind "tool_result") f"[{stamp}] 道具の結果{failed}: {body}"
    (= kind "system") f"[{stamp}] system: {body}"
    (= kind "error") f"[{stamp}] 誤り: {body}"
    True None))


(defk rehydrate-history-of [conversation-id messages records exclude budget]
  {:pre [(: conversation-id str) (: messages tuple) (: records tuple) (: exclude tuple) (: budget int)]
   :post [(: % HistoryFold)]}
  "ACP の会話の記録 → 履歴からの再開の手番の最初の本文に畳む「これまでの会話」(段 8q・R20)— 判断はここ 1 点:
   郵便(spec.to か spec.from がこの会話・exclude = この手番の inputs は除く — 本文として別に届く)と
   turn-record(spec.conversationId がこの会話)の entries を時刻順(同じ時刻は郵便が先)に並べ、会話へ
   届いた郵便(spec.to = この会話)ごとに手番に割る。UTF-8 で budget byte を超えたら**古い手番から要約せず
   落とし**、落とした手番と項の数と全文の在処(ACP の会話の記録)を末尾に名乗る。最新の手番 1 つだけでも
   超えるならその手番の先頭を落として末尾を残し、切った byte を名乗る。記録が無ければ text は空。"
  (setv items [])
  (setv order 0)
  (for [message messages]
    (setv spec message.spec)
    (setv message-id (.get spec "id" message.resource-id))
    (setv inbound (= (.get spec "to") conversation-id))
    (when (and (or inbound (= (.get spec "from") conversation-id)) (not-in message-id exclude))
      (setv at (.get spec "at"))
      (<- line str (history-message-line message (if (isinstance at int) at message.created-at-ms)))
      (.append items #((if (isinstance at int) at message.created-at-ms) order inbound line))
      (setv order (+ order 1))))
  (for [record records]
    (when (= (.get record.spec "conversationId") conversation-id)
      (setv status (if (isinstance record.status dict) record.status {}))
      (setv entries (.get status "entries"))
      (for [entry (if (isinstance entries list) entries [])]
        (when (isinstance entry dict)
          (setv at (.get entry "at"))
          (setv stamp (if (isinstance at int) at record.created-at-ms))
          (<- line (| str None) (history-entry-line entry stamp))
          (when (is-not line None)
            (.append items #(stamp order False line))
            (setv order (+ order 1)))))))
  (setv groups [])
  (for [item (sorted items :key (fn [item] #((get item 0) (get item 1))))]
    (if (or (not groups) (get item 2))
        (.append groups [(get item 3)])
        (.append (get groups -1) (get item 3))))
  (when (not groups)
    (return (HistoryFold :text "" :kept-turns 0 :dropped-turns 0 :dropped-items 0 :size-bytes 0)))
  (setv header f"これまでの会話(ACP の記録から組んだ写し・会話 {conversation-id}・古い順):")
  (setv where (+ f"全文は ACP の会話 {conversation-id} の記録 — kind message(spec.to / spec.from = {conversation-id})"
                 f"と kind turn-record(spec.conversationId = {conversation-id})の行 — にあります"))
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
               :size-bytes (len (.encode text "utf-8"))))


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


(defk message-bodies-of [rows inputs]
  {:pre [(: rows tuple) (: inputs tuple)]
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
    (setv body (if (is row None) None (.get row.spec "body")))
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


(defk node-status-with-lease [row settings now-ms sessions transcripts]
  {:pre [(: row AcpRow) (: settings AgentdSettings) (: now-ms int) (: sessions list) (: transcripts list)]
   :post [(: % dict)]}
  "agentd が書く欄だけを更新した node の status: lease{owner, heartbeatAt, expiresAt} と
   observations{streamCapability, sessions, transcripts, ownership?}(sessions = session-observations-of の列・
   transcripts = 終端の session のうち transcript がこの機体に残る会話の列〔段 8q〕・
   ownership = 起動の前に検めた所有の等級 {grade, proof} — 宣言が無ければ欄ごと書かない = 未観測・
   段 6 lane 6f)。state(scheduling の欄)は写すだけ。"
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
   始まりの下限(送った時刻・拾い直しは行の createdAt)。"
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
    :pending-conditions pending))


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
  "新しい出来事の seq が行の採番(floor = 行の次の seq)より小さければ、floor から順に振り直す
   (拾い直した job は 0 から数え直すので行の seq と衝突する)。衝突しなければそのまま。"
  (when (not entries)
    (return entries))
  (setv first-seq (.get (get entries 0) "seq"))
  (when (and (isinstance first-seq int) (>= first-seq floor))
    (return entries))
  (setv out [])
  (setv seq floor)
  (for [entry entries]
    (setv copy (dict entry))
    (setv (get copy "seq") seq)
    (.append out copy)
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
  "行の上限で古い出来事を落とした印(kind system・truncated・dropped)— 落とした最古の seq と
   最新の at を持ち、列の先頭に立つ。"
  {"seq" seq "at" at "kind" ENTRY-KIND-SYSTEM
   "text" f"行の上限で古い出来事 {dropped} 件を落とした(記録はこの先から)"
   "truncated" True "dropped" dropped})


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
  "行の status に出来事を追記した status(post-image): entries = 行の entries + new-entries を
   行の上限(TURN-RECORD-ENTRIES-BYTE-BUDGET)に収めたもの。他の欄は写す。"
  (setv next (dict status))
  (<- existing tuple (entries-of-status status))
  (<- bounded tuple (entries-within-budget (+ existing new-entries) TURN-RECORD-ENTRIES-BYTE-BUDGET))
  (setv (get next "entries") (list bounded))
  next)


(defk turn-record-ended-status [status usage entries]
  {:pre [(: status dict) (: usage (| dict None)) (: entries tuple)]
   :post [(: % dict)]}
  "手番の終わりの turn-record の status: 残りの出来事(entries)を行の entries に**追記**した上で
   state = ended・usage(素材があれば)。行の entries は落とさない(手番の間に追記した出来事が正本)。"
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
  "追記の結末 → 計器の語(ok | conflict | error — effects.RecordAppendWord の閉語彙)。"
  (cond
    (isinstance outcome RecordAppended) RECORD-APPEND-OK
    (isinstance outcome RecordConflicted) RECORD-APPEND-CONFLICT
    True RECORD-APPEND-ERROR))


(defk record-release-of [outcome]
  {:pre [(: outcome (| RecordAppended RecordConflicted RecordUnsent))]
   :post [(: % bool)]}
  "spool の file を消してよいか: 受理(ok)と 409(同じ鍵で違う本文 — 再送しても積めない・赤の計器で名乗る)は消す、
   送れなかった(error)は残して再送する。"
  (not (isinstance outcome RecordUnsent)))


(defk record-halts-flush [outcome]
  {:pre [(: outcome (| RecordAppended RecordConflicted RecordUnsent))]
   :post [(: % bool)]}
  "spool の再送をこの拍で止めるか: 送れなさが系の側(届かない・5xx・札・窓)なら止める(後ろの batch も同じ理由で
   送れない)。batch だけの断り(400 malformed)は残して次の batch へ進む(1 つの壊れた batch で後ろを塞がない — spool の
   深さの計器が名乗る)。"
  (and (isinstance outcome RecordUnsent) (!= outcome.status RECORD-STATUS-MALFORMED)))


(defk record-flush-due [state now-ms settings]
  {:pre [(: state AgentdState) (: now-ms int) (: settings AgentdSettings)]
   :post [(: % bool)]}
  "spool を読んで送る拍か: 送れている間(backoff なし)は毎拍 — 出来事を読んだ拍の終わりに送る。送れなかった後は
   record_retry_seconds の周期(届かない service へ拍ごとに撃って loop を塞がない)。"
  (<- period-passed bool (due state.record-backoff-ms now-ms settings.record-retry-seconds))
  period-passed)


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


(defk clipped [text limit]
  {:pre [(: text str) (: limit int)]
   :post [(: % tuple)]}
  "本文を limit 字で切る: #(text truncated)。"
  (if (> (len text) limit) #((cut text 0 limit) True) #(text False)))


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


(defk entry-of-body [body]
  {:pre [(: body dict)]
   :post [(: % dict)]}
  "本文(契約 record-service eventIn の形 — 会話の記録の service へ切らずに運ぶ出来事)→ turn-record の entry(ACP の欄)。
   切り詰めの定義点はここ 1 つ: text / system / error = text を ENTRY-TEXT-MAX-CHARS、tool_use / tool_result = 入力 /
   出力の要約(summary-of)を ENTRY-SUMMARY-MAX-CHARS(切れば truncated)。seq = 本文の producerSeq(採番は 1 点)。"
  (setv kind (get body "kind"))
  (setv entry {"seq" (get body "producerSeq") "at" (get body "at") "kind" kind})
  (setv truncated False)
  (if (in kind #{ENTRY-KIND-TOOL-USE ENTRY-KIND-TOOL-RESULT})
      (do
        (<- whole str (summary-of (.get body (if (= kind ENTRY-KIND-TOOL-USE) "input" "output")) 1000000000))
        (<- summary-clip tuple (clipped whole ENTRY-SUMMARY-MAX-CHARS))
        (when (= kind ENTRY-KIND-TOOL-USE)
          (setv (get entry "toolName") (get body "toolName")))
        (setv (get entry "summary") (get summary-clip 0))
        (setv truncated (get summary-clip 1)))
      (do
        (<- text-clip tuple (clipped (get body "text") ENTRY-TEXT-MAX-CHARS))
        (setv (get entry "text") (get text-clip 0))
        (setv truncated (get text-clip 1))))
  (when (in "model" body)
    (setv (get entry "model") (get body "model")))
  (when (in "toolUseId" body)
    (setv (get entry "toolUseId") (get body "toolUseId")))
  (when (is (.get body "isError") True)
    (setv (get entry "isError") True))
  (when truncated
    (setv (get entry "truncated") True))
  entry)


(defk entries-of-bodies [bodies]
  {:pre [(: bodies tuple)]
   :post [(: % tuple)]}
  "本文の列 → turn-record の entries の列(順と seq はそのまま・切り詰めは entry-of-body の 1 点)。"
  (setv out [])
  (for [body bodies]
    (<- entry dict (entry-of-body body))
    (.append out entry))
  (tuple out))


(defk text-entry [seq at text model]
  {:pre [(: seq int) (: at int) (: text str) (: model (| str None))]
   :post [(: % dict)]}
  "assistant の本文の 1 block → entry(kind text・上限 ENTRY-TEXT-MAX-CHARS・切れば truncated)= text-body を切った形。"
  (<- body dict (text-body seq at text model))
  (<- entry dict (entry-of-body body))
  entry)


(defk tool-use-entry [seq at tool-id name input]
  {:pre [(: seq int) (: at int) (: tool-id str) (: name str)
         (: input (| dict list str int float bool None))]
   :post [(: % dict)]}
  "道具の呼び出し → entry(kind tool_use・toolName・toolUseId・summary = 入力の要約 ≤ ENTRY-SUMMARY-MAX-CHARS)
   = tool-use-body を切った形。"
  (<- body dict (tool-use-body seq at tool-id name input))
  (<- entry dict (entry-of-body body))
  entry)


(defk tool-result-entry [seq at tool-id output is-error]
  {:pre [(: seq int) (: at int) (: tool-id str)
         (: output (| dict list str int float bool None)) (: is-error bool)]
   :post [(: % dict)]}
  "道具の結果 → entry(kind tool_result・toolUseId・summary = 出力の要約 ≤ ENTRY-SUMMARY-MAX-CHARS・isError)
   = tool-result-body を切った形。"
  (<- body dict (tool-result-body seq at tool-id output is-error))
  (<- entry dict (entry-of-body body))
  entry)


(defk note-entry [seq at kind text]
  {:pre [(: seq int) (: at int) (: kind str) (: text str)]
   :post [(: % dict)]}
  "器の出来事(kind system)と手番の誤り(kind error)→ entry(text ≤ ENTRY-TEXT-MAX-CHARS)= note-body を切った形。"
  (<- body dict (note-body seq at kind text))
  (<- entry dict (entry-of-body body))
  entry)


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
   text-body 等で切らずに組み(DeltaBatch.bodies — 段 9f lane 9f-2)、entries はそこから entry-of-body の 1 点で
   切って導く。"
  (setv frames [])
  (setv bodies [])
  (setv usage None)
  (setv seen-messages (set))
  (setv model None)
  (setv seq seq-start)
  (for [record records]
    (setv kind (.get record "type"))
    (setv message (.get record "message"))
    (when (and streamed (= kind "system"))
      (<- note (| str None) (claude-system-note record))
      (when (is-not note None)
        (<- system-body dict (note-body seq at ENTRY-KIND-SYSTEM note))
        (.append bodies system-body)
        (setv seq (+ seq 1))))
    (when (and streamed (= kind "result"))
      (<- failure (| str None) (claude-result-error record))
      (when (is-not failure None)
        (<- error-body dict (note-body seq at ENTRY-KIND-ERROR failure))
        (.append bodies error-body)
        (setv seq (+ seq 1))))
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
                (<- use-frame dict (delta-frame job-id seq at "tool_use"
                                                {"toolUseId" tool-id "name" name "summary" summary}))
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
              :next-seq seq :model model))


(defk codex-deltas-of [records job-id seq-start at]
  {:pre [(: records tuple) (: job-id str) (: seq-start int) (: at int)]
   :post [(: % DeltaBatch)]}
  "codex の rollout の行(response_item の message / function_call /
   function_call_output・event_msg の token_count)→ frame と entries。読めない形は飛ばす。"
  (setv frames [])
  (setv bodies [])
  (setv usage None)
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
          (<- use-frame dict (delta-frame job-id seq at "tool_use"
                                          {"toolUseId" call-id "name" name "summary" summary}))
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
            (setv seq (+ seq 1))))
        True None)))
  (<- entries tuple (entries-of-bodies (tuple bodies)))
  (DeltaBatch :frames (tuple frames) :entries entries :bodies (tuple bodies) :usage usage
              :next-seq seq :model None))


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
  (setv seq seq-start)
  (for [record records]
    (setv method (.get record "method"))
    (setv params (.get record "params"))
    (when (and (isinstance method str) (isinstance params dict))
      (setv item (.get params "item"))
      (cond
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
          (<- use-frame dict (delta-frame job-id seq at "tool_use"
                                          {"toolUseId" tool-id "name" "command_execution"
                                           "summary" summary}))
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
          (setv turn (.get params "turn"))
          (when (isinstance turn dict)
            (setv turn-status (.get turn "status"))
            (when (and (isinstance turn-status str) (!= turn-status "completed"))
              (setv error (.get turn "error"))
              (setv message (if (isinstance error dict) (.get error "message") None))
              (<- error-body dict (note-body seq at ENTRY-KIND-ERROR
                                             (if (isinstance message str) message f"turn-{turn-status}")))
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
            (setv seq (+ seq 1))))
        True None)))
  (<- entries tuple (entries-of-bodies (tuple bodies)))
  (DeltaBatch :frames (tuple frames) :entries entries :bodies (tuple bodies) :usage usage
              :next-seq seq :model None))


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
