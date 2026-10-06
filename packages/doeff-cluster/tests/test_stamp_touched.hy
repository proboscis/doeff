;; coordinator の版の付け(resource_policy.stamp)は、settle が触った資源の行だけを組んで比べる(#2615)。
;;
;; - 費用: 資源 N 個のうち 1 つだけ変えた settle の stamp は、全体の snapshot を組まず、行を 2 つ(その鍵の前と後)だけ組む — N を
;;   増やしても同じ。前の形(settle のたびに状態全体の snapshot を前後 2 回組む)は snapshot を呼ぶので赤。
;; - 同値: 触った資源を選ぶ規則(dirty-keys)が材料を見落とすと、版が黙って進まない。本物の coordinator と worker を回す sim の
;;   筋書き(死・作り直し・入れ替え・drain・置けない宣言・切り離した task)の settle の全部で、全部の鍵を比べる形(前の形と同じ —
;;   dirty-keys を両方の snapshot の鍵の全部に差し替えた stamp)と同じ版・出来事の記録になることを確かめる。
(require doeff-hy.macros [deftest defk deff <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_events [MemoryBroker])
(import dataclasses [replace])
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState TaskRecord])
(import doeff_cluster.coordinator.core.resource_policy :as resource-policy)
(import doeff_cluster.coordinator.core.resource_policy [stamp snapshot])
(import doeff_cluster.coordinator.core.api_policy :as api-policy)
(import doeff_cluster.sim.local [sim-cluster SimWorker KillWorker StartWorker DrainWorker])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons handoff-beacons handoff-beacons-v2 gpu-only detaching relay])
(import tests.test_local [crash-and-watch redeclare-and-watch watch-trainer watch-rows])


;; --- 費用 ------------------------------------------------------------------------------------------------------------

(val ROW-OF resource-policy.row-of)
(val BUILT [0])


