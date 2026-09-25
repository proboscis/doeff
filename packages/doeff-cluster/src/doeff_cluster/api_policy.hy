;;; coordinator の HTTP の要求 1 件への返事と、Rollout の調停の段取り(純粋な判断・I/O はしない)。
;;;
;;; 要求は respond が振り分ける。状態を変える要求は settle で「要求そのものの変化(送り手 = 要求の X-Actor)」と
;;; 「それを受けた調停の変化(送り手 = coordinator)」を分けて版と記録を付ける(resource_policy.stamp)。
;;;
;;; HTTP(資源の口・2026-09-24):
;;;   GET    /resources/<Kind>             一覧(Kind = Service | Worker | Task | Rollout)
;;;   GET    /resources/<Kind>/<名>        1 つ(resourceVersion・generation・所有者・作った / 書いた送り手・spec・status)
;;;   POST   /resources/<Kind>             作る {"name", "spec"}(Service と Rollout)。在れば 409
;;;   PUT    /resources/<Kind>/<名>        書き換える {"spec", "resourceVersion"}。版が古ければ 409・版が無ければ 400
;;;   DELETE /resources/<Kind>/<名>?resourceVersion=&force=   消す(Service と Rollout は所有者か force だけ)
;;;   POST   /resources/Service/<名>/readiness   ReportReady の報告 {worker pid revision instance attempt specHash placement ready reason}
;;;   POST   /resources/Service/<名>/metrics     ReportMetrics の報告 {worker … placement metrics}(資源の状態は変えない)
;;;   GET    /metrics                            Prometheus の text: 今動いている process の計器(label service・worker)
;;;   GET    /events?kind=&name=&since=&limit=   出来事の記録(誰が・いつ・何を・前後の版)
;;;   POST   /leases/<名>  {"op" claim|renew|release|drop, "token", "permits", "ttlMs"}  名前付きの lease の操作(期限は coordinator の
;;;                        時計で書き・判じる — semaphore_model.lease-op・2026-09-25)。答え {"ok" "reason" "ttlMs"}
;;;   GET    /workers/<名>                      worker の生存・世代・drain の進み(ready = 生きていて drain 中でない)
;;;   POST   /workers/<名>/drain {"ttlSeconds"?}  drain を頼む(何度でも同じ意味・期限だけ延びる)。DELETE で取り消す(drain_policy)
;;; 書きには header X-Actor(依頼の主体の id・作業係の名・worker の名)が要る。盤と task は無ければ送り元の番地で記録する。
;;; 旧い口(PUT /jobs・/heartbeat・/board・/tasks)は残す。PUT /jobs は資源ごとの compare-and-set に写す(resource_policy)。
(import dataclasses [replace])
(import urllib.parse [unquote :as url-unquote])
(import .cluster_model [ClusterState ClusterTiming ClusterNaming Request PlainText])
(import .metrics_policy [record-metrics metrics-text])
(import .cluster_policy [reconcile register-heartbeat heartbeat-reply state-view submit-task poll-task board-write lease-write
                         still-live-somewhere])
(import .resource_policy [Refused refuse stamp require-actor valid-actor service-readiness record-readiness running-process
                          list-resources get-resource events-view create-resource update-resource delete-resource
                          legacy-put-jobs COORDINATOR])
(import .drain_policy [advance-drains request-drain cancel-drain worker-view drains-view])
(import .rollout_policy [rollout-step target-key deployment-owners drift-status action-due shift-clocks TERMINAL-PHASES])

(setv OBSERVATION-STALE-MS 15000)   ; これより古い k8s の観測は Unknown
(setv ROLLOUT-ACTOR "rollout-controller")


(defn #^ ClusterState settle [#^ ClusterState before #^ ClusterState after #^ str actor #^ int now #^ ClusterTiming timing]
  "要求の変化に送り手の版を付け、調停し、調停の変化に coordinator の版を付ける。"
  (setv changed (stamp before after actor now timing)
        ;; drain(2026-09-25): 割り当ての後に、drain 中の worker の上の入れ替えの Service を並べる・付け替える(readiness を読む)。
        reconciled (advance-drains now (reconcile now changed timing) timing))
  (stamp changed reconciled COORDINATOR now timing))


(defn #^ ClusterState tick [#^ ClusterState state #^ int now #^ ClusterTiming timing]
  "要求の無い拍: 期限の経過だけで調停する(worker の沈黙・task の期限・readiness の window)。"
  (settle state state COORDINATOR now timing))


