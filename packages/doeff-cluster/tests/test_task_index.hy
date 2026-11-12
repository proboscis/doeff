;;; task の表の索引(task_table.TaskIndex)が、任意の書きの列の後に全部の行から作り直した値と同じである事の test(2026-11-12)。
;;;
;;; 実例: 要求ごとの調停が、task の表の全部の行(保持の間の終わった行を含む約 1,800 行)から派生の値を毎回作り直し、1 要求の費用が行の
;;; 総数に比例した。索引は書きのたびに差分で進むので、作り直しと違う値になれば置き方の判断を黙って狂わせる。乱数の書きの列(足す・書き換える・
;;; 消す・同じ鍵を 2 度書く・下書きの freeze)の 1 歩ごとに、索引と全部の行からの作り直しを照らす。
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import random)
(import pytest)
(import dataclasses [replace])
(import doeff_hy._table [Table TableWrite table-of draft-of])
(import doeff_cluster [ClusterState ClusterTiming])
(import doeff_cluster._coordinator.intent.cluster_model [TaskRecord])
(import doeff_cluster._coordinator.intent.task_table [TaskTable TaskIndex task-table-of task-index index-of-rows])
(import doeff_cluster._coordinator.core.api_policy [tick])
(import doeff_cluster._coordinator.core.program_policy [program-refs])
(import doeff_cluster._coordinator.core.cluster_policy [sweep-due])
(import doeff_cluster._coordinator.intent.cluster_model [ProgramRow])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])

