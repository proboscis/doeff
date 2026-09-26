;;; worker の drain の純粋な判断(2026-09-25)。I/O はしない。
;;;
;;; worker の Pod を入れ替える(配備のたびに DaemonSet が 1 台ずつ作り直す)前に、その上の書き手を別の worker へ移す。
;;; 同じ worker の中の版の入れ替え(handoff — worker_policy.handoff-actions / retired-actions)と同じ形を worker をまたいで行う:
;;;
;;;   1. drain を頼まれた worker(POST /workers/<名>/drain)には、新しい置き先(job・task)を割り当てない(cluster_policy.place-jobs)。
;;;   2. その上の入れ替え(update: handoff)の Service を、条件(requires)を満たす別の生きた worker へ**並べて**置く(surge・1 つだけ)。
;;;      並べた先の worker は新しい process を起こし、lease を旧が持つ間は standby で待ち、準備できたを報告する。
;;;   3. coordinator が並べた先の process を Ready と数えたら(standby の Ready でよい — 同じ worker の中の入れ替えと同じ判定)、
;;;      置き先(placements)を並べた先へ付け替える。旧い担い手は次の heartbeat で宣言から外れた job を止め、lease を返す
;;;      (worker_policy の ReleaseLeases)。新は空いた lease を取る。どの拍でも lease を持てる Ready の process が 1 つ以上在る。
;;;   4. 移す先が無い(もう 1 台が死んでいる・drain 中・条件を満たさない・空きが無い)なら、並べず・旧を止めず、drain の進みに
;;;      「移せない」と理由を名乗る(空白を作らない)。移す先が現れたら次の拍で並べる。
;;;   入れ替えでない Service は今までどおり止めて移す(cluster_policy.place-jobs — 他に置ける先が在る時だけ外す)。
;;;
;;; drain は期限(ttlSeconds・頼み直すたびに延びる)で消え、別の process の世代の heartbeat が来ても解ける
;;; (cluster_policy.absorb-boot — Pod を作り直した後の worker は空けない)。取り消しは DELETE /workers/<名>/drain。
(import dataclasses [replace])
(import .cluster_model [ClusterState ClusterTiming Drain Placement])
(import .cluster_policy [alive eligible can-take draining-workers load-of other-generation-boot LIVE-PHASES MAX-EVENTS])
(import .resource_policy [refuse service-readiness])

(setv DRAIN-DEFAULT-TTL-SECONDS 300)          ; 頼み直さない drain が消えるまで(preStop は数秒ごとに頼み直す)
(setv DRAIN-MAX-TTL-SECONDS (* 24 3600))


;; --- 頼む・取り消す -------------------------------------------------------------------------

