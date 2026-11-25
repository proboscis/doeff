;; 同じ Pod(container)の中の世代の入れ替え(_sim/local.hy の RollWorker :same-pod True)で、
;; 拍の掃除(SweepEnvs)で落ちる新しい版を入れた時に、旧い世代の job(recreate・handoff とも)が止まらず、
;; 新しい世代が committed にならずに退けられ、旧い世代が生き続ける事の検。
;;
;; 出自(2026-11-14): 新しい版の worker が env_store.hy の removed-mirror の TypeError で
;; 拍の掃除のたびに落ちた。新しい世代は掃除を 1 周回し切る前に落ちるので committed を書かず、
;; 前の世代の job を奪わない。前の世代はそのまま job を持ち続け、lease の窓の後に coordinator が
;; 今の世代へ戻す(REVIVED)。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "entry"})
(import dataclasses [dataclass replace])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster [ClusterTiming PlainText sim-cluster SimWorker SimProcess SimGeneration
                       ProcessesOf GenerationsOf SharedRows RollWorker ReadCoordinator])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.working_programs [WORK-PREFIX keeping-hosts working-hosts])

(val VERSION-A (* "a" 40))
(val VERSION-B (* "b" 40))
(val SWEEP-ERROR "TypeError: removed-mirror")
(val W1 (SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :doeff-commit VERSION-A))


(defrecord RejectedSwapSeen
  "筋書きが読んだ姿: elder = 前の世代の boot・processes = job の process の列・generations = w1 の世代の列・
   worker-view = 終わり際の GET /workers/w1 の答え・rows = 盤の work/ の行(handoff の検で使う)。"
  (#^ str elder)
  (#^ (get tuple #(SimProcess ...)) processes)
  (#^ (get tuple #(SimGeneration ...)) generations)
  (#^ dict worker-view)
  (#^ dict rows))


(defk rolled-into-sweep-crash [job wait-after]
  {:pre [(: job str) (: wait-after float)] :post [(: % RejectedSwapSeen)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: job が旧い世代 A で動き出した後に、同じ Pod の中で掃除(SweepEnvs)で落ちる版 B へ入れ替え、
   lease の窓を越えて待ってから、job の process・w1 の世代・coordinator の見る w1 を読むため。"
  (<- (Delay 3.0))
  (val broken (replace W1 :doeff-commit VERSION-B :sweep-crash SWEEP-ERROR))
  (<- elder (| str None) (RollWorker "w1" :same-pod True :next-worker broken))
  (assert (is-not elder None))
  (<- (Delay wait-after))
  (<- processes tuple (ProcessesOf job))
  (<- generations tuple (GenerationsOf "w1"))
  (<- view (| dict PlainText) (ReadCoordinator "/workers/w1"))
  (assert (isinstance view dict) view)
  (<- rows dict (SharedRows (+ WORK-PREFIX "/")))
  (RejectedSwapSeen :elder elder
                    :processes (tuple (sorted processes :key (fn [p] p.started-ms)))
                    :generations generations
                    :worker-view view
                    :rows rows))


(deftest test-same-pod-swap-rejects-version-crashing-in-sweep-and-keeps-recreate-job-running
  (<- seen RejectedSwapSeen
      (sim-cluster :notice-broker (MemoryBroker)
                   :timing (ClusterTiming)
                   :workers #(W1)
                   (keeping-hosts sim-foundation)
                   (rolled-into-sweep-crash "keeper" 20.0)))
  ;; 旧い世代の recreate の job(keeper)は 1 度も止まらず、同じ 1 本の process が動き続ける。
  (assert (= (len seen.processes) 1) seen.processes)
  (assert (is (. (get seen.processes 0) ended-ms) None) seen.processes)
  ;; 新しい世代 B は committed を書かずに掃除の例外で落ちて退けられ、旧い世代 A は生き続ける。
  (val elder-gen (next (gfor g seen.generations :if (= g.boot seen.elder) g) None))
  (val fresh-gen (next (gfor g seen.generations :if (!= g.boot seen.elder) g) None))
  (assert (is-not elder-gen None) seen.generations)
  (assert (is-not fresh-gen None) seen.generations)
  (assert (not elder-gen.down) elder-gen)
  (assert (not elder-gen.stopping) elder-gen)
  (assert fresh-gen.down fresh-gen)
  (assert (not fresh-gen.committed) fresh-gen)
  (assert (in SWEEP-ERROR fresh-gen.crash-reason) fresh-gen)
  ;; coordinator は lease の窓の後に旧い世代 A を今の世代へ戻している(REVIVED)。
  (assert (= (.get seen.worker-view "boot") seen.elder) seen.worker-view)
  (assert (= (.get seen.worker-view "doeffCommit") VERSION-A) seen.worker-view))


(deftest test-same-pod-swap-rejects-version-crashing-in-sweep-and-keeps-handoff-job-running
  (<- seen RejectedSwapSeen
      (sim-cluster :notice-broker (MemoryBroker)
                   :timing (ClusterTiming)
                   :workers #(W1)
                   (working-hosts sim-foundation)
                   (rolled-into-sweep-crash "host" 20.0)))
  ;; 旧い世代の handoff の job(host)は止まらずに仕事を終わりまで回し切る(盤に done)。
  (assert (= (len seen.processes) 1) seen.processes)
  (val first (get seen.processes 0))
  (assert (is first.ended-ms None) first)
  (val done-row (.get seen.rows (.format "{}/{}/done" WORK-PREFIX first.instance)))
  (assert (is-not done-row None) seen.rows)
  ;; 新しい世代 B は committed を書かずに掃除の例外で落ち、旧い世代 A が生き続ける。
  (val fresh-gen (next (gfor g seen.generations :if (!= g.boot seen.elder) g) None))
  (assert (is-not fresh-gen None) seen.generations)
  (assert fresh-gen.down fresh-gen)
  (assert (not fresh-gen.committed) fresh-gen)
  (assert (in SWEEP-ERROR fresh-gen.crash-reason) fresh-gen)
  (assert (= (.get seen.worker-view "boot") seen.elder) seen.worker-view)
  (assert (= (.get seen.worker-view "doeffCommit") VERSION-A) seen.worker-view))
