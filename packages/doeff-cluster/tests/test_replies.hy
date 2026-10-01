;; 返事の本文の型と綴り(#2595): core の判断(api_policy.respond)は GET /events と GET /state の答えを型の値(EventsView・StateReply)で返し、
;; JSON の形は coordinator/protocol/replies が綴る。検の入口 responded と、本番と模擬の組の返事の答え手 reply-bodies は同じ綴りを通る。
(require doeff-hy.macros [deftest val])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState EventsView StateReply])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.core.api_policy [respond])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.protocol.replies [reply-json])

(setv T (ClusterTiming))


(defn #^ ClusterState written []
  "盤に 2 行書いた状態(出来事の記録が 2 件)。"
  (setv #(s _ _) (responded (ClusterState) (http-request "PUT" "/board/a" {} {"value" 1} :actor "c") 1000 T))
  (setv #(s2 _ _) (responded s (http-request "PUT" "/board/b" {} {"value" 2} :actor "c") 2000 T))
  s2)


(deftest test-the-events-reply-is-a-typed-value-spelled-by-protocol
  (val s (written))
  (setv #(_ status answer) (respond s (http-request "GET" "/events" {"since" "0"} None) 3000 T {}))
  (assert (= status 200))
  (assert (isinstance answer EventsView) answer)
  (setv #(_ _ body) (responded s (http-request "GET" "/events" {} None) 3000 T))
  (assert (= (sorted body) ["events" "revision" "seq"]) body)
  (assert (= body (reply-json answer)) body)
  (assert (all (gfor e (get body "events") (and (isinstance e dict) (in "seq" e) (in "fromVersion" e)))) body))


(deftest test-the-state-reply-adds-audit-and-drains-to-the-view
  (val s (written))
  (setv #(_ _ answer) (respond s (http-request "GET" "/state" {} None) 3000 T {}))
  (assert (isinstance answer StateReply) answer)
  (setv #(_ _ body) (responded s (http-request "GET" "/state" {} None) 3000 T))
  (assert (and (isinstance body dict) (in "audit" body) (in "drains" body) (in "workers" body)) (sorted body))
  (assert (= (len (get body "audit")) (len answer.audit)) body)
  (assert (all (gfor e (get body "audit") (isinstance e dict))) body))


(deftest test-a-body-that-is-not-a-reply-type-passes-unchanged
  (val raw {"ok" True})
  (assert (is (reply-json raw) raw)))
