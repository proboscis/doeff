;; drain の CLI(drain_main)の coordinator への口 coordinator-calls(#2427 — 宛先の部品の上の HttpRequest): 返事は番号と本文の dict、
;; 届かない時は理由の値(drain の Program が間を置いて問い直す)。宛先が回ったら次の要求はそこから試す。
(require doeff-hy.macros [deftest defk deff <- val var])
(import json)
(import httpx)
(import doeff [run with-handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.drain_main [coordinator-calls])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteCell])
(import doeff_cluster.worker.intent.drain_model [CoordinatorCall])
(import tests.transport_http [transport-http TEST-ROUTE])

(val LAN "http://lan")
(val TAILNET "http://tailnet")


(deff answered [request]  ; defk にできない: httpx の MockTransport が呼ぶ callback
  {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "LAN の宛先は接続できず、tailnet の宛先は要求の path と送り手を本文で返す。"
  (when (= request.url.host "lan")
    (raise (httpx.ConnectError "unreachable" :request request)))
  (httpx.Response 200 :json {"path" request.url.path "actor" (.get request.headers "X-Actor")}))


(deff refused [request]  ; defk にできない: httpx の MockTransport が呼ぶ callback
  {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本文が JSON の dict でない断り。"
  (httpx.Response 404 :text "no such worker"))


(defk calls [times]
  {:pre [(: times int)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "program"}}
  "GET /workers/w を times 回問うため。"
  (var answers [])
  (for [n (range times)]
    (<- answer dict (CoordinatorCall "GET" "/workers/w" None))
    (:= answers (+ answers [answer])))
  answers)


(defn #^ list called [#^ httpx.BaseTransport transport #^ RouteCell cell #^ int times]  ; defk にできない: 検が Program の外から 1 回走らせる入口
  "同じ口で GET /workers/w を times 回問い、答えの列を返す。"
  (run (scheduled (with-handlers [(transport-http transport) (sim-time-handler :clock (SimClock)) (coordinator-calls cell TEST-ROUTE)]
                                 (calls times)))))


(deftest test-the-calls-answer-the-status-and-body-and-keep-the-route
  (val cell (RouteCell (CoordinatorRoute :urls #(LAN TAILNET) :active 0 :switched-at-ms 0)))
  (val answers (called (httpx.MockTransport answered) cell 2))
  (assert (= (get answers 0) {"status" 200 "body" {"path" "/workers/w" "actor" "test-sender"}}) answers)
  (assert (= (get answers 1) (get answers 0)) answers)
  ;; 届いた宛先(tailnet)を次の要求の宛先にする。
  (assert (= cell.route.active 1) cell.route)
  ;; 本文が dict でない断りは空の本文・どこにも届かなければ理由の値。
  (val refusal (get (called (httpx.MockTransport refused) (RouteCell (CoordinatorRoute :urls #(TAILNET) :active 0 :switched-at-ms 0)) 1) 0))
  (assert (= refusal {"status" 404 "body" {}}) refusal)
  (val nowhere (get (called (httpx.MockTransport answered) (RouteCell (CoordinatorRoute :urls #(LAN) :active 0 :switched-at-ms 0)) 1) 0))
  (assert (in "error" nowhere) nowhere))
