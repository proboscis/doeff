;; 出来事を冪等キーで 1 つ引く読み ReadEventByKey(#3750): 列を頭から読まずに、置き場の (列, 冪等キー) の一意の索引で答える。
;;   - 法(doeff_records._laws の law-event-by-key-reads-the-same-event)を memory・PostgreSQL・HTTP の口越し(送り方 2 つ・置き場 2 つ)で回す
;;     — 列の読みと同じ出来事・積んでいない鍵と別の列の鍵は EventAbsent・再送で変わらない・保持の期限を過ぎた鍵は回収の前も後も EventRetired。
;;   - HTTP の口越しの読みは、列の長さ(短い列と長い列)に依らず要求ちょうど 1 つで答える(使い手の前の形 = 列を頭から頁で読み切る読みは、
;;     列の長さに応じて要求が増えた)。PostgreSQL の文が読む行の数が置き場の大きさに依らない事は test_pg_index_reads.hy。
;;   - 置き場に届かない時は Unreachable(memory の置き場の不達・一部の断り、HTTP の口の 503)。
(require doeff-hy.macros [deftest defhandler defk <- val])
(import pytest)
(import doeff [run with_handlers])
(import doeff.result [Nothing Some])
(import doeff [Err Ok])
(import doeff_core_effects [maybe result])
(import doeff_core_effects [scheduled])
(import doeff_core_effects [await-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.http_effects [HttpRequest])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_records [Event EventAbsent EventRetired Unreachable Appended])
(import doeff_records [AppendEvent ReadEventByKey])
(import doeff_records [SetStoreOutage StoreFault StoreOperation AddStoreFault])
(import doeff_records._laws [LAW-SCHEMA MAKER PULSE-KEEP-SECONDS law-event-by-key-reads-the-same-event])
(import doeff_records.http_client [records-unwaited])
(import doeff_records [memory-records-handler]
        doeff_records._memory [MemoryStore])
(import doeff_records._http_server [records-server-config start-records-server])
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
    (run (scheduled (with_handlers [await-handler (http-production-handler) (count-http-requests sent)
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


(defk opened-read [stream key]
  {:pre [(: stream str) (: key str)] :post [(: % (| Ok Err))]
   :tags {:context "records" :role "foundation"}}
  "鍵の読みを宣言どおりに開き、不在は Nothing・失敗は Err に畳んで返すため(maybe を内側・result を外側 — R2 の入れ子の順)。"
  (<- opened (result (maybe (ReadEventByKey stream key))))
  opened)


(defk read-opened-in-all-shapes []
  {:pre [] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "pulses(出来事ごとに PULSE-KEEP-SECONDS 秒で消える列)に積んだ鍵を、在る時・保持の期限を過ぎた後・積んでいない鍵で、開いて読むため。"
  (<- appended (AppendEvent "pulses" "p1" {"n" 1}))
  (<- found (ReadEventByKey "pulses" "p1"))
  (<- present (opened-read "pulses" "p1"))
  (<- absent (opened-read "pulses" "p-never"))
  (<- (Delay (+ PULSE-KEEP-SECONDS 1)))
  (<- retired (opened-read "pulses" "p1"))
  #(appended found present absent retired))


(deftest test-read-event-by-key-is-opened-by-its-declaration
  ;; 宣言(:absent [EventAbsent]・:failure [Unreachable]・:value [EventRetired])どおり、本文の中の <- は不在を Absent に・失敗を Raise に変える
  ;; (maybe が Absent を Nothing に、result が Raise を Err に畳む)。成功の Event と値の EventRetired は束ねた答えのまま。
  ;; 答える handler は今までどおり値で答える(R4)— 上の test-read-event-by-key-is-unreachable-when-the-store-is-down は effect を直に撃つので、
  ;; 開かれない値(Unreachable・EventAbsent)をそのまま受ける。
  (val store (MemoryStore LAW-SCHEMA))
  (setv #(appended found present absent retired) (in-store store (read-opened-in-all-shapes)))
  (assert (isinstance appended Appended) (repr appended))
  (assert (and (isinstance found Event) (= found.sequence appended.sequence)) (repr found))
  (assert (= (repr retired) (repr (Ok (Some (EventRetired appended.sequence))))) (repr retired))
  (assert (= (repr absent) (repr (Ok Nothing))) (repr absent))
  (assert (= (repr present) (repr (Ok (Some found)))) (repr present))
  ;; 失敗: 置き場に届かない間は Err(Unreachable)。
  (in-store store (SetStoreOutage DETAIL))
  (assert (= (repr (in-store store (opened-read "pulses" "p1"))) (repr (Err (Unreachable DETAIL)))))
  ;; 作る時の検め: 列の名と冪等キーは空にできない(defeffect の :pre に移しても ValueError のまま)。
  (for [#(stream key) #(#("" "k1") #("journal" "") #("journal" None))]
    (with [(pytest.raises ValueError)]
      (ReadEventByKey stream key))))
