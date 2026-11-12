;;; 長く待った task の取っておきの検(2026-11-10 — measured_room の頭の註の長く待った task の取っておき)。実例: node-c の 11-10 は jobs の層の
;;; 空き 約 4 GiB で、要り 5632 MiB の task が 4 時間を越えて queued のままだった。置き先は優先と id の順に見て、置けない大きい task を
;;; 飛ばして後ろの要り 1〜3.5 GiB の task を置き続け、置いた task が終わって戻る空きは後ろの小さい task が先に取った。
;;;   (1) 反例の筋書き: STARVE-WAIT-MS を越えて待った大きい task は、今の task が全部終われば入る worker の空きを取っておき、後ろの小さい
;;;       task はその worker に置かれない。待った task の待ちの文は取っておく worker を名指す
;;;   (2) 待ちが短い task は取っておかない(小さい task を先に置く — 直す前と同じ)
;;;   (3) 今の task が全部終わっても入らない worker には取っておかない(層の中の常駐の使いで入らない task のために worker を塞がない)
;;;   (4) 見込み drained-room-mib = 予約 0 の memory の空き + 測りが名乗る task の今の使いの和
;;;   (5) 急ぎの task の取っておき(measured_room の頭の註 — 11-10 に要り 3584 MiB・優先 0 の task が、同じ要り・同じ優先の先に積まれた
;;;       task の後ろで 12 時間を越えて queued のままだった): 優先 KEEP-NOW-PRIORITY の task は、同じ要りで先に積まれた優先 0 の task
;;;       より先に置き先を見て、積んだ直後から空きを取っておく。先に積まれた task も後ろの小さい task もその worker に置かれない
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import dataclasses [replace])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [WorkerInfo ClusterState]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])
(import doeff_cluster._coordinator.core.measured_room [MEMORY-FLOOR-MIB JOBS-ROOM-FLOOR-MIB STARVE-WAIT-MS KEEP-NOW-PRIORITY drained-room-mib keeps-room?])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed])
(import doeff_hy._table [table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val LONG-LEASE-MS (* 24 3600 1000))
(val PY3 #((ComponentVersion "python" "3")))
;; 11-10 08:5x JST の node-c の測りに近い値(MiB)。
(val LINE-MIB 59617)
(val AVAILABLE-MIB 32512)
;; 置いた task 1 本(要り 3584 MiB・今の使い 1300 MiB — まだ 2284 MiB 伸びうる)。
(val RUNNING-NEED 3584)
(val RUNNING-USE 1300)
;; 待っている 2 本: 先に積んだ大きい task と、後に積んだ小さい task。
(val BIG-NEED 5632)
(val SMALL-NEED 1024)
;; 置いた task が全部終われば大きい task が入る jobs の層の空き(4900 − 512 + 1300 = 5688 ≥ 5632)と、入らない空き(4192 − 512 + 1300 = 4980)。
(val ROOMY-JOBS-MIB 4900)
(val TIGHT-JOBS-MIB 4192)


(defk node-c-measured [jobs-room]
  {:pre [(: jobs-room int)] :post [(: % MeasuredLoad)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "node-c の測り(CPU は空いている・機体の空きは広い)に、jobs の層の空き jobs-room と置いた task の今の使いを添えた形を作るため。"
  (MeasuredLoad :at-ms 0 :cpus 16 :cpu-busy 0.1 :memory-total-MiB LINE-MIB :memory-available-MiB AVAILABLE-MIB
                :cpu-some-avg10 0.0 :memory-some-avg10 0.0 :task-memory-MiB {"running" RUNNING-USE} :jobs-room-MiB jobs-room))


(defk waiting-task [id need submitted]
  {:pre [(: id str) (: need int) (: submitted int)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-node-c を要る、要り need MiB の、刻 submitted に積んだ待っている切り離した task を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-node-c") LONG-LEASE-MS LONG-LEASE-MS submitted :detached True :key id
              :memory-mib need))


(defk placed-world [jobs-room waited]
  {:pre [(: jobs-room int) (: waited int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "node-c に task 1 本を置いた世界で、waited ミリ秒前に積んだ大きい task と、その後に積んだ小さい task を置き先に 1 拍回し、
   #(大きい task の行 小さい task の行) を得るため。"
  (val now (* 10 3600 1000))
  (val node-c (WorkerInfo "node-c" #("host-node-c") now :versions PY3 :measured (! (node-c-measured jobs-room))))
  (<- workers (table-from-rows {"node-c" node-c}))
  (<- running TaskRecord (waiting-task "running" RUNNING-NEED 0))
  (<- big TaskRecord (waiting-task "a-big" BIG-NEED (- now waited)))
  (<- small TaskRecord (waiting-task "b-small" SMALL-NEED (- now 1000)))
  (<- tasks (table-from-rows {"running" (replace running :phase "assigned" :worker "node-c" :started-ms 0
                                                 :lease-until-ms (+ now LONG-LEASE-MS))
                              "a-big" big "b-small" small}))
  (val state (ClusterState #() workers (table-of #()) tasks))
  (val placed (! (tasks-placed now state state.placements T)))
  #((.row placed "a-big") (.row placed "b-small")))


(defk urgent-world []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "node-c に task 1 本を置いた世界で、STARVE-WAIT-MS より前に積んだ要り 3584 MiB・優先 0 の task と、その後に積んだ同じ要りで優先
   KEEP-NOW-PRIORITY の task と、最後に積んだ小さい task を置き先に 1 拍回し、#(先に積んだ task 急ぎの task 小さい task) の行を得るため
   (頭の註の (5))。"
  (val now (* 10 3600 1000))
  (val node-c (WorkerInfo "node-c" #("host-node-c") now :versions PY3 :measured (! (node-c-measured ROOMY-JOBS-MIB))))
  (<- workers (table-from-rows {"node-c" node-c}))
  (<- running TaskRecord (waiting-task "running" RUNNING-NEED 0))
  (<- early TaskRecord (waiting-task "a-early" RUNNING-NEED (- now STARVE-WAIT-MS 1)))
  (<- urgent TaskRecord (waiting-task "b-urgent" RUNNING-NEED (- now 1000)))
  (<- small TaskRecord (waiting-task "c-small" SMALL-NEED (- now 500)))
  (<- tasks (table-from-rows {"running" (replace running :phase "assigned" :worker "node-c" :started-ms 0
                                                 :lease-until-ms (+ now LONG-LEASE-MS))
                              "a-early" early "b-urgent" (replace urgent :priority KEEP-NOW-PRIORITY) "c-small" small}))
  (val state (ClusterState #() workers (table-of #()) tasks))
  (val placed (! (tasks-placed now state state.placements T)))
  #((.row placed "a-early") (.row placed "b-urgent") (.row placed "c-small")))


(deftest test-the-room-is-kept-for-an-urgent-priority-or-a-long-wait
  (<- urgent-now bool (keeps-room? KEEP-NOW-PRIORITY 0))
  (assert urgent-now)
  (<- plain-now bool (keeps-room? 0 0))
  (assert (not plain-now))
  (<- plain-long bool (keeps-room? 0 STARVE-WAIT-MS))
  (assert plain-long)
  (<- background-short bool (keeps-room? -1 (- STARVE-WAIT-MS 1)))
  (assert (not background-short)))
