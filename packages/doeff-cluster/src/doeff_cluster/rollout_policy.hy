;;; Rollout の純粋な判断(旧 → 新の切り替えを宣言で表す)。I/O はしない。
;;;
;;; spec:
;;;   from / to    切り替えの相手。{"kind": "Deployment", "namespace", "name", "replicas"?, "dryRun"?} か {"kind": "Service", "name"}
;;;   readyTimeoutSeconds  新が Ready になるまで待つ上限(既定 300)
;;;   stopTimeoutSeconds   旧が止まるまで待つ上限(既定 180)
;;;   observeSeconds       旧を止めた後に新を見続ける時間(既定 1800)
;;;   failAfterSeconds     観察の間に新が続けて NotReady でいてよい時間(既定 30)
;;;   rollbackTimeoutSeconds  戻し(RollingBack)が終わるまでの目安(既定 600)。過ぎても新を止めない — status.stuck に印を出して人を呼ぶ
;;;   markDeployment       完了の間 Deployment に「台数を Rollout が持つ」annotation を置く(既定 偽 — 権限が要る)
;;;   abort                真にすると(作った後に変えてよい唯一の欄)旧を先に戻してから新を止める
;;;
;;; 段(status.phase):
;;;   Pending → WaitingNewReady(新を起こし Ready を待つ)→ StoppingOld(旧を 0 にし止まるのを待つ)→ Observing → Complete
;;;   どの段で失敗しても RollingBack: 旧を元の台数へ戻し Ready を待つ → 新を 0 にし止まるのを待つ → RolledBack。
;;;   旧を先に戻すので、戻しの間も書き手が 0 になる時間を作らない。
;;;
;;; level-triggered: 毎拍、spec・保存した status・いまの観測(view)だけから次の action を決める。action は冪等(台数を N にする)で、
;;; 観測が既に N なら出さない。coordinator が途中で落ちても、作り直した後に同じ段から続く。観測が Unknown(k8s に届かない・
;;; coordinator が起動した直後)の間は台数を変えず、失敗とも数えない(時間切れだけは数える)。
;;; 時間の数え方(2026-09-25):
;;;   - Observing は観測が Unknown の間を観察の時間に数えない。Unknown に入った時刻を unknownSinceMs に持ち、抜けた拍に段の起点
;;;     (phaseSinceMs・notReadySinceMs)を Unknown の長さだけ後ろへずらす。Complete は「いまの観測が Ready」の時だけ。
;;;   - coordinator が止まっていた時間も数えない: 起動の時に shift-clocks で進行中の Rollout の起点を止まっていた長さだけずらす
;;;     (止まり始め = 耐久の鍵 alive の最後の生存の時刻 — api_policy.mark-alive / resume-rollouts)。
;;;   - RollingBack は rollbackTimeoutSeconds を過ぎると status.stuck = {step reason sinceMs} を出す(新は止めない: 旧が Ready でない
;;;     まま新を止めると書き手が 0 になる)。計器 doeff_worker_rollout_stuck と alert DoeffWorkerRolloutStuck で人を呼ぶ。
;;;   - 失敗した action は同じ action の失敗が続く間、間を倍々に空ける(1 秒 → 60 秒・action-due)。
;;; status は遷移の時だけ変える(拍ごとには変えない — 版と出来事の記録が拍ごとに進まないように)。
;;;
;;; status には変わり続ける文(観測の理由・残り時間)を入れない(拍ごとに版と記録が進むため)。いまの観測は資源の表示で見せる。
;;; view(呼び手が作る): {"ready": Ready|NotReady|Unknown  "stopped": 真 / 偽 / None  "specReplicas": 宣言の台数 | None  "reason": …}
;;; Deployment の stopped は「宣言 0 かつ Pod 0(終了中を含む)」(api_policy.target-view)。

