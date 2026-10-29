;; 記録の service の scheduler の thread の仕事の分け(ADR-DOE-RECORDS-002 — _loop_work.hy の頭の註)を確かめる。
;;   - 束の読み(一覧の頁・変更の列・出来事の列)の行の読み解きと、束の読みの答えの綴りは Compute を通る: 行を運ぶ束の読み 1 回は、置き場の
;;     読み解きの Compute 1 つと答えの綴りの Compute 1 つを出す。行を運ばない束(空の頁)と 1 行の読みは Compute を出さない
;;     (置き場は本物の PostgreSQL — 無ければ理由を名指して skip)
;;   - 同じ形を SQL の答えの代役の置き場の上でも確かめ(PostgreSQL の無い機体でも走る)、束の答えは送る byte 列(HttpAnswer の data)まで
;;     thread の外で作る — 行を運ぶ頁の答えだけが data を持ち、空の頁と 1 行の答えは持たない(UTF-8 の変換を scheduler の thread に残す
;;     形を、CPU の秒の差では見分けられない大きさでも型で断る)
;;   - scheduler の thread が一覧の読み 1 回のために使う CPU の秒は、頁の行の数に依らない: 500 行の頁と 5 行の頁の差が、thread の pool で
;;     回す時は要求 1 つあたり LOOP-ALLOWANCE-SECONDS 以下で、その場で回す時の差の 1/10 以下(置き場は SQL の答えの代役 — 頁の行を決まった
;;     綴りで答え、DB の往復を測りに入れない。その場で回す時の差が上限を越える事も確かめ、頁がこの検に足る大きさだと示す)
(require doeff-hy.macros [deftest defeffect defhandler defk <- val var])
(import json)
(import time)
(import collections.abc [Callable])
(import concurrent.futures [ThreadPoolExecutor])
(import doeff [run with_handlers])
;; 束の読みの読み解きと綴りの Compute を、数えながらその場で回す(counted-compute)か、本番と同じ thread の pool で回す。
(import doeff_core_effects [Compute])
(import doeff_core_effects [inline-compute-handler]
        doeff_core_effects._inline_compute [computed])
(import doeff_core_effects [thread-pool-compute-handler])
(import doeff_core_effects [scheduled])
(import doeff_core_effects [state])
(import doeff_core_effects.postgres_sql [postgres-sql-handler])
(import doeff_core_effects.sql_effects [SqlQuery SqlRows])
(import doeff_time [SimClock sim-time-handler])
(import doeff_hy._frozen [FrozenMap])
(import doeff_records [FieldDecl TableDecl RecordsSchema ExpectAbsent])
(import doeff_records [ListRows PutRow AppendEvent])
(import doeff_records._admission [key-text])
(import doeff_records._laws [LAW-SCHEMA MAKER])
(import doeff_records [pg-records-handler]
        doeff_records._pg [IndexBook PreparedStore drop-records-tables])
(import doeff_records._service [HttpAnswer HttpRequest RecordsService respond])
(import tests.interpreters [pg-skip-reason ORIGIN-HOST postgres-connections fresh-prefix run-sql prepared-store])

