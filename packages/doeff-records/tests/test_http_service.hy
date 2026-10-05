;; 記録の service の HTTP の口: 呼び手を断らない(名簿に無い token・token 無しの呼び手は anonymous の書き手として通り、書きも通る —
;; 置き場は書き手の名では断らない・X-Records-Writer で名乗れば token 無しでその名で書く — #2988・#2994)・断りの status と本文・宣言に無い表・置き場に届かない時の 503。置き場は memory(仮想の時計)— PostgreSQL の上の口は test_laws / test_parity の http-pg が確かめる。
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
(import doeff_records.values [Appended ExpectAbsent ExpectAny Missing Unreachable UndeclaredTable Written WrittenRows WatchCursor])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows RowWrite WatchChanges AppendEvent ReadEvents])
(import doeff_records.laws [LAW-SCHEMA])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.http_client [records-unwaited])
(import doeff_records.http_server [records-server-config RunningServer start-records-server])
(import doeff_records.http_client [RecordsEndpoint WireError http-records-handler http-table-records-handler])
(import tests.interpreters [sim-request-handlers])


(defn open-service [handler-for]
  "置き場の上に口を開く(検ごと)。handler-for = 書き手の名 → 置き場の handler。答え = #(開いた口 仮想の時計)。"
  (setv clock (SimClock))
  #((start-records-server (run (records-server-config LAW-SCHEMA handler-for :request-handlers (sim-request-handlers clock))))
    clock))


(defn memory-lease [store]
  "書き手の名 → memory の handler。"
  (fn [writer] (memory-records-handler store writer)))


