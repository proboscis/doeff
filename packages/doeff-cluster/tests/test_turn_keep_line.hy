;;; 会話のターンに残す線(worker/core/load_rules の頭の註)の検: 機体の memory を作り直せない会話のターンと作り直せる
;;; task が分け合う機体で、worker が名乗る memory の空きを、上に載る系のターンを受ける係が書く MemAvailable の線の上に絞る。
;;;   (1) 線の file が読めれば、名乗る空き = min(追い出しまでの空き, MemAvailable − 線)。総量(線の大きさ)は替えない。file が無い・
;;;       数でない間は追い出しまでの空きのまま
;;;   (2) 枠の file の行の keepLineFile が線の file を名指す(heartbeat の前に読み直す — Pod を作り直さない)。形の誤りは malformed
;;;   (3) 追い出しまでの空きは大きいが MemAvailable が線に近い worker には新しい task を置かず、memory の空いた別の worker へ置く。
;;;       その機体でしか走れない task は空きの無い文で待つ
;;; 失敗ケース = 11-08 03:00〜04:06 UTC の node-h: 追い出しまでの空き 34,901〜50,313 MiB を名乗り、係がターンを返している memory に
;;; coordinator が task を置き続けた(task の層の使いが 10,425 → 18,682 MiB)。
(require doeff-hy.macros [deff defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import httpx)
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo RoomReading RoomState WorkerRoom room-of-text]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion TaskUnplacedKind])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad HeartbeatBody])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed task-unplaced-text])
(import doeff_cluster._coordinator.core.measured_room [memory-room-mib])
(import doeff_cluster._worker.core.eviction_rules [EvictionLine LineKind])
(import doeff_cluster._worker.core.load_rules [LoadPaths LoadReading machine-load-of keep-line-of])
(import doeff_cluster._worker.core.memory_rules [MIB])
(import doeff_cluster._worker.protocol.heartbeat [measured-json])
(import tests.link_rig [LinkRig])
(import tests.program_rows [SAMPLE-TASK-PROGRAM heartbeat-of])
(import tests.state_rows [table-from-rows])
(import doeff_hy._table [Table])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
;; 本番 node-h の 11-08 の値: MemTotal 128,480 MiB・係の床 25,696 MiB(MemTotal × 0.20)・係の書く線 37,984 MiB(床 + 1,024 MiB × 12 本)。
;; その刻の追い出しの線は allocatable の線で、追い出しまでの空き 40,000 MiB(読みの幅 34,901〜50,313 MiB の中)・MemAvailable は線の
;; 1,516 MiB 上(係の余白 = 床からの 13,804 MiB)。
(val NODE-H-TOTAL-MIB 128480)
(val KEEP-LINE-MIB 37984)
(val NODE-H-AVAILABLE-MIB (+ KEEP-LINE-MIB 1516))
(val NODE-H-EVICTION (EvictionLine :kind LineKind.ALLOCATABLE :size-bytes (* 100000 MIB) :used-bytes (* 60000 MIB)
                                 :room-bytes (* 40000 MIB)))
;; memory の空いた別の機体(node-c — 線を書く係の無い機体): MemAvailable 20,000 MiB。
(val NODE-C-TOTAL-MIB 65536)
(val NODE-C-AVAILABLE-MIB 20000)
;; CPU 8 個の 2 回の読み(使い 0.1 — CPU は空いている。memory の判定だけを見る)。
(val STAT-A (+ "cpu  0 0 0 1000 0 0 0 0 0 0\n" (.join "" (gfor i (range 8) (.format "cpu{} 0 0 0 0 0 0 0 0 0 0\n" i)))))
(val STAT-B (+ "cpu  100 0 0 1900 0 0 0 0 0 0\n" (.join "" (gfor i (range 8) (.format "cpu{} 0 0 0 0 0 0 0 0 0 0\n" i)))))
(val CALM-PSI "some avg10=0.50 avg60=0.40 avg300=0.30 total=100\nfull avg10=0.10 avg60=0.10 avg300=0.10 total=10\n")
;; 置く task の要り(会話の test の build の 1 本の形)。
(val NEED-MIB 4096)


