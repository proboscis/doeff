;;; worker の drain の純粋な判断(2026-09-25)。I/O はしない。
;;;
;;; worker の Pod を入れ替える(配備のたびに DaemonSet が 1 台ずつ作り直す)前に、その上の書き手を別の worker へ移す。
;;; 同じ worker の中の版の入れ替え(handoff — worker_policy.handoff-actions / retired-actions)と同じ形を worker をまたいで行う:
;;;
;;;   1. drain を頼まれた worker(POST /workers/<名>/drain)には、新しい置き先(job・task)を割り当てない(cluster_policy.place-jobs)。
;;;   2. その上の入れ替え(update: handoff)の Service を、能力(needs)の合う別の生きた worker へ**並べて**置く(surge・1 つだけ)。
;;;      並べた先の worker は新しい process を起こし、lease を旧が持つ間は standby で待ち、準備できたを報告する。
;;;   3. coordinator が並べた先の process を Ready と数えたら(standby の Ready でよい — 同じ worker の中の入れ替えと同じ判定)、
;;;      置き先(placements)を並べた先へ付け替える。旧い担い手は次の heartbeat で宣言から外れた job を止め、lease を返す
;;;      (worker_policy の ReleaseLeases)。新は空いた lease を取る。どの拍でも lease を持てる Ready の process が 1 つ以上在る。
;;;   4. 能力(と固定)の合う別の worker は名簿に居るが、今は移せない(死んでいる・drain 中・空きが無い)なら、並べず・旧を止めず、drain の
;;;      進みに blocked と理由を名乗って待つ(空白を作らない)。移す先が現れたら次の拍で並べる。
;;;   5. 能力(と固定)の合う別の worker が名簿に 1 台も無い job は、待っても移す先が来ない — 移せないと確定し(unmovable)、drain の
;;;      残りに数えない。残りが 0 なら drained を返し、preStop は上限を待たずに終わる(job はこの worker の上で止まるまで動き、Pod を
;;;      作り直した新しい世代が同じ置き先で動かし直す・#3669 — 2026-10-05 に記録の表の service の worker の preStop が上限 90 秒まで
;;;      待ち、その間 service に届かなかった)。判断はここ(drain-view)の 1 か所 — preStop・版上げ・readiness は同じ答えを読む。
;;;   入れ替えでない Service は今までどおり止めて移す(cluster_policy.place-jobs — 他に置ける先が在る時だけ外す)。
;;;   drain の間、この worker には温める表の行(warm)を配らない(cluster_policy.warms-for)— 空ける worker に新しい版の実行環境の
;;;   準備を置かない(#3669)。
;;;
;;; drain の頼みは 2 つの道で来る: POST /workers/<名>/drain(本番の preStop・手の頼み)と、止まり始めを名乗る heartbeat
;;; (absorb-stopping — sigterm などで preStop を通らずに止まる worker・#2819)。
;;; drain は期限(ttlSeconds・頼み直すたびに延びる)で消える。取り消しは DELETE /workers/<名>/drain。
;;; drain が付く先は頼みの形で決まる(#4177):
;;;   世代(boot)つきの頼み(Pod の preStop・止まり始めを告げる heartbeat)= その世代に付く。別の世代の heartbeat が来たら解ける
;;;     (cluster_policy.absorb-boot — Pod を作り直した後の worker は空けない)。
;;;   世代を付けない頼み(版上げの Program・手の頼み)= worker の名前に付く。Pod を何度作り直しても、頼み手が DELETE するか期限が
;;;     来るまで保つ(2026-10-09: 1 つの drain の間に Pod が 2 度入れ替わり、1 度目で解けた後に置かれた仕事が 2 度目で止められた)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [replace])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterJob ClusterState Drain Placement DrainPhase DrainProgress WorkerDrainView])
(import doeff_cluster.coordinator.intent.request_bodies [DrainBody])
(import doeff_cluster.coordinator.core.cluster_policy [alive eligible can-take draining-workers load-of other-generation-boot LIVE-PHASES MAX-EVENTS])
(import doeff_cluster.coordinator.core.resource_policy [refuse service-readiness])

