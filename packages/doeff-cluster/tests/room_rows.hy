;;; 検が worker に名乗らせる機体の測った空き(heartbeat の measured — coordinator は task の置き先をこの値から数える)。
;;;
;;; ROOM-LOADS の n 番目 = 空いた CPU n + 1 個の機体の読み(使い 0・memory 256 GiB・刻 0)。coordinator の数え(core/measured_room)で
;;; task の空きがちょうど n になり、置いた task を 1 本ずつ引く(刻 0 の読みには、置いた task はどれもまだ出ていない)— 手書きの task の
;;; 枠 n(以前の taskReserve + backgroundRoom)と同じ置かれ方を、測った空きで作るため。予約は上限の 1/6(n + 1 が 6 未満なら 0)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "fixture"})
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])

(val ROOM-LOADS (tuple (gfor n (range 0 65)
                             (MeasuredLoad :at-ms 0 :cpus (+ n 1) :cpu-busy 0.0 :memory-total-MiB 262144 :memory-available-MiB 262144
                                           :cpu-some-avg10 0.0 :cpu-full-avg10 0.0 :memory-some-avg10 0.0 :memory-full-avg10 0.0))))

;; ROOM-LOADS と同じ読みを heartbeat の本文の欄 measured の JSON の形で(本文を dict で組む検のため)。
(val ROOM-JSONS (tuple (gfor n (range 0 65)
                             {"atMs" 0 "cpus" (+ n 1) "cpuBusy" 0.0 "memoryTotalMiB" 262144 "memoryAvailableMiB" 262144
                              "cpuSomeAvg10" 0.0 "cpuFullAvg10" 0.0 "memorySomeAvg10" 0.0 "memoryFullAvg10" 0.0})))
