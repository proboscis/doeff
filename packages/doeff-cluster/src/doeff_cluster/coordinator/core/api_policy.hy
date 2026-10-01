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
;;;                        時計で書き・判じる — lease_rules.lease-op・2026-09-25)。答え {"ok" "reason" "ttlMs"}
;;;   GET    /workers/<名>                      worker の生存・世代・drain の進み(ready = 生きていて drain 中でない)
;;;   POST   /workers/<名>/drain {"ttlSeconds"? "boot"?}  drain を頼む(何度でも同じ意味・期限だけ延びる)。DELETE で取り消す(drain_policy)。
;;;                                      boot = 頼み手の process の世代。退いた世代の頼みは今の世代に drain を付けない(2026-09-27)
;;;   POST   /warm {"runtimeEnv" "needs" "ttlSeconds" "holder"} · GET /warm/<キー>
;;;                        実行環境の温める表(2026-09-26 — warm_policy。答えは WarmState)
;;;   POST   /tasks/<id>/result {"worker" "instance" "result" "format"}  task の子 process が終わる前に直に届ける結果(#1387 —
;;;                        cluster_policy.absorb-task-result。終わった task には何もしない・届かなければ heartbeat が運ぶ)
;;;   PUT /programs/<sha> {"blob" "versions"} · GET /programs/<sha>
;;;                        詰めた Program の置き場(2026-09-27 — program_policy。宣言の行と heartbeat の返事は sha だけを運ぶ)
;;;   PUT /detached/<key> · GET /detached/<key> · POST /detached/<key>/cancel · DELETE /detached/<key>
;;;                        切り離した task(呼び手と寿命を切り離した task — 送る・読む・取り消す・保持を解く。detached_policy)
;;; 書きには header X-Actor(依頼の主体の id・作業係の名・worker の名)が要る。盤と task は無ければ送り元の番地で記録する。
;;; 旧い口(PUT /jobs・/heartbeat・/board・/tasks)は残す。PUT /jobs は資源ごとの compare-and-set に写す(resource_policy)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [replace])
(import traceback [extract-tb])
(import doeff_cluster.coordinator.intent.request_bodies [BodyMalformed])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request PlainText BodyInvalid])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming Fault RolloutStatus RolloutTarget])
(import doeff_cluster.coordinator.core.cluster_rules [format-version-refusal])
(import doeff_cluster.coordinator.core.metrics_policy [record-metrics metrics-text])
(import doeff_cluster.coordinator.core.cluster_policy [audit-event-to-json reconcile register-heartbeat heartbeat-reply state-view submit-task poll-task absorb-task-result board-write note-liveness
                         lease-write other-generation-boot])
(import doeff_cluster.coordinator.core.resource_policy [Refused refuse stamp require-actor valid-actor service-readiness service-stopped record-readiness
                          running-process list-resources get-resource events-view create-resource update-resource delete-resource
                          legacy-put-jobs COORDINATOR])
(import doeff_cluster.coordinator.core.drain_policy [advance-drains request-drain cancel-drain worker-view superseded-worker-view drains-view])
(import doeff_cluster.coordinator.core.handoff_policy [watch-handoffs])
(import doeff_cluster.coordinator.intent.cluster_model [HandoffPhase])
(import doeff_cluster.coordinator.core.detached_policy [Reply submit-detached detached-read cancel-detached release-detached])
(import doeff_cluster.coordinator.core.rollout_policy [rollout-step rollout-targets target-key deployment-owners drift-status action-due shift-clocks TERMINAL-PHASES])
(import doeff_cluster.coordinator.core.warm_policy [warm-write warm-read])
(import doeff_cluster.coordinator.core.program_policy [program-write program-read sweep-programs])

(setv OBSERVATION-STALE-MS 15000)   ; これより古い k8s の観測は Unknown
(setv ROLLOUT-ACTOR "rollout-controller")
(setv TICK-MS 1000)               ; 調停ループの要求の無い拍の間隔(本番の NextRequests の待ち — coordinator-step・模擬の列・idle_policy が読む)
(setv ROLLOUT-TICK-MS 1000)       ; Rollout の拍の間隔(coordinator.coordinator-step と idle_policy が読む)


