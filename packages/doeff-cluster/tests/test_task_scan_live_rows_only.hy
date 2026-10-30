;;; 終わった task(結果を保持しているだけの切り離した終わりの行)が、tick の place-tasks と next-wake の task-due の走査から外れている事の検
;;; (ki-7a6a60dc516e)。
;;;
;;; 時間では測らず、1 拍で期限の判断(task-lapse-at)が呼ばれた行の顔ぶれで見る。place-tasks と task-due の答えは、終わった行を回る版と
;;; 回らない版で同じになるので、答えの比べでは「走査に戻った」退行を捕まえられない — 判断が走った行を見る。
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import hy)
(import pytest)
(import unittest.mock [Mock])
(import dataclasses [replace])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [ClusterState WorkerInfo]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord])
(import doeff_cluster._coordinator.core [cluster_policy :as cluster-policy])
(import doeff_cluster [task-due]
        doeff_cluster._coordinator.core.cluster_policy [tasks-placed])
(import doeff_cluster._shared.intent.due_model [DueAt])
(import doeff_hy._table [table-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
;; 走査の検に使う行の lease が切れる刻(切れの判断は、この次の ms から)。
(val LAPSE-ATTACHED 5000)


(defk task-row [id phase [detached False] [finished None] [retain 100000] [worker ""] [lease-until LAPSE-ATTACHED]]
  {:pre [(: id str) (: phase str) (: detached bool) (: finished (| int None)) (: retain int) (: worker str) (: lease-until int)]
   :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "走査の検に使う task の行を作るため(lease が lease-until に切れる・切り離すか・終わった刻 finished を持つか・担い手 worker)。"
  (TaskRecord id "" SAMPLE-TASK-PROGRAM "" #() #() 60000 lease-until 0
              :phase phase :detached detached :finished-ms finished :retain-ms retain :worker worker))

(defk held-rows [count]
  {:pre [(: count int)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "結果を保持しているだけの行(切り離した終わり)count 本を、鍵 → 行の dict で作るため。"
  (val rows {})
  (for [i (range count)]
    (val id (+ "h" (str i)))
    (setv (get rows id) (! (task-row id "finished" :detached True :finished 100))))
  rows)

(defk live-rows []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "走査の対象のまま残る 3 種の行 — 待っている行 q・切り離した置いた行 a・切り離していない終わった行 u(呼び手の lease が生きている間は保つ)。"
  {"q" (! (task-row "q" "queued"))
   "a" (! (task-row "a" "assigned" :detached True :worker "w"))
   "u" (! (task-row "u" "finished" :finished 100))})

(defk state-of [tasks]
  {:pre [(: tasks dict)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "tasks と worker w だけを持つ状態を作るため。"
  (ClusterState :workers (! (table-from-rows {"w" (WorkerInfo "w" #() 0 :task-reserve 0 :background-room 1)}))
                :tasks (! (table-from-rows tasks))))

(defk watch-lapse-decisions [monkeypatch]
  {:pre [(: monkeypatch pytest.MonkeyPatch)] :post [(: % Mock)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "期限の判断 task-lapse-at を、元の関数を呼びながら呼びを記録する Mock に差し替え、その Mock を返す(判断が走った行は decided-ids で読む)。"
  (val spy (Mock :wraps cluster-policy.task-lapse-at))
  (monkeypatch.setattr cluster-policy (hy.mangle "task-lapse-at") spy)
  spy)

(defk decided-ids [spy]
  {:pre [(: spy Mock)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "spy が記録した期限の判断の、行の id を呼ばれた順に返す。"
  (lfor call spy.call-args-list (. (get call.args 0) id)))


(deftest test-place-tasks-decides-only-the-scan-rows_not-the-held-ones [#^ pytest.MonkeyPatch monkeypatch]
  (val spy (! (watch-lapse-decisions monkeypatch)))
  (for [held-count [0 5 80]]
    (spy.reset-mock)
    (val tasks (| (! (held-rows held-count)) (! (live-rows))))
    (! (tasks-placed 1000 (! (state-of tasks)) (table-of #()) T))
    ;; 終わった行の数に依らず、走査の対象の 3 行だけに判断が走る。
    (assert (= (sorted (! (decided-ids spy))) ["a" "q" "u"]) #(held-count (! (decided-ids spy))))))

(deftest test-task-due-decides-only-the-scan-rows_not-the-held-ones [#^ pytest.MonkeyPatch monkeypatch]
  (val spy (! (watch-lapse-decisions monkeypatch)))
  (for [held-count [0 5 80]]
    (spy.reset-mock)
    (val tasks (| (! (held-rows held-count)) (! (live-rows))))
    (val due (! (task-due (! (state-of tasks)) 1000 T)))
    (assert (= (sorted (! (decided-ids spy))) ["a" "q" "u"]) #(held-count (! (decided-ids spy))))
    ;; 保持側の行は最小期限の 1 値で足りる: 走査の対象の期限(lease の切れの次の ms)が先で、保持側(100 + 100000 + 1)より早い。
    (assert (= due (DueAt :at (+ LAPSE-ATTACHED 1))) #(held-count due))))

(deftest test-a-task-is-scanned-until-the-beat-it-ends_and-never-again-after [#^ pytest.MonkeyPatch monkeypatch]
  ;; 切り離した置いた行 a は、lease が切れる拍(5001)で lost になって終わる。その拍までは走査の対象、次の拍からは走査に戻らない。
  (val spy (! (watch-lapse-decisions monkeypatch)))
  (var state (! (state-of {"a" (! (task-row "a" "assigned" :detached True :worker "w"))})))
  (for [now [1000 LAPSE-ATTACHED]]                    ; lease の内 / lease-until ちょうど(切れは次の ms から)
    (spy.reset-mock)
    (:= state (replace state :tasks (! (tasks-placed now state (table-of #()) T))))
    (assert (= (! (decided-ids spy)) ["a"]) #(now (! (decided-ids spy))))
    (assert (= (. (.row state.tasks "a") phase) "assigned") now))
  (spy.reset-mock)
  (:= state (replace state :tasks (! (tasks-placed (+ LAPSE-ATTACHED 1) state (table-of #()) T))))
  (assert (= (! (decided-ids spy)) ["a"]) (! (decided-ids spy)))
  (val ended (.row state.tasks "a"))
  (assert (= ended.phase "lost") ended.phase)
  (assert (= ended.finished-ms (+ LAPSE-ATTACHED 1)) ended.finished-ms)
  ;; 終わった後: 何拍回しても判断は走らず、行は元の object のまま(書き直されない)。
  (for [now [(+ LAPSE-ATTACHED 2) 20000 90000]]
    (spy.reset-mock)
    (:= state (replace state :tasks (! (tasks-placed now state (table-of #()) T))))
    (assert (= (! (decided-ids spy)) []) #(now (! (decided-ids spy))))
    (assert (is (.row state.tasks "a") ended) now))
  ;; 保持の期限(終わった刻 + retain-ms + 1)で行は消える。消える拍も判断は走らない。
  (spy.reset-mock)
  (:= state (replace state :tasks (! (tasks-placed (+ LAPSE-ATTACHED 1 100000 1) state (table-of #()) T))))
  (assert (= (! (decided-ids spy)) []) (! (decided-ids spy)))
  (assert (= (.size state.tasks) 0)))

(deftest test-the-scan-rows-keep-every-decision_with-many-held-rows-beside-them
  ;; 終わっていない行は今どおり判断される: 期限の切れた待つ行・終わった行は落ち、置いた切り離した行は lost、lease の内で担い手の居ない置いた行 p は失敗になる。
  ;; 結果を保持している 50 行は何も書かれない。
  (val held (! (held-rows 50)))
  (val tasks (| held
                {"q" (! (task-row "q" "queued"))
                 "u" (! (task-row "u" "finished" :finished 100))
                 "a" (! (task-row "a" "assigned" :detached True :worker "w"))
                 "p" (! (task-row "p" "assigned" :worker "ghost" :lease-until 9000))}))
  (val state (! (state-of tasks)))
  (val after (! (tasks-placed (+ LAPSE-ATTACHED 1) state (table-of #()) T)))
  (assert (is (.row after "q") None) "lease の切れた待つ行が残った")
  (assert (is (.row after "u") None) "lease の切れた終わった行が残った")
  (assert (= (. (.row after "a") phase) "lost"))
  (assert (= (. (.row after "p") phase) "failed"))
  (for [id held]
    (assert (is (.row after id) (.row state.tasks id)) id)))