(val PHASES #("queued" "assigned" "preparing" "finished" "failed" "cancelled" "lost"))
(val SHAPES #("" "a" "b" "c"))
;; 詰めた Program の置き場のキー(None = Program を持たない旧い形の終わった行)。
(val PROGRAMS #(SAMPLE-TASK-PROGRAM "p1" "p2" None))
(val NOW 1000000)
(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))


(defk random-task [rng id]
  {:pre [(: rng random.Random) (: id str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "索引の読む欄(切り離したか・phase・終わった刻・保持の長さ・形・山・Program の参照)を乱数で選んだ task の行 1 本を作るため。"
  (val phase (.choice rng PHASES))
  (TaskRecord id "digest" (.choice rng PROGRAMS) "rev" #() #() 15000 (+ NOW 20000) 0
              :detached (< (.random rng) 0.8)
              :phase phase
              :worker (if (in phase #("assigned" "preparing")) "w0" None)
              :finished-ms (if (and (not-in phase #("queued" "assigned" "preparing")) (< (.random rng) 0.9))
                               (.randrange rng 0 NOW) None)
              :retain-ms (.randrange rng 0 100000)
              :shape (.choice rng SHAPES)
              :peak-mib (if (< (.random rng) 0.7) (.randrange rng 0 50) None)))


(defk random-rows [rng count]
  {:pre [(: rng random.Random) (: count int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "鍵 t0 … t{count-1} の乱数の行を、表を作る書きの列にするため。"
  (val rows [])
  (for [i (range count)]
    (val key (.format "t{}" i))
    (.append rows (TableWrite key (! (random-task rng key)))))
  (tuple rows))


(defk index-view [index]
  {:pre [(: index TaskIndex)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "索引を比べられる値にするため(走査の対象の行・保持の行の期限・最小の期限・形ごとの山・Program の参照の数え)。"
  #(index.live index.held index.earliest index.peaks index.programs))


(defk random-writes [rng table]
  {:pre [(: rng random.Random) (: table Table)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "表への 1 回の書きの列(1〜4 本 — 新しい鍵を足す・在る鍵を書き換える・消す・同じ鍵を 2 度書く)を作るため。"
  (val keys (.keys table))
  (val writes [])
  (for [_ (range (.randint rng 1 4))]
    (val existing (and keys (< (.random rng) 0.7)))
    (val key (if existing (.choice rng keys) (.format "t{}" (.randrange rng 0 400))))
    (.append writes (TableWrite key (if (< (.random rng) 0.2) None (! (random-task rng key))))))
  (tuple writes))


(deftest test-the-index-matches-a-rebuild-from-all-rows-after-any-writes []
  ;; 失敗ケース: 索引が書きの差分で進む間に、消した行の山や期限を残す・書き換えた行の前の値を引き忘れると、作り直しと違う値になる。
  ;; 乱数の 20 本の列(各 150 歩・with-writes と下書きの freeze を混ぜる)の 1 歩ごとに、全部の行からの作り直しと照らす。
  (for [seed (range 20)]
    (val rng (random.Random seed))
    (var table (task-table-of (! (random-rows rng 30))))
    (task-index table)
    (for [step (range 150)]
      (val writes (! (random-writes rng table)))
      (val before (! (index-view (task-index table))))
      (val after (if (< (.random rng) 0.3)
                     (do (val draft (draft-of table))
                         (for [w writes] (if (is w.value None) (.remove draft w.key) (.put draft w.key w.value)))
                         (.freeze draft))
                     (.with-writes table writes)))
      (assert (isinstance after TaskTable) #(seed step (type after)))
      (assert (= (! (index-view (task-index after))) (! (index-view (index-of-rows (.rows after))))) #(seed step writes))
      ;; 前の表の索引は書きで変わらない(古い表を持つ読み手が古い値を読み続ける)。
      (assert (= (! (index-view (task-index table))) before) #(seed step))
      (:= table after))))


(deftest test-a-task-table-equals-a-plain-table-with-the-same-rows []
  ;; 索引を持つ表と素の Table(test が table-of で作る表)は、中身が同じなら両側から等しい(型の違いで黙って偽にならない)。
  (val rows (! (random-rows (random.Random 5) 5)))
  (val indexed (task-table-of rows))
  (val plain (table-of rows))
  (assert (= indexed plain))
  (assert (= plain indexed))
  (assert (!= indexed (table-of (cut rows 4)))))


(deftest test-the-cluster-state-holds-its-tasks-in-a-task-table []
  ;; 状態の task の表は索引を持つ表(既定の空の表・書いた後の表)— 素の Table に戻ると要求ごとに全部の行から作り直す。
  (val state (ClusterState))
  (assert (isinstance state.tasks TaskTable))
  (val written (.with-writes state.tasks #((TableWrite "t1" (! (random-task (random.Random 1) "t1"))))))
  (assert (isinstance written TaskTable)))


(defn counting-reads [#^ list reads #^ object method]
  "TaskTable の全部の行の読みの口 method を、読んだ回数を reads に足してから読む口にするため。"
  (fn [self] (.append reads 1) (method self)))


(deftest test-a-tick-does-not-read-all-rows-of-the-task-table [#^ pytest.MonkeyPatch monkeypatch]
  ;; 失敗ケース(直す前): 要求ごとの調停が task の表の全部の行(保持の間の終わった行を含む)を何度も読んだ。索引を作った後の調停は、
  ;; 保持の行を 1,000 本持つ表の全部の行を 1 度も読まない(task の表の読みの口 rows・items・keys を数える)。
  (val held (lfor i (range 1000)
                  (TaskRecord (.format "f{}" i) "digest" SAMPLE-TASK-PROGRAM "rev" #() #() 15000 (+ NOW 20000) 0
                              :detached True :phase "finished" :finished-ms (- NOW 1000) :retain-ms 3600000
                              :shape "s" :peak-mib (+ 100 i))))
  (val tasks (task-table-of (tuple (gfor t held (TableWrite t.id t)))))
  (task-index tasks)
  (val reads [])
  (for [name #("rows" "items" "keys")]
    (.setattr monkeypatch TaskTable name (counting-reads reads (getattr Table name))))
  (val ticked (! (tick (ClusterState :tasks tasks) NOW T)))
  (assert (is ticked.tasks tasks))
  (assert (= (len reads) 0) (len reads)))


(deftest test-program-refs-read-no-task-rows-whatever-the-row-count [#^ pytest.MonkeyPatch monkeypatch]
  ;; 失敗ケース(直す前 — 2026-11-13): 置き場の掃除の期限(sweep-due)は調停の毎歩に Program の参照(program-refs)を問い、参照は task の表の
  ;; 全部の行を読んだので、1 要求の費用が保持の間の終わった行を含む行の総数に比例した。参照は索引の数えから読む: 保持の行が 1,000 本でも
  ;; 2,000 本でも、program-refs と sweep-due は task の表の全部の行を 1 度も読まず(読みの口 rows・items・keys を数える)、答えは全部の行から
  ;; 求めた参照と同じ。
  (val reads [])
  (for [name #("rows" "items" "keys")]
    (.setattr monkeypatch TaskTable name (counting-reads reads (getattr Table name))))
  (for [count #(1000 2000)]
    (val held (lfor i (range count)
                    (TaskRecord (.format "f{}" i) "digest" (get PROGRAMS (% i 3)) "rev" #() #() 15000 (+ NOW 20000) 0
                                :detached True :phase "finished" :finished-ms (- NOW 1000) :retain-ms 3600000)))
    (val tasks (task-table-of (tuple (gfor t held (TableWrite t.id t)))))
    (val programs (table-of #((TableWrite SAMPLE-TASK-PROGRAM (ProgramRow :blob "x" :versions {} :put-ms 0))
                              (TableWrite "gone" (ProgramRow :blob "x" :versions {} :put-ms 0)))))
    (val state (ClusterState :tasks tasks :programs programs))
    ;; 索引は最初の読みで 1 度だけ全部の行から作る(読み直しの後と同じ)— 数えるのはその後の読み。
    (task-index tasks)
    (.clear reads)
    (val refs (program-refs state))
    (<- due (sweep-due state NOW T))
    (assert (= (len reads) 0) #(count (len reads)))
    (assert (= refs (frozenset (gfor t held :if t.program t.program))) refs)
    ;; 参照の無い Program "gone" の猶予は過ぎている — 掃除の期限は今。
    (assert (= (. (type due) __name__) "DueNow") due)))
