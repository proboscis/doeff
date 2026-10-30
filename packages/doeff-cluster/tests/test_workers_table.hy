;;; workers の書き手が「変わった行だけの書き」であることの検(ki-c0af02718092 — T4)。
;;;
;;; 時間では測らない。見る物は 2 つ: 表の同一性(is)と、変わった行の鍵(changed-keys)。
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import dataclasses [replace])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster [ClusterState WorkerInfo]
        doeff_cluster._coordinator.intent.cluster_model [NodeLabelsSeen ClusterObservations])
(import doeff_cluster._coordinator.core.api_policy [stamp-alive])
(import doeff_cluster._coordinator.core.cluster_policy [forget-silent-workers with-derived-capabilities])
(import doeff_hy._table [TableWrite table-of])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val NOW 1000000000)

(defn #^ WorkerInfo worker [#^ str name #^ int seen [seen-mark None] [node ""]]
  "最後の連絡が seen・印が seen-mark の worker 1 台。"
  (WorkerInfo name #() seen :task-reserve 0 :seen-mark seen-mark :node node :background-room 10))

(defn table-of-workers [#* workers]
  (table-of (tuple (gfor w workers (TableWrite w.name w)))))


(deftest test-stamp-alive-returns-the-same-table-when-every-mark-matches-the-last-contact
  (val state (ClusterState :workers (table-of-workers (worker "a" 5 5) (worker "b" 7 7))))
  (<- stamped ClusterState (stamp-alive state NOW))
  (assert (is stamped.workers state.workers))
  (assert (= stamped.alive-ms NOW)))

(deftest test-stamp-alive-rewrites-only-the-rows-whose-mark-lags
  (val state (ClusterState :workers (table-of-workers (worker "a" 5 5) (worker "b" 9 7) (worker "c" 3 None))))
  (<- stamped ClusterState (stamp-alive state NOW))
  (assert (= (.changed-keys state.workers stamped.workers) #("b" "c")))
  (assert (is (.row stamped.workers "a") (.row state.workers "a")))
  (assert (= (. (.row stamped.workers "b") seen-mark) 9))
  (assert (= (. (.row stamped.workers "c") seen-mark) 3)))

(deftest test-forgetting-no-one-returns-the-same-state_and-forgetting-a-long-silent-worker-drops-only-its-row
  (val fresh (worker "fresh" NOW NOW))
  (val stale (worker "stale" 0 0))
  (val state (ClusterState :workers (table-of-workers fresh stale)))
  (<- kept ClusterState (forget-silent-workers (replace state :workers (table-of-workers fresh)) NOW T))
  (assert (is (.row kept.workers "fresh") fresh))
  (<- after ClusterState (forget-silent-workers state NOW T))
  (assert (= (.keys after.workers) #("fresh")))
  (assert (is (.row after.workers "fresh") fresh)))

(deftest test-derived-capabilities-rewrite-only-the-workers-whose-capabilities-change
  (val table #(#("zone" "a" "gpu")))
  (val labeled (worker "labeled" NOW NOW :node "n1"))
  (val bare (worker "bare" NOW NOW))
  (val state (ClusterState :workers (table-of-workers labeled bare)
                           :observations (replace (. (ClusterState) observations)
                                                  :nodes (table-of #((TableWrite "n1" (NodeLabelsSeen :at NOW :labels (table-of #((TableWrite "zone" "a"))))))))))
  (val derived (with-derived-capabilities state table))
  (assert (= (.changed-keys state.workers derived.workers) #("labeled")))
  (assert (is (.row derived.workers "bare") bare))
  (assert (= (. (.row derived.workers "labeled") derived) #("gpu")))
  ;; 2 回目は何も変わらないので、状態そのものが返る。
  (val again (with-derived-capabilities derived table))
  (assert (is again derived)))
