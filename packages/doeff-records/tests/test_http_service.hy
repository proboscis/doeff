;; 記録の service の HTTP の口: 身元(名簿に無い token は読み書きを問わず client が RecordsUnauthorized を上げ、何も変えない)・
;; 断りの status と本文・宣言に無い表・置き場に届かない時の 503。置き場は memory(仮想の時計)— PostgreSQL の上の口は test_laws / test_parity の http-pg が確かめる。
(require doeff-hy.macros [deftest defhandler defk deff val var])
(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import json)
(import threading)
(import urllib.error [HTTPError])
(import urllib.request [Request urlopen])
(import doeff [EffectBase run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.values [ExpectAbsent ExpectAny Missing Unreachable UndeclaredTable Written WrittenRows WatchCursor])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows RowWrite WatchChanges AppendEvent ReadEvents])
(import doeff_records.laws [LAW-SCHEMA])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.http_server [RecordsServerConfig RunningServer start-records-server])
(import doeff_records.http_client [RecordsEndpoint RecordsUnauthorized http-records-handler http-table-records-handler])
(import tests.interpreters [LAW-TOKENS law-roster sim-request-handlers])


(defn open-service [handler-for]
  "置き場の上に口を開く(検ごと)。handler-for = 書き手の名 → 置き場の handler。答え = #(開いた口 仮想の時計)。"
  (setv clock (SimClock))
  #((start-records-server (RecordsServerConfig LAW-SCHEMA (law-roster) handler-for :request-handlers (sim-request-handlers clock)))
    clock))


(defn memory-lease [store]
  "書き手の名 → memory の handler。"
  (fn [writer] (memory-records-handler store writer)))


