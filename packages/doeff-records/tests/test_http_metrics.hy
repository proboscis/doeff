;; 記録の service の計器(GET /metrics — #2709)を、本物の待ち受け(127.0.0.1 の空き port)に doeff の HTTP の effect で要求を送って
;; 確かめる。置き場は共有の memory(検の口 SetStoreOutage で「届かない」時間枠を作る)。計器は doeff の CountMetric / ReadMeter で、答え手は
;; 既定の memory-meter-handler(RecordsServing の meter の口で差し替える)。
;;   - 身元の無い GET /metrics は 200 の Prometheus の text で答え、要求の数の系列は起動の時から 0 で在る
;;   - 書きの要求 1 件で、書きの 200 の数がちょうど 1 増える(読みの数は増えない)
;;   - 届かない時間枠に書きを N 件送ると、書きの 503 の数(置き場に届かなかった数)がちょうど N 増え、戻した後の書きは 200 の数に入る
;;   - 反例: 計器の handler を壊す(数えない・種を取り違える・成功だけ数える)と、同じ確かめが壊した所を名指しで赤にする
;; 入口の 3 つの出口(本文の断りの 400・落ちた時の 500・通常)が数える名は、台本の検(test_http_entry_program.hy)が確かめる。
(require doeff-hy.macros [deftest defhandler defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import doeff [with_handlers])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.http_effects [HttpRequest])
(import doeff_core_effects.meter_effects [CountMetric EMPTY-METER MeterSnapshot ReadMeter counted])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.faults [SetStoreOutage])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.wire [STATUS-OF-ERROR])
(import doeff_records.http_server [RecordsServerConfig start-records-server])
(import tests.interpreters [law-roster sim-request-handlers])

;; 届かない時間枠に送る書きの数(筋書き meter-scenario の q1〜q3)と、届かない状態の理由。
(val OUTAGE-WRITES 3)
(val OUTAGE-DETAIL "記録の service の置き場が落ちている(計器の検の筋書き)")

;; 読む系列(描く名 — 計器の名に _total)。
(val WRITTEN "records_requests_write_200_total")
(val WRITE-UNREACHABLE "records_requests_write_503_total")
(val READ-ANSWERED "records_requests_read_200_total")

;; 確かめの名(反例が名指しで破る物)。
(val SERIES-PLACED "書きの 503 の系列が起動の時から 0 で在る")
(val ONE-WRITE-COUNTED "書き 1 件で書きの 200 の数がちょうど 1 増える")
(val OUTAGE-COUNTED "届かない時間枠の書き N 件で、書きの 503 の数(置き場に届かなかった数)がちょうど N 増える")


