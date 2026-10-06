;; 出来事を冪等キーで 1 つ引く読み ReadEventByKey(#3750): 列を頭から読まずに、置き場の (列, 冪等キー) の一意の索引で答える。
;;   - 法(doeff_records.laws の law-event-by-key-reads-the-same-event)を memory・PostgreSQL・HTTP の口越し(送り方 2 つ・置き場 2 つ)で回す
;;     — 列の読みと同じ出来事・積んでいない鍵と別の列の鍵は EventAbsent・再送で変わらない・保持の期限を過ぎた鍵は回収の前も後も EventRetired。
;;   - HTTP の口越しの読みは、列の長さ(短い列と長い列)に依らず要求ちょうど 1 つで答える(使い手の前の形 = 列を頭から頁で読み切る読みは、
;;     列の長さに応じて要求が増えた)。PostgreSQL の文が読む行の数が置き場の大きさに依らない事は test_pg_index_reads.hy。
;;   - 置き場に届かない時は Unreachable(memory の置き場の不達・一部の断り、HTTP の口の 503)。
(require doeff-hy.macros [deftest defhandler defk <- val])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.http_effects [HttpRequest])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.values [Event EventAbsent Unreachable Appended])
(import doeff_records.effects [AppendEvent ReadEventByKey])
(import doeff_records.faults [SetStoreOutage StoreFault StoreOperation AddStoreFault])
(import doeff_records.laws [LAW-SCHEMA MAKER law-event-by-key-reads-the-same-event])
(import doeff_records.http_client [records-unwaited])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.http_server [records-server-config start-records-server])
(import doeff_records.http_client [RecordsEndpoint http-records-handler])
(import tests.interpreters [LawSetup sim-request-handlers])

(val DETAIL "記録の service が落ちている(筋書き)")
;; 短い列と長い列の出来事の数(長い列は列の読みの 1 頁 = 1000 個を越える)。
(val SHORT-COUNT 10)
(val LONG-COUNT 1200)


(deftest test-event-by-key-law-holds-on-every-store
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-event-by-key-reads-the-same-event harness))
  (assert transcript))


(defn in-store [#^ MemoryStore store program]  ; defk にできない: 検の入口で run を撃つ
  "仮想の時計と memory の置き場(書き手 MAKER)の下で program を回して答えを返す。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)] program))))


(defk append-journal [count]
  {:pre [(: count int)] :post [(: % list)]
   :tags {:context "records" :role "foundation"}}
  "journal へ count 個を積み、答えの番号を返す(列の長さに読みの回数が応じないことを見るための列を作るため)。"
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


(defn read-over-http [#^ MemoryStore store #^ list sent ask]  ; defk にできない: 検の入口で記録の service を起こして run を撃つ
  "memory の置き場を口の奥に置いた記録の service を起こし、client の handler で ask を 1 つ撃って答えを返すため(送った要求の URL は sent へ)。"
  (setv clock (SimClock))
  (setv server (start-records-server (run (records-server-config LAW-SCHEMA (fn [writer] (memory-records-handler store writer))
                                                         :request-handlers (sim-request-handlers clock)))))
  (try
    (run (scheduled (with_handlers [(await-handler) (http-production-handler) (count-http-requests sent)
                                    (sim-time-handler :clock clock) records-unwaited
                                    (http-records-handler (RecordsEndpoint server.url :writer MAKER))]
                                   ask)))
    (finally (.close server))))


(defn reads-on-journal [#^ int count]  ; defk にできない: 検の入口で記録の service を起こして run を撃つ
  "journal に count 個を積んだ置き場で、在る鍵と無い鍵を口越しに 1 回ずつ引くため。答え = #(積んだ番号 在る鍵の答え その要求 無い鍵の答え その要求)。"
  (setv store (MemoryStore LAW-SCHEMA))
  (setv sequences (in-store store (append-journal count)))
  (setv sent [])
  (setv found (read-over-http store sent (ReadEventByKey "journal" "k3")))
  (setv absent-sent [])
  (setv absent (read-over-http store absent-sent (ReadEventByKey "journal" "k-never")))
  #(sequences found sent absent absent-sent))


(deftest test-read-event-by-key-over-http-sends-one-request-whatever-the-stream-length
  ;; 短い列でも長い列(列の読みの 1 頁を越える)でも、鍵の読みは口への要求ちょうど 1 つ(read-event-by-key)で、積んだ出来事を答える。
  (val short (reads-on-journal SHORT-COUNT))
  (val long (reads-on-journal LONG-COUNT))
  (for [#(sequences found sent absent absent-sent) #(short long)]
    (assert (and (isinstance found Event) (= found.sequence (get sequences 3)) (= found.idempotency-key "k3")) (repr found))
    (assert (and (= (len sent) 1) (.endswith (get sent 0) "/v1/records/read-event-by-key")) (repr sent))
    (assert (and (= absent (EventAbsent)) (= (len absent-sent) 1)) (repr #(absent absent-sent)))))


(deftest test-read-event-by-key-is-unreachable-when-the-store-is-down
  ;; memory の置き場: 置き場全体の不達(SetStoreOutage)と、読みの断り(AddStoreFault の READ)のどちらも Unreachable で答える。
  (val store (MemoryStore LAW-SCHEMA))
  (assert (isinstance (in-store store (AppendEvent "journal" "k1" {"n" 1})) Appended))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (in-store store (ReadEventByKey "journal" "k1")) (Unreachable DETAIL)))
  (in-store store (SetStoreOutage None))
  (in-store store (AddStoreFault (StoreFault :names (frozenset ["journal"]) :operation StoreOperation.READ
                                             :answer (Unreachable DETAIL))))
  (assert (= (in-store store (ReadEventByKey "journal" "k1")) (Unreachable DETAIL)))
  ;; 名の違う列は断られない(積んでいない鍵は EventAbsent)。
  (assert (= (in-store store (ReadEventByKey "pairs" "ask:k1")) (EventAbsent))))


(deftest test-read-event-by-key-over-http-is-unreachable-when-the-store-is-down
  ;; HTTP の口: 口の奥の置き場に届かなければ 503 store-unavailable で、client は Unreachable の値で答える(例外で上げない)。
  (val store (MemoryStore LAW-SCHEMA))
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (read-over-http store [] (ReadEventByKey "journal" "k1")) (Unreachable DETAIL))))
