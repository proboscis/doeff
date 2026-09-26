;; 記録の service の HTTP の口: 身元(名簿に無い token の書きは Refused で何も変えない)・断りの status と本文・宣言に無い表・
;; 置き場に届かない時の 503。置き場は memory(仮想の時計)— PostgreSQL の上の口は test_laws / test_parity の http-pg が確かめる。
(require doeff-hy.macros [deftest defhandler])
(import contextlib [contextmanager])
(import json)
(import urllib.error [HTTPError])
(import urllib.request [Request urlopen])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.values [ExpectAbsent Missing Refused Unreachable UndeclaredTable Written Events])
(import doeff_records.effects [ReadRow ListRows PutRow WatchChanges AppendEvent ReadEvents])
(import doeff_records.laws [LAW-SCHEMA])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.http_server [RecordsServerConfig start-records-server])
(import doeff_records.http_client [RecordsEndpoint http-records-handler])
(import tests.interpreters [LAW-TOKENS law-roster sim-runner])


(defn open-service [lease-handlers]
  "memory の置き場の上に口を開く(検ごと)。答え = #(開いた口 仮想の時計)。"
  (setv clock (SimClock))
  #((start-records-server (RecordsServerConfig LAW-SCHEMA (law-roster) lease-handlers (sim-runner clock) :concurrent False))
    clock))


(defn memory-lease [store]
  "書き手の名 → memory の handler を渡す lease-handlers。"
  (defn [contextmanager] lease []
    (yield (fn [writer] (memory-records-handler store writer))))
  lease)


(defn run-as [server clock #^ str token program]
  "token の身元の client の handler で Program を走らせる。"
  (run (scheduled (with_handlers [(sim-time-handler :clock clock) (http-records-handler (RecordsEndpoint server.url token))]
                                 program))))


(defn raw [server #^ str method #^ str path * [body None] [token None]]
  "口へ生の要求を送り、#(status 本文の JSON) を返す(client の handler を通さない断りの形の検のため)。"
  (setv headers {"Content-Type" "application/json"})
  (when token (setv (get headers "Authorization") (+ "Bearer " token)))
  (setv request (Request (+ server.url path) :method method :headers headers
                         :data (if (is body None) None (if (isinstance body bytes) body (.encode (json.dumps body) "utf-8")))))
  (try
    (with [response (urlopen request)] #(response.status (json.loads (.read response))))
    (except [error HTTPError] #(error.code (json.loads (.read error))))))


(deftest test-a-write-by-a-token-outside-the-roster-is-refused-and-changes-nothing
  (setv store (MemoryStore LAW-SCHEMA)
        #(server clock) (open-service (memory-lease store)))
  (try
    (setv put (run-as server clock "not-in-roster" (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent)))
          append (run-as server clock "not-in-roster" (AppendEvent "journal" "k1" {"n" 1}))
          read (run-as server clock "not-in-roster" (ReadRow "parts" #("p1"))))
    (assert (isinstance put Refused) (repr put))
    (assert (isinstance append Refused) (repr append))
    (assert (isinstance read Unreachable) (repr read))
    (setv maker (get LAW-TOKENS "maker"))
    (assert (= (run-as server clock maker (ReadRow "parts" #("p1"))) (Missing)))
    (assert (= (. (run-as server clock maker (ReadEvents "journal")) items) #()))
    ;; 名簿に在る書き手の同じ書きは通る(断ったのは身元で、書きの形ではない)。
    (assert (isinstance (run-as server clock maker (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent))) Written))
    (finally (.close server))))


(deftest test-refusals-carry-the-contract-status-and-body
  (setv #(server clock) (open-service (memory-lease (MemoryStore LAW-SCHEMA)))
        maker (get LAW-TOKENS "maker"))
  (try
    (assert (= (raw server "GET" "/healthz") #(200 {"status" "ok"})))
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body {"table" "parts" "key" ["p1"]}))
    (assert (and (= status 401) (= (get body "error") "unauthorized")) (repr body))
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body {"table" "parts" "key" ["p1"]} :token "nope"))
    (assert (and (= status 401) (= (get body "error") "unauthorized")) (repr body))
    (setv #(status body) (raw server "POST" "/v1/records/nothing" :body {} :token maker))
    (assert (and (= status 404) (= (get body "error") "not-found")) (repr body))
    (setv #(status body) (raw server "GET" "/v1/records/read-row" :token maker))
    (assert (and (= status 400) (= (get body "error") "malformed")) (repr body))
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body b"{not json" :token maker))
    (assert (and (= status 400) (= (get body "error") "malformed")) (repr body))
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body {"table" "parts" "key" ["p1"] "extra" 1} :token maker))
    (assert (and (= status 400) (= (get body "error") "malformed")) (repr body))
    (setv #(status body) (raw server "POST" "/v1/records/put-row"
                              :body {"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "maybe"}} :token maker))
    (assert (and (= status 400) (= (get body "error") "malformed")) (repr body))
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body {"table" "nothing" "key" ["p1"]} :token maker))
    (assert (and (= status 404) (= (get body "error") "not-found")) (repr body))
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body {"table" "parts" "key" ["p1"]} :token maker))
    (assert (= #(status body) #(200 {"kind" "missing"})) (repr body))
    (finally (.close server))))


(deftest test-an-undeclared-table-is-a-composition-error-on-the-client
  (setv #(server clock) (open-service (memory-lease (MemoryStore LAW-SCHEMA))))
  (try
    (try
      (run-as server clock (get LAW-TOKENS "maker") (ReadRow "nothing" #("p1")))
      (assert False "宣言に無い表の読みが答えを返した")
      (except [UndeclaredTable] None))
    (finally (.close server))))


(defhandler unreachable-store []
  (ReadRow [table key] (resume (Unreachable "置き場が落ちている(検の代役)")))
  (PutRow [table key value expect approval] (resume (Unreachable "置き場が落ちている(検の代役)"))))


(deftest test-an-unreachable-store-answers-503-and-the-client-sees-unreachable
  (defn [contextmanager] lease []
    (yield (fn [writer] (unreachable-store))))
  (setv #(server clock) (open-service lease)
        maker (get LAW-TOKENS "maker"))
  (try
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body {"table" "parts" "key" ["p1"]} :token maker))
    (assert (and (= status 503) (= (get body "error") "store-unavailable")) (repr body))
    (assert (= (run-as server clock maker (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent)))
               (Unreachable "置き場が落ちている(検の代役)")))
    (finally (.close server))))


(deftest test-a-closed-service-is-unreachable
  (setv #(server clock) (open-service (memory-lease (MemoryStore LAW-SCHEMA))))
  (.close server)
  (setv answer (run-as server clock (get LAW-TOKENS "maker") (ReadRow "parts" #("p1"))))
  (assert (isinstance answer Unreachable) (repr answer)))
