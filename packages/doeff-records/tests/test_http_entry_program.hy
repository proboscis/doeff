;;; 記録の service の入口の Program(main.hy の records-process — 本番と同じ組み立て)を、I/O なしの土台で走らせる検。
;;; 土台だけを差す: 待ち受け = 台本の scripted-http-server・置き場 = memory・時計 = 仮想の時計・止めの合図 = scripted-stop-handler。
;;; 本番の土台(records-foundation)との違いは土台の関数だけ。
;;;
;;; 確かめる不変条件(#880 のレビューの A2):
;;;   - 台本の全部の札に、ちょうど 1 つの HttpRespond が返る(答えの無い札が残らない)— 要求の task が答えの途中で落ちた札(本文の読みの
;;;     答えが語彙の外 = 待ち受けの答え手の欠陥の代役)も 500 internal で答える(try / finally)
;;;   - 同じ 1 つの run の中で、/healthz・書き・読みが答える(要求ごとに run を撃たない)
;;;   - 本文の上限を宣言の長さで超える要求は、本文を読まずに 400 malformed
;;;   - 表の用意が済む前の記録の操作は 503 store-unavailable、/healthz は 200(口は用意の前に開く)
;;;   - 反例: 表の用意が落ちれば、run は例外で終わる(0 で終わらない — 半端に立ったまま答え続けない)
;;;   - 計器(#2709): 実際に送った答えを札ごとにちょうど 1 つ、要求の種と status の counter に数える(400 の本文の断り・500 の落ちた札・
;;;     用意の前の 503 も)。送りが落ちた札は 500 で送り直し、500 として数える(送れなかった答えは数えない)
;;;   - 表の用意の告知(#3733): 用意が済むまで RecordsPrepared は出ず(用意の前の記録の操作は 503)、済んだ拍に 1 度だけ
;;;     出る。告知の後に届いた最初の要求は答える(告知が出たなら要求に答えられる)
;;;   - 置き場に届くかの公開の判断 store-reach(/readyz と同じ判断): 届く・届かない・上限の秒の内に答えない、をそれぞれの型で返す
(require doeff-hy.macros [deftest defeffect defhandler defk <- val])
(require doeff-hy.record [defrecord])
(import json)
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import doeff [Program EffectBase run with-handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.stop_signal_handlers [scripted-stop-handler])
(import doeff_core_effects.scripted_http_server [scripted-http-server])
(import doeff_core_effects.http_server_effects [AppendHttpScript HttpAddress HttpHeader HttpNextRequest HttpReadBody HttpRequestArrived
                                                HttpRespond HttpScript ReadHttpServed ScriptedBody])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_records.laws [LAW-SCHEMA])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.http_server [RecordsServing RecordsListening RecordsPrepared StoreReach StoreReachable StoreSilent StoreUnreachable
                                   serve-records store-reach])
