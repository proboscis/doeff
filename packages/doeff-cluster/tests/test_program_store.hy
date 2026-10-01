;;; 詰めた Program の置き場(ADR-DOE-CLUSTER-001 R3b・改訂 1 の F — program_policy と coordinator の口 /programs/<sha>)。
;;;
;;; 宣言の行と heartbeat の返事は sha だけを運び、本体はこの置き場に置く。キーは中身の sha256(置く時に確かめる)・大きすぎは 413・
;;; 無い物は 404。受け付けた Service のどれも参照しない物は置いてから PROGRAM-GRACE-MS の後に掃除し、参照の在る物と期限の内の物は残す。
;;; 置き場は保存する(state file の "programs"・durable KV の program/<sha>)。
(require doeff-hy.macros [deftest defk <- val])
(import hashlib)
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.foundation.coordinator_inbox [http-request])
(import doeff_cluster.coordinator.core.cluster_policy [state-to-json state-from-json])
(import doeff_cluster.coordinator.core.durable_kv [full-kv state-from-kv])
(import doeff_cluster.coordinator.core.api_policy [respond])
(import doeff_cluster.coordinator.core.program_policy [sweep-programs PROGRAM-GRACE-MS PROGRAM-MAX-BYTES])
(import tests.program_rows [SAMPLE-RUN])

(val T (ClusterTiming))
(val BLOB "cHJvZ3JhbQ==")
(val VERSIONS {"python" "3.14.0" "doeff" "1"})


(defk sha-of [blob]
  {:pre [(: blob str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "詰めた文字列 → 置き場のキー(中身の sha256)。"
  (.hexdigest (hashlib.sha256 (.encode blob "ascii"))))


(defk call [state method path body now]
  {:pre [(: state ClusterState) (: method str) (: path str) (: body (| dict None)) (: now int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator の純粋な振り分け 1 件(送り手 c-me)→ #(次の状態 status 本文)。"
  (respond state (http-request method path {} body :actor "c-me" :peer "10.0.0.9") now T))


(defk stored [blob now]
  {:pre [(: blob str) (: now int)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "空の coordinator に blob を 1 つ置いた後の状態。"
  (<- sha str (sha-of blob))
  (<- put tuple (call (ClusterState) "PUT" (+ "/programs/" sha) {"blob" blob "versions" VERSIONS} now))
  (assert (= (get put 1) 200) put)
  (get put 0))


(deftest test-a-program-is-put-by-its-sha-and-read-back
  (<- sha str (sha-of BLOB))
  (<- s ClusterState (stored BLOB 1000))
  (<- got tuple (call s "GET" (+ "/programs/" sha) None 2000))
  (assert (= (get got 1) 200) got)
  (assert (= (get got 2) {"blob" BLOB "versions" VERSIONS}))
  ;; 同じキーを置き直すと期限(置いた時刻)だけが延びる — 同じ中身は同じ行。
  (<- again tuple (call s "PUT" (+ "/programs/" sha) {"blob" BLOB "versions" VERSIONS} 5000))
  (assert (= (get again 1) 200))
  (assert (= (list (. (get again 0) programs)) [sha]))
  (assert (= (get (. (get again 0) programs) sha "putMs") 5000)))


(deftest test-a-wrong-sha-a-bad-key-or-a-bad-body-is-refused-with-400
  (<- other str (sha-of "b3RoZXI="))
  (<- mismatch tuple (call (ClusterState) "PUT" (+ "/programs/" other) {"blob" BLOB "versions" VERSIONS} 1000))
  (assert (= (get mismatch 1) 400) mismatch)
  (assert (in "sha256 がキーと合わない" (get mismatch 2 "error")))
  (assert (= (. (get mismatch 0) programs) {}))
  (<- bad-key tuple (call (ClusterState) "PUT" "/programs/not-a-sha" {"blob" BLOB} 1000))
  (assert (= (get bad-key 1) 400) bad-key)
  (assert (in "64 桁の sha256" (get bad-key 2 "error")))
  (<- sha str (sha-of BLOB))
  (<- no-blob tuple (call (ClusterState) "PUT" (+ "/programs/" sha) {"versions" VERSIONS} 1000))
  (assert (= (get no-blob 1) 400) no-blob)
  (<- bad-versions tuple (call (ClusterState) "PUT" (+ "/programs/" sha) {"blob" BLOB "versions" "3.14"} 1000))
  (assert (= (get bad-versions 1) 400) bad-versions))


(deftest test-a-program-over-the-limit-is-refused-with-413
  (val big (* "A" (+ PROGRAM-MAX-BYTES 4)))
  (<- sha str (sha-of big))
  (<- put tuple (call (ClusterState) "PUT" (+ "/programs/" sha) {"blob" big "versions" VERSIONS} 1000))
  (assert (= (get put 1) 413) (get put 2))
  (assert (= (. (get put 0) programs) {})))


(deftest test-an-unknown-program-is-404
  (<- sha str (sha-of BLOB))
  (<- got tuple (call (ClusterState) "GET" (+ "/programs/" sha) None 1000))
  (assert (= (get got 1) 404) got)
  (assert (in "置かれていない" (get got 2 "error"))))


(deftest test-unreferenced-programs-are-swept-after-the-grace-and-referenced-ones-stay
  (<- kept-sha str (sha-of BLOB))
  (<- loose-sha str (sha-of "bG9vc2U="))
  (<- s ClusterState (stored BLOB 1000))
  (<- loose tuple (call s "PUT" (+ "/programs/" loose-sha) {"blob" "bG9vc2U=" "versions" VERSIONS} 1000))
  ;; kept は Service の行が参照する。loose はどれも参照しない。
  (<- declared tuple (call (get loose 0) "POST" "/resources/Service"
                           {"name" "svc" "spec" {"revision" "r1" "needs" ["net"] "run" (| SAMPLE-RUN {"program" kept-sha})}}
                           2000))
  (assert (= (get declared 1) 201) declared)
  (val both (get declared 0))
  (assert (= (sorted both.programs) (sorted [kept-sha loose-sha])))
  ;; 期限の内はどちらも残る(declare は Program を先に置いてから行を書くので、その間に消さない)。
  (assert (= (sorted (. (sweep-programs both (+ 1000 PROGRAM-GRACE-MS)) programs)) (sorted [kept-sha loose-sha])))
  ;; 期限を過ぎると参照の無い物だけが消える。
  (val later (sweep-programs both (+ 1001 PROGRAM-GRACE-MS)))
  (assert (= (list later.programs) [kept-sha]) later.programs)
  ;; Service を消すと参照が無くなり、次の掃除で消える(coordinator の調停 — 書きの settle — が掃除を回す)。
  (<- deleted tuple (call both "DELETE" "/resources/Service/svc" None (+ 2000 PROGRAM-GRACE-MS)))
  (assert (= (get deleted 1) 200) deleted)
  (assert (= (. (get deleted 0) programs) {}) (. (get deleted 0) programs)))


(deftest test-programs-survive-a-restart-from-the-state-file-and-the-durable-kv
  (<- sha str (sha-of BLOB))
  (<- s ClusterState (stored BLOB 1000))
  (val want {sha {"blob" BLOB "versions" VERSIONS "putMs" 1000}})
  (assert (= s.programs want))
  (assert (= (. (state-from-json (state-to-json s) 2000) programs) want))
  (val kv (full-kv s))
  (assert (in (+ "program/" sha) kv) (sorted kv))
  (assert (= (. (state-from-kv kv 2000) programs) want)))
