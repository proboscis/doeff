;; 名簿を渡さずに service・設定を組む関数(records-service・records-server-config)の失敗ケース(#3008)。
;; 組んだ物が、名簿なしで起動・応答し、名乗り(X-Records-Writer)の名で書けること。名簿を要る形に戻ると赤。
(require doeff-hy.macros [deftest defk val])
(import json)
(import urllib.request [Request urlopen])
(import doeff [run with_handlers])
(import doeff_records.laws [LAW-SCHEMA])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.service [HttpRequest records-service respond])
(import doeff_records.http_server [records-server-config start-records-server])
(import tests.interpreters [sim-request-handlers])
(import doeff_time [SimClock sim-time-handler])

(setv PUT-BODY {"table" "parts" "key" ["r1"] "value" {"label" "a"} "expect" {"kind" "any"}})


(deftest test-a-service-built-without-a-roster-answers-as-the-named-writer
  (val store (MemoryStore LAW-SCHEMA))
  (val seen [])
  (val service (run (records-service LAW-SCHEMA (fn [writer] (.append seen writer) (memory-records-handler store writer)))))
  (val request (HttpRequest "POST" "/v1/records/put-row" (.encode (json.dumps PUT-BODY) "utf-8") :writer "maker"))
  (val answer (run (with_handlers [(sim-time-handler :clock (SimClock))] (respond service request))))
  (assert (= answer.status 200) answer)
  (assert (= seen ["maker"]) seen))


(deftest test-a-config-built-without-a-roster-starts-and-serves-the-named-writer
  (val store (MemoryStore LAW-SCHEMA))
  (val seen [])
  (val config (run (records-server-config LAW-SCHEMA (fn [writer] (.append seen writer) (memory-records-handler store writer))
                                          :request-handlers (sim-request-handlers (SimClock)))))
  (val server (start-records-server config))
  (try
    (val request (Request (+ server.url "/v1/records/put-row") :method "POST"
                          :headers {"Content-Type" "application/json" "X-Records-Writer" "maker"}
                          :data (.encode (json.dumps PUT-BODY) "utf-8")))
    (with [response (urlopen request)]
      (assert (= response.status 200))
      (assert (= (get (json.loads (.read response)) "kind") "written")))
    (assert (= seen ["maker"]) seen)
    (finally (.close server))))