(defrecord MetricsScrape
  "GET /metrics を 1 回取った答え: status・content-type = Content-Type の値・text = 本文。"
  (#^ int status)
  (#^ str content-type)
  (#^ str text))


(defrecord MeterRun
  "計器の筋書きを 1 回走らせた読み: 4 つの拍の /metrics(before = 何もしない前・after-write = 書き 1 件の後・during-outage = 届かない
   時間枠の書きの後・after-recovery = 戻した後の書き 1 件の後)と、送った書きの答えの status。"
  (#^ MetricsScrape before)
  (#^ MetricsScrape after-write)
  (#^ MetricsScrape during-outage)
  (#^ MetricsScrape after-recovery)
  (#^ int write-status)
  (#^ (get tuple #(int ...)) outage-statuses)
  (#^ int recovered-status))


(defrecord Expectation
  "計器の約束の確かめ 1 つ: what = 何を確かめたか・holds = 守られたか・seen = 読んだ値(赤の時に読む)。"
  (#^ str what)
  (#^ bool holds)
  (#^ str seen))


;; --- 筋書き(口へは doeff の HTTP の effect で送る・届かない状態は共有の置き場の検の口)--------------------------------------------

(defk header-of [headers name]
  {:pre [(: headers (get dict #(str str))) (: name str)] :post [(: % str)] :tags {:context "records" :role "judgment"}}
  "答えの頭から名 name の値を読むため(名の大小を問わない・無ければ空 — 赤の時に読む値)。"
  (.join "," (gfor #(key value) (.items headers) :if (= (.lower key) (.lower name)) value)))


(defk scrape [url]
  {:pre [(: url str)] :post [(: % MetricsScrape)] :tags {:context "records" :role "foundation"}}
  "身元の見出しを付けずに GET /metrics を送り、答えを読むため(断りの status も答えとして返す — 赤の理由を読めるように)。"
  (<- response (HttpRequest "GET" (+ url "/metrics") :max-retries 0))
  (<- content-type str (header-of response.headers "Content-Type"))
  (MetricsScrape :status response.status :content-type content-type :text response.text))


(defk post-write [url key]
  {:pre [(: url str) (: key str)] :post [(: % int)] :tags {:context "records" :role "foundation"}}
  "書き手(maker)を X-Records-Writer で名乗って put-row を 1 件送り、答えの status を返すため(5xx を撃ち直さない — 1 件は 1 要求)。"
  (<- response (HttpRequest "POST" (+ url "/v1/records/put-row")
                            :headers {"X-Records-Writer" MAKER}
                            :body {"table" "parts" "key" [key] "value" {"label" "a"} "expect" {"kind" "any"}}
                            :max-retries 0))
  response.status)


(defk meter-scenario [url]
  {:pre [(: url str)] :post [(: % MeterRun)] :tags {:context "records" :role "foundation"}}
  "口 url に、書き 1 件 → 届かない時間枠の書き 3 件 → 戻した後の書き 1 件を送り、各拍の /metrics を取るため(届かない状態は共有の
   置き場の検の口 SetStoreOutage — 外側の memory の handler が答える)。"
  (<- before MetricsScrape (scrape url))
  (<- write-status int (post-write url "p1"))
  (<- after-write MetricsScrape (scrape url))
  (<- (SetStoreOutage OUTAGE-DETAIL))
  (<- q1 int (post-write url "q1"))
  (<- q2 int (post-write url "q2"))
  (<- q3 int (post-write url "q3"))
  (<- during-outage MetricsScrape (scrape url))
  (<- (SetStoreOutage None))
  (<- recovered-status int (post-write url "p2"))
  (<- after-recovery MetricsScrape (scrape url))
  (MeterRun :before before :after-write after-write :during-outage during-outage :after-recovery after-recovery
            :write-status write-status :outage-statuses #(q1 q2 q3) :recovered-status recovered-status))


(defk metered-run [meter]
  {:pre [(: meter (| (get Callable #(... object)) None))] :post [(: % MeterRun)] :tags {:context "records" :role "foundation"}}
  "計器 meter(None = 既定の memory-meter-handler)で共有の memory の置き場の上に口(検の殻)を開き、筋書きを 1 回走らせて閉じるため。"
  (val store (MemoryStore LAW-SCHEMA))
  (val server (start-records-server (RecordsServerConfig LAW-SCHEMA (law-roster) (fn [writer] (memory-records-handler store writer))
                                                         :request-handlers (sim-request-handlers (SimClock)) :meter meter)))
  (try
    (<- seen MeterRun (with_handlers [(await-handler) (http-production-handler) (sim-time-handler :clock (SimClock))
                                      (memory-records-handler store MAKER)]
                                     (meter-scenario server.url)))
    (finally (.close server)))
  seen)


;; --- 読みの確かめ(純関数)------------------------------------------------------------------------------------------------------

(defk series-count [scrape series]
  {:pre [(: scrape MetricsScrape) (: series str)] :post [(: % float)] :tags {:context "records" :role "judgment"}}
  "取った /metrics の本文から系列 series の数を読むため(行の無い系列は、まだ 1 つも数えていないので 0)。"
  (float (sum (gfor line (.splitlines scrape.text) :if (.startswith line (+ series " ")) (float (get (.rsplit line " " 1) 1))))))


(defk grew [earlier later series]
  {:pre [(: earlier MetricsScrape) (: later MetricsScrape) (: series str)] :post [(: % float)] :tags {:context "records" :role "judgment"}}
  "2 つの拍の間に系列 series が増えた数を読むため(読み手と同じ区間の差)。"
  (<- before float (series-count earlier series))
  (<- after float (series-count later series))
  (- after before))


(defk broken-promises [seen]
  {:pre [(: seen MeterRun)] :post [(: % (get tuple #(Expectation ...)))] :tags {:context "records" :role "judgment"}}
  "筋書きの読みを計器の約束の確かめの組にし、破れた物だけを返すため(空 = 計器が約束どおり)。"
  (<- written float (grew seen.before seen.after-write WRITTEN))
  (<- read float (grew seen.before seen.after-write READ-ANSWERED))
  (<- unreachable float (grew seen.after-write seen.during-outage WRITE-UNREACHABLE))
  (<- written-in-outage float (grew seen.after-write seen.during-outage WRITTEN))
  (<- recovered float (grew seen.during-outage seen.after-recovery WRITTEN))
  (<- unreachable-after float (grew seen.during-outage seen.after-recovery WRITE-UNREACHABLE))
  (val checks #((Expectation :what "身元の無い GET /metrics が 200" :holds (= seen.before.status 200) :seen (str seen.before.status))
                (Expectation :what "/metrics の Content-Type が Prometheus の text の版 0.0.4"
                             :holds (.startswith seen.before.content-type "text/plain; version=0.0.4") :seen seen.before.content-type)
                (Expectation :what SERIES-PLACED :holds (in (+ WRITE-UNREACHABLE " 0.0") (.splitlines seen.before.text))
                             :seen seen.before.text)
                (Expectation :what "書き 1 件が 200" :holds (= seen.write-status 200) :seen (str seen.write-status))
                (Expectation :what ONE-WRITE-COUNTED :holds (= written 1.0) :seen (str written))
                (Expectation :what "書きで読みの数は増えない" :holds (= read 0.0) :seen (str read))
                (Expectation :what "届かない時間枠の書きは全部 503" :holds (= seen.outage-statuses (tuple (gfor _ (range OUTAGE-WRITES) 503)))
                             :seen (str seen.outage-statuses))
                (Expectation :what OUTAGE-COUNTED :holds (= unreachable (float OUTAGE-WRITES)) :seen (str unreachable))
                (Expectation :what "届かない時間枠に書きの 200 の数は増えない" :holds (= written-in-outage 0.0) :seen (str written-in-outage))
                (Expectation :what "戻した後の書きが 200" :holds (= seen.recovered-status 200) :seen (str seen.recovered-status))
                (Expectation :what "戻した後の書きは 200 の数に入る" :holds (= recovered 1.0) :seen (str recovered))
                (Expectation :what "戻した後は届かない数が増えない" :holds (= unreachable-after 0.0) :seen (str unreachable-after))))
  (tuple (gfor check checks :if (not check.holds) check)))


;; --- 壊した計器(反例)— 既定の memory-meter-handler と同じ effect に答え、1 つだけ約束を破る ----------------------------------------

(defhandler uncounting-meter
  "数えない計器: CountMetric を受け流し、断面は空のまま(系列も置かれず、書きの数も届かない数も増えない)。"
  {:tags {:context "records" :role "foundation"}}
  (CountMetric [name amount]
    (resume None))
  (ReadMeter []
    (resume EMPTY-METER)))


(defhandler kind-blind-meter
  "種を見ない計器: どの数えも要求の種 other の系列へ足す(書きの数も届かない書きの数も増えない)。"
  {:tags {:context "records" :role "foundation"}}
  (session var snapshot EMPTY-METER)
  (CountMetric [name amount]
    (<- next-snapshot MeterSnapshot (counted snapshot (.replace (.replace name "_write_" "_other_") "_read_" "_other_") amount))
    (:= snapshot next-snapshot)
    (resume None))
  (ReadMeter []
    (resume snapshot)))


(defhandler success-only-meter
  "成功だけを数える計器: 200 の系列の外への数えを受け流す(置き場に届かなかった書きの 503 が数に入らない)。"
  {:tags {:context "records" :role "foundation"}}
  (session var snapshot EMPTY-METER)
  (CountMetric [name amount]
    (when (.endswith name "_200")
      (<- next-snapshot MeterSnapshot (counted snapshot name amount))
      (:= snapshot next-snapshot))
    (resume None))
  (ReadMeter []
    (resume snapshot)))


;; --- 検 ----------------------------------------------------------------------------------------------------------------------------

(deftest test-metrics-counts-one-write-and-every-write-in-an-outage-window
  ;; 終わりの条件 1・2・4(#2709): 身元の無い /metrics が 200・書き 1 件で書きの数が 1 増える・届かない時間枠の書き N 件で
  ;; 置き場に届かなかった数(書きの 503)がちょうど N 増え、戻した後の書きは成功の数に入る。
  (<- seen MeterRun (metered-run None))
  (<- broken (get tuple #(Expectation ...)) (broken-promises seen))
  (assert (= broken #()) broken))


(deftest test-metrics-turn-red-on-a-broken-meter
  ;; 終わりの条件 3: 計器の handler を壊すと、同じ確かめが壊した所を名指しで赤にする(計器が何も確かめずに緑になる形を外す)。
  (for [#(meter expected) [#(uncounting-meter #(SERIES-PLACED ONE-WRITE-COUNTED OUTAGE-COUNTED))
                           #(kind-blind-meter #(SERIES-PLACED ONE-WRITE-COUNTED OUTAGE-COUNTED))
                           #(success-only-meter #(SERIES-PLACED OUTAGE-COUNTED))]]
    (<- seen MeterRun (metered-run meter))
    (<- broken (get tuple #(Expectation ...)) (broken-promises seen))
    (val named (frozenset (gfor check broken check.what)))
    (assert (<= (frozenset expected) named) #(meter.__name__ broken))))


(deftest test-metrics-answers-without-identity-even-with-a-bad-token
  ;; 終わりの条件 4 の念押し: /metrics は身元を引かない — 名簿に無い token を付けても 401 にならず 200(/readyz と同じ扱い)。
  (val store (MemoryStore LAW-SCHEMA))
  (val server (start-records-server (RecordsServerConfig LAW-SCHEMA (law-roster) (fn [writer] (memory-records-handler store writer))
                                                         :request-handlers (sim-request-handlers (SimClock)))))
  (try
    (<- response (with_handlers [(await-handler) (http-production-handler)]
                                (HttpRequest "GET" (+ server.url "/metrics") :headers {"Authorization" "Bearer not-in-roster"}
                                             :max-retries 0)))
    (assert (= response.status 200) #(response.status response.text))
    (finally (.close server))))


(deftest test-each-refusal-status-names-one-error-word
  ;; 計器は置き場に届かなかった数を 503 の系列で、答えの途中で落ちた数を 500 の系列で読む(別の counter を持たない)。語ごとの status が
  ;; 重なると、503 の系列に別の断りが混ざるので、wire の表の status が語ごとに 1 つずつであることを確かめる。
  (val statuses (tuple (.values STATUS-OF-ERROR)))
  (assert (= (len (frozenset statuses)) (len statuses)) STATUS-OF-ERROR)
  (assert (= (get STATUS-OF-ERROR "store-unavailable") 503) STATUS-OF-ERROR)
  (assert (= (get STATUS-OF-ERROR "internal") 500) STATUS-OF-ERROR))
