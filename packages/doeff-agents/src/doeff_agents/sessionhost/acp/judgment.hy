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
;;;   * capture の是非(購読者の数 → continue | stop・issue #1 の決定)と待ちの長さ。
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
  AgentdSettings
  AgentdState
  AcpRow
  BACKEND-HEADLESS
  CLAUDE-OAUTH-TOKEN-ENV
  CONDITION-INTERRUPTED
  DeltaBatch
  INTERRUPT-ARM-INTERRUPT
  INTERRUPT-ARM-NONE
  InFlightJob
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
  NEXT-ARM-RESUME
  NEXT-ARM-SEND
  NODE-GONE
  PHASE-BOUND
  PHASE-ENDED
  PHASE-RUNNING
  PHASE-WITHDRAWN
  SESSION-OBSERVED-BUSY
  SESSION-OBSERVED-IDLE
  SESSION-TERMINAL-STATUSES
  STREAM-SOURCE-EVENTS
  STREAM-SOURCE-TRANSCRIPT
  SessionView
  TURN-RECORD-ENDED
  TURN-RECORD-KIND
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


(defk next-arm-for-job [plan view]
  {:pre [(: plan LaunchPlan) (: view (| SessionView None))]
   :post [(: % str)]}
  "Bound の job の起こし方(閉語彙 effects.NextArm)— 判断はここ 1 点:
   候補の session が生きて idle → send(温かい・predecessor が生きていればこれが温かい resume)/
   候補が手番の途中 → defer(claim せず次の list で読み直す — 走っている手番に本文を積まない)/
   predecessor が在る(が候補は生きていない)→ resume(cold の session.resume)/
   それ以外 → launch。"
  (<- idle bool (session-idle view))
  (<- busy bool (session-busy view))
  (cond
    idle NEXT-ARM-SEND
    busy NEXT-ARM-DEFER
    (is-not plan.predecessor None) NEXT-ARM-RESUME
    True NEXT-ARM-LAUNCH))


(defk recovered-arm-of [plan]
  {:pre [(: plan LaunchPlan)]
   :post [(: % str)]}
  "拾い直した Running の手番がどう始まっていたか(手番の始まりの offset の読み方): predecessor が
   在れば resume(前の手番の行を混ぜない)、無ければ launch(file の頭から)。send は拾い直せない
   (行に送った時刻が無い)ので resume と同じ読み(今の file の大きさ)にはしない — 拾い直しの
   turn-floor は行の createdAt で、記録の進みは host の判定だけを信じる。"
  (if (is plan.predecessor None) NEXT-ARM-LAUNCH NEXT-ARM-RESUME))


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


(defk stream-capability-of-backend [backend]
  {:pre [(: backend str)]
   :post [(: % str)]}
  "node の observations.streamCapability は backend から導く(契約 turn-delta.json capability):
   headless = events(stdout の行の増分)・tmux / herdr = frames(pane の断面)。"
  (if (= backend BACKEND-HEADLESS) "events" "frames"))


(defk conversation-of-session [rows session-id node-name principal]
  {:pre [(: rows tuple) (: session-id str) (: node-name str) (: principal str)]
   :post [(: % (| str None))]}
  "session の id → その session を使った(最新の)手番の会話(spec.subject)。無ければ None。"
  (setv found None)
  (setv found-at -1)
  (for [row rows]
    (<- sid (| str None) (handle-owned-by row node-name principal))
    (when (and (= sid session-id) (> row.created-at-ms found-at))
      (setv subject (.get row.spec "subject"))
      (when (isinstance subject str)
        (setv found subject)
        (setv found-at row.created-at-ms))))
  found)


(defk session-observations-of [views rows node-name principal]
  {:pre [(: views tuple) (: rows tuple) (: node-name str) (: principal str)]
   :post [(: % list)]}
  "node の status.observations.sessions — 行と器から導いた
   [{conversationId, sessionId, state}](生きている温かい session だけ・state は idle | busy)。
   会話の id を引けない session(行が GC で消えた等)は載せない(発明しない)。"
  (setv out [])
  (for [view views]
    (<- alive bool (session-alive view))
    (when (and alive (= view.lifecycle LIFECYCLE-MULTI-TURN))
      (<- subject (| str None) (conversation-of-session rows view.session-id node-name principal))
      (when (is-not subject None)
        (<- idle bool (session-idle view))
        (.append out {"conversationId" subject
                      "sessionId" view.session-id
                      "state" (if idle SESSION-OBSERVED-IDLE SESSION-OBSERVED-BUSY)}))))
  out)


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
   ∧ 腕が起こす腕(launch / resume)。headless の器は 1 手番 = 1 prompt(claude は 1 手番 1 process・
   codex は turn/start が手番)で、走っている手番の途中に次の本文を積めない(実弾 2026-09-12:
   launch の直後の send が同じ名で --resume を spawn し `headless session already exists`)。
   send の腕(温かい session)は起こさないので畳む先が無い(郵便の本文だけを send)。tui(tmux /
   herdr)は launch の後に send(pane の paste は手番の途中でも積める)で今日どおり。"
  (and (= backend-kind BACKEND-HEADLESS) (in arm #{NEXT-ARM-LAUNCH NEXT-ARM-RESUME})))


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


(defk node-status-with-lease [row settings now-ms sessions]
  {:pre [(: row AcpRow) (: settings AgentdSettings) (: now-ms int) (: sessions list)]
   :post [(: % dict)]}
  "agentd が書く欄だけを更新した node の status: lease{owner, heartbeatAt, expiresAt} と
   observations{streamCapability, sessions}(sessions = session-observations-of の列)。
   state(scheduling の欄)は写すだけ。"
  (<- next dict (status-object-of row))
  (setv (get next "lease")
        {"owner" settings.principal
         "heartbeatAt" now-ms
         "expiresAt" (+ now-ms (* 1000 settings.node-lease-ttl-seconds))})
  (setv (get next "observations")
        {"streamCapability" settings.stream-capability
         "sessions" sessions})
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
  "契約 turn-record の spec(conversationId・agentJobId・node・profile・model)。"
  {"conversationId" job.subject
   "agentJobId" job.job-id
   "node" job.node
   "profile" job.profile
   "model" job.model})


(defk turn-record-ended-status [status usage entries]
  {:pre [(: status dict) (: usage (| dict None)) (: entries tuple)]
   :post [(: % dict)]}
  "手番の終わりの turn-record の status: state = ended・usage(素材があれば)・entries。"
  (setv next (dict status))
  (setv (get next "state") TURN-RECORD-ENDED)
  (when (is-not usage None)
    (setv (get next "usage") usage))
  (setv (get next "entries") (list entries))
  next)


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
   block を text frame に。system / result の行は読まない(手番の終わりは host が読む)。"
  (setv frames [])
  (setv entries [])
  (setv usage None)
  (setv seen-messages (set))
  (setv model None)
  (setv seq seq-start)
  (for [record records]
    (setv kind (.get record "type"))
    (setv message (.get record "message"))
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
                (setv entry {"seq" seq "at" at "kind" "text" "text" (get block "text")})
                (when (isinstance message-model str)
                  (setv (get entry "model") message-model))
                (.append entries entry)
                (setv seq (+ seq 1)))
              (and (= kind "assistant") (= block-type "tool_use"))
              (do
                (setv tool-id (str (.get block "id" "")))
                (setv name (str (.get block "name" "")))
                (<- summary str (summary-of (.get block "input") 4000))
                (<- use-frame dict (delta-frame job-id seq at "tool_use"
                                                {"toolUseId" tool-id "name" name "summary" summary}))
                (.append frames use-frame)
                (.append entries {"seq" seq "at" at "kind" "tool_use"
                                  "toolName" name "summary" summary})
                (setv seq (+ seq 1)))
              (and (= kind "user") (= block-type "tool_result"))
              (do
                (setv tool-id (str (.get block "tool_use_id" "")))
                (setv result (.get block "content"))
                (<- summary str (summary-of result 4000))
                (setv size (len (.encode (json.dumps result :ensure-ascii False) "utf-8")))
                (<- result-frame dict (delta-frame job-id seq at "tool_result"
                                                   {"toolUseId" tool-id "summary" summary
                                                    "bytes" size
                                                    "isError" (bool (.get block "is_error" False))}))
                (.append frames result-frame)
                (.append entries {"seq" seq "at" at "kind" "tool_result" "summary" summary})
                (setv seq (+ seq 1)))
              True None))))))
  (DeltaBatch :frames (tuple frames) :entries (tuple entries) :usage usage
              :next-seq seq :model model))