(val PG-SKIP-REASON (pg-skip-reason))
;; 代役の置き場の表(鍵の欄 id と本文の欄 body だけ)と書き手の名・行に刻む機体の名。
(val NOTES "notes")
(val WRITER "loop-writer")
(val HOST "loop-host")
(val NOTES-SCHEMA (RecordsSchema :tables (FrozenMap {NOTES (TableDecl :name NOTES :key-fields #("id")
                                                                      :fields #((FieldDecl "id" #(WRITER)) (FieldDecl "body" #(WRITER))))})
                                 :streams (FrozenMap {})))
;; 大きな頁と小さな頁の行の数(大きな頁 = 一覧の頁の上限 500 行 — 本番で受けの列を塞いでいた頁の大きさ)と、1 回の run で撃つ要求の数。
(val BIG-ROWS 500)
(val SMALL-ROWS 5)
(val REQUESTS 4)
;; 要求 1 つあたり、scheduler の thread が大きな頁のために小さな頁より多く使ってよい CPU の秒(thread の pool で回す時)。
(val LOOP-ALLOWANCE-SECONDS 0.005)


;; --- 束の読みが出す Compute を数える ---------------------------------------------------------------------------------------------

(defeffect ComputedPrograms
  "counted-compute が今までに回した Compute の program の名(回した順)を読む。"
  {:fields [] :answer tuple :tags {:context "records" :role "foundation"}})


(defhandler counted-compute
  "Compute をその場で回し(inline-compute-handler と同じ答え)、回した program の名を控えて、どの読みが何を scheduler の thread の外へ
   出したかを検が読めるようにするため。"
  {:tags {:context "records" :role "foundation"}}
  ;; names = 回した program の defk の名(回した順・session の値)。
  (session var names #())
  (Compute [program]
    (:= names (+ names #((. program function function __name__))))
    (<- outcome (computed program))
    (resume outcome))
  (ComputedPrograms []
    (resume names)))


(defk served [service path body]
  {:pre [(: service RecordsService) (: path str) (: body dict)] :post [(: % dict)] :tags {:context "records" :role "foundation"}}
  "記録の口の流れ(service の respond)に書き手 MAKER の要求 1 つを撃ち、200 の答えの本文を読むため(200 でなければ赤)。"
  (<- answer (respond service (HttpRequest "POST" path (.encode (json.dumps body) "utf-8") MAKER)))
  (assert (= answer.status 200) answer)
  (json.loads answer.body))


(defk written-parts []
  {:pre [] :post [(: % int)] :tags {:context "records" :role "foundation"}}
  "置き場に parts の行 2 つと journal の出来事 1 つを書き、書く前の置き場の版を返すため(変更の列を頭から読む位置)。"
  (<- start (ListRows "parts"))
  (<- (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent)))
  (<- (PutRow "parts" #("p2") (FrozenMap {"label" "b"}) (ExpectAbsent)))
  (<- (AppendEvent "journal" "j-1" {"n" 1}))
  start.epoch)


(defk counted-reads [service epoch]
  {:pre [(: service RecordsService) (: epoch int)] :post [(: % tuple)] :tags {:context "records" :role "foundation"}}
  "written-parts の置き場に、束の読み(一覧・変更の列・出来事の列)と、行を運ばない束(空の頁)と 1 行の読みを 1 回ずつ撃ち、
   #(答えの kind と運んだ数の列 回した Compute の program の名の列) を返すため。"
  (<- page (served service "/v1/records/list-rows" {"table" "parts"}))
  (<- empty (served service "/v1/records/list-rows" {"table" "parts" "where" {"color" "none"}}))
  (<- changes (served service "/v1/records/watch-changes" {"tables" ["parts"] "cursor" {"epoch" epoch "sequence" 0} "timeout" 0.0}))
  (<- events (served service "/v1/records/read-events" {"stream" "journal"}))
  (<- row (served service "/v1/records/read-row" {"table" "parts" "key" ["p1"]}))
  (<- names tuple (ComputedPrograms))
  #(#((get page "kind") (len (get page "rows")) (get empty "kind") (len (get empty "rows")) (get changes "kind")
      (len (get changes "items")) (get events "kind") (len (get events "items")) (get row "kind"))
    names))


(deftest test-batch-reads-decode-and-spell-their-rows-off-the-scheduler-thread
  {:skip-if (bool PG-SKIP-REASON) :skip-reason PG-SKIP-REASON}
  ;; 束の読み 1 回 = 置き場の読み解きの Compute 1 つ(listed-rows-of・changes-of・events-of)+ 答えの綴りの Compute 1 つ
  ;; (sendable-reply-of)。行を運ばない束と 1 行の読みは Compute を出さない(_loop_work.hy の頭の註)。
  (val connections (postgres-connections))
  (val store (prepared-store connections (fresh-prefix)))
  (val handler-for (fn [writer] (pg-records-handler store writer ORIGIN-HOST)))
  (try
    (val epoch (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) inline-compute-handler (postgres-sql-handler connections)
                                               (handler-for MAKER)]
                                              (written-parts)))))
    (val read (run (scheduled (with_handlers [(state) (sim-time-handler :clock (SimClock)) counted-compute (postgres-sql-handler connections)]
                                             (counted-reads (RecordsService LAW-SCHEMA handler-for) epoch)))))
    (val answers (get read 0))
    (val names (get read 1))
    (assert (= answers #("page" 2 "page" 0 "changes" 2 "events" 1 "row")) answers)
    (assert (= names #("listed_rows_of" "sendable_reply_of" "changes_of" "sendable_reply_of" "events_of" "sendable_reply_of")) names)
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))


;; --- scheduler の thread の CPU の秒は頁の行の数に依らない -------------------------------------------------------------------------

(defhandler scripted-page-sql [#^ tuple records]
  "置き場の SQL の答えの代役: 置き場の頭の読み(store_epoch)には版 1・番号 0 を、ほかの読み(一覧の頁)には決まった頁の行 records を
   答えるため(DB の往復を測りに入れず、scheduler の thread の CPU の秒だけを測る)。"
  {:tags {:context "records" :role "foundation"}}
  ;; 引数に残す理由: records は検ごとの頁の行(大きな頁と小さな頁を同じ答え手で比べる — Ask で読む設定ではない)。
  (SqlQuery [database statement params]
    (resume (SqlRows :rows (if (in "store_epoch" statement) #(#(1 0 0)) records) :rowcount None))))


(defk page-records [count]
  {:pre [(: count int)] :post [(: % tuple)] :tags {:context "records" :role "foundation"}}
  "state_rows の行の形(鍵の綴り・payload の JSON・版)の頁の行を count 個作るため(行の本文は本番の郵便の頁と同じ 2〜3 KB の大きさ —
   日本語の本文と入れ子の object)。"
  (tuple (gfor index (range count)
               :setv id (.format "n{:04d}" index)
               #((key-text #(id))
                 (json.dumps {"id" id "body" {"text" (* "記録の本文の一行。" 80) "tags" (list (range 30)) "nested" {"a" {"b" [1 2 {"c" (* "x" 200)}]}}}}
                             :ensure-ascii False)
                 1))))


(defk scripted-answers [service]
  {:pre [(: service RecordsService)] :post [(: % tuple)] :tags {:context "records" :role "foundation"}}
  "代役の置き場の上の記録の口の流れに、一覧の読みと 1 行の読みを 1 回ずつ撃ち、#(一覧の答え 1 行の答え 回した Compute の program の名の列)を
   返すため。"
  (<- page HttpAnswer (respond service (HttpRequest "POST" "/v1/records/list-rows" (.encode (json.dumps {"table" NOTES}) "utf-8") WRITER)))
  (<- row HttpAnswer (respond service (HttpRequest "POST" "/v1/records/read-row" (.encode (json.dumps {"table" NOTES "key" ["n0000"]}) "utf-8")
                                                  WRITER)))
  (<- names tuple (ComputedPrograms))
  #(page row names))


(defk scripted-run [records]
  {:pre [(: records tuple)] :post [(: % tuple)] :tags {:context "records" :role "foundation"}}
  "頁の行 records を答える代役の置き場の上で scripted-answers を 1 回回すため(Compute は数えながらその場で回す)。"
  (val store (PreparedStore :database "records" :ddl-database "records" :schema NOTES-SCHEMA :prefix "t_" :indexes (IndexBook)))
  (val service (RecordsService NOTES-SCHEMA (fn [writer] (pg-records-handler store writer HOST))))
  (run (scheduled (with_handlers [(state) (sim-time-handler :clock (SimClock)) counted-compute (scripted-page-sql records)]
                                 (scripted-answers service)))))


(deftest test-a-batch-answer-is-spelled-to-its-bytes-off-the-scheduler-thread
  ;; 行を運ぶ頁: 読み解き(listed-rows-of)と綴り(sendable-reply-of)の Compute が 1 つずつで、答えは送る byte 列 data を持つ(送りは
  ;; 綴り直さない)。空の頁と 1 行の読みは Compute を出さず、data を持たない(送る時に body から作る)。
  (<- full tuple (scripted-run (! (page-records SMALL-ROWS))))
  (val page (get full 0))
  (val row (get full 1))
  (val names (get full 2))
  (assert (= names #("listed_rows_of" "sendable_reply_of")) names)
  (assert (= page.status 200) page)
  (assert (= page.data (.encode page.body "utf-8")) page.status)
  (assert (= (len (get (json.loads page.body) "rows")) SMALL-ROWS) page.body)
  (assert (= row.status 200) row)
  (assert (is row.data None) row)
  (<- empty tuple (scripted-run #()))
  (assert (= (get empty 2) #()) (get empty 2))
  (assert (is (. (get empty 0) data) None) (get empty 0)))


(defk listed-pages [service times]
  {:pre [(: service RecordsService) (: times int)] :post [(: % HttpAnswer)] :tags {:context "records" :role "foundation"}}
  "記録の口の流れに一覧の読みを times 回撃ち、最後の答えを返すため(答えの本文の読み解きは検の側の計算なので、測りの後に読む)。"
  (val request (HttpRequest "POST" "/v1/records/list-rows" (.encode (json.dumps {"table" NOTES "limit" BIG-ROWS}) "utf-8") WRITER))
  (var last None)
  (for [_ (range times)]
    (<- answer HttpAnswer (respond service request))
    (assert (= answer.status 200) answer)
    (:= last answer))
  last)


(defk loop-seconds [records compute]
  {:pre [(: records tuple) (: compute Callable)] :post [(: % float)] :tags {:context "records" :role "foundation"}}
  "頁の行 records を答える代役の置き場の上で、一覧の読みを REQUESTS 回撃つ run を、Compute の答え手 compute の下で 1 回回し、その run を
   回した thread(scheduler の thread)が使った CPU の秒を返すため(time.thread_time — 待っている間の秒は数えない)。"
  (val store (PreparedStore :database "records" :ddl-database "records" :schema NOTES-SCHEMA :prefix "t_" :indexes (IndexBook)))
  (val service (RecordsService NOTES-SCHEMA (fn [writer] (pg-records-handler store writer HOST))))
  (val before (time.thread-time))
  (val last (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) compute (scripted-page-sql records)]
                                           (listed-pages service REQUESTS)))))
  (val after (time.thread-time))
  ;; 頁の行が全部答えに出た(測りの外で読む)。
  (assert (= (len (get (json.loads last.body) "rows")) (len records)) last.status)
  (- after before))


(deftest test-the-scheduler-thread-spends-no-more-on-a-page-of-many-rows-than-on-a-page-of-few
  ;; 失敗ケース(本番の 2026-10-22 の切り分け): 頁の行の読み解きと綴りが scheduler の thread で走ると、500 行の頁 1 つで 40〜125 ms その
  ;; thread を塞ぎ、後から届いた要求が受けの列で待つ。thread の pool で回せば、scheduler の thread の CPU の秒の大きな頁と小さな頁の差は
  ;; 要求 1 つあたり LOOP-ALLOWANCE-SECONDS 以下で、その場で回す時の差の 1/10 以下。
  (val big (! (page-records BIG-ROWS)))
  (val small (! (page-records SMALL-ROWS)))
  (val pool (ThreadPoolExecutor :max-workers 2 :thread-name-prefix "loop-work-test"))
  (try
    ;; 1 回目は module の読み込みと温めの分(測りに入れない)。
    (<- _warm float (loop-seconds small (thread-pool-compute-handler pool)))
    (<- inline-big float (loop-seconds big inline-compute-handler))
    (<- inline-small float (loop-seconds small inline-compute-handler))
    (<- pooled-big float (loop-seconds big (thread-pool-compute-handler pool)))
    (<- pooled-small float (loop-seconds small (thread-pool-compute-handler pool)))
    (val inline-growth (- inline-big inline-small))
    (val pooled-growth (- pooled-big pooled-small))
    (print (.format "scheduler の thread の CPU の秒の差(大きな頁 − 小さな頁・要求 {} 本): その場 {:.4f} 秒・pool {:.4f} 秒"
                    REQUESTS inline-growth pooled-growth))
    ;; その場で回すと上限を越える — 頁がこの検に足る大きさ(検が壊れた形を見分けられる)。
    (assert (> inline-growth (* REQUESTS LOOP-ALLOWANCE-SECONDS)) inline-growth)
    (assert (<= pooled-growth (* REQUESTS LOOP-ALLOWANCE-SECONDS)) #(pooled-growth inline-growth))
    (assert (<= pooled-growth (/ inline-growth 10)) #(pooled-growth inline-growth))
    (finally
      (.shutdown pool))))
