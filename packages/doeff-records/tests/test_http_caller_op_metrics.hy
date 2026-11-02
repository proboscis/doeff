;; 記録の service の呼び手ごとの要求の数(GET /metrics の records_requests_caller_* — _http_server.hy の頭の註の呼び手ごとの要求の数)を、
;; 本物の待ち受け(127.0.0.1 の空き port)に doeff の HTTP の effect で要求を送って確かめる。置き場は共有の memory。
;;   - 宣言された呼び手が名乗った行の読み・出来事の読み・行の書きが、呼び手 × 操作の系列に 1 つずつ入る(一覧の読みの外の操作も数える)
;;   - 名乗りの無い要求は unnamed・宣言に無い名乗りは undeclared に数え、名乗りの綴りで系列を増やさない
;;   - 系列は起動の時に 0 で置かず、最初の要求で現れる・記録の操作でない route(/metrics)は数えない
;; 系列の名の閉じた組(語へ畳んだ名の重なり・予約の語)は wire の caller-op-metrics を直に確かめる。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import pytest)
(import doeff [run with_handlers])
(import doeff_core_effects [await-handler])
(import doeff_core_effects [http-production-handler])
(import doeff_core_effects [HttpRequest])
(import doeff_time [SimClock])
(import doeff_records._laws [LAW-SCHEMA MAKER])
(import doeff_records [MemoryStore memory-records-handler])
(import doeff_records._wire [CALLER-HEADER WRITER-HEADER OPERATIONS caller-op-metrics])
(import doeff_records [records-server-config]
        doeff_records._http_server [start-records-server])
(import tests.interpreters [sim-request-handlers])

;; 宣言する呼び手の名(語に畳むと reader_job)と、宣言に無い名乗り。
(val READER "reader-job")
(val STRANGER "stranger-job")


(defrecord OpRun
  "筋書きを 1 回走らせた読み: before = 何もしない前の /metrics の本文・after = 要求の後の本文・statuses = 送った要求の答えの status
   (送った順)。"
  (#^ str before)
  (#^ str after)
  (#^ (get tuple #(int ...)) statuses))


(defk scrape [url]
  {:pre [(: url str)] :post [(: % str)] :tags {:context "records" :role "foundation"}}
  "GET /metrics の本文を読むため(200 でなければ赤 — 読めない計器を 0 と読み違えない)。"
  (<- response (HttpRequest "GET" (+ url "/metrics") :max-retries 0))
  (assert (= response.status 200) #(response.status response.text))
  response.text)


(defk send-op [url operation caller body]
  {:pre [(: url str) (: operation str) (: caller (| str None)) (: body dict)] :post [(: % int)]
   :tags {:context "records" :role "foundation"}}
  "記録の操作 operation を呼び手 caller の名乗り(None = 名乗らない)で送り、答えの status を返すため。"
  (<- response (HttpRequest "POST" (+ url "/v1/records/" operation)
                            :headers (| {WRITER-HEADER MAKER} (if (is caller None) {} {CALLER-HEADER caller}))
                            :body body
                            :max-retries 0))
  response.status)


(defk op-scenario [url]
  {:pre [(: url str)] :post [(: % OpRun)] :tags {:context "records" :role "foundation"}}
  "口 url に、宣言の呼び手の行の書き 1・行の読み 2・出来事の読み 1 と、名乗り無しの行の読み 1・宣言に無い名乗りの出来事の読み 1 を送り、
   前後の /metrics を取るため。"
  (<- before str (scrape url))
  (val put {"table" "parts" "key" ["p0"] "value" {"label" "a"} "expect" {"kind" "any"}})
  (val read {"table" "parts" "key" ["p0"]})
  (val events {"stream" "journal"})
  (<- s1 int (send-op url "put-row" READER put))
  (<- s2 int (send-op url "read-row" READER read))
  (<- s3 int (send-op url "read-row" READER read))
  (<- s4 int (send-op url "read-events" READER events))
  (<- s5 int (send-op url "read-row" None read))
  (<- s6 int (send-op url "read-events" STRANGER events))
  (<- after str (scrape url))
  (OpRun :before before :after after :statuses #(s1 s2 s3 s4 s5 s6)))


(defk series [text name]
  {:pre [(: text str) (: name str)] :post [(: % (| float None))] :tags {:context "records" :role "judgment"}}
  "/metrics の本文から系列 name(描く名)の値を読むため(行が無ければ None — 「無い」と「0」を分けて確かめる)。"
  (for [line (.splitlines text)]
    (when (.startswith line (+ name " "))
      (return (float (get (.rsplit line " " 1) 1)))))
  None)


(defk op-run []
  {:pre [] :post [(: % OpRun)] :tags {:context "records" :role "foundation"}}
  "呼び手 READER を宣言した口を共有の memory の置き場の上に開き、筋書きを 1 回走らせて閉じるため。"
  (val store (MemoryStore LAW-SCHEMA))
  (val server (start-records-server (run (records-server-config LAW-SCHEMA (fn [writer] (memory-records-handler store writer))
                                                                :request-handlers (sim-request-handlers (SimClock))
                                                                :callers #(READER)))))
  (try
    (<- seen OpRun (with_handlers [await-handler (http-production-handler)] (op-scenario server.url)))
    (finally (.close server)))
  seen)


(deftest test-requests-are-counted-per-declared-caller-and-operation
  (<- seen OpRun (op-run))
  (assert (= seen.statuses #(200 200 200 200 200 200)) seen.statuses)
  ;; 呼び手 × 操作の系列は起動の時に置かない(最初の要求で現れる)。
  (assert (not-in "records_requests_caller_" seen.before) seen.before)
  (val expected {"records_requests_caller_reader_job_op_put_row_total" 1.0
                 "records_requests_caller_reader_job_op_read_row_total" 2.0
                 "records_requests_caller_reader_job_op_read_events_total" 1.0
                 "records_requests_caller_unnamed_op_read_row_total" 1.0
                 "records_requests_caller_undeclared_op_read_events_total" 1.0})
  (for [#(name value) (.items expected)]
    (assert (= (! (series seen.after name)) value) #(name (! (series seen.after name)))))
  ;; 送らなかった組の系列は無く、名乗りの綴りで系列を作らず、/metrics の読みは数えない。
  (assert (is (! (series seen.after "records_requests_caller_reader_job_op_list_rows_total")) None) seen.after)
  (assert (not-in "stranger" seen.after) seen.after)
  (assert (not-in "_op_metrics" seen.after) seen.after))


(deftest test-caller-op-metric-names-refuse-clashes-and-reserved-callers
  ;; 閉じた組は (宣言の呼び手 + unnamed・undeclared) × 操作の種類。語へ畳むと同じ名になる呼び手の組と予約の語を名乗る宣言は起動を止める。
  (val placed (! (caller-op-metrics #("batch-reader"))))
  (assert (= (len placed) (* 3 (len OPERATIONS))) placed)
  (assert (in "records_requests_caller_batch_reader_op_read_events" placed) placed)
  (with [(pytest.raises ValueError :match "重なる")]
    (! (caller-op-metrics #("turn-job" "turn_job"))))
  (with [(pytest.raises ValueError :match "予約")]
    (! (caller-op-metrics #("undeclared")))))
