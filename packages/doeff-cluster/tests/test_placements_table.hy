;;; placements と surges の書き手が「変わった job だけの書き」であることの検(ki-6cc74eb8d95d — T5a)。
;;;
;;; 時間では測らない。見る物は 2 つ: 書きの列(place-jobs の答え)に載る job の鍵と、表の同一性(is)。
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import dataclasses [replace])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterJob WorkerInfo Placement ClusterState KeepMark Drain])
(import doeff_cluster.coordinator.core.cluster_policy [place-jobs jobs-placed reconcile unplaced-jobs])
(import doeff_cluster.coordinator.core.drain_policy [advance-drains])
(import doeff_cluster.coordinator.protocol.durable_kv [full-kv state-from-kv])
(import doeff_cluster.coordinator.protocol.state_json [state-to-json state-from-json])
(import doeff_hy._table [TableWrite table-of])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))


(defn #^ ClusterJob job-of [#^ str name [needs #()] [handoff False]]
  (ClusterJob (JobSpec name "m" #() "rev" :handoff handoff) :needs needs))

(defn #^ WorkerInfo worker-of [#^ str name #^ int seen [provides #()]]
  (WorkerInfo name provides 10 seen :task-reserve 0))

(defn rows-table [#^ dict rows]
  (table-of (tuple (gfor #(key row) (.items rows) (TableWrite key row)))))

(defn #^ ClusterState state-of [jobs workers placements #** kwargs]
  (ClusterState (tuple jobs) (rows-table workers) (rows-table placements) #** kwargs))


(deftest test-place-jobs-answers-an-empty-write-list_and-the-table-stays-the-same-object_when-nothing-changes
  (val a (Placement "a" "w1" 1 0))
  (val state (state-of [(job-of "a")] {"w1" (worker-of "w1" 1000)} {"a" a}))
  (<- writes tuple (place-jobs 1000 state T))
  (assert (= writes #()))
  (<- table (jobs-placed 1000 state T))
  (assert (is table state.placements)))

(deftest test-place-jobs-writes-only-the-job-that-gets-a-place_and-keeps-the-other-row-as-the-same-object
  (val a (Placement "a" "w1" 1 0))
  (val state (state-of [(job-of "a") (job-of "b")] {"w1" (worker-of "w1" 1000)} {"a" a}))
  (<- writes tuple (place-jobs 1000 state T))
  (assert (= (tuple (gfor w writes w.key)) #("b")) writes)
  (<- table (jobs-placed 1000 state T))
  (assert (is (.row table "a") a))
  (assert (= (. (.row table "b") worker) "w1")))

(deftest test-place-jobs-writes-a-none-for-a-job-that-left-the-declaration
  (val state (state-of [(job-of "a")] {"w1" (worker-of "w1" 1000)}
                       {"a" (Placement "a" "w1" 1 0) "gone" (Placement "gone" "w1" 1 0)}))
  (<- writes tuple (place-jobs 1000 state T))
  (assert (= (tuple (gfor w writes #(w.key w.value))) #(#("gone" None))) writes))

(deftest test-a-silent-worker-keeps-its-job-until-reassign-after-ms_then-one-write-moves-it
  ;; 失敗ケース: 沈黙した worker の置き先を期限の前に外すと、戻ってきた担い手の返事から job が消えて止まる。
  (val a (Placement "a" "silent" 1 0))
  (val state (state-of [(job-of "a")] {"silent" (worker-of "silent" 0) "other" (worker-of "other" 40000)} {"a" a}))
  (<- before tuple (place-jobs T.reassign-after-ms state T))
  (assert (= before #()))
  (<- after tuple (place-jobs (+ T.reassign-after-ms 1) state T))
  (assert (= (tuple (gfor w after #(w.key w.value.worker w.value.generation))) #(#("a" "other" 2))) after))

(deftest test-a-keep-mark-promise-keeps-the-placement-past-reassign-after-ms
  (val a (Placement "a" "silent" 1 0))
  (val state (state-of [(job-of "a")] {"silent" (worker-of "silent" 0) "other" (worker-of "other" 100000)} {"a" a}
                       :keep-marks #((KeepMark :job "a" :worker "silent" :boot None :since-ms 0))))
  (<- writes tuple (place-jobs (+ T.reassign-after-ms 5000) state T))
  (assert (= writes #())))

(deftest test-a-job_no-worker-can-take_gets-no-write-and-says-why
  (val state (state-of [(job-of "k" :needs #("gpu"))] {"w1" (worker-of "w1" 1000)} {}))
  (<- writes tuple (place-jobs 1000 state T))
  (assert (= writes #()))
  (assert (in "置ける worker が無い" (get (unplaced-jobs 1000 state T) "k"))))

(deftest test-reconcile-carries-the-placements-table-over_and-a-second-beat-changes-nothing
  (val state (state-of [(job-of "a")] {"w1" (worker-of "w1" 1000)} {}))
  (<- placed ClusterState (reconcile 1000 state T))
  (assert (= (. (.row placed.placements "a") worker) "w1"))
  (assert (= (len placed.events) 1))
  (<- steady ClusterState (reconcile 1000 placed T))
  (assert (is steady placed)))

(deftest test-advance-drains-writes-a-surge-row-only_and-leaves-the-placements-table-alone
  ;; 失敗ケース: drain 中の atlas の上の入れ替えの job w を zeus へ並べる。置き先の表は同じ object、並べ直しても書きは増えない。
  (val state (state-of [(job-of "w" :handoff True)]
                       {"atlas" (worker-of "atlas" 1000) "zeus" (worker-of "zeus" 1000)}
                       {"w" (Placement "w" "atlas" 1 0)}
                       :drains (! (table-from-rows {"atlas" (Drain "atlas" 0 600000)}))))
  (val stepped (advance-drains 1000 state T))
  (assert (= (tuple (.keys stepped.surges)) #("w")))
  (assert (= (. (.row stepped.surges "w") worker) "zeus"))
  (assert (is stepped.placements state.placements))
  (val again (advance-drains 1000 stepped T))
  (assert (is again.surges stepped.surges))
  (assert (is again.placements stepped.placements)))

(deftest test-advance-drains-drops-only-the-surge-that-stopped-holding
  ;; drain が解けたら並べた置き先を外す(その行だけ)。置き先の表は同じ object。
  (val surge (Placement "w" "zeus" 2 0))
  (val state (state-of [(job-of "w" :handoff True)]
                       {"atlas" (worker-of "atlas" 1000) "zeus" (worker-of "zeus" 1000)}
                       {"w" (Placement "w" "atlas" 1 0)}
                       :surges (! (table-from-rows {"w" surge}))))
  (val stepped (advance-drains 1000 state T))
  (assert (= (.size stepped.surges) 0))
  (assert (is stepped.placements state.placements)))

(deftest test-the-saved-keys-and-values-do-not-change
  ;; 保存の鍵 placement/<job>・surge/<job> と値、GET /state の placements・surges の JSON は Table にしても同じ。
  (val a (Placement "a" "w1" 3 7))
  (val s (Placement "a" "w2" 4 8))
  (val state (state-of [(job-of "a")] {"w1" (worker-of "w1" 1000) "w2" (worker-of "w2" 1000)} {"a" a}
                       :surges (! (table-from-rows {"a" s}))))
  (<- kv dict (full-kv state))
  (assert (= (get kv "placement/a") {"job" "a" "worker" "w1" "generation" 3 "since_ms" 7}) (get kv "placement/a"))
  (assert (= (get kv "surge/a") {"job" "a" "worker" "w2" "generation" 4 "since_ms" 8}) (get kv "surge/a"))
  (<- back ClusterState (state-from-kv kv 1000))
  (assert (= (.row back.placements "a") a))
  (assert (= (.row back.surges "a") s))
  (<- data dict (state-to-json state))
  (assert (= (get data "placements") {"a" {"job" "a" "worker" "w1" "generation" 3 "since_ms" 7}}))
  (assert (= (get data "surges") {"a" {"job" "a" "worker" "w2" "generation" 4 "since_ms" 8}}))
  (<- from-file ClusterState (state-from-json data 1000))
  (assert (= (.row from-file.placements "a") a))
  (assert (= (.row from-file.surges "a") s)))