(import doeff_records.wire [ANSWER-METRICS])
(import doeff_core_effects.meter_effects [MeterSettings MeterSnapshot ReadMeter])
(import doeff_core_effects.memory_meter [memory-meter-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [Row])
(import doeff_records.effects [ReadRow])
(import doeff_records.store_choice [StorePressure PressureUnread])
(import doeff_records.main [records-process])

;; 本文の上限(検のために小さく — 本番は http_server.hy の REQUEST-MAX-BYTES)。
(val MAX-BYTES 4096)
;; 本文の読みの答えを壊す札(待ち受けの答え手の欠陥の代役 — 要求の task が答えの途中で落ちる)。
(val BROKEN-TICKET "t-broken")
(val READ-PATH "/v1/records/read-row")


(defrecord ScriptedParts
  "検の土台の部品: script = 台本 / broken = 本文の読みの答えを壊す札(None = 壊さない)/ note = 本体の後に台本の待ち受けが受けた命令を
   受け取る口。"
  (#^ HttpScript script)
  (#^ (| str None) broken)
  (#^ Callable note))


(defhandler scripted-extras [#^ (| str None) broken]
  "札 broken の本文の読みに語彙の外の答えを返し、待ち受けの名乗りと表の用意の告知を受け流すため(検の土台の代役)。他の札の読みは外側の
   台本の待ち受けへ回す。"
  {:tags {:context "records" :role "foundation"}}
  ;; 引数に残す理由: 壊す札は検の筋書きごとの値。
  (HttpReadBody [ticket max-bytes] :when (= ticket broken)
    (resume "語彙の外の答え"))
  (RecordsListening [address]
    (resume None))
  (RecordsPrepared [address seconds]
    (resume None)))


(defk noted [parts body]
  {:pre [(: parts ScriptedParts) (: body (| Program EffectBase))] :post [(: % "body の答え")] :tags {:context "records" :role "foundation"}}
  "本体を走らせ、台本の待ち受けが受けた命令を parts.note へ渡すため。"
  (<- answer (with-handlers [(scripted-extras parts.broken)] body))
  (<- commands tuple (ReadHttpServed))
  (parts.note commands)
  answer)


(defk scripted-foundation [parts body]
  {:pre [(: parts ScriptedParts) (: body (| Program EffectBase))] :post [(: % "body の答え")] :tags {:context "records" :role "foundation"}}
  "records-process に渡す検の土台の関数: scheduler・session の値の置き場・仮想の時計・台本の止めの合図と待ち受けの下で本体を走らせるため。"
  (<- answer (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) scripted-stop-handler (scripted-http-server parts.script)]
                                       (noted parts body))))
  answer)


(defk arrival [ticket method target who length]
  {:pre [(: ticket str) (: method str) (: target str) (: who (| str None)) (: length (| int None))] :post [(: % HttpRequestArrived)]
   :tags {:context "records" :role "judgment"}}
  "台本の要求 1 つ(who = 名乗る書き手・length = 宣言する本文の長さ)を作るため。"
  (val auth (if (is who None) #() #((HttpHeader :name "X-Records-Writer" :value who))))
  (val declared (if (is length None) #() #((HttpHeader :name "Content-Length" :value (str length)))))
  (HttpRequestArrived :ticket ticket :method method :path (get (.split target "?") 0) :target target :headers (+ auth declared) :upgrade False))


(defk served-script []
  {:pre [] :post [(: % HttpScript)] :tags {:context "records" :role "judgment"}}
  "筋書きの台本: 生存・書き・読み・本文の上限の超え・答えの途中で落ちる要求。"
  (val put (.encode (json.dumps {"table" "parts" "key" ["p1"] "value" {"label" "a"} "expect" {"kind" "any"}}) "utf-8"))
  (val read (.encode (json.dumps {"table" "parts" "key" ["p1"]}) "utf-8"))
  (HttpScript :arrivals #((! (arrival "t-health" "GET" "/healthz" None None))
                          (! (arrival "t-put" "POST" "/v1/records/put-row" "maker" (len put)))
                          (! (arrival "t-read" "POST" (+ READ-PATH "?trace=1") "maker" (len read)))
                          (! (arrival "t-large" "POST" READ-PATH "maker" (+ MAX-BYTES 1)))
                          (! (arrival BROKEN-TICKET "POST" READ-PATH "maker" (len read))))
              :bodies #((ScriptedBody :ticket "t-put" :data put)
                        (ScriptedBody :ticket "t-read" :data read)
                        (ScriptedBody :ticket "t-large" :data (* b"x" (+ MAX-BYTES 1)))
                        (ScriptedBody :ticket BROKEN-TICKET :data read))))


(defk handlers-at-once [store]
  {:pre [(: store MemoryStore)] :post [(: % Callable)] :tags {:context "records" :role "foundation"}}
  "memory の置き場の用意(I/O なし — 直ぐに 書き手の名 → handler の関数を返す)。"
  (fn [writer] (memory-records-handler store writer)))


(defk handlers-after [store seconds]
  {:pre [(: store MemoryStore) (: seconds float)] :post [(: % Callable)] :tags {:context "records" :role "foundation"}}
  "用意が長い置き場の代役(仮想の時計で seconds 秒かかる — 本番の起動時の長い移行の代わり)。"
  (<- (Delay seconds))
  (fn [writer] (memory-records-handler store writer)))


(defk failing-prepare []
  {:pre [] :post [(: % Callable)] :tags {:context "records" :role "foundation"}}
  "用意が落ちる置き場の代役(置き場に届かない)。"
  (raise (ConnectionError "置き場に届かない(検の代役)")))


(defk serving-of [prepare meter]
  {:pre [(: prepare (| Program EffectBase)) (: meter (| (get Callable #(... object)) None))] :post [(: % RecordsServing)] :tags {:context "records" :role "judgment"}}
  "入口の設定(本番の serve-records-service が env から作る物と同じ形 — 本文の上限だけ小さく・手入れは立てない・meter = 計器の
   差し替え(None = 既定の memory-meter-handler))を作るため。"
  (RecordsServing :address (HttpAddress :host "127.0.0.1" :port 0) :schema LAW-SCHEMA :prepare prepare
                  :request-handlers #() :max-bytes MAX-BYTES :maintenance None :drain-seconds 0.0
                  :meter meter))


(defk run-entry [script broken prepare meter]
  {:pre [(: script HttpScript) (: broken (| str None)) (: prepare (| Program EffectBase)) (: meter (| (get Callable #(... object)) None))] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "入口の Program を本番と同じ records-process で、検の土台の上で 1 回走らせるため。答え = #(終わりの code 札 → 受けた命令の列)。"
  (val got [])
  (val parts (ScriptedParts :script script :broken broken :note (fn [c] (.append got c))))
  (val code (run (records-process (fn [body] (scripted-foundation parts body)) (! (serving-of prepare meter)))))
  (val by-ticket {})
  (for [served (if got (get got 0) #())]
    (.setdefault by-ticket served.ticket [])
    (.append (get by-ticket served.ticket) served))
  #(code by-ticket))


(defk status-of [by-ticket ticket]
  {:pre [(: by-ticket dict) (: ticket str)] :post [(: % tuple)] :tags {:context "records" :role "judgment"}}
  "札に返った答えの status と断りの語を読むため(答えがちょうど 1 つであることも確かめる)。"
  (val served (.get by-ticket ticket []))
  (assert (= (len served) 1) #(ticket served))
  (val one (get served 0))
  (assert (isinstance one.command HttpRespond) one)
  #(one.status (.get (json.loads one.body) "error")))


(deftest test-every-scripted-request-is-answered-exactly-once-by-the-entry-program
  (<- script HttpScript (served-script))
  (val store (MemoryStore LAW-SCHEMA))
  (val outcome (! (run-entry script BROKEN-TICKET (handlers-at-once store) None)))
  (val by-ticket (get outcome 1))
  (assert (= (get outcome 0) 0) outcome)
  (assert (= (set (.keys by-ticket)) (set (gfor a script.arrivals a.ticket))) by-ticket)
  (assert (= (! (status-of by-ticket "t-health")) #(200 None)) by-ticket)
  (assert (= (! (status-of by-ticket "t-put")) #(200 None)) by-ticket)
  (assert (= (! (status-of by-ticket "t-read")) #(200 None)) by-ticket)
  (assert (= (! (status-of by-ticket "t-large")) #(400 "malformed")) by-ticket)
  (assert (= (! (status-of by-ticket BROKEN-TICKET)) #(500 "internal")) by-ticket))


(deftest test-record-operations-answer-503-until-the-store-is-prepared
  (<- script HttpScript (served-script))
  (val store (MemoryStore LAW-SCHEMA))
  ;; 用意は仮想の時計で 1 時間かかる — 台本の要求は全部その前に届き、台本が尽きて待ち受けが閉じると用意は取り消されて 0 で終わる。
  (val outcome (! (run-entry script None (handlers-after store 3600.0) None)))
  (val by-ticket (get outcome 1))
  (assert (= (get outcome 0) 0) outcome)
  (assert (= (! (status-of by-ticket "t-health")) #(200 None)) by-ticket)
  (assert (= (! (status-of by-ticket "t-put")) #(503 "store-unavailable")) by-ticket)
  (assert (= (! (status-of by-ticket "t-read")) #(503 "store-unavailable")) by-ticket))


;; --- 計器(#2709)----------------------------------------------------------------------------------------------
;; 入口は実際に送った答えを 1 つずつ、要求の種と status の counter(records_requests_<種>_<status>)に数える。数えるのは deliver の
;; 1 か所で、通常の答え・本文の断り(上限を超えた本文の 400)・答えの途中で落ちた札の 500・送りが落ちて送り直した 500 のどれも同じ所を
;; 通る(service.respond の中だけで数えると 400 と 500 を取りこぼす)。起動の時に閉じた系列を全部 0 で置く。
;; 数えは run の外の入れ物へ積まず、走り終えた後に ReadMeter で読んだ断面を run の答えにする。

(defrecord MeteredRun
  "計器の台本を 1 回走らせた答え(run の結果): code = 入口の終わりの code・served = 台本の待ち受けが受けた命令(受けた順)・
   snapshot = 走り終えた後に ReadMeter で読んだ計器の断面。"
  (#^ int code)
  (#^ tuple served)
  (#^ MeterSnapshot snapshot))


(defk served-then-read [serving broken]
  {:pre [(: serving RecordsServing) (: broken (| str None))] :post [(: % MeteredRun)] :tags {:context "records" :role "foundation"}}
  "入口の Program を走らせ、終わった後に台本の待ち受けが受けた命令と計器の断面を読んで、run の答えにするため。断面は、この run の中の
   session の値(memory-meter-handler の断面は run ごとに 1 つ)— 入口の中の既定の計器が数えた物を、外側の同じ答え手が読む。"
  (<- code int (with-handlers [(scripted-extras broken)] (serve-records serving)))
  (<- served tuple (ReadHttpServed))
  (<- snapshot MeterSnapshot (ReadMeter))
  (MeteredRun :code code :served served :snapshot snapshot))


(defk metered-entry [script broken prepare]
  {:pre [(: script HttpScript) (: broken (| str None)) (: prepare (| Program EffectBase))] :post [(: % MeteredRun)]
   :tags {:context "records" :role "foundation"}}
  "台本を入口の Program(serve-records)で 1 回走らせ、MeteredRun を run の結果として返すため(土台は他の台本の検と同じ — scheduler・
   session の値の置き場・仮想の時計・台本の止めの合図と待ち受け — に、断面を読むための計器の答え手を足した物)。"
  (<- serving RecordsServing (serving-of prepare None))
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) scripted-stop-handler (scripted-http-server script)
                                  (memory-meter-handler (MeterSettings))]
                                 (served-then-read serving broken)))))


(defk answered-counts [snapshot]
  {:pre [(: snapshot MeterSnapshot)] :post [(: % (get FrozenMap float))] :tags {:context "records" :role "judgment"}}
  "計器の断面のうち、答えを 1 つ以上数えた系列(名 → 数)を読むため(起動の時に 0 で置いただけの系列を除く)。"
  (FrozenMap (gfor #(name count) (.items snapshot.counters) :if (> count 0.0) #(name count))))


(deftest test-every-answer-is-counted-once-by-its-kind-and-status
  ;; 札 5 つ = 数え 5 つ: /healthz(other 200)・書き(write 200)・読み(read 200)・上限を超えた本文(read 400 — 本文の断りの出口)・
  ;; 答えの途中で落ちた札(read 500 — 落ちた時の出口)。閉じた系列(種 3 × status 5)は全部、断面に在る(起動の時に 0 で置いた)。
  (<- script HttpScript (served-script))
  (<- ran MeteredRun (metered-entry script BROKEN-TICKET (handlers-at-once (MemoryStore LAW-SCHEMA))))
  (assert (= ran.code 0) ran)
  (assert (= (frozenset ran.snapshot.counters) (frozenset ANSWER-METRICS)) ran.snapshot)
  (<- answered (get FrozenMap float) (answered-counts ran.snapshot))
  (assert (= answered (FrozenMap {"records_requests_other_200" 1.0 "records_requests_write_200" 1.0 "records_requests_read_200" 1.0
                                  "records_requests_read_400" 1.0 "records_requests_read_500" 1.0}))
          answered)
  ;; 表の用意の前(store-not-prepared)の記録の操作は 503 で数える(置き場に届かなかった数に入る)。壊す札は無い(読みとして 503)。
  (<- unprepared MeteredRun (metered-entry script None (handlers-after (MemoryStore LAW-SCHEMA) 3600.0)))
  (<- unprepared-answered (get FrozenMap float) (answered-counts unprepared.snapshot))
  (assert (= unprepared-answered (FrozenMap {"records_requests_other_200" 1.0 "records_requests_write_503" 1.0
                                             "records_requests_read_503" 2.0 "records_requests_read_400" 1.0}))
          unprepared-answered))


(defhandler unsendable-rows
  "読みの答えの行の値に UTF-8 にできない文字(対の無い surrogate)を入れて答える置き場の代役(答えの本文を byte にする所で落ちる形)。"
  {:tags {:context "records" :role "foundation"}}
  (ReadRow [table key]
    (resume (Row key (FrozenMap {"label" "\ud800"}) 1))))


(defk unsendable-handlers []
  {:pre [] :post [(: % Callable)] :tags {:context "records" :role "foundation"}}
  "表の用意の代役: どの書き手にも unsendable-rows を答える 書き手の名 → handler の関数を返すため(用意の I/O は無い)。"
  (fn [writer] unsendable-rows))


(deftest test-an-answer-that-cannot-be-sent-is-resent-as-500-and-counted-as-500
  ;; 失敗ケース(答えの組み立てが例外になる差し替え): 置き場の代役が、行の値に UTF-8 にできない文字を入れて読みに答える。答えの本文を
  ;; byte にする所(send-answer)で落ち、入口は標準の誤りへ 1 行名指して 500 internal を 1 度だけ送り直し、相手は 500 を受け取る。
  ;; 計器は実際に送った 500 を 1 つ数え、送れなかった 200 は数えない。
  (val read (.encode (json.dumps {"table" "parts" "key" ["p1"]}) "utf-8"))
  (val script (HttpScript :arrivals #((! (arrival "t-unsendable" "POST" READ-PATH "maker" (len read))))
                          :bodies #((ScriptedBody :ticket "t-unsendable" :data read))))
  (<- ran MeteredRun (metered-entry script None (unsendable-handlers)))
  (assert (= ran.code 0) ran)
  (val answers (tuple (gfor served ran.served :if (= served.ticket "t-unsendable") served)))
  (assert (= (tuple (gfor served answers served.status)) #(500)) ran.served)
  (assert (= (get (json.loads (. (get answers 0) body)) "reason") "答えを送る途中で落ちた") answers)
  (<- answered (get FrozenMap float) (answered-counts ran.snapshot))
  (assert (= answered (FrozenMap {"records_requests_read_500" 1.0})) answered))


;; --- 表の用意の告知(#3733)------------------------------------------------------------------------------------
;; 入口は表の用意が済んだ拍(用意の task が 書き手の名 → handler の関数を置いた後)に、RecordsPrepared を 1 度だけ出す — 告知が出たなら
;; 記録の操作に答えられる。使い手の土台は告知を受けて準備の報告を立てる。台本の待ち受けは要求が尽きると閉じるので、要求を仮想の時計で
;; 空けて渡し、用意が長い間も受けの loop を開けておく。告知の答え手は、告知の拍までに答えた札を控え、台本へ要求を 1 つ足す(告知の後の
;; 最初の要求)。控えは run の外へ積まず、走り終えた後に読んで run の答えにする(計器の検と同じ形)。

;; 台本の要求を渡す間隔の秒・用意が済むまでの秒(仮想の時計 — 早い札 2 つは用意の前に届く)・告知の拍に足す札。
(val ARRIVAL-PACE-SECONDS 2.0)
(val PREPARE-SECONDS 5.0)
(val LATE-TICKET "t-late")


(defrecord PreparedNotice
  "用意の告知 1 つの控え: address = 告知が名乗った宛先・seconds = 告知が名乗った用意の秒・answered = 告知の拍までに台本の待ち受けが
   送った答え(#(札 status) の送った順)。"
  (#^ HttpAddress address)
  (#^ float seconds)
  (#^ tuple answered))


(defrecord NoticedRun
  "告知の筋書きを 1 回走らせた答え(run の結果): code = 入口の終わりの code・served = 台本の待ち受けが受けた命令(受けた順)・
   notices = 告知の控え(出た順)。"
  (#^ int code)
  (#^ tuple served)
  (#^ tuple notices))


(defeffect ReadNotices
  "告知の控え(PreparedNotice の出た順の列)を読む(検の土台の問い — prepared-noted が答える)。"
  {:fields [] :answer tuple :tags {:context "records" :role "foundation"}})


(defhandler arrivals-paced [#^ float seconds]
  "台本の要求を seconds 秒ずつ空けて渡すため(台本の待ち受けは尽きると閉じる — 用意が長い間も受けの loop を開けておく)。"
  {:tags {:context "records" :role "foundation"}}
  ;; 引数に残す理由: 間隔は検の筋書きごとの値。
  (HttpNextRequest []
    (<- (Delay seconds))
    (<- event effect)
    (resume event)))


(defhandler prepared-noted [#^ HttpScript late]
  "表の用意の告知(RecordsPrepared)を控え、告知の拍に台本へ要求を足すため(告知の後の最初の要求 — 検の土台の代役)。"
  {:tags {:context "records" :role "foundation"}}
  ;; 引数に残す理由: late は告知の拍に足す台本(検の筋書きごとの値)。notices = 告知の控え(出た順・session の値)。
  (session var notices #())
  (RecordsPrepared [address seconds]
    (<- served tuple (ReadHttpServed))
    (:= notices (+ notices #((PreparedNotice :address address :seconds seconds
                                             :answered (tuple (gfor one served #(one.ticket one.status)))))))
    (<- (AppendHttpScript :arrivals late.arrivals :bodies late.bodies))
    (resume None))
  (ReadNotices []
    (resume notices)))


(defk read-script [ticket]
  {:pre [(: ticket str)] :post [(: % HttpScript)] :tags {:context "records" :role "judgment"}}
  "札 ticket で表 parts の行 p1 を読む要求 1 つの台本を作るため。"
  (val read (.encode (json.dumps {"table" "parts" "key" ["p1"]}) "utf-8"))
  (HttpScript :arrivals #((! (arrival ticket "POST" READ-PATH "maker" (len read))))
              :bodies #((ScriptedBody :ticket ticket :data read))))


(defk served-by-ticket [served]
  {:pre [(: served tuple)] :post [(: % dict)] :tags {:context "records" :role "judgment"}}
  "受けた命令の列を、札 → その札の命令の列(受けた順)の表にするため(status-of が読む形)。"
  (dfor ticket (frozenset (gfor one served one.ticket)) ticket (tuple (gfor one served :if (= one.ticket ticket) one))))


(defk noticed-body [serving]
  {:pre [(: serving RecordsServing)] :post [(: % NoticedRun)] :tags {:context "records" :role "foundation"}}
  "入口の Program を走らせ、終わった後に台本の待ち受けが受けた命令と告知の控えを読んで、run の答えにするため。"
  (<- code int (serve-records serving))
  (<- served tuple (ReadHttpServed))
  (<- notices tuple (ReadNotices))
  (NoticedRun :code code :served served :notices notices))


(defk noticed-run [prepare]
  {:pre [(: prepare (| Program EffectBase))] :post [(: % NoticedRun)] :tags {:context "records" :role "foundation"}}
  "早い札 2 つ(生存 t-health・読み t-early)を ARRIVAL-PACE-SECONDS 秒ずつ空けて渡す台本を、告知の控えの下で入口に走らせるため
   (土台は他の台本の検と同じ — scheduler・session の値の置き場・仮想の時計・台本の止めの合図と待ち受け)。告知が出れば、その拍に
   読み LATE-TICKET を台本へ足す。"
  (<- early HttpScript (read-script "t-early"))
  (<- late HttpScript (read-script LATE-TICKET))
  (val script (HttpScript :arrivals (+ #((! (arrival "t-health" "GET" "/healthz" None None))) early.arrivals) :bodies early.bodies))
  (<- serving RecordsServing (serving-of prepare None))
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) scripted-stop-handler (scripted-http-server script)
                                  (scripted-extras None) (arrivals-paced ARRIVAL-PACE-SECONDS) (prepared-noted late)]
                                 (noticed-body serving)))))


(deftest test-the-prepared-notice-comes-once-when-the-store-is-prepared
  ;; 用意は仮想の 5 秒: 生存(2 秒)と読み(4 秒)は用意の前に届き、読みは 503。5 秒の拍に告知が 1 つ出て、告知の拍までに送った答えは
  ;; その 2 つだけ(告知の前に記録の操作へ答えていない)。告知の拍に足した読み(6 秒に届く)は 200。
  (<- ran NoticedRun (noticed-run (handlers-after (MemoryStore LAW-SCHEMA) PREPARE-SECONDS)))
  (assert (= ran.code 0) ran)
  (assert (= (len ran.notices) 1) ran.notices)
  (val notice (get ran.notices 0))
  (assert (= notice.address (HttpAddress :host "127.0.0.1" :port 0)) notice)
  (assert (= notice.seconds PREPARE-SECONDS) notice)
  (assert (= notice.answered #(#("t-health" 200) #("t-early" 503))) notice)
  (<- by-ticket dict (served-by-ticket ran.served))
  (assert (= (! (status-of by-ticket "t-early")) #(503 "store-unavailable")) by-ticket)
  (assert (= (! (status-of by-ticket LATE-TICKET)) #(200 None)) by-ticket))


(deftest test-no-prepared-notice-comes-while-the-store-is-unprepared
  ;; 反例の側: 用意が 1 時間かかる間に台本が尽きて待ち受けが閉じると、用意は取り消され、告知は 1 つも出ない(記録の操作は 503 のまま)。
  (<- ran NoticedRun (noticed-run (handlers-after (MemoryStore LAW-SCHEMA) 3600.0)))
  (assert (= ran.code 0) ran)
  (assert (= ran.notices #()) ran.notices)
  (<- by-ticket dict (served-by-ticket ran.served))
  (assert (= (! (status-of by-ticket "t-early")) #(503 "store-unavailable")) by-ticket)
  (assert (not-in LATE-TICKET by-ticket) by-ticket))


;; --- /readyz --------------------------------------------------------------------------------------------------
;; /healthz は生存だけ(liveness)・/readyz は置き場を上限 1 秒で問う(readiness)。固まった置き場で口ごと固まらない。

(defk answers-with [value]
  {:pre [(: value bool)] :post [(: % Callable)] :tags {:context "records" :role "foundation"}}
  "直ぐに value を答える置き場の問いの代役を作るため(届く = True・届かない = False)。"
  (fn [] (answered value)))


(defk answered [value]
  {:pre [(: value bool)] :post [(: % bool)] :tags {:context "records" :role "foundation"}}
  "置き場の問いの代役の本体: value をそのまま答えるため。"
  value)


(defk hung-probe []
  {:pre [] :post [(: % bool)] :tags {:context "records" :role "foundation"}}
  "固まった置き場の問いの代役(仮想の時計で 1 時間答えない — DB の pod の入れ替えで接続が固まった形)。"
  (<- (Delay 3600.0))
  True)


(defk readyz-served [prepare readiness pressure]
  {:pre [(: prepare (| Program EffectBase)) (: readiness (| Callable None)) (: pressure (| Callable None))] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "/readyz と /healthz だけの台本を、readiness と pressure を渡した入口で走らせ、終わりの code と札ごとの答えを返すため。"
  (val script (HttpScript :arrivals #((! (arrival "t-ready" "GET" "/readyz" None None))
                                      (! (arrival "t-health" "GET" "/healthz" None None)))
                          :bodies #()))
  (val got [])
  (val parts (ScriptedParts :script script :broken None :note (fn [c] (.append got c))))
  (val serving (RecordsServing :address (HttpAddress :host "127.0.0.1" :port 0) :schema LAW-SCHEMA :prepare prepare
                               :request-handlers #() :max-bytes MAX-BYTES :maintenance None :drain-seconds 0.0
                               :readiness readiness :pressure pressure))
  (val code (run (records-process (fn [body] (scripted-foundation parts body)) serving)))
  (val by-ticket {})
  (for [served (if got (get got 0) #())]
    (.setdefault by-ticket served.ticket [])
    (.append (get by-ticket served.ticket) served))
  #(code by-ticket))


(defk readyz-status [prepare readiness]
  {:pre [(: prepare (| Program EffectBase)) (: readiness (| Callable None))] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "/readyz だけの台本を、readiness を渡した入口で走らせ、その札の答えを読むため。"
  (val ran (! (readyz-served prepare readiness None)))
  #((get ran 0) (! (status-of (get ran 1) "t-ready")) (! (status-of (get ran 1) "t-health"))))


(defk readyz-body [prepare readiness pressure]
  {:pre [(: prepare (| Program EffectBase)) (: readiness (| Callable None)) (: pressure (| Callable None))] :post [(: % dict)]
   :tags {:context "records" :role "foundation"}}
  "/readyz の答えの本文(JSON)を読むため(#1858 — 錠を待つ本数と idle in transaction の最長の秒の欄を確かめる)。"
  (val ran (! (readyz-served prepare readiness pressure)))
  (val served (.get (get ran 1) "t-ready" []))
  (assert (= (len served) 1) served)
  (json.loads (. (get served 0) body)))


(defk pressure-of [value]
  {:pre [(: value (| StorePressure PressureUnread))] :post [(: % Callable)] :tags {:context "records" :role "foundation"}}
  "直ぐに value(StorePressure | PressureUnread)を答える詰まりの読みの代役を作るため。"
  (fn [] (answered-pressure value)))


(defk answered-pressure [value]
  {:pre [(: value (| StorePressure PressureUnread))] :post [(: % (| StorePressure PressureUnread))] :tags {:context "records" :role "foundation"}}
  "詰まりの読みの代役の本体: value をそのまま答えるため。"
  value)


(deftest test-readyz-carries-the-lock-waiters-and-the-longest-idle-transaction
  ;; #1858: 届く置き場の 200 の本文に、錠を待つ本数と idle in transaction の最長の秒を載せる。memory(問いも読みも無い)は 0。
  (val store (MemoryStore LAW-SCHEMA))
  (val busy (! (readyz-body (handlers-at-once store) (! (answers-with True))
                            (! (pressure-of (StorePressure :lock-waiters 2 :idle-in-transaction-max-seconds 7.5))))))
  (assert (= busy {"status" "ready" "lockWaiters" 2 "idleInTransactionMaxSeconds" 7.5}) busy)
  (val quiet (! (readyz-body (handlers-at-once store) None None)))
  (assert (= quiet {"status" "ready" "lockWaiters" 0 "idleInTransactionMaxSeconds" 0.0}) quiet)
  ;; 失敗ケース: 詰まりの読みが答えない時は数を 0 と名乗らず、理由を載せる(200 のまま — 置き場には届いている)。
  (val unread (! (readyz-body (handlers-at-once store) (! (answers-with True)) (! (pressure-of (PressureUnread :reason "断られた"))))))
  (assert (= unread {"status" "ready" "pressureUnread" "断られた"}) unread)
  (assert (not (in "lockWaiters" unread)) unread)
  ;; 届かない置き場は今までどおり 503 で、詰まりは読まない。
  (val down (! (readyz-body (handlers-at-once store) (! (answers-with False))
                            (! (pressure-of (StorePressure :lock-waiters 9 :idle-in-transaction-max-seconds 1.0))))))
  (assert (= (.get down "error") "store-unavailable") down))


(deftest test-readyz-asks-the-store-within-one-second
  (val store (MemoryStore LAW-SCHEMA))
  ;; 届く置き場 → 200。問いの無い置き場(memory)も用意が済めば 200。
  (assert (= (get (! (readyz-status (handlers-at-once store) (! (answers-with True)))) 1) #(200 None)))
  (assert (= (get (! (readyz-status (handlers-at-once store) None)) 1) #(200 None)))
  ;; 届かない置き場 → 503。
  (assert (= (get (! (readyz-status (handlers-at-once store) (! (answers-with False)))) 1) #(503 "store-unavailable")))
  ;; 固まった置き場(1 時間答えない)→ 上限 1 秒で 503、その間も /healthz は 200(反例 = 上限の無い問いは口を固める)。
  (val hung (! (readyz-status (handlers-at-once store) hung-probe)))
  (assert (= (get hung 0) 0) hung)
  (assert (= (get hung 1) #(503 "store-unavailable")) hung)
  (assert (= (get hung 2) #(200 None)) hung)
  ;; 用意の前 → 503(/healthz は 200)。
  (val before (! (readyz-status (handlers-after store 3600.0) (! (answers-with True)))))
  (assert (= (get before 1) #(503 "store-unavailable")) before)
  (assert (= (get before 2) #(200 None)) before))


(defk reach-under-the-clock [readiness]
  {:pre [(: readiness (| Callable None))] :post [(: % StoreReach)] :tags {:context "records" :role "foundation"}}
  "置き場に届くかの公開の判断(store-reach)を、readiness を渡した入口の設定で、scheduler と仮想の時計の下で 1 回撃つため。"
  (val serving (RecordsServing :address (HttpAddress :host "127.0.0.1" :port 0) :schema LAW-SCHEMA
                               :prepare (handlers-at-once (MemoryStore LAW-SCHEMA)) :request-handlers #() :max-bytes MAX-BYTES
                               :maintenance None :drain-seconds 0.0 :readiness readiness))
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock))] (store-reach serving)))))


(deftest test-the-store-reach-judgment-answers-each-outcome-in-its-own-type
  ;; /readyz と同じ判断を、使い手(準備の報告)が撃てる公開の判断として返す(#3733): 届く = StoreReachable(詰まりの読みを持つ)・
  ;; 届かない = StoreUnreachable(理由)・上限の 1 秒の内に答えない = StoreSilent(待った上限の秒)。問いの無い置き場は届く。
  (val quiet (StorePressure :lock-waiters 0 :idle-in-transaction-max-seconds 0.0))
  (assert (= (! (reach-under-the-clock (! (answers-with True)))) (StoreReachable :pressure quiet)))
  (assert (= (! (reach-under-the-clock None)) (StoreReachable :pressure quiet)))
  (val down (! (reach-under-the-clock (! (answers-with False)))))
  (assert (= down (StoreUnreachable :reason "置き場に届かない")) down)
  ;; 固まった置き場(1 時間答えない): 上限の 1 秒で答えないと返す(問いを待ち続けない)。
  (assert (= (! (reach-under-the-clock hung-probe)) (StoreSilent :seconds 1.0))))


(deftest test-a-failed-preparation-ends-the-run-with-the-error
  (<- script HttpScript (served-script))
  (var raised None)
  (try
    (! (run-entry script None (failing-prepare) None))
    (except [e ConnectionError]
      (:= raised e)))
  (assert (is-not raised None) "表の用意が落ちても run が 0 で終わった"))
