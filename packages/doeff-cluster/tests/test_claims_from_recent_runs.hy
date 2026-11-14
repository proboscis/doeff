;;; 申告を近い回の測った山と形の族から出す(ADR-DOE-CLUSTER-005 R4 の改め 2026-11-15・law the-claim-follows-the-measured-peaks-and-stays —
;;; coordinator/intent/task_table の family-peaks-of・RECENT-PEAK-RUNS)。
;;;   (1) 族: 命令の中身だけが違う 2 本目(形は違う・族は同じ)は、1 本目の測った山から申告され、送り手の見当を読まない。
;;;   (2) 近い回: 形の山は近い RECENT-PEAK-RUNS 本の終わった行の山で、保持の間の古い外れ値の 1 本に縛られない。
;;;   (3) 止められた回: 要りの越えで止められた回(lost)は近い回に数え、次の置き直しはその山で申告される(同じ小さい申告を繰り返さない)。
;;;   (4) 族の行: 積んだ拍に書き、その形の行が無くなった歩で外し、state file と耐久の鍵を往復する。形の無い族は断る。
;;;   (5) GET /state の worker の行は、置いた task の申告の和 claimedMiB と測った使いの和 usedMiB を出す。
;;; 失敗ケース = 形が命令の全体の digest を含むので、ほとんどの回が初めての形として送り手の見当(中央の値 3,584 MiB)で申告された形と、
;;; 日次の回の申告が古い 1 本の山 20,804 MiB に保持の間ずっと縛られた形(近い回の山は 614〜7,025 MiB)。
(require doeff-hy.macros [defk deftest <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import math [ceil])
(import doeff_cluster [ClusterState ClusterTiming WorkerInfo]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord ShapeFamily ComponentVersion])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad TaskBody])
(import doeff_cluster._coordinator.intent.task_table [RECENT-PEAK-RUNS])
(import doeff_cluster._coordinator.core.task_claims [shape-peaks-of task-need-of families-noted])
(import doeff_cluster._coordinator.core.cluster_policy [state-view])
(import doeff_cluster._coordinator.core.detached_policy [submit-detached Reply])
(import doeff_cluster._coordinator.core.measured_room [SHAPE-PEAK-MARGIN PEAK-STEP-MIB])
(import doeff_cluster._coordinator.protocol.state_json [state-to-json state-from-json shape-family-from-json])
(import doeff_cluster._coordinator.protocol.durable_kv [full-kv state-from-kv])
(import doeff_cluster._coordinator.protocol.replies [state-view-json])
(import doeff_cluster._shared.core.detached_rules [task-family-refusal task-placing-json])
(import doeff_hy._table [Table TableWrite])
(import tests.program_rows [SAMPLE-TASK-PROGRAM program-placed task-body-of])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
(val FAMILY "repo-a:cargo test")
(val FIRST-SHAPE "repo-a:cargo test #1111aaaa2222bbbb")
(val SECOND-SHAPE "repo-a:cargo test #3333cccc4444dddd")
;; 送り手の見当(long-command の既定と同じ程)と、1 本目の測った山。
(val SENDER-MIB 6144)
(val FIRST-PEAK-MIB 873)


