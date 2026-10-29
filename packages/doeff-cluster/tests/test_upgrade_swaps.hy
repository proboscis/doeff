;; 同じ名の worker を新しい値で作り直す口(sim の ReplaceWorker — 本番の Deployment の env を変えた Recreate に当たる)と、入れ替えの
;; 間に待ち行列・走り中の task がどうなるかの実測(#3366 の単位 2b)。条 V2 の文は、ここで測った振る舞いに合わせる。
;;
;; 測る場面(cisco-c8 の条件 1):
;;   (a) 唯一の合う worker の入れ替えの間の queued — tests/test_detached_runners.hy の
;;       test-a-queued-task-waits-past-its-lease-while-the-only-runner-is-replaced が既に測る(待つ・2026-10-02 の直しの後)。
;;   (b) 唯一の合う worker の入れ替えの間の assigned(走り中の切り離した task)。
;;   (c) coordinator の作り直しの直後の queued — 作り直した coordinator が worker の行を読めない(2026-10-05 の版上げの形: 古い
;;       coordinator が書いた行に taskReserve が無く、新しい coordinator が読まない)時と、読める時。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_events [MemoryBroker])
(import dataclasses [replace])
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedOutcome DetachedLost DetachedSucceeded DetachedUnrunnable])
(import doeff_cluster.shared.entry.service_build [system-of])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import doeff_cluster.coordinator.entry.handler_sets [MemoryWalStore])
(import doeff_cluster.sim.local [sim-cluster SimWorker DrainWorker StopWorker StartWorker StopCoordinator ReplaceWorker WorkerOf
                                 ReadCoordinator])
(import tests.detached_rig [slow-add])

(val OLD "d563ab95a0000000000000000000000000000000")
(val NEW "90fd9a81d97ddf1cf5ae13a4036fa615108abbe7")
(val NO-JOBS (system-of "upgrade-swaps" #()))
(val ON-X (frozenset ["x-tool"]))
(val A (SimWorker :name "a" :provides (frozenset ["x-tool" "host-a"]) :task-reserve 0 :capacity 1 :doeff-commit OLD))
(val POLL 0.5)
(val LEASE 60.0)
;; 待ちの期限を短く(本番の既定は 5 時間 — 期限の内か外かを短い仮想の時間で測るため)。
(val TIMING (ClusterTiming :silent-worker-wait-ms 180000))


(defk worker-view [name]
  {:pre [(: name str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "coordinator の状態の画面の worker 1 台(capacity・taskReserve・live を読むため)。"
  (<- state dict (ReadCoordinator "/state"))
  (get (get state "workers") name))


(defk swap-a [worker]
  {:pre [(: worker SimWorker)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本番の Recreate と同じ順で a を作り直す: drain(preStop)→ 止め(古い Pod が抜けるまで)→ 値の差し替え → 新しい世代で起こす。"
  (<- asked dict (DrainWorker "a"))
  (assert (= (get asked "status") 200) asked)
  (<- (Delay POLL))
  (<- (StopWorker "a"))
  (<- replaced bool (ReplaceWorker "a" worker))
  (<- started bool (StartWorker "a"))
  (and replaced started))


(defk replace-a-with-a-larger-capacity []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 動いている a には差し替えを断られる → 作り直しで capacity 1 → 2・版 OLD → NEW にする → coordinator が新しい capacity を読む。"
  (<- refused bool (ReplaceWorker "a" (replace A :capacity 2)))
  (<- before dict (worker-view "a"))
  (<- swapped bool (swap-a (replace A :capacity 2 :doeff-commit NEW)))
  (<- (Delay 5.0))
  (<- after dict (worker-view "a"))
  (<- now SimWorker (WorkerOf "a"))
  #(refused swapped (get before "capacity") (get after "capacity") (get after "live") now.doeff-commit))


(deftest test-a-replaced-worker-comes-back-with-the-new-values
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (replace-a-with-a-larger-capacity) :workers #(A) :timing TIMING))
  (assert (= seen #(False True 1 2 True NEW)) seen))


(defk running-task-across-the-swap []
  {:pre [] :post [(: % DetachedOutcome)] :tags {:context "doeff-cluster-test" :role "program"}}
  "場面 (b): a に 20 秒の切り離した task を出し、走り出した後で a を作り直す。答え = task の結末。"
  (<- (submit-detached-task (slow-add 20.0 5) :key "k-run" :needs ON-X :lease-seconds LEASE))
  (<- (Delay 3.0))
  (<- (swap-a (replace A :doeff-commit NEW)))
  (<- outcome (AwaitDetached "k-run"))
  outcome)


(deftest test-a-task-running-on-the-swapped-worker-is-lost-when-the-swap-does-not-wait
  ;; 測り (b): drain を頼むだけで空くのを待たずに止めると、走り中の切り離した task は lease が切れて lost(走らせ直さない)— 条 V2 の
  ;; 根拠。本番の preStop は drain の空くのを待つ(sim の DrainWorker は頼むだけ — 本物と違う所)。
  (<- outcome (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (running-task-across-the-swap) :workers #(A) :timing TIMING))
  (assert (isinstance outcome DetachedLost) outcome)
  (assert (in "lease が切れた" outcome.reason) outcome.reason))


(defclass DropsTaskReserve [MemoryWalStore]
  "2026-10-05 の版上げの形の置き場: worker の行に taskReserve を書かない(古い coordinator が書いた行の形)— 作り直した coordinator は
   taskReserve の無い worker の行を読まず、次の heartbeat まで名簿に worker が居ない。"
  (defn #^ None persist [self #^ (get dict #(str object)) delta]
    (setv drop (fn [v] (if (isinstance v dict) (dfor #(a b) (.items v) :if (!= a "taskReserve") a b) v)))
    (.persist (super) (dfor #(k v) (.items delta) k (if (.startswith k "worker/") (drop v) v)))))


(defk queued-task-across-a-coordinator-restart []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "場面 (c): a(capacity 1)を 30 秒の task で埋め、次の task を queued にしてから coordinator を 10 秒止める。答え = 2 つの task の結末。"
  (<- (Delay 3.0))
  (<- (submit-detached-task (slow-add 30.0 1) :key "k-busy" :needs ON-X :lease-seconds LEASE))
  (<- (Delay 3.0))
  (<- (submit-detached-task (slow-add 1.0 2) :key "k-queued" :needs ON-X :lease-seconds LEASE))
  (<- (Delay 1.0))
  (<- (StopCoordinator 10.0))
  (<- (Delay 12.0))
  (<- queued (AwaitDetached "k-queued"))
  (<- busy (AwaitDetached "k-busy"))
  #(queued busy))


(deftest test-a-queued-task-is-dropped-when-the-restarted-coordinator-cannot-read-the-worker-rows
  (<- readable tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (queued-task-across-a-coordinator-restart) :workers #(A) :timing TIMING))
  (<- unreadable tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (queued-task-across-a-coordinator-restart) :workers #(A) :timing TIMING
                                    :store DropsTaskReserve))
  ;; 測り (c): 行が読めれば queued は作り直しを越えて走る。読めない(2026-10-05 の形)と queued は「合う worker が無い」で即 落ち、
  ;; 走り中の task は残る — 条 V4 の根拠(#2440)。
  (assert (= readable #((DetachedSucceeded 102) (DetachedSucceeded 101))) readable)
  (val queued (get unreadable 0))
  (val busy (get unreadable 1))
  (assert (isinstance queued DetachedUnrunnable) queued)
  (assert (in "合う worker が無い" queued.detail) queued.detail)
  (assert (= busy (DetachedSucceeded 101)) busy))