(defn #^ ClusterState settle [#^ ClusterState before #^ ClusterState after #^ str actor #^ int now #^ ClusterTiming timing]
  "要求の変化に送り手の版を付け、調停し、調停の変化に coordinator の版を付ける。"
  (setv changed (stamp before after actor now timing)
        ;; drain(2026-09-25): 割り当ての後に、drain 中の worker の上の入れ替えの Service を並べる・付け替える(readiness を読む)。
        ;; 入れ替えの期限(2026-09-26): 最後に、入れ替えの Service の期限の見張りを進める(heartbeat の返事はこの後の状態から作る)。
        ;; 詰めた Program の置き場(改訂 1 の F): 参照の無くなった物を掃除する。
        ;; 生死の切り替わり(#1934): 最後に、生きていないと数える worker の名を今の時刻で求め直す(変われば Worker の status の live と版が進む)。
        reconciled (note-liveness (sweep-programs (watch-handoffs now (advance-drains now (reconcile now changed timing) timing) timing) now) now timing))
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
   task の lease の期限と、worker の最後の連絡の時刻(と、その写し seen-marks)と、Ready を待っている入れ替えの期限の起点
   (HandoffWatch.since-ms — 止まっていた間は期限に数えない・2026-09-26)。生きていた時刻を知らない置き場(2026-09-25 より前)は 0。
   worker の最後の連絡の時刻は、止まる前の最後の印(alive-ms)の時点の沈黙を今から数え直した値になる: 止まる直前まで連絡のあった
   worker は起き直した直後も生きていて、その後に連絡が無ければ移し替えの期限の後に沈黙と判じる。止まる前から沈黙していた worker は
   沈黙のまま(再起動で「いま連絡があった」に戻さない)。止まっていた長さは沈黙に数えない(担い手が一斉に沈黙に倒れ、最初に
   heartbeat を送った worker へ全 job が移ると、元の担い手と二重に動く — 実測 2026-09-23)。"
  (setv gap (if (> state.alive-ms 0) (max 0 (- now state.alive-ms)) 0))
  (if (= gap 0)
      #((replace state :alive-ms now) 0)
      #((replace state
                 :rollouts (dfor #(k r) (.items state.rollouts) k (replace r :status (shift-clocks r.status gap)))
                 :tasks (dfor #(k t) (.items state.tasks) k (replace t :lease-until-ms (+ t.lease-until-ms gap)))
                 :workers (dfor #(k w) (.items state.workers) k (replace w :last-seen-ms (min now (+ w.last-seen-ms gap))))
                 :seen-marks (dfor #(k v) (.items state.seen-marks) k (min now (+ v gap)))
                 :handoffs (dfor #(k w) (.items state.handoffs)
                                 k (if (= w.phase HandoffPhase.WAITING) (replace w :since-ms (min now (+ w.since-ms gap))) w))
                 :alive-ms now)
        gap)))

;; --- Rollout の相手の観測 ----------------------------------------------------------------------

(defn #^ dict target-view [#^ ClusterState state #^ RolloutTarget target #^ RolloutStatus status #^ int now #^ ClusterTiming timing]
  (if (= target.kind "Service")
      ;; 止まっているかは版の判定(resource_policy.version-state の Stopped)と同じ述語 service-stopped で読む(条件を 2 か所に書かない)。
      (do (setv name target.name
                job (next (gfor j state.jobs :if (= j.spec.name name) j) None)
                verdict (service-readiness state name now timing)
                stopped (service-stopped state name now timing))
          {"ready" (get verdict "state") "stopped" stopped "specReplicas" (if job job.replicas None)
           "reason" (if stopped "止まっている" (get verdict "reason"))})
      (do (setv key (+ target.namespace "/" target.name)
                obs (.get state.deployments key))
          (when (or (is obs None) (in "error" obs) (> (- now (.get obs "at" 0)) OBSERVATION-STALE-MS))
            (return {"ready" "Unknown" "stopped" None "specReplicas" None
                     "reason" (if obs (.get obs "error" "観測が古い") "まだ観測していない")}))
          (setv dry target.dry-run
                simulated (.get (or status.simulated {}) (target-key target))
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
        (ready-instance state job.spec.name now timing)))


(defn #^ (| str None) ready-instance [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing]
  "Service name の走っている process の世代の名(Service が Ready と数えられている時だけ・そうでなければ None)。"
  (setv proc (running-process state name now timing))
  (if (and (get proc "ok") (= (get (service-readiness state name now timing) "state") "Ready"))
      (get proc "instance")
      None))


(defn #^ list deployments-to-observe [#^ ClusterState state #^ int now]
  "読むべき Deployment の「ns/名」: 進行中の Rollout の相手は毎拍、台数を持つ(Observing / Complete の)相手は 10 秒ごと。"
  (setv keys [])
  (for [r (.values state.rollouts)]
    (when (not-in r.status.phase TERMINAL-PHASES)
      (for [t (rollout-targets r.spec)]
        (when (= t.kind "Deployment")
          (.append keys (+ t.namespace "/" t.name))))))
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
    (setv spec r.spec status r.status)
    (when (not-in status.phase TERMINAL-PHASES)
      (setv #(status acts) (rollout-step spec status (target-view state spec.from-target status now timing)
                                         (target-view state spec.to-target status now timing) now))
      (setv (get rollouts name) (replace r :status status))
      ;; 失敗が続く action は間を空けて出す(action-due — 1 秒から倍々・上限 60 秒)。
      (.extend actions (gfor a acts :if (action-due status a now) (| a {"rollout" name})))))
  ;; 台数の持ち主と食い違い。進行中の Rollout が扱っている Deployment は、台数が動くのが意図どおりなので数えない。
  ;; 持ち主でなくなった(後の Rollout へ移った・進行中の Rollout が扱い始めた)Rollout の食い違いは消す。
  ;; Observing の Rollout は旧を止め終えて台数を持つ側なので、ここでは「進行中」に数えない(2026-09-24 の実弾: 数えていたので
  ;; 観察の間に本番の配備の流れが replicas を 1 へ戻したのを食い違いとして出せなかった)。
  (setv busy (sfor r (.values rollouts) :if (not-in r.status.phase (| TERMINAL-PHASES #{"Observing"}))
                   t (rollout-targets r.spec) :if (= t.kind "Deployment")
                   (+ t.namespace "/" t.name))
        owners (dfor #(k v) (.items (deployment-owners rollouts)) :if (not-in k busy) k v)
        owning (sfor v (.values owners) (get v 0)))
  (for [#(name r) (.items rollouts)]
    (when (and r.status.drift (not-in name owning))
      (setv (get rollouts name) (replace r :status (replace r.status :drift None :drift-resolved-ms now)))))
  (for [#(key #(name expected)) (.items owners)]
    (setv r (get rollouts name) status r.status)
    (setv status (drift-status status key expected (.get state.deployments key) now))
    (setv (get rollouts name) (replace r :status status))
    (when (and r.spec.mark-deployment (!= status.marked-deployment key))
      (setv #(ns dep) (.split key "/" 1))
      (.append actions {"rollout" name "op" "annotate" "namespace" ns "name" dep
                        "annotations" {naming.owner-annotation (.format "{}/Rollout/{} replicas={}" naming.owner-scope name expected)}})))
  ;; 段が進まなければ状態そのものを返す(reconcile と同じ — stamp が写しを作らずに返す・#1356)。
  #((if (= rollouts state.rollouts) state (replace state :rollouts rollouts)) actions))


(defn #^ ClusterState scale-service [#^ ClusterState state #^ str name #^ int replicas]
  (replace state :jobs (tuple (gfor j state.jobs (if (= j.spec.name name) (replace j :replicas replicas) j)))))


(defn #^ ClusterState record-action [#^ ClusterState state #^ dict action #^ bool ok #^ (| str None) error #^ int now
                                     #^ (| int None) [result None]]
  "実行した action の結果を Rollout の status に残す(dry-run の台数は simulated に)。同じ失敗の繰り返しは数だけ進める。"
  (setv name (get action "rollout") r (.get state.rollouts name))
  (when (is r None) (return state))
  (setv status r.status
        what (dfor #(k v) (.items action) :if (not-in k #("rollout" "target")) k v)
        target (.get action "target"))
  (when target (setv (get what "target") (target-key target)))
  (setv previous status.last-action)
  (setv entry (| what {"ok" ok "error" error "at" now "count" 1}))
  (when (and previous (= (dfor #(k v) (.items previous) :if (not-in k #("at" "count")) k v)
                         (dfor #(k v) (.items entry) :if (not-in k #("at" "count")) k v)))
    (setv entry (| previous {"count" (+ (.get previous "count" 1) 1)})))
  (setv status (replace status :last-action entry))
  (when (and ok target (= target.kind "Deployment") target.dry-run)
    (setv status (replace status :simulated (| (or status.simulated {}) {(target-key target) (if (is result None) (get action "replicas") result)}))))
  (when (and ok (= (get action "op") "annotate"))
    (setv status (replace status :marked-deployment (+ (get action "namespace") "/" (get action "name")))))
  (replace state :rollouts (| state.rollouts {name (replace r :status status)})))


;; --- 要求の振り分け ---------------------------------------------------------------------------

(defn #^ str loose-actor [#^ Request request]
  "盤と task の送り手(旧い client は X-Actor を付けないので、送り元の番地で記録する)。"
  (or (valid-actor request.actor) (+ "anonymous@" (or request.peer "?"))))


(defn #^ tuple detached-reply [#^ ClusterState state #^ Reply reply #^ Request request #^ int now #^ ClusterTiming timing]
  "切り離した task の口の答え(detached_policy の Reply)→ respond の返り値 #(次の状態 status 本文)。状態を変えた答えだけ
   settle(版と出来事の記録)を通す。"
  #((if (is reply.state state) state (settle state reply.state (loose-actor request) now timing)) reply.status reply.body))


(defn #^ tuple unknown-request [#^ ClusterState state #^ Request request]
  "知らない要求に 404 で答えるため(状態は変えない)。"
  #(state 404 {"error" (.format "知らない要求: {} {}" request.method request.path)}))


(defn #^ tuple respond-resources [#^ ClusterState state #^ Request request #^ object body #^ list parts #^ int now #^ ClusterTiming timing]
  "資源の口(/resources の下)の要求に答えるため。#(次の状態 status 本文) を返し、知らない形は 404(respond の振り分けの 1 群 — 1 つの cond では型検査が解析をあきらめた・#1690)。"
  (setv method request.method
        head (get parts 0))
  (cond
    ;; --- 資源の口 ---
    (and (= head "resources") (= (len parts) 2) (= method "GET"))
      #(state 200 (list-resources state (get parts 1) now timing))
    (and (= head "resources") (= (len parts) 3) (= method "GET"))
      #(state 200 (get-resource state (get parts 1) (get parts 2) now timing))
    (and (= head "resources") (= (len parts) 2) (= method "POST"))
      (do (setv actor (require-actor request.actor))
          (setv after (settle state (create-resource state (get parts 1) body actor now) actor now timing))
          #(after 201 (get-resource after (get parts 1) body.name now timing)))
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
    True (unknown-request state request)))


(defn #^ tuple respond-observations [#^ ClusterState state #^ Request request #^ object body #^ list parts #^ int now #^ ClusterTiming timing]
  "計器(/metrics)と出来事(/events)の読みに答えるため。#(次の状態 status 本文) を返し、知らない形は 404(respond の振り分けの 1 群 — 1 つの cond では型検査が解析をあきらめた・#1690)。"
  (setv method request.method
        head (get parts 0))
  (cond
    (and (= method "GET") (= parts ["metrics"]))
      #(state 200 (PlainText (metrics-text state now timing)))
    (and (= method "GET") (= parts ["events"])) #(state 200 (events-view state request.query))
    True (unknown-request state request)))


(defn #^ tuple respond-legacy [#^ ClusterState state #^ Request request #^ object body #^ list parts #^ int now #^ ClusterTiming timing]
  "旧い口(/jobs・/heartbeat・/state)の要求に答えるため。#(次の状態 status 本文) を返し、知らない形は 404(respond の振り分けの 1 群 — 1 つの cond では型検査が解析をあきらめた・#1690)。"
  (setv method request.method
        head (get parts 0))
  (cond
    ;; --- 旧い口 ---
    (and (= method "PUT") (= parts ["jobs"]))
      (do (setv actor (require-actor (or request.actor body.actor)))
          (setv #(after status reply) (legacy-put-jobs state (list body.jobs) actor))
          #((if (is after state) state (settle state after actor now timing)) status reply))
    (and (= method "POST") (= parts ["heartbeat"]) (is-not (format-version-refusal body.format) None))
      #(state 400 {"error" (format-version-refusal body.format)})
    (and (= method "POST") (= parts ["heartbeat"]))
      (do (setv name body.name)
          (setv after (settle state (register-heartbeat state body now) name now timing))
          #(after 200 (heartbeat-reply after name timing (ready-instances after name now timing) :now now
                                       :boot body.boot :statuses body.statuses)))
    (and (= method "GET") (= parts ["state"]))
      #(state 200 (| (state-view state now timing) {"audit" (lfor e (cut state.audit -30 None) (audit-event-to-json e))
                                                    "drains" (drains-view state now timing)}))
    True (unknown-request state request)))


(defn #^ tuple respond-workers [#^ ClusterState state #^ Request request #^ object body #^ list parts #^ int now #^ ClusterTiming timing]
  "worker の読みと drain の頼み(/workers の下・drain_policy)に答えるため。#(次の状態 status 本文) を返し、知らない形は 404(respond の振り分けの 1 群 — 1 つの cond では型検査が解析をあきらめた・#1690)。"
  (setv method request.method
        head (get parts 0))
  (cond
    ;; --- worker の drain(2026-09-25 — drain_policy)---
    (and (= head "workers") (= (len parts) 2) (= method "GET"))
      #(state 200 (worker-view state (get parts 1) now timing))
    (and (= head "workers") (= (len parts) 3) (= (get parts 2) "drain") (= method "POST"))
      (do (setv actor (require-actor request.actor))
          (setv after (settle state (request-drain state (get parts 1) body actor now) actor now timing))
          ;; 今の世代でない頼み(退いた世代・見ていない世代の preStop)には、その世代の待ちの答え(drain_policy.superseded-worker-view)。
          #(after 200 (if (other-generation-boot after (get parts 1) body.boot)
                          (superseded-worker-view after (get parts 1) body.boot now timing)
                          (worker-view after (get parts 1) now timing))))
    (and (= head "workers") (= (len parts) 3) (= (get parts 2) "drain") (= method "DELETE"))
      (do (setv actor (require-actor request.actor))
          (setv after (settle state (cancel-drain state (get parts 1)) actor now timing))
          #(after 200 (worker-view after (get parts 1) now timing)))
    True (unknown-request state request)))


(defn #^ tuple respond-board [#^ ClusterState state #^ Request request #^ object body #^ list parts #^ int now #^ ClusterTiming timing]
  "盤の読み書き(/board)と lease の書き(/leases)に答えるため。#(次の状態 status 本文) を返し、知らない形は 404(respond の振り分けの 1 群 — 1 つの cond では型検査が解析をあきらめた・#1690)。"
  (setv method request.method
        head (get parts 0))
  (cond
    (and (= method "GET") (= parts ["board"]))
      (do (setv prefix (.get request.query "prefix" ""))
          #(state 200 (if (.get request.query "withVersions")
                          (dfor #(k row) (sorted (.items state.board)) :if (.startswith k prefix)
                                k {"value" row.value "resourceVersion" row.version})
                          (dfor #(k row) (sorted (.items state.board)) :if (.startswith k prefix) k row.value))))
    (and (= method "POST") (= head "leases") (= (len parts) 2))
      (lease-write state (get parts 1) body now)
    (and (= method "PUT") (= head "board") (> (len parts) 1))
      (board-write state (.join "/" (cut parts 1 None)) body now)
    True (unknown-request state request)))


(defn #^ tuple respond-tasks [#^ ClusterState state #^ Request request #^ object body #^ list parts #^ int now #^ ClusterTiming timing]
  "task の頼み・問い・結果・取り下げ(/tasks の下)に答えるため。#(次の状態 status 本文) を返し、知らない形は 404(respond の振り分けの 1 群 — 1 つの cond では型検査が解析をあきらめた・#1690)。"
  (setv method request.method
        head (get parts 0))
  (cond
    (and (= method "POST") (= parts ["tasks"]))
      (do (setv #(after status reply) (submit-task state body now))
          ;; 断った本文(400・429)は状態を変えない — 調停も通さず同じ状態を返す。
          #((if (is after state) state (settle state after (loose-actor request) now timing)) status reply))
    (and (= method "GET") (= head "tasks") (= (len parts) 2)) (poll-task state (get parts 1) now)
    ;; task の子 process が終わる前に直に届ける結果(#1387 — cluster_policy.absorb-task-result)。
    (and (= method "POST") (= head "tasks") (= (len parts) 3) (= (get parts 2) "result"))
      (do (setv #(after status reply) (absorb-task-result state (get parts 1) body now))
          #((if (is after state) state (settle state after (loose-actor request) now timing)) status reply))
    (and (= method "DELETE") (= head "tasks") (= (len parts) 2))
      #((settle state (replace state :tasks (dfor #(k v) (.items state.tasks) :if (!= k (get parts 1)) k v))
                (loose-actor request) now timing)
        200 {"dropped" True})
    True (unknown-request state request)))


(defn #^ tuple respond-stores [#^ ClusterState state #^ Request request #^ object body #^ list parts #^ int now #^ ClusterTiming timing]
  "温める表(/warm)・詰めた Program の置き場(/programs)・切り離した task(/detached)の要求に答えるため。#(次の状態 status 本文) を返し、知らない形は 404(respond の振り分けの 1 群 — 1 つの cond では型検査が解析をあきらめた・#1690)。"
  (setv method request.method
        head (get parts 0))
  (cond
    ;; --- 実行環境の温める表(2026-09-26 — warm_policy)---
    (and (= method "POST") (= parts ["warm"]))
      (do (setv actor (loose-actor request)
                #(after status reply) (warm-write state body now actor timing))
          #((if (is after state) state (settle state after actor now timing)) status reply))
    (and (= method "GET") (= head "warm") (= (len parts) 2)) (warm-read state (get parts 1) now timing)
    ;; --- 詰めた Program の置き場(2026-09-27 — program_policy・改訂 1 の F)---
    (and (= method "PUT") (= head "programs") (= (len parts) 2))
      (do (setv actor (loose-actor request)
                #(after status reply) (program-write state (get parts 1) body now))
          #((if (is after state) state (settle state after actor now timing)) status reply))
    (and (= method "GET") (= head "programs") (= (len parts) 2)) (program-read state (get parts 1))
    ;; --- 切り離した task(2026-09-25 — detached_policy)---
    (and (= method "PUT") (= head "detached") (= (len parts) 2))
      (detached-reply state (submit-detached state (get parts 1) body now) request now timing)
    (and (= method "GET") (= head "detached") (= (len parts) 2))
      (detached-reply state (detached-read state (get parts 1) now timing) request now timing)
    (and (= method "POST") (= head "detached") (= (len parts) 3) (= (get parts 2) "cancel"))
      (detached-reply state (cancel-detached state (get parts 1) now) request now timing)
    (and (= method "DELETE") (= head "detached") (= (len parts) 2))
      (detached-reply state (release-detached state (get parts 1)) request now timing)
    True (unknown-request state request)))


(defn #^ tuple respond [#^ ClusterState state #^ Request request #^ int now #^ ClusterTiming timing #^ object body]
  "要求 1 件と、その本文を道の型に解いた値(coordinator/protocol/request_bodies の body-of — 型の値・まだ型にしていない道は JSON の
   object・形が合わなければ BodyMalformed)→ #(次の状態 status 本文)。"
  (setv method request.method
        parts (list request.parts)
        head (get parts 0))
  (try
    (when (isinstance body BodyMalformed)
      (raise (BodyInvalid body.reason)))
    (match head
      "resources" (respond-resources state request body parts now timing)
      (| "metrics" "events") (respond-observations state request body parts now timing)
      (| "jobs" "heartbeat" "state") (respond-legacy state request body parts now timing)
      "workers" (respond-workers state request body parts now timing)
      (| "board" "leases") (respond-board state request body parts now timing)
      "tasks" (respond-tasks state request body parts now timing)
      (| "warm" "programs" "detached") (respond-stores state request body parts now timing)
      _ (unknown-request state request))
    (except [refused Refused]
      #(state refused.status refused.body))
    (except [invalid BodyInvalid]
      #(state 400 {"error" (str invalid)}))
    ;; 送り手の誤りの型(Refused・BodyInvalid)でない例外は coordinator の中の欠陥 — 400 に畳まず 500 の Fault で返す。状態は受ける前の
    ;; まま(途中まで進めた変化を残さない)。log の 1 行は coordinator-step が CoordinatorFault で出す(#1024)。
    ;; where = 例外が上がった一番内側の所(file:行 関数)。
    (except [error Exception]
      (setv inner (get (extract-tb error.__traceback__) -1))
      #(state 500 (Fault method request.path (. (type error) __name__) (str error)
                         (.format "{}:{} {}" (get (.rsplit (.replace inner.filename "\\" "/") "/" 1) -1) inner.lineno inner.name))))))
