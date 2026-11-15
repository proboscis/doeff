;;; 置く時の要りが実の山を下回らない事の検(coordinator/core/measured_room の頭の註の余裕 PEAK-STEP-MIB の訳と形ごとの余裕):
;;;   (1) task の行の山は、worker の見張りが task ごとに測った山(heartbeat の測りの taskPeakMiB)で書く。走っている間は worker の山との差が
;;;       PEAK-STEP-MIB 以下で、終わった後は worker の log の「job の memory の最大」と同じ値になる
;;;   (2) 同じ形の要りの余裕は、近い回の finished の山の散らばり(最大 / 最小)で、SHAPE-PEAK-MARGIN から SHAPE-MARGIN-CAP の間
;;; 失敗ケース (1) = t47120(11-10〜11-12): 行の山 666 MiB・worker の log の山 940 MiB。行の山は heartbeat の今の使いの最大で、heartbeat の
;;; 間の短い山と終わる前の最後の伸びを取り逃がした(差 274 MiB は刻み 256 MiB を越える)。
;;; 失敗ケース (2) = t46855(日次の回): 前の回の山 2,915 MiB から要り 2,915 × 1.25 + 256 = 3,900 MiB で置かれ、山 4,260 MiB
;;; (1.46 倍)まで伸びた。同じ形の近い回の山は 2,000〜2,915 MiB に散っていた。
(require doeff-hy.macros [defk deftest defhandler defeffect <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import math [ceil])
(import doeff [with-handlers])
(import doeff_core_effects [SlogEffect state])
(import doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])
(import doeff_cluster._coordinator.core.cluster_policy [task-peaks-noted shape-peaks-of PEAK-STEP-MIB])
(import doeff_cluster._coordinator.core.measured_room [learned-need shape-margin-of ShapePeak SHAPE-PEAK-MARGIN SHAPE-MARGIN-CAP])
(import doeff_cluster._worker.core.memory_rules [JobUse JobRoot MIB])
(import doeff_cluster._worker.core.load_rules [task-uses-of])
(import doeff_cluster._worker.protocol.memory_watch [MemoryCell noted-peaks JOB-PEAK-LOG ENDED-PEAK-KEEP-MS])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])
(import doeff_hy._table [Table TableWrite])

