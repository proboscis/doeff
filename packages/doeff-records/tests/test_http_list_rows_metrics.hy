;; 記録の service の一覧の読みの数(GET /metrics の records_list_rows_* — _http_server.hy の頭の註の一覧の読みの数)を、本物の待ち受け
;; (127.0.0.1 の空き port)に doeff の HTTP の effect で要求を送って確かめる。置き場は共有の memory。
;;   - 宣言の表ごとの系列(呼びの数と返した行の数)は起動の時から 0 で在る
;;   - 宣言された呼び手が名乗った一覧の読み 1 回で、表ごとと呼び手 × 表ごとの呼びの数が 1・行の数が返した行の数だけ増える
;;   - 名乗りの無い要求は unnamed・宣言に無い名乗りは undeclared に数え、名乗りの綴りで系列を増やさない
;;   - 宣言に無い表の要求(404)と書きの要求は一覧の読みに数えない
;; 系列の名の閉じた組(語へ畳んだ名の重なり・予約の語)は wire の list-rows-metrics を直に確かめる。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import pytest)
(import doeff [run with_handlers])
(import doeff_core_effects [await-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.http_effects [HttpRequest])
(import doeff_time [SimClock])
(import doeff_records._laws [LAW-SCHEMA MAKER])
(import doeff_records [MemoryStore memory-records-handler])
(import doeff_records._wire [CALLER-HEADER WRITER-HEADER list-rows-metrics])
(import doeff_records._http_server [records-server-config start-records-server])
(import tests.interpreters [sim-request-handlers])

;; 宣言する呼び手の名(語に畳むと reader_job)と、宣言に無い名乗り。
(val READER "reader-job")
(val STRANGER "stranger-job")
;; 筋書きが parts に書く行の数(一覧の読み 1 回が返す行の数)。
(val PART-COUNT 3)


(defrecord ListRun
  "筋書きを 1 回走らせた読み: before = 何もしない前の /metrics の本文・after = 一覧の読みと断りと書きの後の本文・statuses = 送った
   一覧の読みの答えの status(宣言の呼び手・名乗り無し・宣言に無い名乗り・宣言に無い表の順)。"
  (#^ str before)
  (#^ str after)
  (#^ (get tuple #(int ...)) statuses))


(defk scrape [url]
  {:pre [(: url str)] :post [(: % str)] :tags {:context "records" :role "foundation"}}
  "GET /metrics の本文を読むため(200 でなければ赤 — 読めない計器を 0 と読み違えない)。"
  (<- response (HttpRequest "GET" (+ url "/metrics") :max-retries 0))
  (assert (= response.status 200) #(response.status response.text))
  response.text)


(defk put-part [url key]
  {:pre [(: url str) (: key str)] :post [(: % int)] :tags {:context "records" :role "foundation"}}
  "parts に行を 1 つ書き、答えの status を返すため(書きは一覧の読みに数えない事も確かめる)。"
  (<- response (HttpRequest "POST" (+ url "/v1/records/put-row")
                            :headers {WRITER-HEADER MAKER CALLER-HEADER READER}
                            :body {"table" "parts" "key" [key] "value" {"label" "a"} "expect" {"kind" "any"}}
                            :max-retries 0))
  response.status)


(defk list-table [url table caller]
  {:pre [(: url str) (: table str) (: caller (| str None))] :post [(: % int)] :tags {:context "records" :role "foundation"}}
  "表 table を呼び手 caller の名乗り(None = 名乗らない)で一覧に読み、答えの status を返すため。"
  (<- response (HttpRequest "POST" (+ url "/v1/records/list-rows")
                            :headers (| {WRITER-HEADER MAKER} (if (is caller None) {} {CALLER-HEADER caller}))
                            :body {"table" table}
                            :max-retries 0))
  response.status)


(defk list-scenario [url]
  {:pre [(: url str)] :post [(: % ListRun)] :tags {:context "records" :role "foundation"}}
  "口 url に、parts の行を書いてから、宣言の呼び手・名乗り無し・宣言に無い名乗り・宣言に無い表の一覧の読みを 1 回ずつ送り、前後の
   /metrics を取るため。"
  (<- before str (scrape url))
  (for [index (range PART-COUNT)]
    (<- status int (put-part url (.format "p{}" index)))
    (assert (= status 200) status))
  (<- declared int (list-table url "parts" READER))
  (<- unnamed int (list-table url "tickets" None))
  (<- stranger int (list-table url "parts" STRANGER))
  (<- undeclared-table int (list-table url "nowhere" READER))
  (<- after str (scrape url))
  (ListRun :before before :after after :statuses #(declared unnamed stranger undeclared-table)))


(defk series [text name]
  {:pre [(: text str) (: name str)] :post [(: % (| float None))] :tags {:context "records" :role "judgment"}}
  "/metrics の本文から系列 name(描く名)の値を読むため(行が無ければ None — 「無い」と「0」を分けて確かめる)。"
  (for [line (.splitlines text)]
    (when (.startswith line (+ name " "))
      (return (float (get (.rsplit line " " 1) 1)))))
  None)


(defk listed-run []
  {:pre [] :post [(: % ListRun)] :tags {:context "records" :role "foundation"}}
  "呼び手 READER を宣言した口を共有の memory の置き場の上に開き、筋書きを 1 回走らせて閉じるため。"
  (val store (MemoryStore LAW-SCHEMA))
  (val server (start-records-server (run (records-server-config LAW-SCHEMA (fn [writer] (memory-records-handler store writer))
                                                                :request-handlers (sim-request-handlers (SimClock))
                                                                :callers #(READER)))))
  (try
    (<- seen ListRun (with_handlers [(await-handler) (http-production-handler)] (list-scenario server.url)))
    (finally (.close server)))
  seen)


(deftest test-list-rows-are-counted-per-table-and-per-declared-caller
  (<- seen ListRun (listed-run))
  (assert (= seen.statuses #(200 200 200 404)) seen.statuses)
  ;; 宣言の表ごとの系列は起動の時から 0 で在る(呼び手 × 表の系列は最初の答えまで無い)。
  (for [name (gfor table (sorted LAW-SCHEMA.tables) measure #("calls" "rows")
                  (.format "records_list_rows_table_{}_{}_total" table measure))]
    (assert (= (! (series seen.before name)) 0.0) #(name seen.before)))
  (assert (is (! (series seen.before "records_list_rows_caller_reader_job_table_parts_calls_total")) None) seen.before)
  ;; parts は宣言の呼び手と宣言に無い名乗りの 2 回(書きは数えない)・tickets は名乗り無しの 1 回で 0 行。
  (val expected {"records_list_rows_table_parts_calls_total" 2.0
                 "records_list_rows_table_parts_rows_total" (float (* 2 PART-COUNT))
                 "records_list_rows_table_tickets_calls_total" 1.0
                 "records_list_rows_table_tickets_rows_total" 0.0
                 "records_list_rows_table_charters_calls_total" 0.0
                 "records_list_rows_caller_reader_job_table_parts_calls_total" 1.0
                 "records_list_rows_caller_reader_job_table_parts_rows_total" (float PART-COUNT)
                 "records_list_rows_caller_undeclared_table_parts_calls_total" 1.0
                 "records_list_rows_caller_undeclared_table_parts_rows_total" (float PART-COUNT)
                 "records_list_rows_caller_unnamed_table_tickets_calls_total" 1.0
                 "records_list_rows_caller_unnamed_table_tickets_rows_total" 0.0})
  (for [#(name value) (.items expected)]
    (assert (= (! (series seen.after name)) value) #(name (! (series seen.after name)))))
  ;; 名乗りの綴りでも宣言に無い表の名でも系列を作らない。
  (assert (not-in "stranger" seen.after) seen.after)
  (assert (not-in "nowhere" seen.after) seen.after)
  ;; 表ごとの系列は # HELP の説明を持つ。
  (assert (in "# HELP records_list_rows_table_parts_rows_total " seen.after) seen.after))


(deftest test-list-rows-metric-names-refuse-clashes-and-reserved-callers
  ;; 語へ畳むと同じ名になる表の組・呼び手と表の組は、数えを混ぜずに起動を止める。予約の語を名乗る宣言の呼び手も止める。
  (val placed (! (list-rows-metrics #("turn-job" "turn-log") #("card-scheduler"))))
  (assert (= placed #("records_list_rows_table_turn_job_calls" "records_list_rows_table_turn_job_rows"
                      "records_list_rows_table_turn_log_calls" "records_list_rows_table_turn_log_rows"))
          placed)
  (with [(pytest.raises ValueError :match "重なる")]
    (! (list-rows-metrics #("turn-job" "turn_job") #())))
  (with [(pytest.raises ValueError :match "重なる")]
    (! (list-rows-metrics #("x" "b_table_x") #("a" "a_table_b"))))
  (with [(pytest.raises ValueError :match "予約")]
    (! (list-rows-metrics #("parts") #("unnamed")))))