(defn run-as [server clock #^ str token program]
  "token の身元の client の handler で Program を走らせる。"
  (run (scheduled (with_handlers [(await-handler) (http-production-handler) (sim-time-handler :clock clock)
                                  (http-records-handler (RecordsEndpoint server.url token))]
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


(defk identity-refusal-of [server clock token program]
  {:pre [(: server RunningServer) (: clock SimClock) (: token str) (: program EffectBase)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "token の身元の client で program を走らせ、client の handler が RecordsUnauthorized を上げたことを確かめて、その文を返す
   (答えの値を返せば赤 — 身元の断りを Unreachable や Refused の値に写す client を通さない)。"
  (var answer None)
  (try
    (:= answer (run-as server clock token program))
    (except [error RecordsUnauthorized]
      (return (str error))))
  (raise (AssertionError (.format "身元の断りが答えの値になった: {!r}" answer))))


(deftest test-a-token-outside-the-roster-raises-on-every-operation-and-changes-nothing
  ;; 名簿に無い token は組み立ての誤り: 読みも書きも答えの値(Unreachable・Refused)にせず RecordsUnauthorized を上げる。読みを
  ;; Unreachable に写していた時は、読み手が時間で晴れる届かなさと区別できず、token を誤った呼び手が読みを撃ち直し続けた。
  ;; 文は口・status・操作を名指し、token そのものは写さない。1 行も書かない。
  (val store (MemoryStore LAW-SCHEMA))
  (val opened (open-service (memory-lease store)))
  (val server (get opened 0))
  (val clock (get opened 1))
  (val maker (get LAW-TOKENS "maker"))
  (val stranger "not-in-roster")
  (try
    (for [#(operation program) [#("read-row" (ReadRow "parts" #("p1")))
                                 #("list-rows" (ListRows "parts"))
                                 #("watch-changes" (WatchChanges #("parts") (WatchCursor 0 0) :timeout 0.0))
                                 #("read-events" (ReadEvents "journal"))
                                 #("put-row" (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent)))
                                 #("append-event" (AppendEvent "journal" "k1" {"n" 1}))
                                 #("put-rows" (PutRows #((RowWrite "parts" #("p1") {"label" "a"} (ExpectAbsent)))))]]
      (val said (! (identity-refusal-of server clock stranger program)))
      (assert (in (+ "(401 " operation ")") said) said)
      (assert (in server.url said) said)
      (assert (in "名簿に無い token" said) said)
      (assert (not-in stranger said) said))
    ;; 何も変えていない(名簿に在る書き手が読むと行も出来事も無い)。
    (assert (= (run-as server clock maker (ReadRow "parts" #("p1"))) (Missing)))
    (assert (= (. (run-as server clock maker (ReadEvents "journal")) items) #()))
    ;; 名簿に在る書き手の同じ書きは通る(断ったのは身元で、書きの形ではない)。
    (assert (isinstance (run-as server clock maker (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent))) Written))
    (finally (.close server))))


(defk status-and-error [reply]
  {:pre [(: reply tuple)] :post [(: % tuple)]}
  "raw の答え #(status 本文) を #(status 断りの語) にする(断りの形の比べを 1 行にするため)。"
  #((get reply 0) (.get (get reply 1) "error")))


(deftest test-a-put-rows-by-a-token-outside-the-roster-raises-and-writes-no-row
  ;; 名簿に無い呼び手の束: 口は 401 unauthorized で断り(口の契約は変えない)、client の handler は RecordsUnauthorized を上げる。1 行も書かない。
  (val store (MemoryStore LAW-SCHEMA))
  (val opened (open-service (memory-lease store)))
  (val server (get opened 0))
  (val clock (get opened 1))
  (val writes #((RowWrite "parts" #("p1") {"label" "a"} (ExpectAbsent)) (RowWrite "parts" #("p2") {"label" "b"} (ExpectAbsent))))
  (val maker (get LAW-TOKENS "maker"))
  (try
    (val said (! (identity-refusal-of server clock "not-in-roster" (PutRows writes))))
    (assert (in "(401 put-rows)" said) said)
    (assert (= (run-as server clock maker (ReadRow "parts" #("p1"))) (Missing)))
    (assert (= (run-as server clock maker (ReadRow "parts" #("p2"))) (Missing)))
    (val refused (raw server "POST" "/v1/records/put-rows"
                      :body {"writes" [{"table" "parts" "key" ["p1"] "value" {"label" "a"} "expect" {"kind" "absent"}}]}
                      :token "nope"))
    (assert (= (! (status-and-error refused)) #(401 "unauthorized")) (repr refused))
    ;; 名簿に在る書き手の同じ束は通る(断ったのは身元で、束の形ではない)。
    (assert (isinstance (run-as server clock maker (PutRows writes)) WrittenRows))
    (finally (.close server))))


(defclass FrontRefusal [BaseHTTPRequestHandler]
  "記録の service の前に立つ口の代役: どの要求にも server.status と本文 server.payload で答える(前に立つ口の身元の断りの本文は、
   記録の service の JSON の断りとは限らない)。"
  (deff do-POST [self]  ; defk にできない: http.server が要求ごとの thread で呼ぶ素の method
    {:pre [(: self FrontRefusal)] :post [(: % None)] :tags {:context "records" :role "foundation"}}
    (.read self.rfile (int (.get self.headers "Content-Length" "0")))
    (.send-response self self.server.status)
    (.send-header self "Content-Type" "text/html")
    (.send-header self "Content-Length" (str (len self.server.payload)))
    (.end-headers self)
    (.write self.wfile self.server.payload)
    None)
  (deff log-message [self #* args]  ; defk にできない: http.server が要求ごとに呼ぶ log の口(検の出力を要求の行で埋めない)
    {:pre [(: self FrontRefusal) (: args tuple)] :post [(: % None)] :tags {:context "records" :role "foundation"}}
    None))


(defk front-refusal-server [status payload]
  {:pre [(: status int) (: payload bytes)] :post [(: % ThreadingHTTPServer)] :tags {:context "records" :role "foundation"}}
  "前に立つ口の代役(127.0.0.1 の空き port)を立てるため: どの要求にも status と HTML の本文 payload で答える。"
  (val server (ThreadingHTTPServer #("127.0.0.1" 0) FrontRefusal))
  (setv server.status status server.payload payload)
  (.start (threading.Thread :target server.serve-forever :daemon True))
  server)


(deftest test-an-identity-refusal-with-a-non-json-body-raises-by-status
  ;; 前に立つ口の 401 / 403 は本文が HTML でも status で身元の断りと読む(本文を JSON として読んで WireError にしない)。
  ;; 理由は本文の頭だけを写す。
  (val page (+ b"<html><body>" (* b"x" 2000) b"</body></html>"))
  (for [status [401 403]]
    (val server (! (front-refusal-server status page)))
    (val url (+ "http://127.0.0.1:" (str (get server.server-address 1))))
    (try
      (do
        (val endpoint (RecordsEndpoint url "t" :request-timeout 5.0))
        (var said None)
        (try
          (run (scheduled (with_handlers [(await-handler) (http-production-handler) (sim-time-handler :clock (SimClock))
                                          (http-records-handler endpoint)]
                                         (ReadRow "parts" #("p1")))))
          (except [error RecordsUnauthorized]
            (:= said (str error))))
        (assert (is-not said None) (.format "{} の前の口の断りが答えの値になった" status))
        (assert (in (.format "({} read-row)" status) said) said)
        (assert (in "<html>" said) said)
        (assert (< (len said) 1000) said))
      (finally (.shutdown server) (.server-close server)))))



(deftest test-put-rows-refusals-carry-the-contract-status
  ;; 束の形の誤り(同じ鍵が 2 度・空の束)は 400、宣言に無い表を名指す束は 404(client は UndeclaredTable を上げる)で、どれも 1 行も書かない。
  (val store (MemoryStore LAW-SCHEMA))
  (val opened (open-service (memory-lease store)))
  (val server (get opened 0))
  (val clock (get opened 1))
  (val maker (get LAW-TOKENS "maker"))
  (val one {"table" "parts" "key" ["p1"] "value" {"label" "a"} "expect" {"kind" "any"}})
  (try
    (val twice (raw server "POST" "/v1/records/put-rows" :body {"writes" [one one]} :token maker))
    (assert (= (! (status-and-error twice)) #(400 "malformed")) (repr twice))
    (val empty (raw server "POST" "/v1/records/put-rows" :body {"writes" []} :token maker))
    (assert (= (! (status-and-error empty)) #(400 "malformed")) (repr empty))
    (val undeclared (raw server "POST" "/v1/records/put-rows"
                         :body {"writes" [one {"table" "nothing" "key" ["x"] "value" {} "expect" {"kind" "any"}}]} :token maker))
    (assert (= (! (status-and-error undeclared)) #(404 "not-found")) (repr undeclared))
    (try
      (run-as server clock maker (PutRows #((RowWrite "parts" #("p1") {"label" "a"} (ExpectAny))
                                            (RowWrite "nothing" #("x") {} (ExpectAny)))))
      (assert False "宣言に無い表を名指す束が答えを返した")
      (except [UndeclaredTable] None))
    (assert (= (run-as server clock maker (ReadRow "parts" #("p1"))) (Missing)))
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
  (PutRow [table key value expect] (resume (Unreachable "置き場が落ちている(検の代役)"))))


(deftest test-an-unreachable-store-answers-503-and-the-client-sees-unreachable
  (setv #(server clock) (open-service (fn [writer] (unreachable-store)))
        maker (get LAW-TOKENS "maker"))
  (try
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body {"table" "parts" "key" ["p1"]} :token maker))
    (assert (and (= status 503) (= (get body "error") "store-unavailable")) (repr body))
    ;; 読みの 503 も今までどおり Unreachable の値(時間で晴れる届かなさ — 身元の断りとは別の答え)。
    (assert (= (run-as server clock maker (ReadRow "parts" #("p1"))) (Unreachable "置き場が落ちている(検の代役)")))
    (assert (= (run-as server clock maker (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent)))
               (Unreachable "置き場が落ちている(検の代役)")))
    (finally (.close server))))


(deftest test-a-closed-service-is-unreachable
  (setv #(server clock) (open-service (memory-lease (MemoryStore LAW-SCHEMA))))
  (.close server)
  (setv answer (run-as server clock (get LAW-TOKENS "maker") (ReadRow "parts" #("p1"))))
  (assert (isinstance answer Unreachable) (repr answer)))


(deftest test-a-client-sending-by-the-http-effect-reads-an-unreachable-service-as-a-value
  ;; 送り方は HttpRequest の effect で、届かない口は Unreachable の値で答える(例外で上げない)—
  ;; 処理ループと同じ scheduler の task が読む時に、記録の service の不達で task を落とさないため。
  (val closed (RecordsEndpoint "http://127.0.0.1:9" "t" :request-timeout 2.0))
  (val answer (run (scheduled (with_handlers [(await-handler) (http-production-handler) (sim-time-handler :clock (SimClock))
                                              (http-records-handler closed)]
                                             (ReadRow "parts" #("p1"))))))
  (assert (isinstance answer Unreachable) (repr answer))
  (assert (in "記録の service に届かない" answer.detail) (repr answer)))


(deftest test-a-table-scoped-client-answers-its-tables-and-passes-the-rest-to-the-outer-service
  ;; 記録が表ごとに 2 つの service に在る時: 内側の http-table-records-handler は自分の表(parts)だけを自分の口へ撃ち、他の表(tickets)の
  ;; 読み書きは外側の http-records-handler(もう 1 つの口)へ渡す。束の書きは表が全部自分の表の時だけ答える。
  (val parts-store (MemoryStore LAW-SCHEMA))
  (val tickets-store (MemoryStore LAW-SCHEMA))
  (setv #(parts-server clock) (open-service (memory-lease parts-store)))
  (setv #(tickets-server _) (open-service (memory-lease tickets-store)))
  (val maker (get LAW-TOKENS "maker"))
  (try
    (defn both [program]
      (run (scheduled (with_handlers [(await-handler) (http-production-handler) (sim-time-handler :clock clock)
                                      (http-records-handler (RecordsEndpoint tickets-server.url maker))
                                      (http-table-records-handler (RecordsEndpoint parts-server.url maker) (frozenset ["parts"]))]
                                     program))))
    (assert (isinstance (both (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent))) Written))
    (assert (isinstance (both (PutRow "tickets" #("g" "t1") {"state" "open"} (ExpectAbsent))) Written))
    (assert (isinstance (both (PutRows #((RowWrite "parts" #("p2") {"label" "b"} (ExpectAbsent))))) WrittenRows))
    ;; 各行は自分の表の口の置き場にだけ在る。
    (assert (= (run-as parts-server clock maker (ReadRow "tickets" #("g" "t1"))) (Missing)))
    (assert (= (run-as tickets-server clock maker (ReadRow "parts" #("p1"))) (Missing)))
    (assert (!= (run-as parts-server clock maker (ReadRow "parts" #("p2"))) (Missing)))
    (assert (!= (both (ReadRow "tickets" #("g" "t1"))) (Missing)))
    (finally (.close parts-server) (.close tickets-server))))
