;; 名前付きの lease の空きを、待つ側が coordinator の知らせで受ける(#3865 の後の単位・2026-10-07 の決め)。
;; 模擬の世界(sim-cluster — 本物の coordinator)で、筋書きが担い手と待つ側の両方になる。
;;   1 担い手が返した刻ちょうどに、待っていた claim が取れる(前は poll の間ごとに問い直したので、最大で poll の間だけ遅れた)。
;;   2 担い手が延ばさずに期限が切れた刻ちょうどに、待っていた claim が取れる(前は期限の後の次の poll)。
;; 待つ側は poll の間を持たない SemaphoreSession(:poll-seconds None)で、GET /watch?lease=<名> で空きを待つ。
(require doeff-hy.macros [deftest defk <- val])
(import doeff_core_effects.scheduler [Spawn Wait Task])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.semaphore_handlers [SemaphoreSession acquire-lease])
(import doeff_cluster.shared.intent.semaphore_model [ClusterSemaphore LeaseOp LeaseAnswer])
(import doeff_cluster.sim.local [sim-cluster])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons])

(val LOCK (ClusterSemaphore "wait-lock" 1))
(val HOLDER-TOKEN "holder/1")
;; 担い手が持つ長さ(秒)— 待つ側の poll の 1 秒の刻みと揃わない刻に返す。
(val HOLD-SECONDS 5.5)
;; 担い手の期限(ms)— 延ばさずに切れる。
(val SHORT-TTL-MS 3500)
;; 「刻ちょうど」と数える遅れの上限(ms)— 要求の往復の分。poll の 1 秒より十分短い。
(val SLACK-MS 100)


(defk waiter [started]
  {:pre [(: started int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "poll の間を持たない待つ側として lease を取り、取れた刻(epoch ms)を返すため。"
  (<- (Delay 1.0))
  (<- _token str (acquire-lease (SemaphoreSession "waiter" :ttl-seconds 30.0 :poll-seconds None) LOCK))
  (<- at int (now-epoch-ms))
  at)


(defk released-then-taken []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "担い手として lease を取り、HOLD-SECONDS 後に返す。待つ側が取れた刻と、返した刻を返すため。"
  (<- held LeaseAnswer (LeaseOp LOCK.name "claim" HOLDER-TOKEN LOCK.permits 30000))
  (assert held.ok held)
  (<- started int (now-epoch-ms))
  (<- waiting Task (Spawn (waiter started)))
  (<- (Delay HOLD-SECONDS))
  (<- released-at int (now-epoch-ms))
  (<- gone LeaseAnswer (LeaseOp LOCK.name "release" HOLDER-TOKEN LOCK.permits 0))
  (assert gone.ok gone)
  (<- taken-at int (Wait waiting))
  #(released-at taken-at))


(defk expired-then-taken []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "担い手として短い期限で lease を取り、延ばさずに置く。待つ側が取れた刻と、担い手の期限が切れる刻(送る前の刻 + 期限)を返すため。"
  (<- sent int (now-epoch-ms))
  (<- held LeaseAnswer (LeaseOp LOCK.name "claim" HOLDER-TOKEN LOCK.permits SHORT-TTL-MS))
  (assert held.ok held)
  (<- waiting Task (Spawn (waiter sent)))
  (<- taken-at int (Wait waiting))
  #((+ sent SHORT-TTL-MS) taken-at))


(deftest test-a-waiting-claim-takes-the-lease-at-the-release
  (<- #(released-at taken-at) tuple (sim-cluster (beacons sim-foundation) (released-then-taken)))
  (assert (<= released-at taken-at (+ released-at SLACK-MS)) #(released-at taken-at)))


(deftest test-a-waiting-claim-takes-the-lease-at-the-expiry-of-a-holder-that-stopped-renewing
  (<- #(expires-at taken-at) tuple (sim-cluster (beacons sim-foundation) (expired-then-taken)))
  (assert (<= expires-at taken-at (+ expires-at SLACK-MS)) #(expires-at taken-at)))