(defk codex-deltas-of [records job-id seq-start at]
  {:pre [(: records tuple) (: job-id str) (: seq-start int) (: at int)]
   :post [(: % DeltaBatch)]}
  "codex の rollout の行(response_item の message / function_call /
   function_call_output・event_msg の token_count)→ frame と entries。読めない形は飛ばす。"
  (setv frames [])
  (setv entries [])
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
            (.append entries {"seq" seq "at" at "kind" "text" "text" (get block "text")})
            (setv seq (+ seq 1))))
        (and (= kind "response_item") (= ptype "function_call"))
        (do
          (setv name (str (.get payload "name" "")))
          (<- summary str (summary-of (.get payload "arguments") 4000))
          (<- use-frame dict (delta-frame job-id seq at "tool_use"
                                          {"toolUseId" (str (.get payload "call_id" ""))
                                           "name" name "summary" summary}))
          (.append frames use-frame)
          (.append entries {"seq" seq "at" at "kind" "tool_use" "toolName" name "summary" summary})
          (setv seq (+ seq 1)))
        (and (= kind "response_item") (= ptype "function_call_output"))
        (do
          (setv output (.get payload "output"))
          (<- summary str (summary-of output 4000))
          (<- whole str (summary-of output 1000000000))
          (<- result-frame dict (delta-frame job-id seq at "tool_result"
                                             {"toolUseId" (str (.get payload "call_id" ""))
                                              "summary" summary
                                              "bytes" (len (.encode whole "utf-8"))}))
          (.append frames result-frame)
          (.append entries {"seq" seq "at" at "kind" "tool_result" "summary" summary})
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
  (DeltaBatch :frames (tuple frames) :entries (tuple entries) :usage usage
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
  (setv entries [])
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
          (.append entries {"seq" seq "at" at "kind" "text" "text" (get item "text")})
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
          (.append entries {"seq" seq "at" at "kind" "tool_use"
                            "toolName" "command_execution" "summary" summary})
          (setv seq (+ seq 1))
          (setv output (.get item "aggregatedOutput"))
          (when (isinstance output str)
            (<- result-summary str (summary-of output 4000))
            (setv exit-code (.get item "exitCode"))
            (<- result-frame dict (delta-frame job-id seq at "tool_result"
                                               {"toolUseId" tool-id "summary" result-summary
                                                "bytes" (len (.encode output "utf-8"))
                                                "isError" (and (isinstance exit-code int)
                                                               (!= exit-code 0))}))
            (.append frames result-frame)
            (.append entries {"seq" seq "at" at "kind" "tool_result" "summary" result-summary})
            (setv seq (+ seq 1))))
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
  (DeltaBatch :frames (tuple frames) :entries (tuple entries) :usage usage
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
  "次の watch の待ちの上限: capture 中は frame の間隔、手番が走っていれば transcript の
   周期、何も無ければ idle の上限。"
  (cond
    (any (gfor job state.jobs job.capturing)) (float settings.frame-interval-seconds)
    state.jobs (float settings.transcript-poll-seconds)
    True (float settings.idle-wait-seconds)))


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
