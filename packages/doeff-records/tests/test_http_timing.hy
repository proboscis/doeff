;; 記録の service の要求ごとの計時(#3688)を、本物の待ち受け(127.0.0.1 の空き port)に doeff の HTTP の effect で要求を送り、GET /metrics の
;; 区間の秒の系列(records_stage_<操作>_<区間>_seconds_sum / _count)で確かめる。時計は本物: 要求ごとの外側の handler に時計を被せず、刻は口の
;; 土台の async-time-handler の GetMonotonic、受けた刻は待ち受けの time.monotonic(同じ物差し)。
;;   - 書き(put-row)1 件で、put-row の区間 queue・body・decode・handler・encode・send・total の観測がちょうど 1 つずつ増え、待ちの区間
;;     (wait・woke)は増えない。queue〜send の秒の和は total と ADD-UP-SECONDS の内で等しく、受けた → 答えが決まった(total − send)は
;;     client が測った往復の秒を超えない(送った刻は答えを待ち受けへ渡した後の要求の task の刻で、client が答えを受けた後になり得る)
;;   - 変化の待ち(watch-changes)の long-poll が、待ちの WRITE-AFTER 秒目の書きで 1 回答えると、watch-changes の区間の観測が wait と woke を
;;     含めてちょうど 1 つずつ増え、wait は書くまで待った秒(WAIT-FLOOR 以上)・起きてから送るまでの秒(woke)は待ちより
;;     短い。queue〜send と wait の秒の和は total に等しい(handler に待ちを混ぜると和が total を超える)
;;   - 同じことが PostgreSQL の置き場(使い捨て)でも成り立つ(置き場の待ちは LISTEN の呼び鈴と WaitWithin)
;;   - 反例: 秒の観測を捨てる計器・区間 decode だけを捨てる計器を差すと、同じ確かめが観測の欠けを名指しで赤にする(計時を外した形)
(require doeff-hy.macros [deftest defhandler defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import collections.abc [Callable])
(import json)
(import pytest)
(import doeff [EffectBase Program run with_handlers])
(import doeff_hy.frozen [FrozenMap])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse])
(import doeff_core_effects.meter_effects [ObserveSeconds])
(import concurrent.futures [ThreadPoolExecutor])
(import doeff_core_effects.pooled_postgres_sql [pooled-postgres-sql-handler])
(import doeff_core_effects.scheduler [Spawn Task Wait])
(import doeff_time [Delay GetMonotonic async-time-handler])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.pg [drop-records-tables])
(import doeff_records.http_server [records-server-config start-records-server])
(import doeff_records.request_timing [RequestStage])
(import tests.interpreters [fresh-prefix pg-skip-reason postgres-connections prepared-store records-handler-for run-sql])

;; 区間の和と total の許す差(秒)— 和も total も同じ刻の差なので、違いは浮動小数の丸めだけ。
(val ADD-UP-SECONDS 1e-6)
;; 変化の待ちを撃ってから書くまでの秒と、書くまで待った秒の下限(待ちの要求が口へ届くまでの秒を引いても残る幅)。
(val WRITE-AFTER 1.0)
(val WAIT-FLOOR 0.5)
;; 変化の待ちの上限の秒(書きで起きるので、ここまでは待たない)と、測る前に 1 度撃つ静かな待ちの秒(置き場の待ちの用意 — PostgreSQL の
;; LISTEN の接続 — を済ませ、測る待ちの起きている秒に初めの接続を混ぜない)。
(val WATCH-SECONDS 10.0)
(val WARM-SECONDS 0.1)

;; 待たない要求(書き)が観測する区間と観測しない区間。和を取る区間は total と woke の外(woke は handler・encode・send に重なる)。
(val WRITE-STAGES #(RequestStage.QUEUE RequestStage.BODY RequestStage.DECODE RequestStage.HANDLER RequestStage.ENCODE RequestStage.SEND
                    RequestStage.TOTAL))
(val WAIT-STAGES #(RequestStage.WAIT RequestStage.WOKE))
(val SUMMED-STAGES #(RequestStage.QUEUE RequestStage.BODY RequestStage.DECODE RequestStage.HANDLER RequestStage.WAIT RequestStage.ENCODE
                     RequestStage.SEND))

