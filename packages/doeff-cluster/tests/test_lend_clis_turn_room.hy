;;; 需要の余白の貸し(worker/core/eviction_rules の頭の註の需要の余白の貸し・worker/core/load_rules の頭の註の線の内の貸し・2026-11-12)の検:
;;; 会話の CLI の層を掛け直す機体(node-c)の需要の余白のうち使われていない分を、場所を譲る名乗りの task に借りとして貸し、上に載る系が
;;; memory の不足で仕事を待たせる間だけ取り返す。
;;;   (1) 見張り: 需要の余白を 1 本の要り ÷ CLIS-ROOM-SHARE まで縮めた clis の上限で、平常より広い借りの受けを数える。借りを全部置いた次の回も
;;;       clis の上限は 1 本の要りを残す(受け手の受けられる本数を 0 にしない)
;;;   (2) heartbeat: 取り返しの印が古い間は借りの受けを名乗り、新しい間は貸さずに需要の余白を戻す量を名乗る
;;;   (3) 置き先: 平常の空きで置けない譲れる task は借りに置かれ、譲れない task は置かれない。取り返す量を名乗る間は借りを止め、名乗らない間は止めない
;;;   (4) 枠の file: keepLineFile の無い行は reclaimFile だけを持てる。keepLineFile の在る行は今までどおり lendLineFile と組
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff [with-handlers])
(import doeff_core_effects [state slog-discard-handler memory-file-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed])
(import doeff_cluster._coordinator.core.measured_room [MEMORY-FLOOR-MIB JOBS-ROOM-FLOOR-MIB])
(import doeff_cluster._worker.core.memory_rules [MIB JobUse])
(import doeff_cluster._worker.core.eviction_rules [CLI-TURN-BYTES CLIS-ROOM-SHARE clis-limit clis-lend-limit clis-reclaim task-needs-of])
(import doeff_cluster._worker.core.load_rules [clis-room-lend])
(import doeff_cluster._worker.core.room_rules [room-of-text RoomState])
(import doeff_cluster._worker.protocol.eviction_line [EvictionCell EvictionSettings CliLayerSettings])
(import doeff_hy._table [table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])
(import tests.test_eviction_cli_layer [node-a-files node-a-settings node-a-api ticked-files content-at ALLOCATABLE CLIS JOBS FLOOR CEILING])

(val GIB (* 1024 MIB))
;; node-c の 11-12 18:2x JST の形: clis の床 8 GiB・天井 16 GiB・使い 7959 MiB・需要の余白 4 本 × 1 GiB。空きの半分が 3991 MiB に当たる空き。
(val TURN CLI-TURN-BYTES)
(val TURN-ROOM (* 4 CLI-TURN-BYTES))
(val NODE-C-FLOOR (* 8 GIB))
(val NODE-C-CEILING (* 16 GIB))
(val NODE-C-USE (* 7959 MIB))
(val NODE-C-FREE (* 2 3991 MIB))


;; --- 判断 ---------------------------------------------------------------------------------------------------------------------------

