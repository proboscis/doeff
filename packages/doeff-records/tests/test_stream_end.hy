;; 列の末尾の読み ReadStreamEnd: 列の最後の出来事の番号を 1 回で答え、空の列は StreamEmpty で答える。
;;   - 法(doeff_records.laws の law-stream-end-is-the-last-sequence)を memory・PostgreSQL・HTTP の口越し(送り方 2 つ・置き場 2 つ)で回す
;;     — 空の列・積んだ後・別の列を数えない・再送で動かない・保持で一部 / 全部刈った後。
;;   - HTTP の口越しの読みは要求 1 つで答える(使い手の前の形 = ReadEvents の倍々の先読みと二分は、列の長さに応じて要求が増えた)。
;;   - 置き場に届かない時は Unreachable(memory の置き場の不達・一部の断り、HTTP の口の 503)。
(require doeff-hy.macros [deftest defhandler defk <- val])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.http_effects [HttpRequest])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.values [StreamEnd StreamEmpty Unreachable Appended])
(import doeff_records.effects [AppendEvent ReadStreamEnd])
(import doeff_records.faults [SetStoreOutage StoreFault StoreOperation AddStoreFault])
(import doeff_records.laws [LAW-SCHEMA MAKER law-stream-end-is-the-last-sequence])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.http_server [RecordsServerConfig start-records-server])
(import doeff_records.http_client [RecordsEndpoint http-records-handler])
(import tests.interpreters [LAW-TOKENS LawSetup law-roster sim-request-handlers])

(val DETAIL "記録の service が落ちている(筋書き)")
(val EVENT-COUNT 40)


(deftest test-stream-end-law-holds-on-every-store
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-stream-end-is-the-last-sequence harness))
  (assert transcript))


(defn in-store [#^ MemoryStore store program]  ; defk にできない: 検の入口で run を撃つ
  "仮想の時計と memory の置き場(書き手 MAKER)の下で program を回して答えを返す。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defk append-journal [count]
  {:pre [(: count int)] :post [(: % list)]
   :tags {:context "records" :role "foundation"}}
  "journal へ count 個を積み、答えの番号を返す(列の長さに読みの回数が応じないことを見るための長い列を作るため)。"
  (val sequences [])
  (for [i (range count)]
    (<- appended (AppendEvent "journal" (.format "k{}" i) {"n" i}))
    (.append sequences appended.sequence))
  sequences)


(defhandler count-http-requests [#^ list sent]
  ;; 引数に残す理由: 送った要求の URL を検の側が読む入れ物(検ごとに別)。
  "client が送った HTTP の要求の URL を sent に積み、要求は外側の答え手へそのまま渡すため(送った要求の数を数える)。"
  (HttpRequest []
    (.append sent effect.url)
    (<- answer effect)
    (resume answer)))


(deftest test-read-stream-end-over-http-sends-one-request
  ;; 長い列(EVENT-COUNT 個)でも、列の末尾の読みは口への要求ちょうど 1 つ(read-stream-end)で答える。
  (val store (MemoryStore LAW-SCHEMA))
  (val sequences (in-store store (append-journal EVENT-COUNT)))
  (val clock (SimClock))
  (val server (start-records-server (RecordsServerConfig LAW-SCHEMA (law-roster) (fn [writer] (memory-records-handler store writer))
                                                         :request-handlers (sim-request-handlers clock))))
  (val sent [])
  (try
    (val endpoint (RecordsEndpoint server.url (get LAW-TOKENS MAKER)))
    (val answer (run (scheduled (with_handlers [(await-handler) (http-production-handler) (count-http-requests sent)
                                                (sim-time-handler :clock clock) (http-records-handler endpoint)]
                                               (ReadStreamEnd "journal")))))
    (assert (= answer (StreamEnd (get sequences -1))) (repr answer))
    (assert (= (len sent) 1) (repr sent))
    (assert (.endswith (get sent 0) "/v1/records/read-stream-end") (repr sent))
    (finally (.close server))))


(deftest test-read-stream-end-is-unreachable-when-the-store-is-down
  ;; memory の置き場: 置き場全体の不達(SetStoreOutage)と、読みの断り(AddStoreFault の READ)のどちらも Unreachable で答える。
  (val store (MemoryStore LAW-SCHEMA))
  (assert (isinstance (in-store store (AppendEvent "journal" "k1" {"n" 1})) Appended))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (ReadStreamEnd "journal")) (Unreachable DETAIL)))
  (in-store store (SetStoreOutage None))
  (in-store store (AddStoreFault (StoreFault :names (frozenset ["journal"]) :operation StoreOperation.READ
                                             :answer (Unreachable DETAIL))))
  (assert (= (in-store store (ReadStreamEnd "journal")) (Unreachable DETAIL)))
  ;; 名の違う列は断られない(空の列は空と答える)。
  (assert (= (in-store store (ReadStreamEnd "pairs")) (StreamEmpty))))


(deftest test-read-stream-end-over-http-is-unreachable-when-the-store-is-down
  ;; HTTP の口: 口の奥の置き場に届かなければ 503 store-unavailable で、client は Unreachable の値で答える(例外で上げない)。
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (SetStoreOutage DETAIL))
  (val clock (SimClock))
  (val server (start-records-server (RecordsServerConfig LAW-SCHEMA (law-roster) (fn [writer] (memory-records-handler store writer))
                                                         :request-handlers (sim-request-handlers clock))))
  (try
    (val answer (run (scheduled (with_handlers [(await-handler) (http-production-handler) (sim-time-handler :clock clock)
                                                (http-records-handler (RecordsEndpoint server.url (get LAW-TOKENS MAKER)))]
                                               (ReadStreamEnd "journal")))))
    (assert (= answer (Unreachable DETAIL)) (repr answer))
    (finally (.close server))))