;; 確かめの名(反例が名指しで破る物)。
(val STAGES-OBSERVED-ONCE "要求 1 件で、その操作の区間の観測がちょうど 1 つずつ増える")
(val STAGES-ADD-UP "区間の秒の和が total(受けた → 送った)に等しい")
(val TOTAL-OBSERVED "受けた → 答えが決まった(total − send)が 0 より長く、client の往復の秒を超えない")
(val WAIT-SEPARATED "待った秒と、起きている秒・起きてからの秒が分かれている")


(defrecord StageGrowth
  "区間 1 つの 2 つの拍の間の増え: stage = 区間・seconds = 秒の和の増え・count = 観測の数の増え。"
  (#^ RequestStage stage)
  (#^ float seconds)
  (#^ float count))


(defrecord TimedWrite
  "書き 1 件の筋書きの読み: before・after = 書きの前後の /metrics の本文・status = 書きの答えの status・round-trip = client が測った往復の秒。"
  (#^ str before)
  (#^ str after)
  (#^ int status)
  (#^ float round-trip))


(defrecord WatchAnswer
  "変化の待ち 1 回の答え: status・changed = 答えに載った変化の数・round-trip = client が測った往復の秒。"
  (#^ int status)
  (#^ int changed)
  (#^ float round-trip))


(defrecord TimedWatch
  "変化の待ち 1 回の筋書きの読み: before・after = 前後の /metrics の本文・write-status = 待ちの間の書きの status・watch = 待ちの答え。"
  (#^ str before)
  (#^ str after)
  (#^ int write-status)
  (#^ WatchAnswer watch))


(defrecord TimedRuns
  "1 つの口で走らせた 2 つの筋書きの読み: write = 書き 1 件・watch = 変化の待ち 1 回。"
  (#^ TimedWrite write)
  (#^ TimedWatch watch))


(defrecord Expectation
  "計時の約束の確かめ 1 つ: what = 何を確かめたか・holds = 守られたか・seen = 読んだ値(赤の時に読む)。"
  (#^ str what)
  (#^ bool holds)
  (#^ str seen))


;; --- 筋書き(口へは doeff の HTTP の effect で送る)-----------------------------------------------------------------------------------

(defk scrape [url]
  {:pre [(: url str)] :post [(: % str)] :tags {:context "records" :role "foundation"}}
  "GET /metrics の本文を読むため。"
  (<- response HttpResponse (HttpRequest "GET" (+ url "/metrics") :max-retries 0))
  response.text)


(defk post-record [url operation body]
  {:pre [(: url str) (: operation str) (: body dict)] :post [(: % HttpResponse)] :tags {:context "records" :role "foundation"}}
  "書き手(maker)を名乗って記録の操作を 1 件送り、答えを返すため(5xx を撃ち直さない — 1 件は 1 要求)。body = wire の JSON の本文。"
  (<- response HttpResponse (HttpRequest "POST" (+ url "/v1/records/" operation) :headers {"X-Records-Writer" MAKER} :body body
                                         :max-retries 0))
  response)


(defk post-write [url key]
  {:pre [(: url str) (: key str)] :post [(: % int)] :tags {:context "records" :role "foundation"}}
  "表 parts に行 key を 1 件書き、答えの status を返すため。"
  (<- response HttpResponse (post-record url "put-row" {"table" "parts" "key" [key] "value" {"label" "a"} "expect" {"kind" "any"}}))
  response.status)


(defk cursor-now [url]
  {:pre [(: url str)] :post [(: % dict)] :tags {:context "records" :role "foundation"}}
  "表 parts の今の位置を、変化の待ちの要求の本文に載せる wire の JSON の値で読むため。"
  (<- response HttpResponse (post-record url "list-rows" {"table" "parts"}))
  (val page (json.loads response.text))
  {"epoch" (get page "epoch") "sequence" (get page "sequence")})


(defk timed-write [url]
  {:pre [(: url str)] :post [(: % TimedWrite)] :tags {:context "records" :role "foundation"}}
  "書き 1 件の前後の /metrics と、client が測った書きの往復の秒を読むため。"
  (<- before str (scrape url))
  (<- began float (GetMonotonic))
  (<- status int (post-write url "p1"))
  (<- ended float (GetMonotonic))
  (<- after str (scrape url))
  (TimedWrite :before before :after after :status status :round-trip (- ended began)))


(defk watched [url cursor seconds]
  {:pre [(: url str) (: cursor dict) (: seconds float)] :post [(: % WatchAnswer)] :tags {:context "records" :role "foundation"}}
  "表 parts の変化を位置 cursor(wire の JSON の値)から seconds 秒まで待ち、答えを読むため。"
  (<- began float (GetMonotonic))
  (<- response HttpResponse (post-record url "watch-changes" {"tables" ["parts"] "cursor" cursor "timeout" seconds}))
  (<- ended float (GetMonotonic))
  (WatchAnswer :status response.status :changed (len (.get (json.loads response.text) "items" [])) :round-trip (- ended began)))


(defk timed-watch [url]
  {:pre [(: url str)] :post [(: % TimedWatch)] :tags {:context "records" :role "foundation"}}
  "表 parts の今の位置から変化の待ちを撃ち、WRITE-AFTER 秒後に別の要求で 1 行書いて待ちを起こし、前後の /metrics を読むため(測る前に
   静かな待ちを WARM-SECONDS だけ 1 度撃ち、置き場の待ちの用意を済ませる)。"
  (<- cursor dict (cursor-now url))
  (<- _warm WatchAnswer (watched url cursor WARM-SECONDS))
  (<- before str (scrape url))
  (<- watcher Task (Spawn (watched url cursor WATCH-SECONDS)))
  (<- (Delay WRITE-AFTER))
  (<- write-status int (post-write url "w1"))
  (<- watch WatchAnswer (Wait watcher))
  (<- after str (scrape url))
  (TimedWatch :before before :after after :write-status write-status :watch watch))


(defk on-real-clock [program]
  {:pre [(: program (| Program EffectBase))] :post [(: % "program の答え")] :tags {:context "records" :role "foundation"}}
  "client の筋書きを本物の時計と HTTP の答え手の下で走らせるため。"
  (<- answer (with_handlers [(await-handler) (http-production-handler) (async-time-handler)] program))
  answer)


;; --- 読みの確かめ(純関数)------------------------------------------------------------------------------------------------------

(defk series-values [text]
  {:pre [(: text str)] :post [(: % (get FrozenMap float))] :tags {:context "records" :role "judgment"}}
  "/metrics の本文の値の行を、描く名 → 値の写像にするため(# で始まる HELP と TYPE の行は読まない)。"
  (FrozenMap (gfor line (.splitlines text) :if (and line (not (.startswith line "#"))) :setv #(name value) (.rsplit line " " 1)
                   #(name (float value)))))


(defk stage-growths [before after operation]
  {:pre [(: before str) (: after str) (: operation str)] :post [(: % (get tuple #(StageGrowth ...)))]
   :tags {:context "records" :role "judgment"}}
  "2 つの拍の間に、操作 operation(計器の名の綴り — put_row など)の区間ごとの秒の和と観測の数が増えた分を読むため(読み手と同じ区間の
   差。行の無い系列は、まだ観測していないので 0)。"
  (<- earlier (get FrozenMap float) (series-values before))
  (<- later (get FrozenMap float) (series-values after))
  (tuple (gfor stage RequestStage
               :setv name (.format "records_stage_{}_{}_seconds" operation stage)
               (StageGrowth :stage stage
                            :seconds (- (.get later (+ name "_sum") 0.0) (.get earlier (+ name "_sum") 0.0))
                            :count (- (.get later (+ name "_count") 0.0) (.get earlier (+ name "_count") 0.0))))))


(defk seconds-of [grown stage]
  {:pre [(: grown (get tuple #(StageGrowth ...))) (: stage RequestStage)] :post [(: % float)] :tags {:context "records" :role "judgment"}}
  "区間ごとの増えから区間 stage の秒の和の増えを読むため。"
  (sum (gfor growth grown :if (= growth.stage stage) growth.seconds)))


(defk summed-seconds [grown]
  {:pre [(: grown (get tuple #(StageGrowth ...)))] :post [(: % float)] :tags {:context "records" :role "judgment"}}
  "total の内訳の区間(SUMMED-STAGES)の秒の和を読むため。"
  (sum (gfor growth grown :if (in growth.stage SUMMED-STAGES) growth.seconds)))


(defk write-broken [seen]
  {:pre [(: seen TimedWrite)] :post [(: % (get tuple #(Expectation ...)))] :tags {:context "records" :role "judgment"}}
  "書き 1 件の筋書きの読みを計時の約束の確かめの組にし、破れた物だけを返すため(空 = 約束どおり)。"
  (<- grown (get tuple #(StageGrowth ...)) (stage-growths seen.before seen.after "put_row"))
  (<- summed float (summed-seconds grown))
  (<- total float (seconds-of grown RequestStage.TOTAL))
  (<- send float (seconds-of grown RequestStage.SEND))
  (val checks #((Expectation :what "書き 1 件が 200" :holds (= seen.status 200) :seen (str seen.status))
                (Expectation :what STAGES-OBSERVED-ONCE
                             :holds (all (gfor growth grown
                                               (= growth.count (if (in growth.stage WRITE-STAGES) 1.0 0.0))))
                             :seen (str grown))
                (Expectation :what STAGES-ADD-UP :holds (<= (abs (- summed total)) ADD-UP-SECONDS) :seen (str #(summed total grown)))
                (Expectation :what TOTAL-OBSERVED :holds (< 0.0 (- total send) seen.round-trip) :seen (str #(total send seen.round-trip)))))
  (tuple (gfor check checks :if (not check.holds) check)))


(defk watch-broken [seen]
  {:pre [(: seen TimedWatch)] :post [(: % (get tuple #(Expectation ...)))] :tags {:context "records" :role "judgment"}}
  "変化の待ち 1 回の筋書きの読みを計時の約束の確かめの組にし、破れた物だけを返すため(空 = 約束どおり)。"
  (<- grown (get tuple #(StageGrowth ...)) (stage-growths seen.before seen.after "watch_changes"))
  (<- summed float (summed-seconds grown))
  (<- total float (seconds-of grown RequestStage.TOTAL))
  (<- send float (seconds-of grown RequestStage.SEND))
  (<- wait float (seconds-of grown RequestStage.WAIT))
  (<- woke float (seconds-of grown RequestStage.WOKE))
  (val checks #((Expectation :what "待ちの間の書きと待ちが 200 で、待ちの答えに書いた 1 行が載る"
                             :holds (= #(seen.write-status seen.watch.status seen.watch.changed) #(200 200 1))
                             :seen (str seen))
                (Expectation :what STAGES-OBSERVED-ONCE
                             :holds (all (gfor growth grown
                                               (= growth.count (if (in growth.stage (+ WRITE-STAGES WAIT-STAGES)) 1.0 0.0))))
                             :seen (str grown))
                (Expectation :what STAGES-ADD-UP :holds (<= (abs (- summed total)) ADD-UP-SECONDS) :seen (str #(summed total grown)))
                (Expectation :what TOTAL-OBSERVED :holds (< 0.0 (- total send) seen.watch.round-trip) :seen (str #(total send seen.watch)))
                (Expectation :what WAIT-SEPARATED :holds (and (>= wait WAIT-FLOOR) (< woke wait) (<= woke total))
                             :seen (str #(wait woke total)))))
  (tuple (gfor check checks :if (not check.holds) check)))


;; --- 口の組み立て --------------------------------------------------------------------------------------------------------------------

(defk memory-timed-runs [meter]
  {:pre [(: meter (| (get Callable #(... object)) None))] :post [(: % TimedRuns)] :tags {:context "records" :role "foundation"}}
  "計器 meter(None = 既定の memory-meter-handler)で memory の置き場の上に口を開き、書きと変化の待ちの筋書きを 1 回ずつ走らせて閉じるため。"
  (val store (MemoryStore LAW-SCHEMA))
  (val server (start-records-server (run (records-server-config LAW-SCHEMA (fn [writer] (memory-records-handler store writer))
                                                                :meter meter))))
  (try
    (<- write TimedWrite (on-real-clock (timed-write server.url)))
    (<- watch TimedWatch (on-real-clock (timed-watch server.url)))
    (finally (.close server)))
  (TimedRuns :write write :watch watch))


(defhandler unobserving-meter
  "秒を観測しない計器(計時を外した形の代役): ObserveSeconds を受け流す。数え(CountMetric)と断面の読みは外側の既定の計器へ回す。"
  {:tags {:context "records" :role "foundation"}}
  (ObserveSeconds [name seconds]
    (resume None)))


(defhandler decode-dropping-meter
  "区間 decode の秒だけを観測しない計器(刻を 1 つ打ち忘れた形の代役): 名が _decode で終わる ObserveSeconds を受け流し、他は外側へ回す。"
  {:tags {:context "records" :role "foundation"}}
  (ObserveSeconds [name seconds] :when (.endswith name "_decode")
    (resume None)))


;; --- 検 ----------------------------------------------------------------------------------------------------------------------------

(deftest test-a-write-and-a-long-poll-are-timed-stage-by-stage-on-memory
  ;; 書き 1 件: 区間の観測がちょうど 1 つずつ・和が total・total が往復の内。変化の待ち 1 回: 待った秒と起きている秒が分かれる。
  (<- runs TimedRuns (memory-timed-runs None))
  (<- write-broken-checks (get tuple #(Expectation ...)) (write-broken runs.write))
  (<- watch-broken-checks (get tuple #(Expectation ...)) (watch-broken runs.watch))
  (assert (= write-broken-checks #()) write-broken-checks)
  (assert (= watch-broken-checks #()) watch-broken-checks))


(deftest test-a-write-and-a-long-poll-are-timed-stage-by-stage-on-postgresql
  ;; 同じ筋書きを使い捨ての PostgreSQL の置き場の上で(書きは錠の transaction・待ちは LISTEN の呼び鈴と WaitWithin)。SQL の答え手は本番と
  ;; 同じ pooled-postgres-sql-handler(driver の I/O を pool の thread へ — scheduler を塞がない)。
  (val reason (pg-skip-reason))
  (when reason
    (pytest.skip reason))
  (val connections (postgres-connections))
  (val pool (ThreadPoolExecutor :max-workers 4))
  (val store (prepared-store connections (fresh-prefix)))
  (val server (start-records-server (run (records-server-config LAW-SCHEMA (records-handler-for store)
                                                                :request-handlers #((pooled-postgres-sql-handler connections pool))))))
  (try
    (<- write TimedWrite (on-real-clock (timed-write server.url)))
    (<- watch TimedWatch (on-real-clock (timed-watch server.url)))
    (finally
      (.close server)
      (run-sql connections (drop-records-tables store))
      (.shutdown pool)
      (.close connections)))
  (<- write-broken-checks (get tuple #(Expectation ...)) (write-broken write))
  (<- watch-broken-checks (get tuple #(Expectation ...)) (watch-broken watch))
  (assert (= write-broken-checks #()) write-broken-checks)
  (assert (= watch-broken-checks #()) watch-broken-checks))


(deftest test-the-timing-checks-turn-red-when-observations-are-dropped
  ;; 反例: 秒を観測しない計器(計時を外した形)と、区間 decode だけを観測しない計器(刻を 1 つ打ち忘れた形)を差すと、同じ確かめが
  ;; 欠けを名指しで赤にする(確かめが何も見ずに緑になる形を外す)。
  (for [#(meter expected) [#(unobserving-meter #(STAGES-OBSERVED-ONCE TOTAL-OBSERVED))
                           #(decode-dropping-meter #(STAGES-OBSERVED-ONCE STAGES-ADD-UP))]]
    (<- runs TimedRuns (memory-timed-runs meter))
    (<- write-broken-checks (get tuple #(Expectation ...)) (write-broken runs.write))
    (<- watch-broken-checks (get tuple #(Expectation ...)) (watch-broken runs.watch))
    (val named-write (frozenset (gfor check write-broken-checks check.what)))
    (val named-watch (frozenset (gfor check watch-broken-checks check.what)))
    (assert (<= (frozenset expected) named-write) #(meter.__name__ write-broken-checks))
    (assert (<= (frozenset expected) named-watch) #(meter.__name__ watch-broken-checks))))