(defk meminfo-text [total-mib available-mib]
  {:pre [(: total-mib int) (: available-mib int)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "MemTotal total-mib・MemAvailable available-mib(どちらも MiB)の /proc/meminfo の中身を作るため。"
  (.format "MemTotal: {} kB\nMemFree: 1024 kB\nMemAvailable: {} kB\n" (* total-mib 1024) (* available-mib 1024)))


(defk measured-of [meminfo line keep]
  {:pre [(: meminfo str) (: line (| EvictionLine None)) (: keep (| str None))] :post [(: % MeasuredLoad)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "/proc の 2 回の読み(meminfo・追い出しの線 line・線の file の中身 keep)を、worker の測り(machine-load-of)と heartbeat の綴り
   (measured-json)と coordinator の受け口の解き(heartbeat-of)に通した測った空きを作るため。"
  (<- first LoadReading (machine-load-of STAT-A meminfo CALM-PSI CALM-PSI None 0 line :keep-line keep))
  (<- second LoadReading (machine-load-of STAT-B meminfo CALM-PSI CALM-PSI first.ticks 5000 line :keep-line keep))
  (<- spelled dict (measured-json second.load))
  (<- body HeartbeatBody (heartbeat-of {"name" "w" "provides" [] "measured" spelled}))
  body.measured)


(defk queued [id needs]
  {:pre [(: id str) (: needs tuple)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力 needs を要る、要り NEED-MIB の待っている切り離した task を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 needs 15000 20000 0 :detached True :key id :memory-mib NEED-MIB))


(defk placed-on [node-h-measured]
  {:pre [(: node-h-measured MeasuredLoad)] :post [(: % (get Table TaskRecord))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "node-h が node-h-measured を、node-c が空いた測りを名乗る時に、どの機体にも置ける task any と node-h でしか走れない task only を 1 拍で
   置いた task の表を知るため。"
  (<- node-c-measured MeasuredLoad (measured-of (! (meminfo-text NODE-C-TOTAL-MIB NODE-C-AVAILABLE-MIB)) None None))
  (val node-h (WorkerInfo "node-h" #("host-any" "host-node-h") 0 :versions PY3 :measured node-h-measured))
  (val node-c (WorkerInfo "node-c" #("host-any" "host-node-c") 0 :versions PY3 :measured node-c-measured))
  (<- workers (table-from-rows {"node-h" node-h "node-c" node-c}))
  (<- any TaskRecord (queued "any" #("host-any")))
  (<- only TaskRecord (queued "only" #("host-node-h")))
  (<- rows (table-from-rows {"any" any "only" only}))
  (<- none (table-from-rows {}))
  (val state (ClusterState #() workers none rows))
  (! (tasks-placed 1000 state state.placements T)))


(deftest test-the-named-room-stops-at-the-line-the-turn-host-keeps
  ;; 通る例: 線が読めれば、名乗る空き = min(追い出しまでの空き 40,000, MemAvailable 39,500 − 線 37,984 = 1,516)。総量は線の大きさのまま。
  (val meminfo (! (meminfo-text NODE-H-TOTAL-MIB NODE-H-AVAILABLE-MIB)))
  (<- kept MeasuredLoad (measured-of meminfo NODE-H-EVICTION (.format "{}\n" KEEP-LINE-MIB)))
  (assert (= #(kept.memory-available-MiB kept.memory-total-MiB) #(1516 100000)) kept)
  ;; 置ける要りの最大は床 1,536 MiB を引いて負 — どの大きさの task も置かない。
  (<- room int (memory-room-mib kept 0))
  (assert (= room -20) room)
  ;; MemAvailable が線を割っていれば 0(負にしない)。
  (<- under MeasuredLoad (measured-of (! (meminfo-text NODE-H-TOTAL-MIB (- KEEP-LINE-MIB 500))) NODE-H-EVICTION (str KEEP-LINE-MIB)))
  (assert (= under.memory-available-MiB 0) under)
  ;; 追い出しまでの空きの方が狭ければ、その空きのまま(線は空きを減らす向きにだけ効く)。
  (<- roomy MeasuredLoad (measured-of (! (meminfo-text NODE-H-TOTAL-MIB 100000)) NODE-H-EVICTION (str KEEP-LINE-MIB)))
  (assert (= roomy.memory-available-MiB 40000) roomy)
  ;; 失敗ケース(直す前 = 線を読まない): 追い出しまでの空き 40,000 MiB を名乗り、係がターンを返す memory に 38 GiB の task を置ける。
  (<- before-fix MeasuredLoad (measured-of meminfo NODE-H-EVICTION None))
  (assert (= before-fix.memory-available-MiB 40000) before-fix)
  ;; 線の file の中身が数でない・0・空の間は線が無いと読む(係がまだ起きていない Pod の起動の直後)。
  (for [text ["" "\n" "abc\n" "0\n" "-5\n" "１２\n" "37984 MiB\n"]]
    (<- read (| int None) (keep-line-of text))
    (assert (is read None) #(text read))
    (<- loose MeasuredLoad (measured-of meminfo NODE-H-EVICTION text))
    (assert (= loose.memory-available-MiB 40000) #(text loose)))
  (<- spaced (| int None) (keep-line-of " 37984 \n"))
  (assert (= spaced KEEP-LINE-MIB) spaced))


(deftest test-the-room-file-row-names-the-keep-line-file
  ;; 通る例: 行の keepLineFile が線の file を名指す。欄の無い行は読まない(空)。
  (<- node-h RoomReading (room-of-text (json.dumps {"node-h" {"memoryMiB" 63488 "keepLineFile" "/cargo-build/.keep-available-mib"}}) "node-h"))
  (assert (= node-h.room (WorkerRoom :memory-mib 63488 :keep-line-file "/cargo-build/.keep-available-mib")) node-h)
  (<- plain RoomReading (room-of-text (json.dumps {"node-c" {"memoryMiB" 28672}}) "node-c"))
  (assert (= plain.room.keep-line-file "") plain)
  ;; 失敗ケース: 文字列でない・相対の path は malformed(黙って線を読まない側へ倒れない)。
  (for [#(value reason) [[1 "worker node-h の行の keepLineFile は / で始まる file の path で書く: 1"]
                         ["keep.txt" "worker node-h の行の keepLineFile は / で始まる file の path で書く: 'keep.txt'"]]]
    (<- read RoomReading (room-of-text (json.dumps {"node-h" {"memoryMiB" 63488 "keepLineFile" value}}) "node-h"))
    (assert (= read (RoomReading :state RoomState.MALFORMED :room None :reason reason)) read)))


(deftest test-the-heartbeat-cuts-the-room-at-the-line-named-by-the-room-file [tmp-path]
  (setv sent #())
  (deff handle [#^ httpx.Request request]  ; cannot be a defk: httpx.MockTransport が呼ぶ callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
    "口が送った heartbeat の本文を控え、宣言の空の返事で答えるため。"
    (nonlocal sent)
    (setv sent (+ sent #((json.loads request.content))))
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" [] "draining" False}))
  ;; 検の機体の /proc の代わりの file(線の無い機体の読み — MemAvailable 39,500 MiB)と、係が書く線の file。
  (val root (/ tmp-path "proc"))
  (.mkdir (/ root "pressure") :parents True)
  (.write-text (/ root "stat") STAT-A)
  (.write-text (/ root "meminfo") (! (meminfo-text NODE-H-TOTAL-MIB NODE-H-AVAILABLE-MIB)))
  (.write-text (/ root "pressure" "cpu") CALM-PSI)
  (.write-text (/ root "pressure" "memory") CALM-PSI)
  (val line (/ tmp-path "cargo-build" ".keep-available-mib"))
  (.mkdir line.parent)
  (.write-text line (.format "{}\n" KEEP-LINE-MIB))
  (val room (/ tmp-path "room.json"))
  (.write-text room (json.dumps {"node-h" {"memoryMiB" 63488 "keepLineFile" (str line)}}))
  (val link (LinkRig "http://coord" "node-h" #() 60000 :task-dir (str (/ tmp-path "tasks")) :transport (httpx.MockTransport handle)))
  (setv link.state.room-file (str room)
        link.state.load-paths (LoadPaths :stat (str (/ root "stat")) :meminfo (str (/ root "meminfo"))
                                         :cpu-pressure (str (/ root "pressure" "cpu")) :memory-pressure (str (/ root "pressure" "memory"))))
  (val available (fn [] (get (get (get sent -1) "measured") "memoryAvailableMiB")))
  ;; 通る例: 枠の file が線の file を名指し、名乗る空きは MemAvailable − 線。
  (.poll link)
  (assert (= #((available) link.state.keep-line-file) #(1516 (str line))) (get sent -1))
  ;; 係がまだ線を書いていない間(file が無い)は MemAvailable のまま。
  (.unlink line)
  (setv link.state.load-read-ms None)
  (.poll link)
  (assert (= (available) NODE-H-AVAILABLE-MIB) (get sent -1))
  ;; 失敗ケース(直す前の枠 = keepLineFile の無い行): 線の file が在っても読まない。
  (.write-text line (.format "{}\n" KEEP-LINE-MIB))
  (.write-text room (json.dumps {"node-h" {"memoryMiB" 63488}}))
  (setv link.state.room-read-ms None link.state.load-read-ms None)
  (.poll link)
  (assert (= #((available) link.state.keep-line-file) #(NODE-H-AVAILABLE-MIB "")) (get sent -1)))