(deftest test-the-lend-limit-keeps-one-turn-over-the-share-and-the-reclaim-restores-the-demand-room
  ;; node-c の形: 平常の上限は 7959 + 3991 = 11950 MiB(空きの半分が余白を切る)。借りの受けを数える上限は使いの上に 1 本 ÷ 0.5 = 2 GiB だけ。
  (<- limit int (clis-limit NODE-C-USE NODE-C-FREE None NODE-C-FLOOR NODE-C-CEILING TURN-ROOM 0))
  (<- lend int (clis-lend-limit limit NODE-C-USE NODE-C-FREE None NODE-C-FLOOR TURN))
  (assert (= #((// limit MIB) (// lend MIB)) #(11950 (+ 7959 2048))) #((// limit MIB) (// lend MIB)))
  ;; 余白が 2 GiB 以下の回・床が使い + 2 GiB を越える回は縮めない(借りの受けは平常と同じ)。
  (<- small int (clis-limit NODE-C-USE (* 2 GIB) None NODE-C-FLOOR NODE-C-CEILING TURN-ROOM 0))
  (<- small-lend int (clis-lend-limit small NODE-C-USE (* 2 GIB) None NODE-C-FLOOR TURN))
  (<- floored int (clis-lend-limit (* 12 GIB) GIB NODE-C-FREE None NODE-C-FLOOR TURN))
  (assert (= #(small-lend floored) #(small NODE-C-FLOOR)) #((// small-lend MIB) (// floored MIB)))
  ;; 上に載る系が残させる余白 kept は縮めずに残す。
  (<- kept int (clis-lend-limit (+ NODE-C-USE (* 6 GIB)) NODE-C-USE (* 20 GIB) None NODE-C-FLOOR TURN GIB))
  (assert (= kept (+ NODE-C-USE GIB (* 2 GIB))) (// kept MIB))
  ;; 返す量: 余白の届くべき値(使い + 4 GiB)との差 105 MiB を 0.5 で割った 210 MiB。差が無ければ 0・天井で切る。
  (<- short int (clis-reclaim limit NODE-C-USE NODE-C-FREE None NODE-C-CEILING TURN-ROOM))
  (<- full int (clis-reclaim (+ NODE-C-USE TURN-ROOM) NODE-C-USE NODE-C-FREE None NODE-C-CEILING TURN-ROOM))
  (<- capped int (clis-reclaim NODE-C-CEILING (* 15 GIB) NODE-C-FREE None NODE-C-CEILING TURN-ROOM))
  (assert (= #((// short MIB) full capped) #(210 0 0)) #(short full capped)))


(deftest test-the-heartbeat-lends-while-the-mark-is-stale-and-names-the-reclaim-while-it-is-fresh
  ;; 待っていない: 借りの受けが平常より広い時だけ、memory の空きを平常のまま貸しの空きとして名乗る。
  (<- quiet object (clis-room-lend False 20000 2679 4622 (* 210 MIB)))
  (assert (= #(quiet.available-mib quiet.jobs-room-mib quiet.reclaim-mib) #(20000 4622 None)) quiet)
  (<- same object (clis-room-lend False 20000 2679 2679 0))
  (<- unread object (clis-room-lend False 20000 2679 None 0))
  (assert (= #(same.available-mib unread.available-mib) #(None None)) #(same unread))
  ;; 待っている: 貸さず、返す量を MiB の切り上げで名乗る。返す量が 0 なら名乗らない。
  (<- waiting object (clis-room-lend True 20000 2679 4622 (+ (* 210 MIB) 1)))
  (assert (= #(waiting.available-mib waiting.jobs-room-mib waiting.reclaim-mib) #(None None 211)) waiting)
  (<- enough object (clis-room-lend True 20000 2679 4622 0))
  (assert (= #(enough.available-mib enough.reclaim-mib) #(None None)) enough))


;; --- worker の読み(eviction-noted)--------------------------------------------------------------------------------------------------

(defk lend-settings []
  {:pre [] :post [(: % EvictionSettings)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の node-a の線の設定に、需要の余白 4 本 × 1 本の要りの会話の CLI の層を置くため(余白が 1 本 ÷ CLIS-ROOM-SHARE より大きい機体)。"
  (<- base EvictionSettings (node-a-settings True))
  (replace base :clis (CliLayerSettings :dir CLIS :floor-bytes FLOOR :ceiling-bytes CEILING :container-dir base.clis.container-dir
                                        :turn-room-bytes TURN-ROOM :turn-bytes TURN :layers base.clis.layers)))


(deftest test-the-worker-widens-the-loan-intake-and-keeps-one-turn-after-the-loan-is-placed
  ;; 平常の node-a: Pod の和 12 GiB・jobs 0.5 GiB・clis 1 GiB・container 3 GiB・余裕 1 GiB。clis の上限は空きの半分が余白を切る。
  (val pods (* 12 GIB))
  (val free (- (- ALLOCATABLE pods) GIB))
  (<- settings EvictionSettings (lend-settings))
  (val cell (EvictionCell))
  (<- start object (node-a-files pods (// GIB 2) GIB (* 3 GIB)))
  (<- files object (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler start) (node-a-api) slog-discard-handler]
                     (ticked-files settings cell 2)))
  (val clis-max (+ GIB (min TURN-ROOM (int (* free CLIS-ROOM-SHARE)))))
  (<- clis-written str (content-at files (+ CLIS "/memory.max")))
  (assert (= (int clis-written) clis-max) (// clis-max MIB))
  ;; 借りの受けは、clis の使いの上に 2 GiB だけを残して数えた受け(平常の受けは床を越えて貸した量を引く)。memory.max は平常のまま。
  (assert (= cell.clis-lend-limit (+ GIB (* 2 GIB))) (// cell.clis-lend-limit MIB))
  (assert (= cell.jobs-intake (- (+ (// GIB 2) free) (- clis-max FLOOR))) (// cell.jobs-intake MIB))
  (assert (= cell.jobs-intake-lend (- (+ (// GIB 2) free) (* 2 GIB))) (// cell.jobs-intake-lend MIB))
  (assert (> cell.jobs-intake-lend cell.jobs-intake) #(cell.jobs-intake cell.jobs-intake-lend))
  (assert (= cell.clis-reclaim (int (- (// (- (- (+ GIB TURN-ROOM) clis-max)) CLIS-ROOM-SHARE)))) (// cell.clis-reclaim MIB))
  ;; 失敗ケース(直す前の形): 置き先が借りの受けの全部の要りの task を置いた。次の回の clis の上限は使いの上に 1 本の要りを残し、
  ;; jobs の層の memory.max はその task の要りを覆う。
  (val need (- cell.jobs-intake-lend (// GIB 2)))
  (<- needs tuple (task-needs-of #({"id" "t-build" "memoryMiB" (// need MIB)})))
  (setv cell.needs needs cell.uses #((JobUse :job "task/t-build" :pid 20 :bytes 0 :processes 1)))
  (<- after object (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files) (node-a-api) slog-discard-handler]
                     (ticked-files settings cell 1)))
  (<- clis-text str (content-at after (+ CLIS "/memory.max")))
  (val clis-after (int clis-text))
  (assert (>= (- clis-after GIB) TURN) (// clis-after MIB))
  (<- jobs-text str (content-at after (+ JOBS "/memory.max")))
  (val jobs-after (int jobs-text))
  (assert (>= (- jobs-after (// GIB 2)) (* (// need MIB) MIB)) #((// jobs-after MIB) (// need MIB))))


;; --- 置き先 ---------------------------------------------------------------------------------------------------------------------------

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
(val NOW 100000)
;; node-c の名乗り: memory の空きは大きく、jobs の層の空きは床の上 2679 MiB。借りの jobs の層の空きは床の上 4622 MiB。
(val JOBS-ROOM-MIB 2679)
(val LEND-JOBS-ROOM-MIB 4622)
(val NEED-MIB 3584)


(defk node-c [* [lend True] [reclaim None] [uses {}]]
  {:pre [(: lend bool) (: reclaim (| int None)) (: uses dict)] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "jobs の層の空きが需要の余白で縮んだ node-c を作るため(CPU は空き・PSI は低い・memory の空きは線で縮まない)。lend = 借りの jobs の層の
   空きを名乗るか(memory の空きは平常と同じ値)・reclaim = 返す量(MiB — None = 上に載る系が待っていない)・uses = task ごとの今の使い。"
  (WorkerInfo "node-c" #("host-node-c") NOW :versions PY3
              :measured (MeasuredLoad :at-ms NOW :cpus 15 :cpu-busy 0.1 :memory-total-MiB 59600 :memory-available-MiB 34000
                                      :jobs-room-MiB (+ JOBS-ROOM-FLOOR-MIB JOBS-ROOM-MIB)
                                      :lend-memory-available-MiB (if lend 34000 None)
                                      :lend-jobs-room-MiB (if lend (+ JOBS-ROOM-FLOOR-MIB LEND-JOBS-ROOM-MIB) None)
                                      :reclaim-MiB reclaim :task-memory-MiB uses)))


(defk queued [id * [preemptible True]]
  {:pre [(: id str) (: preemptible bool)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 host-node-c を要る、待っている切り離した task(要り NEED-MIB・走らせ直しの名乗り・場所を譲る名乗り preemptible)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-node-c") 15000 20000 0 :detached True :key id
              :memory-mib NEED-MIB :rerun True :preemptible preemptible))


(defk borrowed [id]
  {:pre [(: id str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "node-c の需要の余白に借りとして置かれて走っている、場所を譲る名乗りの切り離した task(要り NEED-MIB)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-node-c") 15000 (+ NOW 15000) 0 :detached True :key id
              :memory-mib NEED-MIB :rerun True :preemptible True :borrowed True :phase "assigned" :worker "node-c" :started-ms 1000))


(defk placed-rows [rows worker]
  {:pre [(: rows dict) (: worker WorkerInfo)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker 1 台の状態で task rows を置き先に 1 拍回し、拍の後の全部の行(id → 行)を得るため。"
  (<- workers (table-from-rows {worker.name worker}))
  (<- tasks (table-from-rows rows))
  (val cluster (ClusterState #() workers (table-of #()) tasks))
  (val placed (! (tasks-placed NOW cluster cluster.placements T)))
  (dfor t (.rows placed) t.id t))


(deftest test-a-preemptible-task-borrows-the-demand-room-and-a-plain-task-waits
  (<- task TaskRecord (queued "t-build"))
  (<- keep TaskRecord (queued "t-keep" :preemptible False))
  (<- lending WorkerInfo (node-c))
  (<- plain-worker WorkerInfo (node-c :lend False))
  (<- rows dict (placed-rows {"t-build" task} lending))
  (val placed (get rows "t-build"))
  (assert (= #(placed.phase placed.worker placed.borrowed) #("assigned" "node-c" True)) placed)
  ;; 失敗ケース(直す前の形): 借りの受けを名乗らない node-c では、要り 3584 MiB の task は jobs の層の空き 2679 MiB を待つ。
  (<- before dict (placed-rows {"t-build" task} plain-worker))
  (assert (= (. (get before "t-build") phase) "queued") (get before "t-build"))
  ;; 譲れない task は借りに置かない。
  (<- plain dict (placed-rows {"t-keep" keep} lending))
  (assert (= #((. (get plain "t-keep") phase) (. (get plain "t-keep") borrowed)) #("queued" False)) (get plain "t-keep")))


(deftest test-the-loan-is-stopped-only-while-the-upper-system-waits
  ;; 待っている(返す量 211 MiB を名乗る): 借りの task を止めて次の試行の待ちの行へ戻す(消失に数えない)。
  (<- loan TaskRecord (borrowed "a-build"))
  (<- waiting WorkerInfo (node-c :lend False :reclaim 211 :uses {"a-build" 3000}))
  (<- quiet-worker WorkerInfo (node-c :lend False :uses {"a-build" 3000}))
  (<- rows dict (placed-rows {"a-build" loan} waiting))
  (assert (not-in "a-build" rows) rows)
  (val again (get rows "a-build-r2"))
  (assert (= #(again.phase again.losses again.reclaimed again.borrowed) #("queued" 0 True False)) again)
  ;; 待っていない: 借りの受けが無くても止めない。
  (<- quiet dict (placed-rows {"a-build" loan} quiet-worker))
  (assert (= #((. (get quiet "a-build") phase) (. (get quiet "a-build") borrowed)) #("assigned" True)) quiet))


;; --- 枠の file ----------------------------------------------------------------------------------------------------------------------

(deftest test-a-row-without-a-keep-line-takes-the-reclaim-mark-alone
  (<- alone object (room-of-text "{\"node-c\": {\"memoryMiB\": 8192, \"reclaimFile\": \"/run/mark\"}}" "node-c"))
  (assert (= #(alone.state alone.room.lend-line-file alone.room.reclaim-file) #(RoomState.OK "" "/run/mark")) alone)
  ;; keepLineFile の在る行は今までどおり lendLineFile と組(線の内の貸しと需要の余白の貸しを 1 行で混ぜない)。
  (<- mixed object (room-of-text "{\"node-c\": {\"memoryMiB\": 1, \"keepLineFile\": \"/k\", \"reclaimFile\": \"/r\"}}" "node-c"))
  (assert (= mixed.state RoomState.MALFORMED) mixed)
  ;; / で始まらない印の path は読まない。
  (<- relative object (room-of-text "{\"node-c\": {\"memoryMiB\": 1, \"reclaimFile\": \"mark\"}}" "node-c"))
  (assert (= relative.state RoomState.MALFORMED) relative))
