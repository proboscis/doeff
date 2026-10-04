;; task の子 process が終わる前に結果を coordinator へ直に届ける口(POST /tasks/<id>/result — #1387)。
;; - coordinator の受け方(cluster_policy.absorb-task-result を本物の api_policy.respond の口で): 置いた worker からの結果で task を終える・
;;   終わった task への 2 度目の結果(直の届けの再送・heartbeat が後から運んだ物)は何も変えない・別の worker は 409・知らない task は
;;   404・結果の欄の無い本文は 400。
;; - 本番の子の送り(task_result.delivered-task-result): 本物の coordinator の判断(MemoryCoordinator)に届いて task を終える・断られた /
;;   届かない時は偽を返して file と heartbeat の路に任せる。
;; 窓そのもの(exit 0 の直後の worker の死)は sim-cluster の反例 test_task_result_window.hy が通す。
(require doeff-hy.macros [deftest defk deff <- val])
(import httpx)
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.shared.intent.run_context [RunContext])
(import doeff [with-handlers])
(import doeff_core_effects.handlers [slog-discard-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.shared.protocol.task_result [task-result-request delivered-task-result])
(import tests.transport_http [transport-http TEST-ROUTE])
(import tests.clock_fixtures [clock-at])
(import tests.detached_rig [MemoryCoordinator])
(import tests.program_rows [program-placed])

(val T (ClusterTiming))
(val V {"python" "3.14.0" "doeff" "1"})


(defk answer [state method path body now]
  {:pre [(: state ClusterState) (: method str) (: path str) (: body (| dict None)) (: now int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "coordinator の口 1 つに要求を送った答え #(次の状態 status 本文) を得るため(本物の api_policy.respond)。"
  (responded state (! (http-request method path {} body :actor "test")) now T))


(defk beat-body [worker statuses]
  {:pre [(: worker str) (: statuses list)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker の heartbeat の本文(能力 net・版 V・状態の報告 statuses)を作るため。"
  {"name" worker "provides" ["net"] "capacity" 10 "taskReserve" 0 "versions" V "statuses" statuses})


(defk placed-task [worker]
  {:pre [(: worker str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "worker が名乗り、task を 1 本出し、worker の次の heartbeat でその worker に置いた状態 #(状態 task の id) を作るため(時刻 0〜200)。"
  (<- alive dict (beat-body worker []))
  (<- joined tuple (answer (ClusterState) "POST" "/heartbeat" alive 0))
  (<- placed tuple (program-placed (get joined 0) V :now 100))
  (<- submitted tuple (answer (get placed 0) "POST" "/tasks"
                              {"program" (get placed 1) "revision" "r" "needs" ["net"] "name" "n" "leaseSeconds" 15.0} 100))
  (<- assigned tuple (answer (get submitted 0) "POST" "/heartbeat" alive 200))
  (assert (= (. (get (. (get assigned 0) tasks) (get submitted 2 "task")) phase) "assigned") assigned)
  #((get assigned 0) (get submitted 2 "task")))


(defk send-result [state id worker result now]
  {:pre [(: state ClusterState) (: id str) (: worker str) (: result str) (: now int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "task id の結果を worker の子 process として coordinator の口へ届けた答え #(次の状態 status 本文) を得るため(本番の子と同じ要求の形
   task_result.task-result-request)。"
  (val request (task-result-request id worker (+ worker "-p1") result))
  (<- answered tuple (answer state (get request 0) (get request 1) (get request 3) now))
  answered)


(deff unreachable [request]  ; defk にできない: httpx の MockTransport が呼ぶ callback
  {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "coordinator に届かない網(接続の段で落ちる)を作るため。"
  (raise (httpx.ConnectError "coordinator に届かない" :request request)))


(deftest test-a-result-from-the-placed-worker-finishes-the-task-once
  ;; 置いた worker の子の結果で task は終わり、呼び手の問い合わせに結果が出る。同じ task への 2 度目の結果(直の届けの再送)と、後から
  ;; heartbeat が運んだ結果は何も変えない(task は 1 度だけ終わる)。終わった task は worker へ渡し直さない。
  (<- placed tuple (placed-task "w"))
  (val id (get placed 1))
  (<- first tuple (send-result (get placed 0) id "w" "R" 300))
  (assert (= (cut first 1 None) #(200 {"accepted" True "phase" "finished"})) first)
  (<- again tuple (send-result (get first 0) id "w" "R2" 310))
  (assert (= (cut again 1 None) #(200 {"accepted" False "phase" "finished"})) again)
  (<- late dict (beat-body "w" [{"name" (+ "task/" id) "phase" "finished" "result" "R3" "detail" ""}]))
  (<- beat tuple (answer (get again 0) "POST" "/heartbeat" late 320))
  (assert (= (get beat 2 "tasks") []) beat)
  (<- view tuple (answer (get beat 0) "GET" (+ "/tasks/" id) None 330))
  (assert (= #((get view 2 "phase") (get view 2 "result")) #("finished" "R")) view))


(deftest test-a-result-from-another-worker-or-for-an-unknown-task-is-refused-without-change
  ;; 別の worker に置いた task の結果は 409(古い送り手)・知らない task は 404・結果の欄の無い本文は 400。どれも task を変えない。
  (<- placed tuple (placed-task "w"))
  (val id (get placed 1))
  (<- stranger tuple (send-result (get placed 0) id "x" "R" 300))
  (assert (= (get stranger 1) 409) stranger)
  (assert (= (. (get (. (get stranger 0) tasks) id) phase) "assigned") stranger)
  (<- unknown tuple (send-result (get placed 0) "t-none" "w" "R" 300))
  (assert (= (get unknown 1) 404) unknown)
  (<- bare tuple (answer (get placed 0) "POST" (+ "/tasks/" id "/result") {"worker" "w"} 300))
  (assert (= (get bare 1) 400) bare)
  (assert (= (. (get (. (get bare 0) tasks) id) phase) "assigned") bare))


(defk delivered [ctx transport]
  {:pre [(: ctx RunContext) (: transport httpx.BaseTransport)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "子の届け(delivered-task-result)を、transport の後ろの coordinator への検の HTTP の答え手と模擬の時計の下で走らせるため。"
  (<- sent bool (with-handlers [(transport-http transport) slog-discard-handler (sim-time-handler :clock (SimClock))]
                               (delivered-task-result ctx.coordinator-url ctx.job ctx.worker ctx.instance "R" TEST-ROUTE)))
  sent)


(deftest test-the-child-delivers-its-result-and-leaves-a-refusal-or-an-outage-to-the-heartbeat
  ;; 本番の子の送り(delivered-task-result)は本物の coordinator の判断に届いて task を終える(真)。coordinator が断る(知らない task)・
  ;; 届かない時は偽を返し、結果は file と worker の heartbeat の路に任せる(子は落ちない)。
  (<- placed tuple (placed-task "w"))
  (val id (get placed 1))
  (val coordinator (MemoryCoordinator (! (clock-at 300))))
  (setv coordinator.state (get placed 0))
  (val ctx (RunContext "http://coordinator" "w" "r" (+ "task/" id) :instance "w-p1"))
  (assert (! (delivered ctx (httpx.MockTransport coordinator.handle))))
  (assert (= #((. (get coordinator.state.tasks id) phase) (. (get coordinator.state.tasks id) result)) #("finished" "R"))
          (get coordinator.state.tasks id))
  (val stray (RunContext "http://coordinator" "w" "r" "task/t-none" :instance "w-p2"))
  (assert (not (! (delivered stray (httpx.MockTransport coordinator.handle)))))
  (assert (not (! (delivered ctx (httpx.MockTransport unreachable))))))