(setv TERMINAL-PHASES #{"Complete" "RolledBack"})
(setv DEFAULTS {"readyTimeoutSeconds" 300 "stopTimeoutSeconds" 180 "observeSeconds" 1800 "failAfterSeconds" 30
                "rollbackTimeoutSeconds" 600 "markDeployment" False "abort" False})
(setv TIMEOUT-KEYS #("readyTimeoutSeconds" "stopTimeoutSeconds" "observeSeconds" "failAfterSeconds" "rollbackTimeoutSeconds"))
;; 段の時刻の起点(coordinator が止まっていた時間を除く時にずらす欄)。
(setv CLOCK-FIELDS #("phaseSinceMs" "notReadySinceMs" "unknownSinceMs"))
(setv RETRY-FIRST-MS 1000 RETRY-MAX-MS 60000)
(setv HISTORY-LIMIT 30)


(defn #^ dict validate-target [target #^ str label]
  (when (not (isinstance target dict)) (raise (ValueError (+ label " は dict"))))
  (setv kind (.get target "kind"))
  (cond
    (= kind "Service")
      (do (when (not (isinstance (.get target "name") str)) (raise (ValueError (+ label ".name が要る"))))
          {"kind" "Service" "name" (get target "name")})
    (= kind "Deployment")
      (do (for [k #("namespace" "name")]
            (when (not (isinstance (.get target k) str)) (raise (ValueError (.format "{}.{} が要る" label k)))))
          (setv replicas (.get target "replicas"))
          (when (and (is-not replicas None) (not (and (isinstance replicas int) (>= replicas 1))))
            (raise (ValueError (+ label ".replicas は 1 以上"))))
          {"kind" "Deployment" "namespace" (get target "namespace") "name" (get target "name")
           "replicas" replicas "dryRun" (bool (.get target "dryRun" False))})
    True (raise (ValueError (.format "{}.kind は Service か Deployment: {!r}" label kind)))))


(defn #^ str target-key [#^ dict target]
  (if (= (get target "kind") "Service")
      (+ "Service:" (get target "name"))
      (.format "Deployment:{}/{}" (get target "namespace") (get target "name"))))


(defn #^ dict validate-rollout-spec [#^ dict spec]
  (setv from (validate-target (.get spec "from") "from") to (validate-target (.get spec "to") "to"))
  (when (= (target-key from) (target-key to)) (raise (ValueError "from と to が同じ")))
  (setv out (| DEFAULTS {"from" from "to" to "owner" (.get spec "owner")}))
  (for [k TIMEOUT-KEYS]
    (setv v (.get spec k (get DEFAULTS k)))
    (when (not (and (isinstance v #(int float)) (>= v 0))) (raise (ValueError (+ k " は 0 以上の数"))))
    (setv (get out k) v))
  (setv (get out "markDeployment") (bool (.get spec "markDeployment" False))
        (get out "abort") (bool (.get spec "abort" False)))
  out)


(defn #^ list rollout-targets [#^ dict spec]
  [(get spec "from") (get spec "to")])


(defn #^ int new-replicas [#^ dict target]
  "新として起こす台数。Service は 1(1 つだけ動かす)・Deployment は宣言の replicas(既定 1)。"
  (if (= (get target "kind") "Service") 1 (or (.get target "replicas") 1)))


(defn #^ dict enter [#^ dict status #^ str phase #^ int now [reason ""] #** extra]
  "段に入る。Unknown の起点(段ごとの物)は持ち越さない。"
  (setv history (+ (list (.get status "history" [])) [{"phase" phase "at" now "reason" reason}])
        kept (dfor #(k v) (.items status) :if (!= k "unknownSinceMs") k v))
  (| kept {"phase" phase "phaseSinceMs" now "reason" reason "history" (cut history (- HISTORY-LIMIT) None)} extra))


(defn #^ (| int float) spec-seconds [#^ dict spec #^ str key]
  "spec の秒の欄(この欄が無かった頃に作った Rollout は既定の値)。"
  (.get spec key (get DEFAULTS key)))


(defn scale [#^ dict target #^ int replicas]
  {"op" "scale" "target" target "replicas" replicas})


(defn #^ list ensure-replicas [#^ dict target #^ dict view #^ int replicas]
  "観測の宣言の台数が replicas でなければ、そうする action(観測が無ければ何もしない)。"
  (setv current (.get view "specReplicas"))
  (if (or (is current None) (= current replicas)) [] [(scale target replicas)]))


(defn #^ tuple rollout-step [#^ dict spec #^ dict status #^ dict from-view #^ dict to-view #^ int now]
  "1 拍。返り値 #(次の status action の list)。"
  (setv phase (.get status "phase" "Pending") since (.get status "phaseSinceMs" now)
        old (get spec "from") new (get spec "to"))
  (when (in phase TERMINAL-PHASES) (return #(status [])))
  (when (and (get spec "abort") (!= phase "RollingBack"))
    (return (rollout-step spec (enter status "RollingBack" now "中止の指示(abort)" :rollbackStep "restoreOld"
                                      :failure "中止の指示(abort)")
                          from-view to-view now)))
  (defn fail [reason]
    (rollout-step spec (enter status "RollingBack" now reason :rollbackStep "restoreOld" :failure reason) from-view to-view now))
  (cond
    (= phase "Pending")
      ;; 旧の今の台数を控える(戻す時の台数)。旧が Deployment で観測が無ければ、spec の replicas か 1。
      (do (setv observed (.get from-view "specReplicas")
                restore (cond (= (get old "kind") "Service") 1
                              (.get old "replicas") (get old "replicas")
                              (and observed (> observed 0)) observed
                              (is observed None) None
                              True 1))
          (if (is restore None)
              #((| status {"reason" "旧の台数を観測できるまで待つ"}) [])
              (rollout-step spec (enter status "WaitingNewReady" now "新を起こす" :fromReplicas restore :startedMs now)
                            from-view to-view now)))
    (= phase "WaitingNewReady")
      (cond
        (= (get to-view "ready") "Ready")
          (rollout-step spec (enter status "StoppingOld" now "新が Ready になった") from-view to-view now)
        (> (- now since) (* 1000 (spec-seconds spec "readyTimeoutSeconds")))
          (fail (.format "新が {} 秒で Ready にならなかった: {}" (get spec "readyTimeoutSeconds") (.get to-view "reason" "")))
        True #((| status {"reason" "新の Ready を待つ"})
               (ensure-replicas new to-view (new-replicas new))))
    (= phase "StoppingOld")
      (cond
        (= (get to-view "ready") "NotReady")
          (fail (+ "旧を止める途中で新が Ready でなくなった: " (.get to-view "reason" "")))
        (.get from-view "stopped")
          (rollout-step spec (enter status "Observing" now "旧が止まった" :stoppedOldMs now) from-view to-view now)
        ;; 新の観測が Unknown(担い手の heartbeat が途絶えた・coordinator が起動した直後)の間は、旧を止める命令を新しく出さない。
        ;; 失敗とも数えない(時間切れだけは数える)。新が本当に落ちていたら、旧を止めた後で書き手が 0 になるため(2026-09-25)。
        (= (get to-view "ready") "Unknown")
          #((| status {"reason" "新の観測が Unknown の間は旧を止める命令を控える"}) [])
        (> (- now since) (* 1000 (spec-seconds spec "stopTimeoutSeconds")))
          (fail (.format "旧が {} 秒で止まらなかった: {}" (get spec "stopTimeoutSeconds") (.get from-view "reason" "")))
        True #((| status {"reason" "旧が止まるのを待つ"})
               (ensure-replicas old from-view 0)))
    (= phase "Observing")
      (do (setv state (get to-view "ready") down (.get status "notReadySinceMs") blind (.get status "unknownSinceMs"))
          ;; 観測が Unknown の間は観察の時間に数えない(完了も失敗もしない)。入った時刻だけを控える(status は入った拍だけ変わる)。
          (when (= state "Unknown")
            (return #((if (is blind None)
                          (| status {"unknownSinceMs" now "reason" "観察中(観測が Unknown の間は時間を数えない)"})
                          status)
                      [])))
          ;; Unknown を抜けた拍: 段の起点(と NotReady の起点)を Unknown の長さだけ後ろへずらす。
          (when (is-not blind None)
            (setv gap (max 0 (- now blind)) since (+ since gap) down (if (is down None) None (+ down gap))
                  status (| status {"phaseSinceMs" since "notReadySinceMs" down "unknownSinceMs" None})))
          (cond
            (= state "Ready") (setv down None)
            (= state "NotReady") (setv down (or down now)))
          (cond
            (and down (> (- now down) (* 1000 (spec-seconds spec "failAfterSeconds"))))
              (fail (.format "観察の間に新が {} 秒 Ready でなかった: {}" (// (- now down) 1000) (.get to-view "reason" "")))
            (and (= state "Ready") (>= (- now since) (* 1000 (spec-seconds spec "observeSeconds"))))
              #((enter (| status {"notReadySinceMs" None}) "Complete" now "観察の期間を終えた" :completedMs now) [])
            True #((| status {"notReadySinceMs" down
                              "reason" "観察中"})
                   [])))
    (= phase "RollingBack")
      (do (setv step (.get status "rollbackStep" "restoreOld") restore (or (.get status "fromReplicas") 1)
                limit (spec-seconds spec "rollbackTimeoutSeconds")
                late (> (- now since) (* 1000 limit)))
          (defn #^ tuple waiting [#^ str reason #^ str stuck-reason #^ list actions]
            ;; 時間切れの後も同じ action を出し続ける(新は止めない)。stuck は印を付けた拍と step が変わった拍だけ変わる。
            (setv current (.get status "stuck")
                  stuck (cond (not late) None
                              (and current (= (.get current "step") step)) current
                              True {"step" step "reason" (.format "戻しが {} 秒で終わらない: {}" limit stuck-reason) "sinceMs" now}))
            #((| status {"reason" reason} (if (= stuck current) {} {"stuck" stuck})) actions))
          (cond
            (= step "restoreOld")
              (if (= (get from-view "ready") "Ready")
                  (rollout-step spec (| status {"rollbackStep" "stopNew" "restoredOldMs" now}) from-view to-view now)
                  (waiting "戻し: 旧を元の台数へ戻し Ready を待つ" "旧が Ready に戻らない(新は動かしたまま)"
                           (ensure-replicas old from-view restore)))
            (= step "stopNew")
              (if (.get to-view "stopped")
                  #((enter status "RolledBack" now (+ "戻した: " (.get status "failure" "")) :completedMs now
                           #** (if (.get status "stuck") {"stuck" None "stuckClearedMs" now} {}))
                    [])
                  (waiting "戻し: 新を止め、止まるのを待つ" "新が止まらない" (ensure-replicas new to-view 0)))
            True #(status [])))
    True #((enter status "RollingBack" now (+ "知らない段: " phase) :rollbackStep "restoreOld") [])))


;; --- action の送り直しの間(2026-09-25) ------------------------------------------------------------

(defn #^ dict action-identity [#^ dict action]
  "送り直しで同じ action か比べる欄(api_policy.record-action が lastAction に残す欄と同じ — 結末と時刻と数を除く)。"
  (setv target (.get action "target"))
  (| (dfor #(k v) (.items action) :if (not-in k #("rollout" "target" "ok" "error" "at" "count")) k v)
     ;; lastAction の target は既に「Kind:名」の文字列(record-action が畳んだ形)。
     (cond (isinstance target dict) {"target" (target-key target)}
           target {"target" target}
           True {})))


(defn #^ int retry-delay-ms [#^ int failures]
  "同じ action が failures 回続けて失敗した後、次に出すまでの間(1 秒から倍々・上限 60 秒)。"
  (min RETRY-MAX-MS (* RETRY-FIRST-MS (** 2 (max 0 (- failures 1))))))


(defn #^ bool action-due [#^ dict status #^ dict action #^ int now]
  "純粋: この拍に action を出してよいか。直前の同じ action が失敗していれば、失敗の数に応じた間を空ける(k8s の API が
   断り続ける間、毎秒同じ書きを出して記録と版を進めない)。成功した・違う action は、すぐ出す。"
  (setv last (.get status "lastAction"))
  (when (or (not last) (.get last "ok")) (return True))
  (when (!= (action-identity last) (action-identity action)) (return True))
  (>= (- now (.get last "at" 0)) (retry-delay-ms (.get last "count" 1))))


;; --- coordinator が止まっていた時間(2026-09-25) --------------------------------------------------

(defn #^ dict shift-clocks [#^ dict status #^ int gap-ms]
  "純粋: 進行中の Rollout の段の起点を gap-ms だけ後ろへずらした status(coordinator が止まっていた時間を段の時間に数えない)。
   終わった Rollout・gap が 0 以下はそのまま。"
  (if (or (<= gap-ms 0) (in (.get status "phase") TERMINAL-PHASES))
      status
      (| status (dfor k CLOCK-FIELDS :if (is-not (.get status k) None) k (+ (get status k) gap-ms)))))


;; --- 完了の後: 台数の持ち主と食い違い -----------------------------------------------------------

(defn #^ dict deployment-owners [#^ dict rollouts]
  "Deployment ごとに台数を持つ Rollout(その Deployment を扱った、旧を止め終えた — Observing か Complete の — 最後の物)。
   dry-run の相手は持たない。返り値 = 「ns/名」→ #(Rollout の名 期待する台数)。旧として止めたなら 0・新として起こしたなら replicas。"
  (setv best {})
  (for [#(name r) (sorted (.items rollouts))]
    (setv spec (get r "spec") status (get r "status"))
    (when (in (.get status "phase") #("Observing" "Complete"))
      (for [#(side target) #(#("from" (get spec "from")) #("to" (get spec "to")))]
        (when (and (= (get target "kind") "Deployment") (not (get target "dryRun")))
          (setv key (+ (get target "namespace") "/" (get target "name"))
                expected (if (= side "from") 0 (new-replicas target))
                at (.get status "stoppedOldMs" 0))
          (when (or (not-in key best) (> at (get (get best key) 2)))
            (setv (get best key) #(name expected at)))))))
  (dfor #(k v) (.items best) k #((get v 0) (get v 1))))


(defn #^ dict drift-status [#^ dict status #^ str deployment #^ int expected #^ (| dict None) observation #^ int now]
  "完了した Rollout が台数を持つ Deployment の、宣言の台数と期待の食い違い(本番の配備の流れが replicas を当て直した等)。
   直さない(配備の流れと取り合わない)— status に出すだけ。"
  (setv observed (if (and observation (not-in "error" observation)) (.get observation "specReplicas") None)
        current (.get status "drift"))
  (cond
    (is observed None) status
    (= observed expected)
      (if current (| status {"drift" None "driftResolvedMs" now}) status)
    (and current (= (.get current "observed") observed)) status
    True (| status {"drift" {"deployment" deployment "expected" expected "observed" observed
                             "sinceMs" (if current (get current "sinceMs") now)
                             "note" "Deployment の宣言の台数が Rollout の期待と違う(配備の流れが当て直した等)。直していない"}})))
