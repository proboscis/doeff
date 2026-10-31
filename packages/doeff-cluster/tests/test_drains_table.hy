;;; drains の書き手が「変わった worker の行だけの書き」であることの検(T5b)。
;;;
;;; 時間では測らない。見る物は 2 つ: 残る行(鍵)と、書きの無い拍で表が元と同じ物(is)であること。
(require doeff-hy.macros [deftest val <- defk])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import doeff_cluster [ClusterState]
        doeff_cluster._coordinator.intent.cluster_model [Drain])
(import doeff_cluster._coordinator.core.cluster_policy [absorb-boot sweep-drains])
(import doeff_cluster._coordinator.core.drain_policy [cancel-drain drains-view])
(import doeff_cluster._coordinator.protocol.durable_kv [full-kv state-from-kv DRAIN])
(import doeff_cluster._coordinator.protocol.state_json [state-to-json state-from-json])
(import doeff_hy._table [Table])
(import tests.state_rows [table-from-rows])


(defk state-with-drains [rows]
  {:pre [(: rows dict)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "drains だけを持つ状態を、鍵 → Drain の dict から作るため。"
  (<- table Table (table-from-rows rows))
  (ClusterState :drains table))


(deftest test-sweep-drains-drops-only-the-expired-rows-and-keeps-the-table-when-none-expired
  (<- state ClusterState (state-with-drains {"a" (Drain "a" 0 100) "b" (Drain "b" 0 500)}))
  (<- same ClusterState (sweep-drains state 99))
  (assert (is same state) "期限の内の drain だけの拍で状態が変わった")
  (<- swept ClusterState (sweep-drains state 100))
  (assert (= (.keys swept.drains) #("b")) (.keys swept.drains))
  (assert (is (.row swept.drains "b") (.row state.drains "b")) "残る行が作り直された"))


(deftest test-absorb-boot-only-clears-a-drain-of-another-generation
  (<- state ClusterState (state-with-drains {"a" (Drain "a" 0 500 "b1") "n" (Drain "n" 0 500)}))
  (assert (is (absorb-boot state "a" "b1") state) "同じ世代の heartbeat が drain を解いた")
  (assert (is (absorb-boot state "n" "b9") state) "世代を付けない drain が別の世代の heartbeat で解けた")
  (val cleared (absorb-boot state "a" "b2"))
  (assert (= (.keys cleared.drains) #("n")) (.keys cleared.drains)))


(deftest test-cancel-drain-drops-one-row-and-an-absent-name-keeps-the-state
  (<- state ClusterState (state-with-drains {"a" (Drain "a" 0 500) "b" (Drain "b" 0 500)}))
  (assert (is (cancel-drain state "zz") state))
  (val cancelled (cancel-drain state "a"))
  (assert (= (.keys cancelled.drains) #("b")))
  (assert (is (.row cancelled.drains "b") (.row state.drains "b"))))


(deftest test-drains-keep-their-stored-keys-and-values
  (<- state ClusterState (state-with-drains {"a" (Drain "a" 0 500 "b1" "me")}))
  (<- kv dict (full-kv state))
  (assert (in (+ DRAIN "a") kv) (sorted kv))
  (<- again ClusterState (state-from-kv kv 0))
  (assert (= again.drains state.drains))
  (<- data dict (state-to-json state))
  (assert (= (get data "drains" "a" "boot") "b1"))
  (<- from-file ClusterState (state-from-json data 0))
  (assert (= from-file.drains state.drains)))
