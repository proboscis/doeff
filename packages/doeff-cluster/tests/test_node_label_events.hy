;;; worker の置かれた node の label を、coordinator が時間で k8s へ読みに行かず、Node の変化の出来事で受けて能力を導き直す事の失敗ケース
;;; (#4070 — 利用者 2026-10-06 "so anything that require polling, are to be fixed. polling is a last resort")。
;;;
;;; 本物の coordinator と worker を模擬の cluster(仮想の時計・偽の k8s KubeMemory)で回す。worker w1 は node n1 に置かれ、n1 は会社の機体の
;;; label(doeff.dev/company-machine=true)を持つので、coordinator は w1 に能力 company-machine を足す(ClusterNaming の node-capabilities の
;;; 既定 — ADR-DOE-CLUSTER-001 R4b・改訂 1 の I):
;;;
;;;   (a) label が変わらない静かな 150 秒の間、coordinator が node の label を読みに行く数は 0(変化の出来事が来ない — 直す前は 60 秒ごとに
;;;       読みに行くので 2 回で赤)。
;;;   (b) n1 から会社の機体の label を外した刻(仮想の時計の 1 秒の格子から外した刻)の 1 秒の内に、w1 の能力から company-machine が外れる
;;;       (直す前は次の 60 秒ごとの読みの刻まで、会社の機体でなくなった node の worker が company-machine を持ち続けるので赤)。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster.sim.local [sim-cluster SimWorker NodeReads RelabelNode ReadCoordinator])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons])

(val NODE "n1")
(val COMPANY-LABELS {"doeff.dev/company-machine" "true" "kubernetes.io/hostname" NODE})
(val WORKERS #((SimWorker :name "w1" :provides (frozenset #{"cluster-net"}) :task-reserve 0 :node NODE)))
;; worker が heartbeat で node を申告し coordinator が最初の観測を持つまでの秒・label が変わらない静かな区間の秒(直す前の読み直しの間隔 60 秒の 2 倍より長い)・
;; label を替える刻を 1 秒の格子から外す端数・替えた後に能力を読むまでの秒。
(val SETTLE-SECONDS 10.0)
(val QUIET-SECONDS 150.0)
(val OFF-GRID-SECONDS 0.37)
(val ANSWER-SECONDS 1.0)


(defrecord NodeLabelRun
  "筋書きの結果: quiet-reads = 静かな区間に coordinator へ伝えた node の数・derived-before = 会社の機体の label を外す前の w1 の導いた能力・
   derived-after = 外してから ANSWER-SECONDS 後の w1 の導いた能力。"
  (#^ int quiet-reads)
  (#^ tuple derived-before)
  (#^ tuple derived-after))


(defk derived-of [view]
  {:pre [(: view dict)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "coordinator の状態の画面(GET /state の JSON)から、w1 に node の label から導いた能力を読むため。"
  (tuple (get view "workers" "w1" "derived")))


(defk relabelling-the-node []
  {:pre [] :post [(: % NodeLabelRun)] :tags {:context "doeff-cluster-test" :role "program"}}
  "w1 が node を申告した後の静かな区間に k8s へ伝えた node の数と、会社の機体の label を外す前後の w1 の導いた能力を読むため。"
  (<- (Delay SETTLE-SECONDS))
  (<- first dict (ReadCoordinator "/state"))
  (<- before tuple (NodeReads))
  (<- (Delay QUIET-SECONDS))
  (<- after tuple (NodeReads))
  (<- (Delay OFF-GRID-SECONDS))
  (<- (RelabelNode NODE {"kubernetes.io/hostname" NODE}))
  (<- (Delay ANSWER-SECONDS))
  (<- relabelled dict (ReadCoordinator "/state"))
  (<- derived-before tuple (derived-of first))
  (<- derived-after tuple (derived-of relabelled))
  (NodeLabelRun :quiet-reads (- (len after) (len before)) :derived-before derived-before :derived-after derived-after))


(defk run-relabelling []
  {:pre [] :post [(: % NodeLabelRun)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きを模擬の cluster で回すため(n1 は会社の機体の label を持って始まる)。"
  (<- seen NodeLabelRun (sim-cluster :notice-broker (MemoryBroker) (beacons sim-foundation) (relabelling-the-node)
                                     :workers WORKERS :nodes {NODE COMPANY-LABELS}))
  seen)


(deftest test-a-quiet-node-is-not-read-on-a-timer
  (<- seen NodeLabelRun (run-relabelling))
  (assert (= seen.derived-before #("company-machine")) seen)
  (assert (= seen.quiet-reads 0) seen))


(deftest test-a-capability-leaves-at-the-instant-the-node-label-changes
  (<- seen NodeLabelRun (run-relabelling))
  (assert (= seen.derived-before #("company-machine")) seen)
  (assert (= seen.derived-after #()) seen))
