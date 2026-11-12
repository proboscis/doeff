;;; 置けないと判じた待っている task と、置き方の入力の同じ後ろの task を、置き先の数え(負荷・組の升・取っておき)が変わらない間は
;;; place-tasks が数え直さない事の検(2026-11-12)。
;;;
;;; 実例: どの worker にも置けない task が数百本溜まり、要求ごとの調停の place-tasks が待っている task の数 × worker の数だけ要り・空き・
;;; 置けない理由を数え直して、coordinator の主 thread を埋めた(返事の遅れが worker の拍と task の送りを遅くした)。時間では測らず、1 拍で
;;; 置けない理由の判断(task-misfit)が呼ばれた回数と、置いた task・待ちの文の答えで見る。
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import hy)
(import pytest)
(import unittest.mock [Mock])
(import doeff_hy._table [Table TableWrite table-of])
(import doeff_cluster [ClusterTiming ClusterState WorkerInfo TaskLimit GroupLimit])
(import doeff_cluster._coordinator.intent.cluster_model [TaskRecord ComponentVersion TaskUnplacedKind])
(import doeff_cluster._coordinator.core [cluster_policy :as cluster-policy])
(import doeff_cluster._coordinator.core.cluster_policy [tasks-placed task-unplaced-text])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.room_rows [ROOM-LOADS])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val PY3 #((ComponentVersion "python" "3")))
;; 組 one は worker ごとに 1 本(scope worker)。
(val GROUPS (table-of #((TableWrite "one" (GroupLimit :group "one" :scope "worker" :limit 1)))))
;; 待っている task の数(置き方の入力が同じ)。
(val QUEUED 40)


(defk workers-of [count]
  {:pre [(: count int)] :post [(: % Table)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "task の空き 4 本ずつを測った worker w0 … w{count-1}(最後の連絡 0・task と同じ版)の表を作るため。"
  (! (table-from-rows (dfor i (range count) :setv name (.format "w{}" i)
                            name (WorkerInfo name #() 0 :measured (get ROOM-LOADS 4) :versions PY3)))))

(defk waiting-task [i * [memory None] [limits #()]]
  {:pre [(: i int) (: memory (| int None)) (: limits tuple)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "i 番目の待っている切り離した task(memory の要り memory・名乗る組 limits — id と key と積んだ刻だけが task ごとに違う)を作るため。"
  (TaskRecord (.format "t{:03d}" i) "digest" SAMPLE-TASK-PROGRAM "rev" PY3 #() 15000 20000 i :detached True
              :key (.format "k{}" i) :memory-mib memory :limits limits))

(defk waiting-tasks [ids * [memory None] [limits #()]]
  {:pre [(: ids range) (: memory (| int None)) (: limits tuple)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "番号 ids の待っている task(置き方の入力が同じ — waiting-task)の列を作るため。"
  (val rows [])
  (for [i ids]
    (.append rows (! (waiting-task i :memory memory :limits limits))))
  rows)

(defk state-of [workers tasks]
  {:pre [(: workers Table) (: tasks list)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker の表 workers と task の列 tasks を持ち、組 one を宣言した状態を作るため。"
  (ClusterState #() workers (table-of #()) (table-of (tuple (gfor t tasks (TableWrite t.id t)))) :group-limits GROUPS))

(defk watch-misfits [monkeypatch]
  {:pre [(: monkeypatch pytest.MonkeyPatch)] :post [(: % Mock)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "置けない理由の判断 task-misfit を、元の関数を呼びながら呼びを記録する Mock に差し替え、その Mock を返すため。"
  (val spy (Mock :wraps cluster-policy.task-misfit))
  (monkeypatch.setattr cluster-policy (hy.mangle "task-misfit") spy)
  spy)

(defk phase-count [tasks phase]
  {:pre [(: tasks Table) (: phase str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "表 tasks の phase が phase の行の数を読むため。"
  (len (lfor t (.rows tasks) :if (= t.phase phase) t)))


(deftest test-a-queue-that-fits-nowhere-is-judged-once-per-worker_not-once-per-task [#^ pytest.MonkeyPatch monkeypatch]
  ;; 失敗ケース(直す前は task ごとに worker の数だけ数え直した — 40 本 × 3 台 = 120 回): 要りがどの worker の memory の空きにも入らない
  ;; 同じ形の task 40 本。最初の 1 本だけが worker ごとに判じられ、後ろの 39 本は同じ理由の文で待つ。
  (val spy (! (watch-misfits monkeypatch)))
  (val tasks (! (waiting-tasks (range QUEUED) :memory 10000000)))
  (val placed (! (tasks-placed 1000 (! (state-of (! (workers-of 3)) tasks)) (table-of #()) T)))
  (assert (= spy.call-count 3) spy.call-count)
  (val text (! (task-unplaced-text TaskUnplacedKind.TASK-NO-MEMORY)))
  (assert (all (gfor t (.rows placed) (and (= t.phase "queued") (in text t.detail))))
          (lfor t (.rows placed) #(t.id t.phase t.detail))))

(deftest test-each-placement-makes-the-next-task-judged-again [#^ pytest.MonkeyPatch monkeypatch]
  ;; 置くたびに負荷と組の升が変わるので、置けないと判じた答えは借りない: 組 one(worker ごとに 1 本)を名乗る同じ形の task 40 本を 3 台へ
  ;; 置くと、ちょうど 3 本が別々の worker に置かれ、残る 37 本は組の上限の文で待つ(借りた答えで置き過ぎも置き損ねもしない)。
  ;; 判断の回数は、置いた 3 本(置くたびに数え直す)と、3 台が埋まった後の最初の 1 本 × 3 台に収まる(task ごとには数えない)。
  (val spy (! (watch-misfits monkeypatch)))
  (val tasks (! (waiting-tasks (range QUEUED) :limits #((TaskLimit :group "one")))))
  (val placed (! (tasks-placed 1000 (! (state-of (! (workers-of 3)) tasks)) (table-of #()) T)))
  (assert (= (! (phase-count placed "assigned")) 3))
  (assert (= (len (sfor t (.rows placed) :if (= t.phase "assigned") t.worker)) 3))
  (assert (= (! (phase-count placed "queued")) (- QUEUED 3)))
  (val text (! (task-unplaced-text TaskUnplacedKind.TASK-LIMITED)))
  (assert (all (gfor t (.rows placed) :if (= t.phase "queued") (in text t.detail))))
  (assert (<= spy.call-count (* 4 3)) spy.call-count))

(deftest test-a-task-with-other-placement-inputs-is-judged-on-its-own [#^ pytest.MonkeyPatch monkeypatch]
  ;; 置き方の入力の違う task は答えを借りない: 要りの入らない task 20 本の後ろに、要りの入る task(memory の要りが小さい)1 本を積むと、
  ;; その 1 本は置かれる(入らない task の答えを借りて待たない)。
  (val spy (! (watch-misfits monkeypatch)))
  (val tasks (+ (! (waiting-tasks (range 20) :memory 10000000)) (! (waiting-tasks (range 20 21) :memory 256))))
  (val placed (! (tasks-placed 1000 (! (state-of (! (workers-of 2)) tasks)) (table-of #()) T)))
  (assert (= (. (.row placed "t020") phase) "assigned"))
  (assert (= (! (phase-count placed "queued")) 20))
  (assert (<= spy.call-count 4) spy.call-count))