(defn run-as [server clock #^ (| str None) writer program]
  "writer を名乗る client の handler で Program を走らせる(None = 名乗らない — anonymous の書き手)。client は token を送らない(#2986)。"
  (run (scheduled (with_handlers [(await-handler) (http-production-handler) (sim-time-handler :clock clock)
                                  records-unwaited (http-records-handler (RecordsEndpoint server.url :writer writer))]
                                 program))))


(defn raw [server #^ str method #^ str path * [body None] [token None] [writer None]]
  "口へ生の要求を送り、#(status 本文の JSON) を返す(client の handler を通さない断りの形の検のため)。writer = X-Records-Writer の名乗り。"
  (setv headers {"Content-Type" "application/json"})
  (when token (setv (get headers "Authorization") (+ "Bearer " token)))
  (when writer (setv (get headers "X-Records-Writer") writer))
  (setv request (Request (+ server.url path) :method method :headers headers
                         :data (if (is body None) None (if (isinstance body bytes) body (.encode (json.dumps body) "utf-8")))))
  (try
    (with [response (urlopen request)] #(response.status (json.loads (.read response))))
    (except [error HTTPError] #(error.code (json.loads (.read error))))))


(deftest test-an-unnamed-caller-is-served-as-anonymous-and-writes
  ;; 呼び手を断らない(#2988): 名乗らない呼び手の読みは答えの値になり(例外を上げない)、書きも anonymous の書き手として
  ;; 通る(置き場は書き手の名では断らない — #2994)。口が 401 で断る形・置き場が書き手で断る形に戻ると赤。
  (val store (MemoryStore LAW-SCHEMA))
  (val opened (open-service (memory-lease store)))
  (val server (get opened 0))
  (val clock (get opened 1))
  (val maker "maker")
  (val stranger None)
  (try
    (assert (= (run-as server clock stranger (ReadRow "parts" #("p1"))) (Missing)))
    (assert (= (. (run-as server clock stranger (ListRows "parts")) rows) #()))
    (assert (= (. (run-as server clock stranger (ReadEvents "journal")) items) #()))
    (assert (isinstance (run-as server clock stranger (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent))) Written))
    (assert (isinstance (run-as server clock stranger (AppendEvent "journal" "k1" {"n" 1})) Appended))
    ;; 書いた行と出来事は名簿に在る書き手からも読める。
    (assert (= (get (. (run-as server clock maker (ReadRow "parts" #("p1"))) value) "label") "a"))
    (assert (= (len (. (run-as server clock maker (ReadEvents "journal")) items)) 1))
    (finally (.close server))))


(deftest test-a-writer-named-by-the-header-writes-without-a-token
  ;; token 無しの呼び手は X-Records-Writer で名乗った名で書く(名は確かめない — #2988)。名乗らず token も無ければ anonymous で、書きも
  ;; 通る(status は 200・401 ではない・置き場は書き手の名では断らない — #2994)。
  (val store (MemoryStore LAW-SCHEMA))
  (val opened (open-service (memory-lease store)))
  (val server (get opened 0))
  (val clock (get opened 1))
  (val maker "maker")
  (try
    (val unnamed (raw server "POST" "/v1/records/put-row"
                      :body {"table" "parts" "key" ["p1"] "value" {"label" "a"} "expect" {"kind" "absent"}}))
    (assert (= #((get unnamed 0) (.get (get unnamed 1) "kind")) #(200 "written")) (repr unnamed))
    (val named (raw server "POST" "/v1/records/put-row"
                    :body {"table" "parts" "key" ["p2"] "value" {"label" "b"} "expect" {"kind" "absent"}} :writer "maker"))
    (assert (= #((get named 0) (.get (get named 1) "kind")) #(200 "written")) (repr named))
    (assert (= (get (. (run-as server clock maker (ReadRow "parts" #("p2"))) value) "label") "b"))
    (finally (.close server))))


(deftest test-a-token-alone-names-no-writer
  ;; Authorization の token だけを送った要求の書き手は anonymous(token を名簿で引かない — #3008): 口が handler を組む時に渡した書き手の名を
  ;; 控え、token だけの要求は anonymous・名乗りが在ればその名と分かる。token から名を引く形に戻ると anonymous でなくなり赤。
  (val store (MemoryStore LAW-SCHEMA))
  (val seen [])
  (val opened (open-service (fn [writer] (.append seen writer) (memory-records-handler store writer))))
  (val server (get opened 0))
  (try
    (val body {"table" "parts" "key" ["t1"] "value" {"label" "a"} "expect" {"kind" "any"}})
    (assert (= (get (raw server "POST" "/v1/records/put-row" :body body :token "any-token") 0) 200))
    (assert (= seen ["anonymous"]) seen)
    (assert (= (get (raw server "POST" "/v1/records/put-row" :body body :token "any-token" :writer "maker") 0) 200))
    (assert (= seen ["anonymous" "maker"]) seen)
    (finally (.close server))))


(defk status-and-error [reply]
  {:pre [(: reply tuple)] :post [(: % tuple)]}
  "raw の答え #(status 本文) を #(status 断りの語) にする(断りの形の比べを 1 行にするため)。"
  #((get reply 0) (.get (get reply 1) "error")))


(deftest test-a-put-rows-by-a-caller-outside-the-roster-writes-the-rows
  ;; 名乗らない呼び手と名簿に無い token の束: 口は断らず(401 を出さない・#2988)、anonymous の書き手の束も書かれる(置き場は書き手の名では断らない — #2994)。
  (val store (MemoryStore LAW-SCHEMA))
  (val opened (open-service (memory-lease store)))
  (val server (get opened 0))
  (val clock (get opened 1))
  (val writes #((RowWrite "parts" #("p1") {"label" "a"} (ExpectAbsent)) (RowWrite "parts" #("p2") {"label" "b"} (ExpectAbsent))))
  (val maker "maker")
  (try
    (assert (isinstance (run-as server clock None (PutRows writes)) WrittenRows))
    (assert (= (get (. (run-as server clock maker (ReadRow "parts" #("p2"))) value) "label") "b"))
    (val raw-rows (raw server "POST" "/v1/records/put-rows"
                       :body {"writes" [{"table" "parts" "key" ["p3"] "value" {"label" "c"} "expect" {"kind" "absent"}}]}
                       :token "nope"))
    (assert (= #((get raw-rows 0) (.get (get raw-rows 1) "kind")) #(200 "writtenRows")) (repr raw-rows))
    (finally (.close server))))


(defclass FixedStatusPage [BaseHTTPRequestHandler]
  "記録の service との間に立つ口(proxy)の代役: どの要求にも server.status と HTML の本文 server.payload で答える(間の口の本文は、
   記録の service の JSON の断りとは限らない)。"
  (deff do-POST [self]  ; defk にできない: http.server が要求ごとの thread で呼ぶ素の method
    {:pre [(: self FixedStatusPage)] :post [(: % None)] :tags {:context "records" :role "foundation"}}
    (.read self.rfile (int (.get self.headers "Content-Length" "0")))
    (.send-response self self.server.status)
    (.send-header self "Content-Type" "text/html")
    (.send-header self "Content-Length" (str (len self.server.payload)))
    (.end-headers self)
    (.write self.wfile self.server.payload)
    None)
  (deff log-message [self #* args]  ; defk にできない: http.server が要求ごとに呼ぶ log の口(検の出力を要求の行で埋めない)
    {:pre [(: self FixedStatusPage) (: args tuple)] :post [(: % None)] :tags {:context "records" :role "foundation"}}
    None))


(defk fixed-status-server [status payload]
  {:pre [(: status int) (: payload bytes)] :post [(: % ThreadingHTTPServer)] :tags {:context "records" :role "foundation"}}
  "間に立つ口の代役(127.0.0.1 の空き port)を立てるため: どの要求にも status と HTML の本文 payload で答える。"
  (val server (ThreadingHTTPServer #("127.0.0.1" 0) FixedStatusPage))
  (setv server.status status server.payload payload)
  (.start (threading.Thread :target server.serve-forever :daemon True))
  server)


(deftest test-a-401-or-403-is-the-general-failure
  ;; 401 / 403 は他の 4xx と同じ一般の失敗: 本文が JSON でなければ WireError(status ごとの枝も、身元の断りの名の付いた例外も持たない —
  ;; 頼まれていない security の名残を外した #2986)。読みも書きも同じ。401 / 403 だけを別の例外(WireError の子を含む)や答えの値
  ;; (Unreachable・Refused)へ写す枝が戻ると赤。
  (val page b"<html><body>refused</body></html>")
  (for [status [401 403]]
    (val server (! (fixed-status-server status page)))
    (val url (+ "http://127.0.0.1:" (str (get server.server-address 1))))
    (try
      (do
        (val endpoint (RecordsEndpoint url :request-timeout 5.0))
        (for [ask [(ReadRow "parts" #("p1")) (PutRow "parts" #("p1") {"label" "a"} (ExpectAny))]]
          (var raised None)
          (try
            (run (scheduled (with_handlers [(await-handler) (http-production-handler) (sim-time-handler :clock (SimClock))
                                            records-unwaited (http-records-handler endpoint)]
                                           ask)))
            (except [error WireError]
              (:= raised error)))
          (assert (is (type raised) WireError) (.format "status {} の {} が一般の失敗にならない: {!r}" status ask raised))
          (assert (in (.format "(status {})" status) (str raised)) (str raised))))
      (finally (.shutdown server) (.server-close server)))))



(deftest test-put-rows-refusals-carry-the-contract-status
  ;; 束の形の誤り(同じ鍵が 2 度・空の束)は 400、宣言に無い表を名指す束は 404(client は UndeclaredTable を上げる)で、どれも 1 行も書かない。
  (val store (MemoryStore LAW-SCHEMA))
  (val opened (open-service (memory-lease store)))
  (val server (get opened 0))
  (val clock (get opened 1))
  (val maker "maker")
  (val one {"table" "parts" "key" ["p1"] "value" {"label" "a"} "expect" {"kind" "any"}})
  (try
    (val twice (raw server "POST" "/v1/records/put-rows" :body {"writes" [one one]} :writer maker))
    (assert (= (! (status-and-error twice)) #(400 "malformed")) (repr twice))
    (val empty (raw server "POST" "/v1/records/put-rows" :body {"writes" []} :writer maker))
    (assert (= (! (status-and-error empty)) #(400 "malformed")) (repr empty))
    (val undeclared (raw server "POST" "/v1/records/put-rows"
                         :body {"writes" [one {"table" "nothing" "key" ["x"] "value" {} "expect" {"kind" "any"}}]} :writer maker))
    (assert (= (! (status-and-error undeclared)) #(404 "not-found")) (repr undeclared))
    (try
      (run-as server clock "maker" (PutRows #((RowWrite "parts" #("p1") {"label" "a"} (ExpectAny))
                                            (RowWrite "nothing" #("x") {} (ExpectAny)))))
      (assert False "宣言に無い表を名指す束が答えを返した")
      ;; 欄は束が名指した表のうち宣言に無い物だけ。
      (except [refused UndeclaredTable]
        (assert (= #(refused.tables refused.streams) #(#("nothing") #())) refused)))
    (assert (= (run-as server clock "maker" (ReadRow "parts" #("p1"))) (Missing)))
    (finally (.close server))))


(deftest test-refusals-carry-the-contract-status-and-body
  (setv #(server clock) (open-service (memory-lease (MemoryStore LAW-SCHEMA)))
        maker "maker")
  (try
    (assert (= (raw server "GET" "/healthz") #(200 {"status" "ok"})))
    ;; 呼び手は断らない(#2988): token 無し・名簿に無い token の読みも 200 の答え。
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body {"table" "parts" "key" ["p1"]}))
    (assert (and (= status 200) (= (get body "kind") "missing")) (repr body))
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body {"table" "parts" "key" ["p1"]} :token "nope"))
    (assert (and (= status 200) (= (get body "kind") "missing")) (repr body))
    (setv #(status body) (raw server "POST" "/v1/records/nothing" :body {} :writer maker))
    (assert (and (= status 404) (= (get body "error") "not-found")) (repr body))
    (setv #(status body) (raw server "GET" "/v1/records/read-row" :writer maker))
    (assert (and (= status 400) (= (get body "error") "malformed")) (repr body))
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body b"{not json" :writer maker))
    (assert (and (= status 400) (= (get body "error") "malformed")) (repr body))
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body {"table" "parts" "key" ["p1"] "extra" 1} :writer maker))
    (assert (and (= status 400) (= (get body "error") "malformed")) (repr body))
    (setv #(status body) (raw server "POST" "/v1/records/put-row"
                              :body {"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "maybe"}} :writer maker))
    (assert (and (= status 400) (= (get body "error") "malformed")) (repr body))
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body {"table" "nothing" "key" ["p1"]} :writer maker))
    (assert (and (= status 404) (= (get body "error") "not-found")) (repr body))
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body {"table" "parts" "key" ["p1"]} :writer maker))
    (assert (= #(status body) #(200 {"kind" "missing"})) (repr body))
    (finally (.close server))))


(deftest test-an-undeclared-table-is-a-composition-error-on-the-client
  (setv #(server clock) (open-service (memory-lease (MemoryStore LAW-SCHEMA))))
  (try
    (try
      (run-as server clock "maker" (ReadRow "nothing" #("p1")))
      (assert False "宣言に無い表の読みが答えを返した")
      ;; client は断りの欄に宣言に無い表の名を入れる(memory の置き場と同じ — 使い手が欄で照らす)。
      (except [refused UndeclaredTable]
        (assert (= #(refused.tables refused.streams) #(#("nothing") #())) refused)))
    (finally (.close server))))


(defhandler unreachable-store []
  (ReadRow [table key] (resume (Unreachable "置き場が落ちている(検の代役)")))
  (PutRow [table key value expect] (resume (Unreachable "置き場が落ちている(検の代役)"))))


(deftest test-an-unreachable-store-answers-503-and-the-client-sees-unreachable
  (setv #(server clock) (open-service (fn [writer] (unreachable-store)))
        maker "maker")
  (try
    (setv #(status body) (raw server "POST" "/v1/records/read-row" :body {"table" "parts" "key" ["p1"]} :writer maker))
    (assert (and (= status 503) (= (get body "error") "store-unavailable")) (repr body))
    ;; 読みの 503 も今までどおり Unreachable の値(時間で晴れる届かなさ — 身元の断りとは別の答え)。
    (assert (= (run-as server clock "maker" (ReadRow "parts" #("p1"))) (Unreachable "置き場が落ちている(検の代役)")))
    (assert (= (run-as server clock "maker" (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent)))
               (Unreachable "置き場が落ちている(検の代役)")))
    (finally (.close server))))


(deftest test-a-closed-service-is-unreachable
  (setv #(server clock) (open-service (memory-lease (MemoryStore LAW-SCHEMA))))
  (.close server)
  (setv answer (run-as server clock "maker" (ReadRow "parts" #("p1"))))
  (assert (isinstance answer Unreachable) (repr answer)))


(deftest test-a-client-sending-by-the-http-effect-reads-an-unreachable-service-as-a-value
  ;; 送り方は HttpRequest の effect で、届かない口は Unreachable の値で答える(例外で上げない)—
  ;; 処理ループと同じ scheduler の task が読む時に、記録の service の不達で task を落とさないため。
  (val closed (RecordsEndpoint "http://127.0.0.1:9" :request-timeout 2.0))
  (val answer (run (scheduled (with_handlers [(await-handler) (http-production-handler) (sim-time-handler :clock (SimClock))
                                              records-unwaited (http-records-handler closed)]
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
  (val maker "maker")
  (try
    (defn both [program]
      (run (scheduled (with_handlers [(await-handler) (http-production-handler) (sim-time-handler :clock clock)
                                      records-unwaited (http-records-handler (RecordsEndpoint tickets-server.url :writer maker))
                                      (http-table-records-handler (RecordsEndpoint parts-server.url :writer maker) (frozenset ["parts"]))]
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