(deff counted-row-of [state jobs key now timing]  ; defk にできない: stamp(外の library の純粋な関数)が呼ぶ差し替えの callback
  {:pre [(: state ClusterState) (: jobs dict) (: key str) (: now int) (: timing ClusterTiming)] :post [(: % (| dict None))] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本物の row-of を呼び、組んだ行の数を BUILT に数える。"
  (setv (get BUILT 0) (+ (get BUILT 0) 1))
  (ROW-OF state jobs key now timing))


(deff refuse-whole-snapshot [state now timing]  ; defk にできない: stamp(外の library の純粋な関数)が呼ぶ差し替えの callback
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "stamp が状態全体の snapshot を組んだら赤にする。"
  (raise (AssertionError "stamp が状態全体の snapshot を組んだ")))


(deftest test-a-settle-that-touches-one-resource-builds-only-that-resources-rows [monkeypatch]
  (val timing (ClusterTiming))
  (val empty (ClusterState))
  (for [n #(50 400)]
    (val tasks (dfor i (range n) (.format "t{}" i)
                     (TaskRecord :id (.format "t{}" i) :name "job" :program None :revision "r1" :versions #() :needs #("k3s")
                                 :lease-ms 1000 :lease-until-ms 0 :submitted-ms 0)))
    (val seeded (stamp empty (replace empty :tasks tasks) "test" 1000 timing))
    (val moved (replace seeded :tasks (| seeded.tasks {"t7" (replace (get seeded.tasks "t7") :phase "assigned" :worker "w1")})))
    (setv (get BUILT 0) 0)
    (.setattr monkeypatch resource-policy "row_of" counted-row-of)
    (.setattr monkeypatch resource-policy "snapshot" refuse-whole-snapshot)
    (val after (stamp seeded moved "test" 2000 timing))
    (.undo monkeypatch)
    (assert (= (get BUILT 0) 2) #(n (get BUILT 0)))
    (assert (= (. (get after.meta "Task/t7") resource-version) (+ seeded.revision 1)) after.meta)
    ;; 出来事は 1 件だけ(記録は種類ごとに件数の上限で切られるので、数は通し番号の差で読む)。
    (assert (= (- after.audit-seq seeded.audit-seq) 1) #(seeded.audit-seq after.audit-seq))
    (assert (= #((. (get after.audit -1) verb) (. (get after.audit -1) name)) #("status" "t7")) (get after.audit -1))))


;; --- 同値 ------------------------------------------------------------------------------------------------------------

(val DIRTY-KEYS resource-policy.dirty-keys)
(val SEEN [0 #()])


(deff every-key [before after jobs-before jobs-after]  ; defk にできない: stamp(外の library の純粋な関数)が呼ぶ差し替えの callback
  {:pre [(: before ClusterState) (: after ClusterState) (: jobs-before dict) (: jobs-after dict)] :post [(: % frozenset)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "前の形の比べる範囲 = 両方の snapshot の鍵の全部(物差し)。"
  (| (frozenset (snapshot before 0 (ClusterTiming))) (frozenset (snapshot after 0 (ClusterTiming)))))


(deff checking-stamp [before after actor now timing]  ; defk にできない: api_policy.settle(外の library の純粋な関数)が呼ぶ差し替えの callback
  {:pre [(: before ClusterState) (: after ClusterState) (: actor str) (: now int) (: timing ClusterTiming)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "settle の stamp を、触った資源だけの本物と、全部の鍵を比べる物差しの両方で組み、食い違いを SEEN に積む(答えは本物の方)。"
  (setv got (stamp before after actor now timing))
  (setv resource-policy.dirty-keys every-key)
  (try
    (setv want (stamp before after actor now timing))
    (finally (setv resource-policy.dirty-keys DIRTY-KEYS)))
  (setv (get SEEN 0) (+ (get SEEN 0) 1))
  (when (!= #(got.meta got.revision got.audit got.audit-seq) #(want.meta want.revision want.audit want.audit-seq))
    (setv (get SEEN 1) (+ (get SEEN 1) #(#(actor now (sorted (^ (set (.items got.meta)) (set (.items want.meta)))))))))
  got)


(defk kill-drain-restart []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: worker を drain し、もう 1 台を殺して移し替えの期限を越え、起こし直す(生死・drain・置き直しの拍を通る)。"
  (<- (Delay 5.0))
  (<- (DrainWorker "w1" :ttl-seconds 20.0))
  (<- (Delay 10.0))
  (<- (KillWorker "w2"))
  (<- (Delay 60.0))
  (<- (StartWorker "w2"))
  (<- (Delay 15.0))
  None)


(val TWO-WORKERS #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0)
                   (SimWorker :name "w2" :provides (frozenset ["cluster-net"]) :task-reserve 0)))


(deftest test-touched-only-stamp-matches-the-every-key-stamp-on-every-settle [monkeypatch]
  (setv (get SEEN 0) 0 (get SEEN 1) #())
  (.setattr monkeypatch api-policy "stamp" checking-stamp)
  (<- (sim-cluster :notice-broker (MemoryBroker) (relay sim-foundation) (watch-rows 10.0 "relay/")))
  (<- (sim-cluster :notice-broker (MemoryBroker) (beacons sim-foundation) (crash-and-watch "beacon" "beacon/")))
  (<- (sim-cluster :notice-broker (MemoryBroker) (handoff-beacons sim-foundation)
                   (redeclare-and-watch (handoff-beacons-v2 sim-foundation) "beacon" "beacon/" 15.0)))
  (<- (sim-cluster :notice-broker (MemoryBroker) (gpu-only sim-foundation) (watch-trainer)
                   :workers #((SimWorker :name "cpu-1" :provides (frozenset ["cluster-net"]) :task-reserve 0)
                              (SimWorker :name "gpu-1" :provides (frozenset ["cluster-net" "gpu"]) :task-reserve 0))))
  (<- (sim-cluster :notice-broker (MemoryBroker) (detaching sim-foundation) (watch-rows 15.0 "detached/")))
  (<- (sim-cluster :notice-broker (MemoryBroker) (beacons sim-foundation) (kill-drain-restart) :workers TWO-WORKERS))
  (assert (> (get SEEN 0) 100) SEEN)
  (assert (= (get SEEN 1) #()) (get SEEN 1)))


;; --- 担い手の報告だけの変化 ------------------------------------------------------------------------------------------
;; readiness の宣言の無い Service は、担い手の worker の報告の行が running になった拍に Ready になる — 名ごとの写像(宣言・置き先・
;; readiness の報告)は動かず、worker の報告(statuses)だけが動く。dirty-keys がこの材料を見ないと、その拍の ready の変化を黙って
;; 版に写さない。sim の筋書きはこの拍を他の名ごとの変化と同じ settle に重ねることが多いので、ここで材料を 1 つだけ動かして確かめる。

(val CAPTURED [None])


(deff capture-stamp [before after actor now timing]  ; defk にできない: api_policy.settle(外の library の純粋な関数)が呼ぶ差し替えの callback
  {:pre [(: before ClusterState) (: after ClusterState) (: actor str) (: now int) (: timing ClusterTiming)] :post [(: % ClusterState)]}
  "本物の stamp の答えのうち、copier が Ready の最後の状態を CAPTURED に残す(終わりの止めの拍では Ready でなくなるため)。"
  (setv got (stamp before after actor now timing)
        row (.get (snapshot got now timing) "Service/copier"))
  (when (and row (= (get row "status" "ready") "Ready"))
    (setv (get CAPTURED 0) #(got now timing)))
  got)


(deftest test-a-carriers-report-alone-marks-the-services-it-carries [monkeypatch]
  (.setattr monkeypatch api-policy "stamp" capture-stamp)
  (<- (sim-cluster :notice-broker (MemoryBroker) (relay sim-foundation) (watch-rows 10.0 "relay/")))
  (.undo monkeypatch)
  (val state (get (get CAPTURED 0) 0))
  (val now (get (get CAPTURED 0) 1))
  (val timing (get (get CAPTURED 0) 2))
  (val key "Service/copier")
  (val carrier (. (get state.placements "copier") worker))
  (val report (get state.statuses carrier))
  ;; 担い手の報告の copier の行だけを starting に戻した前の状態(他の材料は同じ物のまま)。
  (val before (replace state :statuses (| state.statuses
                                          {carrier (replace report :jobs (tuple (gfor r report.jobs
                                                                                      (if (= r.name "copier") (replace r :phase "starting") r))))})))
  (val rows-before (snapshot before now timing))
  (val rows-after (snapshot state now timing))
  (assert (= (get rows-after key "status" "ready") "Ready") (get rows-after key))
  (assert (!= (get rows-before key) (get rows-after key)) (get rows-before key))
  (val jobs (dfor j state.jobs j.spec.name j))
  (assert (in key (resource-policy.dirty-keys before state jobs jobs)) key))
