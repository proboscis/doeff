;; 返事の本文の型と綴り(#2595): core の判断(api_policy.respond)は GET /events・GET /state・GET /workers/<名> の答えを型の値(EventsView・StateReply・WorkerDrainView)で返し、
;; JSON の形は coordinator/protocol/replies が綴る。検の入口 responded と、本番と模擬の組の返事の答え手 reply-bodies は同じ綴りを通る。
(require doeff-hy.macros [deftest defk <- val])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState EventsView StateReply HeartbeatReply WorkerInfo WorkerDrainView ResourceList ResourceView ErrorReply RowConflict BoardWritten BoardConflict BoardRead TaskAccepted TaskProgress TaskMissing TaskResultTaken TaskDropped DetachedUnknown DetachedWarming DetachedSubmitted DetachedCancelled DetachedReleased ProgramStored ProgramRow])
(import doeff_cluster.shared.intent.warm_model [WarmState])
(import doeff_cluster.coordinator.core.cluster_policy [heartbeat-reply])
(import doeff_cluster.coordinator.core.drain_policy [superseded-worker-view])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.core.api_policy [respond])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.protocol.replies [reply-json])

(setv T (ClusterTiming))


(defk written []
  {:pre [] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "盤に 2 行書いた状態(出来事の記録が 2 件)を、返事の検の出発点として組むため。"
  (val first (responded (ClusterState) (! (http-request "PUT" "/board/a" {} {"value" 1} :actor "c")) 1000 T))
  (val second (responded (get first 0) (! (http-request "PUT" "/board/b" {} {"value" 2} :actor "c")) 2000 T))
  (get second 0))


(deftest test-the-events-reply-is-a-typed-value-spelled-by-protocol
  (val s (! (written)))
  (setv #(_ status answer) (! (respond s (! (http-request "GET" "/events" {"since" "0"} None)) 3000 T {})))
  (assert (= status 200))
  (assert (isinstance answer EventsView) answer)
  (setv #(_ _ body) (responded s (! (http-request "GET" "/events" {} None)) 3000 T))
  (assert (= (sorted body) ["events" "revision" "seq"]) body)
  (assert (= body (! (reply-json answer))) body)
  (assert (all (gfor e (get body "events") (and (isinstance e dict) (in "seq" e) (in "fromVersion" e)))) body))


(deftest test-the-state-reply-adds-audit-and-drains-to-the-view
  (val s (! (written)))
  (setv #(_ _ answer) (! (respond s (! (http-request "GET" "/state" {} None)) 3000 T {})))
  (assert (isinstance answer StateReply) answer)
  (setv #(_ _ body) (responded s (! (http-request "GET" "/state" {} None)) 3000 T))
  (assert (and (isinstance body dict) (in "audit" body) (in "drains" body) (in "workers" body)) (sorted body))
  (assert (= (len (get body "audit")) (len answer.audit)) body)
  (assert (all (gfor e (get body "audit") (isinstance e dict))) body))


(deftest test-a-body-that-is-not-a-reply-type-passes-unchanged
  (val raw {"ok" True})
  (assert (is (! (reply-json raw)) raw)))


(deftest test-the-heartbeat-reply-is-typed-and-spelled-in-the-old-shape
  ;; heartbeat の返事は型の値(HeartbeatReply)で、JSON の欄は前と同じ — superseded は退いた世代への返事の時だけ書く。
  (val reply (! (heartbeat-reply (ClusterState :revision 3) "w1" T)))
  (assert (isinstance reply HeartbeatReply) reply)
  (val body (! (reply-json reply)))
  (assert (= (sorted body) ["draining" "formats" "jobs" "revision" "tasks" "timing" "warm"]) body)
  (assert (= #((get body "revision") (get body "jobs") (get body "tasks") (get body "warm")) #(3 [] [] [])) body))


(deftest test-the-worker-view-is-typed-and-spelled-in-the-old-shape
  ;; worker 1 つの画面は型の値(WorkerDrainView)で、JSON の欄は前と同じ — 退いた世代の待ちの答えだけが superseded を書き、
  ;; その drain には頼みの記録(sinceMs・untilMs・actor)が無い。
  (setv s (ClusterState :workers {"w1" (WorkerInfo :name "w1" :provides #("cpu") :capacity 1 :last-seen-ms 1000 :boot "b2" :task-reserve 0)}))
  (setv #(_ _ answer) (! (respond s (! (http-request "GET" "/workers/w1" {} None)) 2000 T {})))
  (assert (isinstance answer WorkerDrainView) answer)
  (setv body (! (reply-json answer)))
  (assert (= (sorted body) ["alive" "boot" "derived" "drain" "draining" "exclusive" "name" "node" "provides" "ready" "silentMs"]) body)
  (assert (= #((get body "drain") (get body "draining") (get body "ready") (get body "silentMs")) #(None False True 1000)) body)
  (setv old (! (reply-json (superseded-worker-view s "w1" "b1" 2000 T))))
  (assert (= #((get old "superseded") (get old "draining") (get old "ready")) #(True True False)) old)
  (assert (= (sorted (get old "drain"))
             ["blocked" "boot" "drained" "moving" "movingReady" "phase" "remaining" "superseded" "worker"])
          old)
  (assert (= #((get old "drain" "phase") (get old "drain" "drained") (get old "drain" "boot")) #("Drained" True "b1")) old))


(deftest test-the-resources-are-typed-and-spelled-in-the-old-shape
  ;; 資源の一覧と 1 つは型の値(ResourceList・ResourceView)で、status は比べる単位の status に種類ごとの観測を足した物。
  (setv s (ClusterState :workers {"w1" (WorkerInfo :name "w1" :provides #("cpu") :capacity 1 :last-seen-ms 1000 :boot "b2" :task-reserve 0)}))
  (setv #(_ _ answer) (! (respond s (! (http-request "GET" "/resources/Worker" {} None)) 2000 T {})))
  (assert (isinstance answer ResourceList) answer)
  (assert (all (gfor v answer.items (isinstance v ResourceView))) answer)
  (setv body (! (reply-json answer)))
  (assert (= (sorted body) ["items" "kind" "revision"]) body)
  (setv item (get body "items" 0))
  (assert (= (sorted item) ["createdBy" "createdMs" "generation" "kind" "name" "owner" "resourceVersion" "spec" "status" "updatedBy"
                            "updatedMs"])
          item)
  (assert (= #((get item "name") (get item "status" "live") (get item "status" "alive") (get item "status" "silentMs"))
             #("w1" True True 1000))
          item))


(deftest test-a-refusal-is-typed-and-spelled-in-the-old-shape
  ;; 断りの答えは型の値(ErrorReply)で、JSON は {error …} — 付け足しの欄(current・conflicts・open・fault)は在る時だけ書く。
  (setv #(_ status answer) (! (respond (ClusterState) (! (http-request "GET" "/nowhere" {} None)) 1000 T {})))
  (assert (= status 404))
  (assert (isinstance answer ErrorReply) answer)
  (assert (= (sorted (! (reply-json answer))) ["error"]) (! (reply-json answer)))
  (assert (= (! (reply-json (ErrorReply :message "版が古い" :current 3 :conflicts #((RowConflict :name "a" :message "x" :current 2)
                                                                               (RowConflict :name "b" :message "y")))))
             {"error" "版が古い" "current" 3 "conflicts" [{"name" "a" "error" "x" "current" 2} {"name" "b" "error" "y"}]})))


(deftest test-the-board-answers-are-typed-and-spelled-in-the-old-shape
  ;; 盤の読みの答えは型の値(BoardRead)で、読みと書きの答えの JSON は前と同じ形(書きは本文を解く入口 responded を通す)。
  (setv #(s write-status written) (responded (ClusterState) (! (http-request "PUT" "/board/a" {} {"value" 1} :actor "c")) 1000 T))
  (assert (= #(write-status written) #(200 {"ok" True "resourceVersion" 1})) written)
  (setv #(_ clash-status clash) (responded s (! (http-request "PUT" "/board/a" {} {"value" 2 "expectVersion" 5} :actor "c")) 1000 T))
  (assert (= #(clash-status clash) #(409 {"ok" False "current" 1 "resourceVersion" 1})) clash)
  (setv #(_ _ plain) (! (respond s (! (http-request "GET" "/board" {} None)) 1000 T {})))
  (assert (isinstance plain BoardRead) plain)
  (assert (= (! (reply-json plain)) {"a" 1}))
  (setv #(_ _ versioned) (! (respond s (! (http-request "GET" "/board" {"withVersions" "1"} None)) 1000 T {})))
  (assert (= (! (reply-json versioned)) {"a" {"value" 1 "resourceVersion" 1}}))
  (assert (= (! (reply-json (BoardWritten :version None))) {"ok" True "resourceVersion" None}))
  (assert (= (! (reply-json (BoardConflict :current 1 :version 1 :reason "x"))) {"ok" False "current" 1 "resourceVersion" 1 "error" "x"})))


(deftest test-the-task-answers-are-typed-and-spelled-in-the-old-shape
  ;; task の口の答えは型の値で、JSON は前と同じ形(知らない task の問いは {phase: missing})。
  (setv #(_ _ missing) (! (respond (ClusterState) (! (http-request "GET" "/tasks/t9" {} None)) 1000 T {})))
  (assert (isinstance missing TaskMissing) missing)
  (assert (= (! (reply-json missing)) {"phase" "missing"}))
  (assert (= (! (reply-json (TaskAccepted :id "t1"))) {"task" "t1"}))
  (assert (= (! (reply-json (TaskResultTaken :accepted False :phase "finished"))) {"accepted" False "phase" "finished"}))
  (assert (= (! (reply-json (TaskDropped :id "t1"))) {"dropped" True}))
  (assert (= (sorted (! (reply-json (TaskProgress :phase "queued" :worker None :detail "" :result None :failure-kind "" :retryable False))))
             ["detail" "failureKind" "phase" "result" "retryable" "worker"])))


(deftest test-the-detached-answers-are-typed-and-spelled-in-the-old-shape
  ;; 切り離した task の口の答えは型の値で、JSON は前と同じ形(行の無い key は {key phase: unknown}・起きた直後は 503 の warming)。
  (setv #(_ unknown-status unknown) (! (respond (ClusterState :started-ms -100000) (! (http-request "GET" "/detached/k1" {} None)) 1000 T {})))
  (assert (and (= unknown-status 200) (isinstance unknown DetachedUnknown)) unknown)
  (assert (= (! (reply-json unknown)) {"key" "k1" "phase" "unknown"}))
  (setv #(_ warming-status warming) (! (respond (ClusterState :started-ms 900) (! (http-request "GET" "/detached/k1" {} None)) 1000 T {})))
  (assert (and (= warming-status 503) (isinstance warming DetachedWarming)) warming)
  (assert (= (sorted (! (reply-json warming))) ["error" "key" "phase"]))
  (assert (= (! (reply-json (DetachedSubmitted :key "k" :id "t1" :created True :phase "queued")))
             {"key" "k" "task" "t1" "created" True "phase" "queued"}))
  (assert (= (! (reply-json (DetachedCancelled :key "k" :cancelled False :phase "unknown"))) {"key" "k" "cancelled" False "phase" "unknown"}))
  (assert (= (! (reply-json (DetachedReleased :key "k" :released True))) {"key" "k" "released" True})))


(deftest test-the-program-and-warm-answers-are-typed-and-spelled-in-the-old-shape
  ;; Program の置き場の答えは型の値(ProgramStored・置いた行 ProgramRow)、温める表の答えは WarmState で、JSON は前と同じ形。
  (assert (= (! (reply-json (ProgramStored :sha "ab"))) {"program" "ab"}))
  (assert (= (! (reply-json (ProgramRow :blob "b" :versions {"doeff" "1"} :put-ms 5))) {"blob" "b" "versions" {"doeff" "1"}}))
  (setv warm (! (reply-json (WarmState :key "k" :ready #("w1") :preparing #() :failed #() :until-ms 9))))
  (assert (and (isinstance warm dict) (= (get warm "key") "k")) warm))


;; --- Rollout の相手の Deployment の観測(#2728 J1)---------------------------------------------------------------------------
;; 観測は型の値(DeploymentSeen・DeploymentUnreadable — ClusterState.observations の表)になったが、GET /resources/Rollout/<名> の
;; status.observed の JSON は前と同じ形(鍵の順まで)で綴る。

(import json)
(import doeff [with_handlers])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterNaming])
(import doeff_cluster.coordinator.core.program [rollout-tick])
(import doeff_cluster.coordinator.protocol.kube [KubeMemory kube-memory])

(val DEPLOYMENT-TO-DEPLOYMENT {"from" {"kind" "Deployment" "namespace" "prod" "name" "old"}
                                "to" {"kind" "Deployment" "namespace" "prod" "name" "new" "replicas" 1}})


(defk rollout-observed-json [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "GET /resources/Rollout/r の status.observed を、欄の順を保った JSON の文字列にするため(形を byte の単位で比べる)。"
  (val answered (responded state (! (http-request "GET" "/resources/Rollout/r" {} None)) now T))
  (assert (= (get answered 1) 200) answered)
  (json.dumps (get answered 2 "status" "observed") :ensure-ascii False))


(deftest test-the-rollout-observed-json-keeps-the-deployment-observation-shape
  ;; 読めた相手 = specReplicas・replicas・readyReplicas・availableReplicas・updatedReplicas・generation・observedGeneration・
  ;; annotations の 8 欄と at(この順 — k8s の答えの欄の順に依らない)・読めなかった相手 = {at error}・まだ読んでいない相手 = null。
  ;; annotations は中を読まずにそのまま写す(欄の順も保つ)。
  (val kube (KubeMemory {"prod/old" {"annotations" {"b" "2" "a" "1"} "observedGeneration" 4 "generation" 4 "specReplicas" 2
                                     "replicas" 2 "readyReplicas" 1 "availableReplicas" 1 "updatedReplicas" 2}}))
  (val created (responded (ClusterState) (! (http-request "POST" "/resources/Rollout" {} {"name" "r" "spec" DEPLOYMENT-TO-DEPLOYMENT}
                                                       :actor "c"))
                          1000 T))
  (assert (< (get created 1) 300) created)
  (<- unread str (rollout-observed-json (get created 0) 1000))
  (assert (= unread "{\"Deployment:prod/old\": null, \"Deployment:prod/new\": null}") unread)
  (<- ticked ClusterState (with_handlers [(kube-memory kube)] (rollout-tick (get created 0) T (ClusterNaming) 5000)))
  (<- observed str (rollout-observed-json ticked 5000))
  (assert (= observed
             (+ "{\"Deployment:prod/old\": {\"specReplicas\": 2, \"replicas\": 2, \"readyReplicas\": 1, \"availableReplicas\": 1, "
                "\"updatedReplicas\": 2, \"generation\": 4, \"observedGeneration\": 4, \"annotations\": {\"b\": \"2\", \"a\": \"1\"}, "
                "\"at\": 5000}, "
                "\"Deployment:prod/new\": {\"at\": 5000, \"error\": \"無い Deployment: prod/new\"}}"))
          observed))
