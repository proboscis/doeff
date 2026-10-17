;;; tasks の書き手が「変わった行だけの書き」であることの検(ki-c9fb4d5d2408 — T3b)。
;;;
;;; 時間では測らない。数える物は 3 つ: TaskRecord の replace の呼び数・書きの列の長さ(変わった行の数)・行の同一性(is)。
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import hy)
(import pathlib [Path])
(import dataclasses [replace :as dc-replace])
(import unittest.mock [patch])
(import contextlib [contextmanager])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState TaskRecord WorkerInfo ComponentVersion])
(import doeff_cluster.coordinator.intent.request_bodies [StatusRow])
(import doeff_cluster.coordinator.core [cluster_policy])
(import doeff_cluster.coordinator.core.cluster_policy [place-tasks tasks-placed heard-tasks])
(import doeff_hy.table [TableWrite draft-of])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])
(import tests.state_rows [table-from-rows])

(setv T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(setv NOW 1000000)
(setv CORE-DIR (. (Path cluster_policy.__file__) parent))

(defn #^ TaskRecord row [#^ str id #^ str phase #^ (| str None) worker #^ bool [detached True] #^ int [priority 0]]
  "task 1 本の行(lease の期限は十分先)— 優先度 priority で置く順を試す。"
  (TaskRecord id "" SAMPLE-TASK-PROGRAM "" #() #() 60000 (+ NOW 600000) 0
              :phase phase :worker worker :detached detached :priority priority))

(defn #^ WorkerInfo live-worker [#^ str name [capacity 10]]
  (WorkerInfo name #() capacity NOW :task-reserve 0))

(defk state-of [rows workers]
  {:pre [(: rows dict) (: workers dict)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "task の行 rows と worker workers を持つ状態を作るため。"
  (<- tasks (table-from-rows rows))
  (<- worker-rows (table-from-rows workers))
  (ClusterState :workers worker-rows :tasks tasks))

(defn [contextmanager] counting-replaces []
  "with の間に cluster_policy が呼んだ replace のうち、TaskRecord を書き直した呼びの数(答えの list の 0 番)を数える。"
  (setv count [0])
  (defn counting [obj #* args #** kwargs]
    (when (isinstance obj TaskRecord) (+= (get count 0) 1))
    (dc-replace obj #* args #** kwargs))
  (with [(patch.object cluster_policy "replace" counting)]
    (yield count)))

(defn #^ dict four-hundred [#^ str worker #^ int n-mine]
  "400 本の切り離した置いた task。先頭 n-mine 本が worker の担当、ほかは別の worker o の担当。"
  (dfor i (range 400)
        :setv id (.format "t{:03d}" i)
        id (row id "assigned" (if (< i n-mine) worker "o"))))

(defn [contextmanager] counting-drafts []
  "with の間に cluster_policy が下書きを作った回数(答えの list の 0 番)を数える。"
  (setv count [0])
  (defn counting [table]
    (+= (get count 0) 1)
    (draft-of table))
  (with [(patch.object cluster_policy "draft_of" counting)]
    (yield count)))


;; --- register-heartbeat の 3 周(heard-tasks)---------------------------------------------------------------------------

(deftest test-a-heartbeat-that-changes-nothing-returns-the-same-table-and-replaces-no-row
  (<- state ClusterState (state-of (four-hundred "w" 0) {"w" (live-worker "w")}))
  (with [replaces (counting-replaces)]
    (setv answer (heard-tasks state "w" #() NOW None False None)))
  (assert (is answer state.tasks))
  (assert (= (get replaces 0) 0)))

(deftest test-the-three-passes-share-one-draft_and-write-only-the-changed-rows
  ;; w の担当は 3 本(lease の延長で 3 行)。400 本の表で、replace も変わる行も 3 に比例し、ほかの行は元の object のまま。
  (<- state ClusterState (state-of (four-hundred "w" 3) {"w" (live-worker "w")}))
  (with [replaces (counting-replaces) drafts (counting-drafts)]
    (setv answer (heard-tasks state "w" #() NOW None False None)))
  (assert (= (get drafts 0) 1))
  (assert (= (get replaces 0) 3))
  (assert (= (.changed-keys state.tasks answer) #("t000" "t001" "t002")))
  (assert (all (gfor id ["t003" "t200" "t399"] (is (.row answer id) (.row state.tasks id)))))
  (assert (= (. (.row answer "t000") lease-until-ms) (+ NOW 60000))))

(deftest test-one-pass-over-reports_renewals-and-promotions-writes-each-row-once
  ;; 報告で終わる 1 本・lease を延ばす 1 本 — 下書き 1 つで 2 行、400 本の他の行は同じ object(準備済みの進めは test_env_warm が読む)。
  (val rows (four-hundred "w" 0))
  (setv (get rows "t010") (dc-replace (get rows "t010") :worker "w" :detached False))
  (setv (get rows "t020") (dc-replace (get rows "t020") :worker "w"))
  (<- state ClusterState (state-of rows {"w" (live-worker "w")}))
  (val report (StatusRow :name "task/t010" :phase "finished" :detail "終わった" :result "ok"))
  (with [drafts (counting-drafts)]
    (setv answer (heard-tasks state "w" #(report) NOW None False (live-worker "w"))))
  (assert (= (get drafts 0) 1))
  (assert (= (.changed-keys state.tasks answer) #("t010" "t020")))
  (assert (= (. (.row answer "t010") phase) "finished"))
  (assert (is (.row answer "t399") (.row state.tasks "t399"))))


;; --- place-tasks は書きの列を返す --------------------------------------------------------------------------------------------

(deftest test-place-tasks-answers-an-empty-write-list-when-nothing-changes
  (<- state ClusterState (state-of (four-hundred "w" 400) {"w" (live-worker "w")}))
  (with [replaces (counting-replaces)]
    (<- writes tuple (place-tasks NOW state {} T)))
  (assert (= writes #()))
  (assert (= (get replaces 0) 0)))

(deftest test-place-tasks-writes-only-the-placed-rows_in-priority-then-id-order
  ;; 空きは 1 つ: 優先の大きい順、同じ優先の中は id の順で置く。置かれた 1 本だけが書きになる。
  (val rows (dict (+ (list (.items (four-hundred "w" 0)))
                     [#("q-a" (row "q-a" "queued" None :priority 0))
                      #("q-b" (row "q-b" "queued" None :priority 5))
                      #("q-c" (row "q-c" "queued" None :priority 5))])))
  (<- state ClusterState (state-of rows {"w" (live-worker "w" 1) "o" (live-worker "o" 0)}))
  (<- writes tuple (place-tasks NOW state {} T))
  ;; 置かれるのは q-b だけ。q-a・q-c は待つ理由の文が書きになる(まだ queued)。
  (val by-key (dfor w writes w.key w.value))
  (assert (= (sorted by-key) ["q-a" "q-b" "q-c"]))
  (assert (isinstance (get writes 0) TableWrite))
  (assert (= (. (get by-key "q-b") worker) "w"))
  (assert (= #((. (get by-key "q-a") phase) (. (get by-key "q-c") phase)) #("queued" "queued")))
  (<- roomy ClusterState (state-of rows {"w" (live-worker "w" 2) "o" (live-worker "o" 0)}))
  (<- both tuple (place-tasks NOW roomy {} T))
  (val placed (dfor w both :if (= w.value.phase "assigned") w.key w.value))
  (assert (= (sorted placed) ["q-b" "q-c"])))

(deftest test-place-tasks-drops-lapsed-and-expired-rows-by-a-none-write_and-is-idempotent
  ;; lease が切れた切り離していない行と、保持期限を過ぎた終わりの行は None の書き。書きを当てた表にもう 1 度かけると空。
  (val gone (dc-replace (row "gone" "assigned" "w" :detached False) :lease-until-ms (- NOW 1)))
  (val held (TaskRecord "held" "" SAMPLE-TASK-PROGRAM "" #() #() 60000 60000 0
                        :phase "finished" :detached True :finished-ms (- NOW 5000) :retain-ms 1000))
  (val fresh (TaskRecord "fresh" "" SAMPLE-TASK-PROGRAM "" #() #() 60000 60000 0
                         :phase "finished" :detached True :finished-ms NOW :retain-ms 1000))
  (<- state ClusterState (state-of {"gone" gone "held" held "fresh" fresh "keep" (row "keep" "assigned" "w")} {"w" (live-worker "w")}))
  (<- writes tuple (place-tasks NOW state {} T))
  (assert (= (sorted (gfor w writes #(w.key w.value))) [#("gone" None) #("held" None)]))
  (val after (.with-writes state.tasks writes))
  (assert (= (sorted (.keys after)) ["fresh" "keep"]))
  (assert (is (.row after "fresh") fresh))
  (<- blank ClusterState (state-of {} {}))
  (<- empty tuple (place-tasks NOW blank {} T))
  (<- again tuple (place-tasks NOW (replace-tasks state after) {} T))
  (assert (= empty #()))
  (assert (= again #())))

(defn #^ ClusterState replace-tasks [#^ ClusterState state #^ object tasks]
  (dc-replace state :tasks tasks))

(deftest test-a-sunk-detached-placed-row-ends-lost-on-lease-lapse
  (val lapsed (dc-replace (row "d1" "assigned" "w") :lease-until-ms (- NOW 1)))
  (<- state ClusterState (state-of {"d1" lapsed} {"w" (live-worker "w")}))
  (<- writes tuple (place-tasks NOW state {} T))
  (assert (= (tuple (gfor w writes w.key)) #("d1")))
  (assert (= (. (get writes 0) value phase) "lost")))

(deftest test-tasks-placed-applies-the-writes-to-state-tasks
  (<- state ClusterState (state-of {"q" (row "q" "queued" None)} {"w" (live-worker "w")}))
  (<- table (tasks-placed NOW state {} T))
  (assert (= (. (.row table "q") worker) "w"))
  (<- settled ClusterState (state-of {"a" (row "a" "assigned" "w")} {"w" (live-worker "w")}))
  (<- unchanged (tasks-placed NOW settled {} T))
  (assert (is unchanged settled.tasks)))


;; --- 下書きは `<-` をまたがない -----------------------------------------------------------------------------------------------

(defn #^ list flatten-form [#^ object form]
  (if (isinstance form hy.models.Sequence)
      (sum (lfor f form (flatten-form f)) [])
      [form]))

(defn #^ list effect-binding-drafting-forms [#^ str source]
  "source の最上位の形のうち draft-of を含み、`<-`・`!`・yield も含む形の名の列。"
  (lfor form (hy.read-many source)
        :setv symbols (set (gfor node (flatten-form form) :if (isinstance node hy.models.Symbol) (str node)))
        :if (and (in "draft-of" symbols) (& symbols #{"<-" "!" "yield"}))
        (str (get form 1))))

(deftest test-no-drafting-form-in-the-policy-files-binds-an-effect
  ;; 下書きを作って freeze する間に `<-`(別の task が状態を置き換えられる所)を置かない — 下書きは 1 つの純粋な判断の中だけ。
  (for [name ["cluster_policy.hy" "api_policy.hy" "resource_policy.hy" "detached_policy.hy"]]
    (assert (= (effect-binding-drafting-forms (.read-text (/ CORE-DIR name))) []) name)))

(deftest test-the-detector-sees-a-bound-draft
  (assert (= (effect-binding-drafting-forms "(defk bad [t] (setv d (draft-of t)) (<- x (f)) (.freeze d))") ["bad"]))
  (assert (= (effect-binding-drafting-forms "(defn good [t] (setv d (draft-of t)) (.freeze d))") [])))
