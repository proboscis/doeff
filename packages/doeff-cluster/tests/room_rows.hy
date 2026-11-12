;;; 検が worker に名乗らせる機体の測った空き(heartbeat の measured — coordinator は task の置き先をこの値から数える)。
;;;
;;; ROOM-LOADS の n 番目 = 空いた CPU n + 1 個の機体の読み(使い 0・memory 256 GiB・刻 0)。coordinator の数え(core/measured_room)で
;;; task の空きがちょうど n になり、置いた task を 1 本ずつ引く(刻 0 の読みには、置いた task はどれもまだ出ていない)— 手書きの task の
;;; 枠 n(以前の taskReserve + backgroundRoom)と同じ置かれ方を、測った空きで作るため。予約は上限の 1/6(n + 1 が 6 未満なら 0)。
(require doeff-hy.macros [val defk <-])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "fixture"})
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])

(val ROOM-LOADS (tuple (gfor n (range 0 65)
                             (MeasuredLoad :at-ms 0 :cpus (+ n 1) :cpu-busy 0.0 :memory-total-MiB 262144 :memory-available-MiB 262144
                                           :cpu-some-avg10 0.0 :cpu-full-avg10 0.0 :memory-some-avg10 0.0 :memory-full-avg10 0.0))))

;; ROOM-LOADS と同じ読みを heartbeat の本文の欄 measured の JSON の形で(本文を dict で組む検のため)。
(val ROOM-JSONS (tuple (gfor n (range 0 65)
                             {"atMs" 0 "cpus" (+ n 1) "cpuBusy" 0.0 "memoryTotalMiB" 262144 "memoryAvailableMiB" 262144
                              "cpuSomeAvg10" 0.0 "cpuFullAvg10" 0.0 "memorySomeAvg10" 0.0 "memoryFullAvg10" 0.0})))


;; 測った空きの読みに当たる tasks 区画の宣言(ADR-DOE-CLUSTER-005 R1 — 置き方は測った空きを読まず、区画の申告の和で置く)。
;; compartments-like-rooms は worker の名 → WorkerInfo の dict から、測りを名乗る worker ごとに tasks 区画 1 つ(層 jobs)を宣言した表を作る:
;; cpuMillis = ⌊CPU の数 × (1 − 使い)− 1⌋ core(以前の数の空きの CPU の項 — ROOM-LOADS の n 番目は n core)・memoryMiB = memory の空き −
;; MEMORY-FLOOR-MIB(以前の memory の空き)。機体の行の名は worker の名。測りを名乗らない worker は区画を持たない。検が以前の測った空きと
;; 同じ置かれ方を区画の和で作るため。
(import math [floor])
(import doeff_cluster._shared.intent.compartment_model [Compartment MachineCompartments])
(import doeff_cluster._coordinator.core.measured_room [MEMORY-FLOOR-MIB])
(import doeff_hy._table [Table TableWrite table-of])


(defk compartments-like-rooms [workers]
  {:pre [(: workers dict)] :post [(: % Table)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker の名 → WorkerInfo の dict から、測った空きに当たる tasks 区画の表(頭の註)を作るため。"
  (table-of (tuple (gfor #(name w) (sorted (.items workers) :key (fn [item] (get item 0)))
                         :if (is-not w.measured None)
                         :setv m w.measured
                         :setv cores (max 0 (floor (- (* m.cpus (- 1 (or m.cpu-busy 0.0))) 1)))
                         :setv memory (max 0 (- m.memory-available-MiB MEMORY-FLOOR-MIB))
                         (TableWrite name (MachineCompartments :machine name
                                                               :compartments #((Compartment :use "tasks" :worker name :layer "jobs"
                                                                                            :memory-mib memory :memory-limit-mib memory
                                                                                            :cpu-millis (* 1000 cores)))))))))