(defn #^ ClusterState request-drain [#^ ClusterState state #^ str name #^ dict body #^ str actor #^ int now]
  "POST /workers/<名>/drain {\"ttlSeconds\"?}。何度頼んでも同じ意味(始めた時刻と世代は最初の頼みのまま・期限だけ延びる)。"
  (setv worker (.get state.workers name)
        ttl (.get body "ttlSeconds" DRAIN-DEFAULT-TTL-SECONDS)
        boot (.get body "boot"))
  (when (is worker None) (refuse 404 (+ "知らない worker: " name)))
  (when (not (and (isinstance ttl #(int float)) (not (isinstance ttl bool)) (< 0 ttl (+ DRAIN-MAX-TTL-SECONDS 1))))
    (refuse 400 (.format "ttlSeconds は 0 より大きく {} 以下: {!r}" DRAIN-MAX-TTL-SECONDS ttl)))
  (when (not (or (is boot None) (isinstance boot str)))
    (refuse 400 (.format "boot は頼み手の worker の process の世代の文字列: {!r}" boot)))
  ;; 今の世代でない頼み(退いた世代 = 旧い Pod の preStop・一度も見ていない世代 = 最初の heartbeat の前に消された新しい Pod の
  ;; preStop)は、同じ名の今の世代に drain を付けない(2026-09-27)。答えは superseded-worker-view(その世代に置いた task が
  ;; 終わるまで待たせる — 見ていない世代には task が無いので drained)。boot の無い頼み(旧い版の preStop・手の頼み)だけ今の世代に付ける。
  (when (other-generation-boot state name boot) (return state))
  (setv until (+ now (int (* 1000 ttl)))
        current (.get state.drains name))
  (replace state :drains (| state.drains
                            {name (if (and current (> current.until-ms now))
                                      (replace current :until-ms until)
                                      (Drain name now until worker.boot actor))})))


(defn #^ ClusterState cancel-drain [#^ ClusterState state #^ str name]
  "DELETE /workers/<名>/drain。並べた置き先は次の調停(advance-drains)で外れる(旧はそのまま動き続ける)。"
  (if (in name state.drains)
      (replace state :drains (dfor #(k v) (.items state.drains) :if (!= k name) k v))
      state))


;; --- 調停 -----------------------------------------------------------------------------------

(defn #^ (| str None) move-target [#^ int now #^ ClusterState state #^ object job #^ str source #^ ClusterTiming timing
                                   #^ frozenset draining #^ dict load]
  "入れ替えの job を source から並べて置く先の worker の名(空きの多い順・同点は名前順)。無ければ None。"
  (setv candidates (sorted (lfor w (.values state.workers)
                                 :if (and (!= w.name source) (can-take now state job w load timing draining))
                                 w)
                           :key (fn [w] #((.get load w.name 0) w.name))))
  (if candidates (. (get candidates 0) name) None))


(defn #^ bool surge-holds [#^ int now #^ ClusterState state #^ object job #^ Placement surge #^ ClusterTiming timing
                           #^ frozenset draining]
  "並べた置き先を持ち続けてよいか: 元の置き先が drain 中の worker に在り、並べた先が生きていて条件を満たし、drain 中でない。"
  (setv placed (.get state.placements job.spec.name)
        w (.get state.workers surge.worker))
  (and (> job.replicas 0) job.spec.handoff
       (is-not placed None) (in placed.worker draining) (!= placed.worker surge.worker)
       (is-not w None) (alive now w timing.lease-ms) (eligible job w) (not-in w.name draining)))


(defn #^ ClusterState advance-drains [#^ int now #^ ClusterState state #^ ClusterTiming timing]
  "1 拍: 要らなくなった並べた置き先を外し、Ready になった並べた置き先へ付け替え、まだ並べていない入れ替えの job を並べる。
   変える物が無ければ同じ object。"
  (setv draining (draining-workers state now)
        jobs (dfor j state.jobs j.spec.name j))
  (when (and (not draining) (not state.surges)) (return state))
  (setv surges {} placements (dict state.placements) events [])
  ;; 1. 並べた置き先の見直し(drain が解けた・job が消えた・並べた先が死んだ等は外す — 並べた先の worker は次の heartbeat で止める)。
  (for [#(name surge) (sorted (.items state.surges))]
    (setv job (.get jobs name))
    (if (and (is-not job None) (surge-holds now state job surge timing draining))
        (setv (get surges name) surge)
        (.append events {"at" now "job" name "from" surge.worker "to" None "generation" surge.generation
                         "drain" "並べた置き先を外した"})))
  (setv state (replace state :surges surges))
  ;; 2. 並べた先の process が Ready なら付け替える。3. drain 中の worker の上の入れ替えの job を並べる。
  (setv load (load-of state placements))
  (for [#(name placed) (sorted (.items state.placements))]
    (setv job (.get jobs name))
    (when (and (is-not job None) (> job.replicas 0) job.spec.handoff (in placed.worker draining))
      (setv surge (.get surges name))
      (cond
        (is-not surge None)
          (when (= (get (service-readiness state name now timing surge) "state") "Ready")
            (setv (get placements name) surge)
            (del (get surges name))
            (.append events {"at" now "job" name "from" placed.worker "to" surge.worker "generation" surge.generation
                             "drain" "並べた先が Ready — 置き先を付け替えた"}))
        True
          (do (setv target (move-target now state job placed.worker timing draining load))
              (when (is-not target None)
                (setv (get surges name) (Placement name target (+ placed.generation 1) now))
                (+= (get load target) 1)
                (.append events {"at" now "job" name "from" placed.worker "to" target "generation" (+ placed.generation 1)
                                 "drain" "並べて置いた(standby の Ready を待つ)"}))))))
  (if (and (= surges state.surges) (= placements state.placements) (not events))
      state
      (replace state :surges surges :placements placements
               :events (tuple (cut (+ (list state.events) events) (- MAX-EVENTS) None)))))


;; --- 見せる形 ------------------------------------------------------------------------------

(defn #^ list live-rows [#^ ClusterState state #^ str worker]
  "worker の最新の報告のうち、まだ動いている job の名(task は数えない — task は drain で移さない)。退いた process は元の名で数える。"
  (setv st (.get state.statuses worker {}))
  (sorted (sfor row (.get st "jobs" [])
                :setv name (or (.get row "retiredFrom") (.get row "name") "")
                :if (and (in (.get row "phase") LIVE-PHASES) (not (.startswith name "task/")))
                name)))


(defn #^ list detached-rows [#^ ClusterState state #^ str worker]
  "worker に置いた、まだ終わっていない切り離した task の名(task/<id>)。drain はこれが 0 になるまで Drained にしない
   (RemoteJob の task は数えない — 呼び手と寿命を共にし、drain で止まってよい)。"
  (sorted (gfor t (.values state.tasks) :if (and t.detached (in t.phase #("assigned" "preparing")) (= t.worker worker))
                (+ "task/" t.id))))


(defn #^ (| dict None) drain-view [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing]
  "drain の進み。remaining = まだこの worker に置かれている job と、この worker の上でまだ動いている job と、この worker に置いた
   終わっていない切り離した task(全部が 0 で drained)。
   moving = 並べた先の worker(Ready 待ち)。blocked = 移せない job と理由。drain が無ければ None。"
  (setv d (.get state.drains name))
  (when (or (is d None) (<= d.until-ms now)) (return None))
  (setv draining (draining-workers state now)
        jobs (dfor j state.jobs j.spec.name j)
        load (load-of state state.placements)
        placed (sorted (gfor #(n a) (.items state.placements) :if (= a.worker name) n))
        ;; 切り離した task(2026-09-25)は移せない(走らせ直さない)ので、この worker の上で終わるまで drain を待たせる。
        remaining (sorted (| (set placed) (set (live-rows state name)) (set (detached-rows state name))))
        moving (dfor n placed :if (in n state.surges) n (. (get state.surges n) worker))
        blocked {})
  (for [n placed]
    (setv job (.get jobs n))
    (when (and (is-not job None) (not-in n moving)
               (is (move-target now state job name timing draining load) None))
      (setv (get blocked n)
            (.format "移す先が無い(求める label {}・固定 {}。生きていて drain 中でなく空きの在る別の worker が無い)— 旧を止めずに待つ"
                     (dict job.requires) job.pin))))
  {"worker" name "sinceMs" d.since-ms "untilMs" d.until-ms "boot" d.boot "actor" d.actor
   "phase" (cond (not remaining) "Drained" (and blocked (not moving)) "Blocked" True "Draining")
   "drained" (not remaining)
   "remaining" remaining
   "moving" moving
   "blocked" blocked
   "movingReady" (dfor #(n w) (.items moving)
                       n (get (service-readiness state n now timing (get state.surges n)) "reason"))})


(defn #^ dict worker-view [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing]
  "GET /workers/<名>: 生存・世代・drain の進み。ready = 生きていて drain 中でない(新しい Pod の readinessProbe が見る)。"
  (setv w (.get state.workers name))
  (when (is w None) (refuse 404 (+ "知らない worker: " name)))
  (setv drain (drain-view state name now timing)
        live (alive now w timing.lease-ms))
  {"name" name "alive" live "silentMs" (- now w.last-seen-ms) "boot" w.boot "labels" (dict w.labels)
   "draining" (is-not drain None) "drain" drain
   "ready" (and live (is drain None))})


(defn #^ dict superseded-worker-view [#^ ClusterState state #^ str name #^ str boot #^ int now #^ ClusterTiming timing]
  "退いた世代の process(旧い Pod の preStop)が drain を頼んだ時の答え(2026-09-27)。名の置き先と drain は今の
   世代の物なので、退いた世代が待つのは、その世代に置いてまだ終わっていない切り離した task だけ(0 で drained — preStop が終わる)。
   形は worker-view と同じ(drain_client.drain-outcome が drain.drained を読む)。"
  (setv w (get state.workers name)
        remaining (sorted (gfor t (.values state.tasks)
                                :if (and t.detached (in t.phase #("assigned" "preparing")) (= t.worker name) (= t.boot boot))
                                (+ "task/" t.id))))
  {"name" name "alive" (alive now w timing.lease-ms) "silentMs" (- now w.last-seen-ms) "boot" w.boot "labels" (dict w.labels)
   "draining" True "superseded" True
   "drain" {"worker" name "boot" boot "superseded" True
            "phase" (if remaining "Draining" "Drained") "drained" (not remaining) "remaining" remaining
            "moving" {} "blocked" {} "movingReady" {}}
   "ready" False})


(defn #^ dict drains-view [#^ ClusterState state #^ int now #^ ClusterTiming timing]
  "GET /state の drains(worker の名 → drain の進み)。"
  (dfor n (sorted state.drains)
        :setv v (drain-view state n now timing)
        :if (is-not v None)
        n v))