(val PY3 #((ComponentVersion "python" "3")))
(val SHAPE "daily-verify repo-a/repo-a")
;; 見張りの読みの間(worker/protocol/memory_watch の MemoryWatchSettings.sample-seconds)。
(val SAMPLE-MS 5000)
;; t47120 の形の使いの列(MiB — 見張りの 5 秒ごとの読み)と、heartbeat の届く読みの番。None = 根が観測から消えた(task が終わった)読み。
;; heartbeat の間の 900 MiB の跳ねと、終わる直前の 940 MiB の伸びは、heartbeat の今の使いには出ない。
(val USES #(300 500 666 700 900 650 660 940 None None))
(val HEARTBEATS #{2 5 9})
(val LOGGED-PEAK-MIB 940)


(defhandler peak-log-world []
  "見張りの log の行のうち JOB-PEAK-LOG の行を溜める偽の世界(ReadPeakLines で問う)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var lines #())
  (SlogEffect []
    (when (= effect.msg JOB-PEAK-LOG)
      (:= lines (+ lines #((dict effect.kwargs)))))
    (resume None))
  (ReadPeakLines []
    (resume lines)))


(defeffect ReadPeakLines
  "見張りが出した JOB-PEAK-LOG の行(欄の dict の列 — 出した順)を問う — テストだけの effect。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defk task-of [id phase peak]
  {:pre [(: id str) (: phase str) (: peak (| int None))] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker w1 に置いた(phase)形 SHAPE の切り離した task を、測った山 peak で作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #() 15000 20000 0 :detached True :key id :memory-mib 3584 :shape SHAPE
              :phase phase :worker "w1" :started-ms 0 :finished-ms (if (= phase "assigned") None 1) :peak-mib peak))


(defk heard [rows now cell told]
  {:pre [(: rows (get Table TaskRecord)) (: now int) (: cell MemoryCell) (: told str)] :post [(: % (get Table TaskRecord))]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "見張りの入れ物 cell の今の読みを、worker の heartbeat の測りの欄 told(\"peak\" = taskPeakMiB・\"use\" = 前の版の taskMemoryMiB だけ)に
   して、coordinator が task の行の山を書き直した表を知るため。"
  (<- uses (| tuple None) (task-uses-of cell.eviction.uses cell.eviction.uses-at-ms now))
  (<- peaks (| tuple None) (task-uses-of cell.eviction.peaks cell.eviction.uses-at-ms now))
  (val measured (MeasuredLoad :at-ms now :cpus 8 :memory-total-MiB 64000 :memory-available-MiB 32000
                              :task-memory-MiB (dfor u uses u.id u.mib)
                              :task-peak-MiB (if (= told "peak") (dfor u peaks u.id u.mib) None)))
  (<- noted (get Table TaskRecord) (task-peaks-noted rows "w1" measured))
  noted)


(defk watched-rows [told]
  {:pre [(: told str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "USES の読みを本物の見張りの山の数え(noted-peaks)に順に通し、HEARTBEATS の番で heartbeat の測りを coordinator の行へ当てるため。
   答え = #(heartbeat ごとの #(行の山 その刻の worker の山) の列  最後の行の山  JOB-PEAK-LOG の行)。task は USES が None の読みで終わる
   (状態の報告で終わった行になる)。"
  (val cell (MemoryCell))
  (<- start TaskRecord (task-of "t1" "assigned" None))
  (<- rows0 (get Table TaskRecord) (table-from-rows {"t1" start}))
  (var rows rows0)
  (var seen #())
  (for [#(n mib) (enumerate USES)]
    (val now (* n SAMPLE-MS))
    (val uses (if (is mib None) #() #((JobUse :job "task/t1" :pid 20 :bytes (* mib MIB) :processes 2))))
    (val roots (if (is mib None) #() #((JobRoot :name "task/t1" :pid 20))))
    (setv cell.eviction.uses uses cell.eviction.uses-at-ms now)
    (<- (noted-peaks cell uses roots now))
    (when (and (is mib None) (= (. (.row rows "t1") phase) "assigned"))
      (:= rows (.with-writes rows #((TableWrite "t1" (replace (.row rows "t1") :phase "finished" :finished-ms now))))))
    (when (in n HEARTBEATS)
      (<- noted (get Table TaskRecord) (heard rows now cell told))
      (:= rows noted)
      (val worker-peak (max (gfor m (cut USES 0 (+ n 1)) :if (is-not m None) m)))
      (:= seen (+ seen #(#((. (.row rows "t1") peak-mib) worker-peak))))))
  (<- lines tuple (ReadPeakLines))
  #(seen (. (.row rows "t1") peak-mib) lines))




(deftest test-the-row-peak-stays-within-the-step-of-the-worker-peak-and-ends-at-the-logged-peak
  (<- got tuple (with-handlers [(state) (peak-log-world)] (watched-rows "peak")))
  (val seen (get got 0))
  (val last (get got 1))
  (val lines (get got 2))
  ;; worker の log の山は 1 行・940 MiB(終わる直前の読み)。
  (assert (= (tuple (gfor l lines #((get l "job") (get l "peak_mib")))) #(#("task/t1" LOGGED-PEAK-MIB))) lines)
  ;; 走っている間の heartbeat ごとに、行の山は worker の山との差が PEAK-STEP-MIB 以下(900 MiB の跳ねは 666 + 256 に届かず書かない)。
  (assert (all (gfor #(row worker) seen (<= 0 (- worker row) PEAK-STEP-MIB))) seen)
  (assert (= (get seen 1) #(666 900)) seen)
  ;; 終わった後の heartbeat で、行の山は log の山と同じ値になる。
  (assert (= last LOGGED-PEAK-MIB) last))


(deftest test-the-current-use-alone-misses-the-peak-between-heartbeats
  ;; 失敗ケース t47120 の再現: 前の版の worker(taskPeakMiB を名乗らない)の今の使いだけでは、行の山は 666 MiB のまま、log の山 940 MiB との
  ;; 差 274 MiB が PEAK-STEP-MIB を越える(前の版の worker とも組めるよう、今の使いだけの heartbeat の読みは前のまま)。
  (<- got tuple (with-handlers [(state) (peak-log-world)] (watched-rows "use")))
  (val seen (get got 0))
  (val last (get got 1))
  (val lines (get got 2))
  (assert (= last 666) seen)
  (assert (> (- LOGGED-PEAK-MIB last) PEAK-STEP-MIB) last))


(deftest test-an-ended-peak-is-told-only-for-a-while
  ;; 終わった task の最後の山は ENDED-PEAK-KEEP-MS の間だけ名乗る(task の終わりの報告と次の heartbeat の間を覆い、名乗りを溜め続けない)。
  (val cell (MemoryCell))
  (val use (JobUse :job "task/t1" :pid 20 :bytes (* 700 MIB) :processes 1))
  (<- (with-handlers [(state) (peak-log-world)] (noted-peaks cell #(use) #((JobRoot :name "task/t1" :pid 20)) 0)))
  (<- (with-handlers [(state) (peak-log-world)] (noted-peaks cell #() #() SAMPLE-MS)))
  (assert (= (tuple (gfor p cell.eviction.peaks #(p.job p.bytes))) #(#("task/t1" (* 700 MIB)))) cell.eviction.peaks)
  (<- (with-handlers [(state) (peak-log-world)] (noted-peaks cell #() #() (+ SAMPLE-MS ENDED-PEAK-KEEP-MS))))
  (assert (= cell.eviction.peaks #()) cell.eviction.peaks))


(defk ended-run [id phase peak at]
  {:pre [(: id str) (: phase str) (: peak int) (: at int)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "形 SHAPE の終わった行(phase・山 peak・終わった刻 at)を作るため。"
  (<- t TaskRecord (task-of id phase peak))
  (replace t :finished-ms at))


(defk shape-peak-of [runs]
  {:pre [(: runs dict)] :post [(: % ShapePeak)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "終わった行 runs(id → TaskRecord)だけの表の形 SHAPE の ShapePeak を知るため。"
  (<- rows (get Table TaskRecord) (table-from-rows runs))
  (<- peaks dict (shape-peaks-of rows))
  (get peaks SHAPE))


(deftest test-the-shape-margin-follows-the-spread-of-the-recent-finished-runs
  ;; 失敗ケース t46855 の形: 近い回の finished の山 2,000・2,915 MiB(散らばり 1.46)の形の次の回は、2,915 × 1.46 + 256 で置く — 山 4,260 MiB を
  ;; 下回らない(固定の 1.25 倍では 3,900 MiB)。
  (<- a TaskRecord (ended-run "a" "finished" 2000 1))
  (<- b TaskRecord (ended-run "b" "finished" 2915 2))
  (<- spread ShapePeak (shape-peak-of {"a" a "b" b}))
  (assert (= #(spread.mib spread.settled) #(2915 True)) spread)
  (assert (< (abs (- spread.spread (/ 2915 2000))) 1e-9) spread)
  (<- need int (learned-need 3584 None spread None))
  (assert (= need (+ (ceil (* 2915 (/ 2915 2000))) PEAK-STEP-MIB)) need)
  (assert (>= need 4260) need)
  (assert (< (+ (ceil (* 2915 SHAPE-PEAK-MARGIN)) PEAK-STEP-MIB) 4260))
  ;; 散らばりの小さい形は SHAPE-PEAK-MARGIN のまま(固い仕事の要りを広げない)。finished の行が 1 本の形も同じ。
  (<- c TaskRecord (ended-run "c" "finished" 2100 1))
  (<- tight ShapePeak (shape-peak-of {"c" c "b" (replace b :peak-mib 2200)}))
  (<- tight-margin float (shape-margin-of tight))
  (assert (= tight-margin SHAPE-PEAK-MARGIN) tight)
  (<- single ShapePeak (shape-peak-of {"b" b}))
  (assert (= #(single.spread (! (shape-margin-of single))) #(1.0 SHAPE-PEAK-MARGIN)) single)
  ;; 止まった行(lost)の小さい山は散らばりに数えない(止まった時までの途中の値)— 山の最大には数える。
  (<- lost TaskRecord (ended-run "l" "lost" 300 3))
  (<- with-lost ShapePeak (shape-peak-of {"c" c "b" (replace b :peak-mib 2200) "l" lost}))
  (assert (= #(with-lost.mib (! (shape-margin-of with-lost))) #(2200 SHAPE-PEAK-MARGIN)) with-lost)
  ;; 散らばりの大きい形(何もしない回 600 MiB と組む回 2,500 MiB)の余裕は SHAPE-MARGIN-CAP で止まる。
  (<- small TaskRecord (ended-run "s" "finished" 600 1))
  (<- large TaskRecord (ended-run "g" "finished" 2500 2))
  (<- wide ShapePeak (shape-peak-of {"s" small "g" large}))
  (<- wide-need int (learned-need 3584 None wide None))
  (assert (= wide-need (+ (ceil (* 2500 SHAPE-MARGIN-CAP)) PEAK-STEP-MIB)) wide-need))