(defk learned-of [peak]
  {:pre [(: peak int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "終わりまで測った山 peak から数える申告(余裕と刻みを足す — measured_room の頭の註)を知るため。"
  (+ (ceil (* peak SHAPE-PEAK-MARGIN)) PEAK-STEP-MIB))


(defk task-of [id shape]
  {:pre [(: id str) (: shape str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "見当 SENDER-MIB と形 shape を名乗る、待っている切り離した task を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #("host-a") 15000 20000 0 :detached True :key id :memory-mib SENDER-MIB :shape shape))


(defk ended-of [id shape peak at phase]
  {:pre [(: id str) (: shape str) (: peak int) (: at int) (: phase str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "形 shape の、山 peak で刻 at に phase(finished か lost)で終わった行を作るため。"
  (<- row TaskRecord (task-of id shape))
  (replace row :phase phase :worker "a" :started-ms 0 :finished-ms at :peak-mib peak))


(defk runs-of [prefix shape peaks start]
  {:pre [(: prefix str) (: shape str) (: peaks tuple) (: start int)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "形 shape の、山 peaks の順に刻 start から 1 ずつ遅く finished で終わった行(id = prefix と番号)を、id → 行の dict にするため。"
  (var rows {})
  (for [#(i peak) (enumerate peaks)]
    (val id (.format "{}{}" prefix i))
    (<- row TaskRecord (ended-of id shape peak (+ start i) "finished"))
    (:= rows (| rows {id row})))
  rows)


(defk families-of [pairs]
  {:pre [(: pairs dict)] :post [(: % Table)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "形 → 族の写像 pairs から、形 → 族の表(ClusterState.shape-families の形)を作るため。"
  (<- table (table-from-rows (dfor #(shape family) (.items pairs) shape (ShapeFamily :shape shape :family family))))
  table)


(deftest test-a-second-command-of-the-same-family-is-declared-from-the-first-ones-peak
  ;; (1) 1 本目(形 FIRST-SHAPE)は山 873 MiB で終わった。命令の中身だけが違う 2 本目(形 SECOND-SHAPE — 初めての形)は族の山で申告され、
  ;; 送り手の見当 6,144 MiB を読まない。
  (<- first TaskRecord (ended-of "first" FIRST-SHAPE FIRST-PEAK-MIB 10 "finished"))
  (<- second TaskRecord (task-of "second" SECOND-SHAPE))
  (<- rows (table-from-rows {"first" first "second" second}))
  (<- families (families-of {FIRST-SHAPE FAMILY SECOND-SHAPE FAMILY}))
  (<- peaks dict (shape-peaks-of rows families))
  (<- need int (task-need-of second None peaks))
  (<- expected int (learned-of FIRST-PEAK-MIB))
  (assert (= need expected) #(need expected))
  ;; 族を名乗らない 2 本目(族の表に行が無い)は今までどおり見当のまま。族で補わない読み(families を渡さない)も同じ。
  (<- unfamilied dict (shape-peaks-of rows (! (families-of {FIRST-SHAPE FAMILY}))))
  (<- alone int (task-need-of second None unfamilied))
  (<- own-only int (task-need-of second None (! (shape-peaks-of rows))))
  (assert (= #(alone own-only) #(SENDER-MIB SENDER-MIB)) #(alone own-only))
  ;; 形そのものに finished の行が在れば、族の山より形の山が勝つ(族の大きい山に縛られない)。
  (<- big TaskRecord (ended-of "big" FIRST-SHAPE 9000 20 "finished"))
  (<- own TaskRecord (ended-of "own" SECOND-SHAPE 500 30 "finished"))
  (<- both (table-from-rows {"big" big "own" own "second" second}))
  (<- own-need int (task-need-of second None (! (shape-peaks-of both families))))
  (assert (= own-need (! (learned-of 500))) own-need))


(deftest test-the-shape-peak-is-the-recent-runs-not-the-retained-outlier
  ;; (2) 日次の回の形: 古い 1 本が 20,804 MiB、その後の近い回は 614〜7,025 MiB。近い RECENT-PEAK-RUNS 本の山の最大で申告される。
  (val shape "repo-b:daily-verify")
  (val recent-peaks #(614 3751 5610 7025 4200))
  (assert (= (len recent-peaks) RECENT-PEAK-RUNS))
  (val rows (| {"old" (! (ended-of "old" shape 20804 1 "finished"))}
               (! (runs-of "r" shape recent-peaks 100))
               {"next" (! (task-of "next" shape))}))
  (<- table (table-from-rows rows))
  (<- need int (task-need-of (.row table "next") None (! (shape-peaks-of table))))
  (<- expected int (learned-of (max recent-peaks)))
  (assert (= need expected) #(need expected))
  ;; まだ終わっていない同じ形の行の山は近い回に入る(伸びている今の回は要りの下限)。
  (<- running (table-from-rows (| rows {"run" (replace (! (task-of "run" shape)) :phase "assigned" :worker "a" :started-ms 0 :peak-mib 12000)})))
  (<- with-running int (task-need-of (.row running "next") None (! (shape-peaks-of running))))
  (assert (= with-running (! (learned-of 12000))) with-running))


(deftest test-a-run-stopped-over-its-claim-raises-the-next-claim
  ;; (3) 近い回は小さい山(800 MiB)で終わった形の回が、要りの越えで止められた(lost・止まった時の山 6,000 MiB)。次の置き直しの申告は
  ;; 止められた回の山から数え、同じ小さい申告で止めと置き直しを繰り返さない。
  (val shape "repo-c:pytest")
  (val rows (| (! (runs-of "f" shape (* #(800) (- RECENT-PEAK-RUNS 1)) 10))
               {"stopped" (! (ended-of "stopped" shape 6000 100 "lost"))
                "retry" (! (task-of "retry" shape))}))
  (<- table (table-from-rows rows))
  (<- need int (task-need-of (.row table "retry") None (! (shape-peaks-of table))))
  (assert (= need (! (learned-of 6000))) need)
  ;; 走らせ直しの次の試行が行の山を持っていれば(同じ task の前の試行の山 — R4 の own peak)、近い回の外でもその山を読む。
  (<- own (table-from-rows (| (! (runs-of "g" shape (* #(800) RECENT-PEAK-RUNS) 200))
                              {"again" (replace (! (task-of "again" shape)) :peak-mib 6000)})))
  (<- own-need int (task-need-of (.row own "again") None (! (shape-peaks-of own))))
  (assert (= own-need (! (learned-of 6000))) own-need))


(defk submitted [state body]
  {:pre [(: state ClusterState) (: body dict)] :post [(: % Reply)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "詰めた Program を置き場に置いた状態 state へ、本文 body を受け口と同じ解きで PUT /detached/<key> として送った答えを得るため。"
  (<- placed tuple (program-placed state {"python" "3"}))
  (setv #(placed-state sha) placed)
  (<- parsed TaskBody (task-body-of (| {"program" sha "revision" "rev" "name" "build" "needs" ["host-a"] "memoryMiB" SENDER-MIB} body)))
  (submit-detached placed-state (get body "key") parsed 0))


(deftest test-the-family-row-is-written-at-submit-dropped-with-the-shape-and-saved
  ;; (4) 族を名乗る task を積んだ拍に、形 → 族の行が書かれる。族は形と一緒にだけ名乗れる。
  (val base (ClusterState))
  (val reply (! (submitted base {"key" "k1" "shape" FIRST-SHAPE "family" FAMILY})))
  (assert (= reply.status 200) reply.body)
  (assert (= (.row reply.state.shape-families FIRST-SHAPE) (ShapeFamily :shape FIRST-SHAPE :family FAMILY)) reply.state.shape-families)
  (val refused (! (submitted base {"key" "k2" "family" FAMILY})))
  (assert (= refused.status 400) refused.body)
  (assert (in "family" refused.body.message) refused.body.message)
  (assert (and (task-family-refusal FAMILY "") (= (task-family-refusal FAMILY "") (task-family-refusal FAMILY None))) "形の無い族は同じ訳で断る")
  (assert (is (task-family-refusal None None) None))
  (assert (= (task-placing-json 0 #() 1024 FIRST-SHAPE FAMILY) {"memoryMiB" 1024 "shape" FIRST-SHAPE "family" FAMILY}))
  ;; state file と耐久の鍵を往復する。族の欄の無い旧い file は空の表で読む。壊れた保存の値は理由つきで断る。
  (val saved reply.state)
  (<- data dict (state-to-json saved))
  (<- from-file ClusterState (state-from-json data 0))
  (<- from-keys ClusterState (state-from-kv (! (full-kv saved)) 0))
  (assert (= (.row from-file.shape-families FIRST-SHAPE) (.row from-keys.shape-families FIRST-SHAPE) (ShapeFamily :shape FIRST-SHAPE :family FAMILY)))
  (<- old ClusterState (state-from-json (dfor #(k v) (.items data) :if (!= k "shapeFamilies") k v) 0))
  (assert (= (.size old.shape-families) 0) old.shape-families)
  (for [spec [{} {"family" ""} {"family" 3} "x"]]
    (try
      (shape-family-from-json FIRST-SHAPE spec)
      (raise (AssertionError (.format "壊れた保存の値を読んだ: {!r}" spec)))
      (except [error ValueError]
        (assert (in FIRST-SHAPE (str error)) (str error)))))
  ;; その形の task の行が 1 本も無くなった歩で族の行を外す(行が残る間は外さない)。
  (val id (. (get (.rows saved.tasks) 0) id))
  (<- kept ClusterState (families-noted saved (replace saved :tasks (.with-writes saved.tasks #((TableWrite "other" (! (task-of "other" SECOND-SHAPE))))))))
  (assert (is-not (.row kept.shape-families FIRST-SHAPE) None) kept.shape-families)
  (<- dropped ClusterState (families-noted saved (replace saved :tasks (.with-writes saved.tasks #((TableWrite id None))))))
  (assert (is (.row dropped.shape-families FIRST-SHAPE) None) dropped.shape-families))


(defk placed-of [id]
  {:pre [(: id str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker a に置いた、見当 2,048 MiB(形の山の無い形 — 申告は見当)の task を作るため。"
  (<- row TaskRecord (task-of id "repo-d:y"))
  (replace row :phase "assigned" :worker "a" :started-ms 0 :memory-mib 2048))


(deftest test-get-state-shows-the-claim-sum-beside-the-measured-use
  ;; (5) worker a に置いた 2 本の申告の和と、測りが名乗る同じ 2 本の今の使いの和(ほかの worker の task・終わった task は数えない)。
  (val measured (MeasuredLoad :at-ms 0 :cpus 8 :memory-total-MiB 64000 :memory-available-MiB 40000 :cpu-busy 0.1 :cpu-nice 0.0
                              :cpu-some-avg10 0.5 :memory-some-avg10 0.0 :task-memory-MiB {"p1" 300 "p2" 500 "gone" 900}))
  (<- workers (table-from-rows {"a" (WorkerInfo "a" #("host-a") 0 :versions PY3 :measured measured)
                                "b" (WorkerInfo "b" #("host-a") 0 :versions PY3)}))
  (<- tasks (table-from-rows {"p1" (! (placed-of "p1")) "p2" (! (placed-of "p2"))
                              "gone" (! (ended-of "gone" "repo-d:x" 900 5 "finished"))}))
  (<- none (table-from-rows {}))
  (val state (ClusterState #() workers none tasks))
  (<- view (state-view state 0 T))
  (<- body dict (state-view-json view))
  (val a (get (get body "workers") "a"))
  (val b (get (get body "workers") "b"))
  (assert (= #((get a "claimedMiB") (get a "usedMiB")) #(4096 800)) a)
  (assert (= (get b "claimedMiB") 0) b)
  (assert (not-in "usedMiB" b) b))