(setv DRAIN-DEFAULT-TTL-SECONDS 300)          ; 頼み直さない drain が消えるまで(preStop は数秒ごとに頼み直す)
(setv DRAIN-MAX-TTL-SECONDS (* 24 3600))


;; --- 頼む・取り消す -------------------------------------------------------------------------

(defn #^ ClusterState request-drain [#^ ClusterState state #^ str name #^ DrainBody body #^ str actor #^ int now]
  "POST /workers/<名>/drain {\"ttlSeconds\"? \"boot\"?}。何度頼んでも同じ意味(始めた時刻は最初の頼みのまま・期限だけ延びる)。
   世代つきの頼みの drain はその世代に、世代を付けない頼みの drain は worker の名前に付く(頭の註 — 世代に付いた drain の最中に
   世代を付けない頼みが来たら、名前に付け替える。名前に付いた drain は世代つきの頼みでは世代へ戻らない)。"
  (setv worker (.get state.workers name)
        ttl (if (is body.ttl-seconds None) DRAIN-DEFAULT-TTL-SECONDS body.ttl-seconds)
        boot body.boot)
  (when (is worker None) (refuse 404 (+ "知らない worker: " name)))
  (when (not (and (isinstance ttl #(int float)) (not (isinstance ttl bool)) (< 0 ttl (+ DRAIN-MAX-TTL-SECONDS 1))))
    (refuse 400 (.format "ttlSeconds は 0 より大きく {} 以下: {!r}" DRAIN-MAX-TTL-SECONDS ttl)))
  ;; 今の世代でない頼み(退いた世代 = 旧い Pod の preStop・一度も見ていない世代 = 最初の heartbeat の前に消された新しい Pod の
  ;; preStop)は、同じ名の今の世代に drain を付けない(2026-09-27)。答えは superseded-worker-view(その世代に置いた task が
  ;; 終わるまで待たせる — 見ていない世代には task が無いので drained)。boot の無い頼み(版上げの Program・手の頼み)は名前に付ける。
  (when (other-generation-boot state name boot) (return state))
  (setv until (+ now (int (* 1000 ttl)))
        current (.get state.drains name))
  (replace state :drains (| state.drains
                            {name (if (and current (> current.until-ms now))
                                      (replace current :until-ms until :boot (if (is boot None) None current.boot))
                                      (Drain name now until boot actor))})))


(val STOPPING-DRAIN-ACTOR "worker-stopping")   ; 止まり始めの名乗りから立てた drain の頼み手(GET /state の drains に出る)


(defn #^ ClusterState absorb-stopping [#^ ClusterState state #^ str name #^ (| str None) boot #^ bool stopping #^ int now]
  "heartbeat で止まり始めを名乗った世代(stopping — sigterm を受けた worker・#2819)を、その worker 自身の drain の頼みとして
   request-drain に通し、新しい置き先と task をその世代へ置かないため。drain の頼み(本番の preStop)を通らない止め(機体の終了・
   手の kill)でも、止まる途中の世代に置いた task が始まらずに lease まで止まる穴を塞ぐ。世代の扱い(今の世代でない名乗りは付けない)と
   期限(既定の 300 秒)は request-drain のまま。既に drain の最中なら頼み直さない(明示の drain の期限を縮めない)。名乗りをやめても
   解かない — 解くのは新しい世代の heartbeat(cluster_policy.absorb-boot)と期限(sweep-drains)。"
  (if (and stopping (not-in name (draining-workers state now)))
      (request-drain state name (DrainBody :boot boot) STOPPING-DRAIN-ACTOR now)
      state))


(defn #^ ClusterState cancel-drain [#^ ClusterState state #^ str name]
  "DELETE /workers/<名>/drain。並べた置き先は次の調停(advance-drains)で外れる(旧はそのまま動き続ける)。"
  (if (in name state.drains)
      (replace state :drains (dfor #(k v) (.items state.drains) :if (!= k name) k v))
      state))


;; --- 調停 -----------------------------------------------------------------------------------

(defn #^ (| str None) move-target [#^ int now #^ ClusterState state #^ ClusterJob job #^ str source #^ ClusterTiming timing
                                   #^ frozenset draining #^ dict load]
  "入れ替えの job を source から並べて置く先の worker の名(担っている数の少ない順・同点は名前順)。無ければ None。並べた置き先(surge)は
   job の側で数える(can-take — job の側の空き job-room-of と全体の空き task-room-of の両方が要る)ので、job の側に空きが無ければ並べず、
   入れ替えは空くまで待つ(task のために空けておく分 task-reserve を食わない)。load = cluster_policy.load-of の答え。"
  (setv candidates (sorted (lfor w (.values state.workers)
                                 :if (and (!= w.name source) (can-take now state job w load timing draining))
                                 w)
                           :key (fn [w] #((+ (. (get load w.name) jobs) (. (get load w.name) tasks)) w.name))))
  (if candidates (. (get candidates 0) name) None))


(defn #^ bool surge-holds [#^ int now #^ ClusterState state #^ ClusterJob job #^ Placement surge #^ ClusterTiming timing
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
  ;; 2. 並べた先の process が Ready なら付け替える。3. drain 中の worker の上の入れ替えの job を並べる(並べた置き先は job の側で数える —
  ;;    load-of の jobs。並べるたびに同じ数えを進める)。
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
                (setv (get surges name) (Placement name target (+ placed.generation 1) now)
                      used (get load target)
                      (get load target) (replace used :jobs (+ used.jobs 1)))
                (.append events {"at" now "job" name "from" placed.worker "to" target "generation" (+ placed.generation 1)
                                 "drain" "並べて置いた(standby の Ready を待つ)"}))))))
  (if (and (= surges state.surges) (= placements state.placements) (not events))
      state
      (replace state :surges surges :placements placements
               :events (tuple (cut (+ (list state.events) events) (- MAX-EVENTS) None)))))


;; --- 見せる形 ------------------------------------------------------------------------------

(defn #^ list live-rows [#^ ClusterState state #^ str worker]
  "worker の最新の報告のうち、まだ動いている job の名(task は数えない — task は drain で移さない)。退いた process は元の名で数える。"
  (setv st (.get state.statuses worker))
  (sorted (sfor row (if (is st None) #() st.jobs)
                :setv name (or row.retired-from row.name)
                :if (and (in row.phase LIVE-PHASES) (not (.startswith name "task/")))
                name)))


(defn #^ list detached-rows [#^ ClusterState state #^ str worker]
  "worker に置いた、まだ終わっていない切り離した task の名(task/<id>)。drain はこれが 0 になるまで Drained にしない
   (RemoteJob の task は数えない — 呼び手と寿命を共にし、drain で止まってよい)。"
  (sorted (gfor t (.values state.tasks) :if (and t.detached (in t.phase #("assigned" "preparing")) (= t.worker worker))
                (+ "task/" t.id))))


(defn #^ (| DrainProgress None) drain-view [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing]
  "drain の進み — 空けてよいかの判断の 1 か所(preStop の drain_client.drain-outcome が drained を読み、GET /workers/<名> と GET /state の
   drains も同じ答えを出す)。
   remaining = まだこの worker に置かれている job と、この worker の上でまだ動いている job と、この worker に置いた終わっていない
   切り離した task から、移せないと確定した job(unmovable)を除いた物(0 で drained)。
   unmovable = この worker に置かれた job のうち、能力(と固定)の合う別の worker が名簿に 1 台も無い物の名(生死・drain・空きを問わない —
   待っても移す先は来ないので待たず、この worker の上で止まるまで動かす・#3669)。
   moving = 並べた先の worker(Ready 待ち)。blocked = 能力の合う別の worker は名簿に居るが、今は死んでいる・drain 中・空きが無いので
   移せず待っている job と理由(待てば移す先が来うる)。drain が無ければ None。"
  (setv d (.get state.drains name))
  (when (or (is d None) (<= d.until-ms now)) (return None))
  (setv draining (draining-workers state now)
        jobs (dfor j state.jobs j.spec.name j)
        load (load-of state state.placements)
        placed (sorted (gfor #(n a) (.items state.placements) :if (= a.worker name) n))
        ;; 能力の合う別の worker が名簿に 1 台も無い job は、待っても移せない(2026-10-05 の記録の表の service — 記録の能力を名乗る
        ;; worker が 1 台だけで、preStop が上限 90 秒まで待ち、その間 service に届かなかった)。待たずに drained へ進め、この worker の
        ;; 上で止まるまで動かす。
        unmovable (tuple (gfor n placed
                               :setv job (.get jobs n)
                               :if (and (is-not job None)
                                        (not (any (gfor w (.values state.workers) (and (!= w.name name) (eligible job w))))))
                               n))
        ;; 切り離した task(2026-09-25)は移せない(走らせ直さない)ので、この worker の上で終わるまで drain を待たせる。
        remaining (sorted (- (| (set placed) (set (live-rows state name)) (set (detached-rows state name))) (set unmovable)))
        moving (dfor n placed :if (in n state.surges) n (. (get state.surges n) worker))
        blocked {})
  (for [n placed]
    (setv job (.get jobs n))
    (when (and (is-not job None) (not-in n moving) (not-in n unmovable)
               (is (move-target now state job name timing draining load) None))
      (setv (get blocked n)
            (.format "移す先が無い(要る能力 {}・固定 {}。能力の合う別の worker は名簿に居るが、生きていて drain 中でなく空きの在る物が無い)— 旧を止めずに待つ"
                     (list job.needs) job.pin))))
  (DrainProgress :worker name :boot d.boot :superseded False :since-ms d.since-ms :until-ms d.until-ms :actor d.actor
                 :phase (cond (not remaining) DrainPhase.DRAINED (and blocked (not moving)) DrainPhase.BLOCKED True DrainPhase.DRAINING)
                 :remaining (tuple remaining)
                 :moving moving
                 :blocked blocked
                 :unmovable unmovable
                 :moving-ready (dfor #(n w) (.items moving)
                                     n (get (service-readiness state n now timing (get state.surges n)) "reason"))))


(defn #^ WorkerDrainView worker-view [#^ ClusterState state #^ str name #^ int now #^ ClusterTiming timing]
  "GET /workers/<名>: 生存・世代・drain の進み。ready = 生きていて drain 中でない(新しい Pod の readinessProbe が見る)。"
  (setv w (.get state.workers name))
  (when (is w None) (refuse 404 (+ "知らない worker: " name)))
  (setv drain (drain-view state name now timing)
        live (alive now w timing.lease-ms))
  (WorkerDrainView :info w :alive live :silent-ms (- now w.last-seen-ms) :superseded False :drain drain
                   :ready (and live (is drain None))))


(defn #^ WorkerDrainView superseded-worker-view [#^ ClusterState state #^ str name #^ str boot #^ int now #^ ClusterTiming timing]
  "退いた世代の process(旧い Pod の preStop)が drain を頼んだ時の答え(2026-09-27)。名の置き先と drain は今の
   世代の物なので、退いた世代が待つのは、その世代に置いてまだ終わっていない切り離した task だけ(0 で drained — preStop が終わる)。
   形は worker-view と同じ(drain_client.drain-outcome が drain.drained を読む)。"
  (setv w (get state.workers name)
        remaining (sorted (gfor t (.values state.tasks)
                                :if (and t.detached (in t.phase #("assigned" "preparing")) (= t.worker name) (= t.boot boot))
                                (+ "task/" t.id))))
  (WorkerDrainView :info w :alive (alive now w timing.lease-ms) :silent-ms (- now w.last-seen-ms) :superseded True
                   :drain (DrainProgress :worker name :boot boot :superseded True :since-ms None :until-ms None :actor None
                                         :phase (if remaining DrainPhase.DRAINING DrainPhase.DRAINED) :remaining (tuple remaining)
                                         :moving {} :blocked {} :unmovable #() :moving-ready {})
                   :ready False))


(defn #^ (get dict #(str DrainProgress)) drains-view [#^ ClusterState state #^ int now #^ ClusterTiming timing]
  "GET /state の drains(worker の名 → drain の進み)。"
  (dfor n (sorted state.drains)
        :setv v (drain-view state n now timing)
        :if (is-not v None)
        n v))