;; --- coordinator が止まっていた時間(2026-09-25) --------------------------------------------------
;; 止まっている間は、誰も Rollout を進めず・task の問い合わせにも答えられない。起動の時にその時間を「経った」と数えると、
;; 進行中の Rollout は観測の無いまま時間切れで戻しに入り、task は呼び手が問い合わせていたのに lease 切れで落ちる。
;; 生きていた最後の時刻(alive-ms)を ALIVE-MARK-MS ごとに耐久の鍵へ書き、起動の時に止まっていた長さだけ時計をずらす。
;; 書きの間隔の分(最大 ALIVE-MARK-MS)だけ長めに見積もる = 時間切れを遅らせる側に外れる。
(setv ALIVE-MARK-MS 5000)

(defn #^ ClusterState mark-alive [#^ ClusterState state #^ int now]
  "純粋: ALIVE-MARK-MS 経っていれば生きていた時刻を進め、同じ拍の各 worker の最後の連絡の時刻を写した状態(耐久の鍵 counter と、
   連絡のあった worker の worker/<名> が変わる = 次の Persist に載る。沈黙している worker の鍵は変わらない)。"
  (if (>= (- now state.alive-ms) ALIVE-MARK-MS)
      (replace state :alive-ms now :seen-marks (dfor #(n w) (.items state.workers) n w.last-seen-ms))
      state))

(defn #^ tuple resume-after-downtime [#^ ClusterState state #^ int now]
  "純粋: 読み直した状態 → #(時計をずらした状態 止まっていた長さ ms)。ずらすのは進行中の Rollout の段の起点(shift-clocks)と
   task の lease の期限と、worker の最後の連絡の時刻(と、その写し seen-marks)。生きていた時刻を知らない置き場(2026-09-25 より前)は 0。
   worker の最後の連絡の時刻は、止まる前の最後の印(alive-ms)の時点の沈黙を今から数え直した値になる: 止まる直前まで連絡のあった
   worker は起き直した直後も生きていて、その後に連絡が無ければ移し替えの期限の後に沈黙と判じる。止まる前から沈黙していた worker は
   沈黙のまま(再起動で「いま連絡があった」に戻さない)。止まっていた長さは沈黙に数えない(担い手が一斉に沈黙に倒れ、最初に
   heartbeat を送った worker へ全 job が移ると、元の担い手と二重に動く — 実測 2026-09-23)。"
  (setv gap (if (> state.alive-ms 0) (max 0 (- now state.alive-ms)) 0))
  (if (= gap 0)
      #((replace state :alive-ms now) 0)
      #((replace state
                 :rollouts (dfor #(k r) (.items state.rollouts) k (| r {"status" (shift-clocks (get r "status") gap)}))
                 :tasks (dfor #(k t) (.items state.tasks) k (replace t :lease-until-ms (+ t.lease-until-ms gap)))
                 :workers (dfor #(k w) (.items state.workers) k (replace w :last-seen-ms (min now (+ w.last-seen-ms gap))))
                 :seen-marks (dfor #(k v) (.items state.seen-marks) k (min now (+ v gap)))
                 :alive-ms now)
        gap)))

;; --- Rollout の相手の観測 ----------------------------------------------------------------------

(defn #^ dict target-view [#^ ClusterState state #^ dict target #^ dict status #^ int now #^ ClusterTiming timing]
  (if (= (get target "kind") "Service")
      (do (setv name (get target "name")
                job (next (gfor j state.jobs :if (= j.spec.name name) j) None)
                verdict (service-readiness state name now timing)
                stopped (and (or (is job None) (= job.replicas 0)) (not-in name state.placements)
                             (not (still-live-somewhere now state name timing))))
          {"ready" (get verdict "state") "stopped" stopped "specReplicas" (if job job.replicas None)
           "reason" (if stopped "止まっている" (get verdict "reason"))})
      (do (setv key (+ (get target "namespace") "/" (get target "name"))
                obs (.get state.deployments key))
          (when (or (is obs None) (in "error" obs) (> (- now (.get obs "at" 0)) OBSERVATION-STALE-MS))
            (return {"ready" "Unknown" "stopped" None "specReplicas" None
                     "reason" (if obs (.get obs "error" "観測が古い") "まだ観測していない")}))
          (setv dry (get target "dryRun")
                simulated (.get (.get status "simulated" {}) (target-key target))
                want (if (and dry (is-not simulated None)) simulated (get obs "specReplicas"))
                ready-n (get obs "readyReplicas")
                settled (or dry (and (>= (get obs "observedGeneration") (get obs "generation"))
                                     (>= (get obs "updatedReplicas") want)))
                ready (and (> want 0) (>= ready-n want) settled)
                stopped (and (= want 0) (or dry (= (get obs "replicas") 0))))
          {"ready" (if ready "Ready" "NotReady") "stopped" stopped "specReplicas" want
           "reason" (.format "宣言 {}{}・Pod {}・ready {}" want (if (and dry (is-not simulated None)) "(dry-run の値)" "")
                             (get obs "replicas") ready-n)})))


(defn #^ dict ready-instances [#^ ClusterState state #^ str worker #^ int now #^ ClusterTiming timing]
  "worker に割り当てた入れ替え(handoff)の Service の名 → Ready と数えている process の世代の名(Ready でなければ None)。
   worker はこれが新しい process の世代の名になった後に、退いた旧い process を止める。"
  (dfor job state.jobs
        :setv a (.get state.placements job.spec.name)
        :if (and a (= a.worker worker) job.spec.handoff)
        job.spec.name
        (do (setv proc (running-process state job.spec.name now timing))
            (if (and (get proc "ok") (= (get (service-readiness state job.spec.name now timing) "state") "Ready"))
                (get proc "instance")
                None))))


(defn #^ list deployments-to-observe [#^ ClusterState state #^ int now]
  "読むべき Deployment の「ns/名」: 進行中の Rollout の相手は毎拍、台数を持つ(Observing / Complete の)相手は 10 秒ごと。"
  (setv keys [])
  (for [r (.values state.rollouts)]
    (when (not-in (.get (get r "status") "phase") TERMINAL-PHASES)
      (for [t #((get (get r "spec") "from") (get (get r "spec") "to"))]
        (when (= (get t "kind") "Deployment")
          (.append keys (+ (get t "namespace") "/" (get t "name")))))))
  (for [key (deployment-owners state.rollouts)]
    (when (> (- now (.get (.get state.deployments key {}) "at" 0)) 10000)
      (.append keys key)))
  (list (dict.fromkeys keys)))


(defn #^ tuple plan-rollouts [#^ ClusterState state #^ int now #^ ClusterTiming timing #^ ClusterNaming [naming (ClusterNaming)]]
  "全 Rollout を 1 拍進める。返り値 #(次の状態 action の list)。action = rollout(名)・op(scale / annotate)・target・replicas の dict。
   台数の食い違い(配備の流れが Deployment の replicas を当て直した等)は直さず status.drift に出す。
   naming = 台数の持ち主の annotation の鍵と値の頭(配備する側が決める — cluster_model.ClusterNaming)。"
  (setv rollouts (dict state.rollouts) actions [])
  (for [#(name r) (sorted (.items state.rollouts))]
    (setv spec (get r "spec") status (get r "status"))
    (when (not-in (.get status "phase") TERMINAL-PHASES)
      (setv #(status acts) (rollout-step spec status (target-view state (get spec "from") status now timing)
                                         (target-view state (get spec "to") status now timing) now))
      (setv (get rollouts name) (| r {"status" status}))
      ;; 失敗が続く action は間を空けて出す(action-due — 1 秒から倍々・上限 60 秒)。
      (.extend actions (gfor a acts :if (action-due status a now) (| a {"rollout" name})))))
  ;; 台数の持ち主と食い違い。進行中の Rollout が扱っている Deployment は、台数が動くのが意図どおりなので数えない。
  ;; 持ち主でなくなった(後の Rollout へ移った・進行中の Rollout が扱い始めた)Rollout の食い違いは消す。
  ;; Observing の Rollout は旧を止め終えて台数を持つ側なので、ここでは「進行中」に数えない(2026-09-24 の実弾: 数えていたので
  ;; 観察の間に本番の配備の流れが replicas を 1 へ戻したのを食い違いとして出せなかった)。
  (setv busy (sfor r (.values rollouts) :if (not-in (.get (get r "status") "phase") (| TERMINAL-PHASES #{"Observing"}))
                   t #((get (get r "spec") "from") (get (get r "spec") "to")) :if (= (get t "kind") "Deployment")
                   (+ (get t "namespace") "/" (get t "name")))
        owners (dfor #(k v) (.items (deployment-owners rollouts)) :if (not-in k busy) k v)
        owning (sfor v (.values owners) (get v 0)))
  (for [#(name r) (.items rollouts)]
    (when (and (.get (get r "status") "drift") (not-in name owning))
      (setv (get rollouts name) (| r {"status" (| (get r "status") {"drift" None "driftResolvedMs" now})}))))
  (for [#(key #(name expected)) (.items owners)]
    (setv r (get rollouts name) status (get r "status"))
    (setv status (drift-status status key expected (.get state.deployments key) now))
    (setv (get rollouts name) (| r {"status" status}))
    (when (and (get (get r "spec") "markDeployment") (!= (.get status "markedDeployment") key))
      (setv #(ns dep) (.split key "/" 1))
      (.append actions {"rollout" name "op" "annotate" "namespace" ns "name" dep
                        "annotations" {naming.owner-annotation (.format "{}/Rollout/{} replicas={}" naming.owner-scope name expected)}})))
  #((replace state :rollouts rollouts) actions))


(defn #^ ClusterState scale-service [#^ ClusterState state #^ str name #^ int replicas]
  (replace state :jobs (tuple (gfor j state.jobs (if (= j.spec.name name) (replace j :replicas replicas) j)))))


(defn #^ ClusterState record-action [#^ ClusterState state #^ dict action #^ bool ok #^ (| str None) error #^ int now
                                     [result None]]
  "実行した action の結果を Rollout の status に残す(dry-run の台数は simulated に)。同じ失敗の繰り返しは数だけ進める。"
  (setv name (get action "rollout") r (.get state.rollouts name))
  (when (is r None) (return state))
  (setv status (dict (get r "status"))
        what (dfor #(k v) (.items action) :if (not-in k #("rollout" "target")) k v)
        target (.get action "target"))
  (when target (setv (get what "target") (target-key target)))
  (setv previous (.get status "lastAction"))
  (setv entry (| what {"ok" ok "error" error "at" now "count" 1}))
  (when (and previous (= (dfor #(k v) (.items previous) :if (not-in k #("at" "count")) k v)
                         (dfor #(k v) (.items entry) :if (not-in k #("at" "count")) k v)))
    (setv entry (| previous {"count" (+ (.get previous "count" 1) 1)})))
  (setv (get status "lastAction") entry)
  (when (and ok target (= (get target "kind") "Deployment") (get target "dryRun"))
    (setv (get status "simulated") (| (.get status "simulated" {}) {(target-key target) (if (is result None) (get action "replicas") result)})))
  (when (and ok (= (get action "op") "annotate"))
    (setv (get status "markedDeployment") (+ (get action "namespace") "/" (get action "name"))))
  (replace state :rollouts (| state.rollouts {name (| r {"status" status})})))


;; --- 要求の振り分け ---------------------------------------------------------------------------

(defn #^ str loose-actor [#^ Request request]
  "盤と task の送り手(旧い client は X-Actor を付けないので、送り元の番地で記録する)。"
  (or (valid-actor request.actor) (+ "anonymous@" (or request.peer "?"))))


(defn #^ tuple respond [#^ ClusterState state #^ Request request #^ int now #^ ClusterTiming timing]
  "要求 1 件 → #(次の状態 status 本文)。"
  (setv method request.method
        parts (lfor p (.split (.strip request.path "/") "/") (url-unquote p))
        head (get parts 0)
        body (or request.body {}))
  (try
    (cond
      ;; --- 資源の口 ---
      (and (= head "resources") (= (len parts) 2) (= method "GET"))
        #(state 200 (list-resources state (get parts 1) now timing))
      (and (= head "resources") (= (len parts) 3) (= method "GET"))
        #(state 200 (get-resource state (get parts 1) (get parts 2) now timing))
      (and (= head "resources") (= (len parts) 2) (= method "POST"))
        (do (setv actor (require-actor request.actor))
            (setv after (settle state (create-resource state (get parts 1) body actor now) actor now timing))
            #(after 201 (get-resource after (get parts 1) (get body "name") now timing)))
      (and (= head "resources") (= (len parts) 3) (= method "PUT"))
        (do (setv actor (require-actor request.actor))
            (setv after (settle state (update-resource state (get parts 1) (get parts 2) body actor) actor now timing))
            #(after 200 (get-resource after (get parts 1) (get parts 2) now timing)))
      (and (= head "resources") (= (len parts) 3) (= method "DELETE"))
        (do (setv actor (require-actor request.actor))
            (setv after (settle state (delete-resource state (get parts 1) (get parts 2) request.query actor now timing)
                                actor now timing))
            #(after 200 {"deleted" (+ (get parts 1) "/" (get parts 2)) "revision" after.revision}))
      (and (= head "resources") (= (len parts) 4) (= (get parts 1) "Service") (= (get parts 3) "readiness") (= method "POST"))
        (do (setv actor (loose-actor request))
            #((settle state (record-readiness state (get parts 2) body now) actor now timing) 200 {"ok" True}))
      ;; 計器の報告は資源の状態を変えない(版も記録も進めない・永続化しない)ので settle を通さない。
      (and (= head "resources") (= (len parts) 4) (= (get parts 1) "Service") (= (get parts 3) "metrics") (= method "POST"))
        #((record-metrics state (get parts 2) body now) 200 {"ok" True})
      (and (= method "GET") (= parts ["metrics"]))
        #(state 200 (PlainText (metrics-text state now timing)))
      (and (= method "GET") (= parts ["events"])) #(state 200 (events-view state request.query))
      ;; --- 旧い口 ---
      (and (= method "PUT") (= parts ["jobs"]))
        (do (setv actor (require-actor (or request.actor (.get body "actor"))))
            (setv #(after status reply) (legacy-put-jobs state (get body "jobs") actor))
            #((if (is after state) state (settle state after actor now timing)) status reply))
      (and (= method "POST") (= parts ["heartbeat"]))
        (do (setv name (get body "name"))
            (setv after (settle state (register-heartbeat state body now) name now timing))
            #(after 200 (heartbeat-reply after name timing (ready-instances after name now timing))))
      (and (= method "GET") (= parts ["state"]))
        #(state 200 (| (state-view state now timing) {"audit" (list (cut state.audit -30 None))
                                                      "drains" (drains-view state now timing)}))
      ;; --- worker の drain(2026-09-25 — drain_policy)---
      (and (= head "workers") (= (len parts) 2) (= method "GET"))
        #(state 200 (worker-view state (get parts 1) now timing))
      (and (= head "workers") (= (len parts) 3) (= (get parts 2) "drain") (= method "POST"))
        (do (setv actor (require-actor request.actor))
            (setv after (settle state (request-drain state (get parts 1) body actor now) actor now timing))
            #(after 200 (worker-view after (get parts 1) now timing)))
      (and (= head "workers") (= (len parts) 3) (= (get parts 2) "drain") (= method "DELETE"))
        (do (setv actor (require-actor request.actor))
            (setv after (settle state (cancel-drain state (get parts 1)) actor now timing))
            #(after 200 (worker-view after (get parts 1) now timing)))
      (and (= method "GET") (= parts ["board"]))
        (do (setv prefix (.get request.query "prefix" ""))
            #(state 200 (if (.get request.query "withVersions")
                            (dfor #(k v) (sorted (.items state.board)) :if (.startswith k prefix)
                                  k {"value" v "resourceVersion" (.get state.board-versions k 1)})
                            (dfor #(k v) (sorted (.items state.board)) :if (.startswith k prefix) k v))))
      (and (= method "POST") (= head "leases") (= (len parts) 2))
        (lease-write state (get parts 1) body now)
      (and (= method "PUT") (= head "board") (> (len parts) 1))
        (board-write state (.join "/" (cut parts 1 None)) body now)
      (and (= method "POST") (= parts ["tasks"]))
        (do (setv #(after status reply) (submit-task state body now))
            #((settle state after (loose-actor request) now timing) status reply))
      (and (= method "GET") (= head "tasks") (= (len parts) 2)) (poll-task state (get parts 1) now)
      (and (= method "DELETE") (= head "tasks") (= (len parts) 2))
        #((settle state (replace state :tasks (dfor #(k v) (.items state.tasks) :if (!= k (get parts 1)) k v))
                  (loose-actor request) now timing)
          200 {"dropped" True})
      True #(state 404 {"error" (.format "知らない要求: {} {}" method request.path)}))
    (except [refused Refused]
      #(state refused.status refused.body))
    (except [error [KeyError TypeError ValueError AttributeError]]
      #(state 400 {"error" (.format "{}: {}" (. (type error) __name__) error)}))))
