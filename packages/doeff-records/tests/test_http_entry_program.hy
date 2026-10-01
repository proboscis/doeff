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
(require doeff-hy.macros [deftest defhandler defk <- val])
(require doeff-hy.record [defrecord])
(import json)
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import doeff [Program EffectBase run with-handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.stop_signal_handlers [scripted-stop-handler])
(import doeff_core_effects.scripted_http_server [scripted-http-server])
(import doeff_core_effects.http_server_effects [HttpAddress HttpHeader HttpReadBody HttpRequestArrived HttpRespond HttpScript ReadHttpServed
                                                ScriptedBody])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_records.laws [LAW-SCHEMA])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.http_server [RecordsServing RecordsListening])
(import doeff_records.main [records-process])
(import tests.interpreters [LAW-TOKENS law-roster])

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
  "札 broken の本文の読みに語彙の外の答えを返し、待ち受けの名乗りを受け流すため(検の土台の代役)。他の札の読みは外側の台本の待ち受けへ回す。"
  {:tags {:context "records" :role "foundation"}}
  ;; 引数に残す理由: 壊す札は検の筋書きごとの値。
  (HttpReadBody [ticket max-bytes] :when (= ticket broken)
    (resume "語彙の外の答え"))
  (RecordsListening [address]
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
  "台本の要求 1 つ(who = 身元の名簿の書き手・length = 宣言する本文の長さ)を作るため。"
  (val auth (if (is who None) #() #((HttpHeader :name "Authorization" :value (+ "Bearer " (get LAW-TOKENS who))))))
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


(defk serving-of [prepare]
  {:pre [(: prepare (| Program EffectBase))] :post [(: % RecordsServing)] :tags {:context "records" :role "judgment"}}
  "入口の設定(本番の serve-records-service が env から作る物と同じ形 — 本文の上限だけ小さく・手入れは立てない)を作るため。"
  (RecordsServing :address (HttpAddress :host "127.0.0.1" :port 0) :schema LAW-SCHEMA :roster (law-roster) :prepare prepare
                  :request-handlers #() :max-bytes MAX-BYTES :maintenance None :stop-poll-seconds 1.0 :drain-seconds 0.0))


(defk run-entry [script broken prepare]
  {:pre [(: script HttpScript) (: broken (| str None)) (: prepare (| Program EffectBase))] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "入口の Program を本番と同じ records-process で、検の土台の上で 1 回走らせるため。答え = #(終わりの code 札 → 受けた命令の列)。"
  (val got [])
  (val parts (ScriptedParts :script script :broken broken :note (fn [c] (.append got c))))
  (val code (run (records-process (fn [body] (scripted-foundation parts body)) (! (serving-of prepare)))))
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
  (val outcome (! (run-entry script BROKEN-TICKET (handlers-at-once store))))
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
  (val outcome (! (run-entry script None (handlers-after store 3600.0))))
  (val by-ticket (get outcome 1))
  (assert (= (get outcome 0) 0) outcome)
  (assert (= (! (status-of by-ticket "t-health")) #(200 None)) by-ticket)
  (assert (= (! (status-of by-ticket "t-put")) #(503 "store-unavailable")) by-ticket)
  (assert (= (! (status-of by-ticket "t-read")) #(503 "store-unavailable")) by-ticket))


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


(defk readyz-status [prepare readiness]
  {:pre [(: prepare (| Program EffectBase)) (: readiness (| Callable None))] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "/readyz だけの台本を、readiness を渡した入口で走らせ、その札の答えを読むため。"
  (val script (HttpScript :arrivals #((! (arrival "t-ready" "GET" "/readyz" None None))
                                      (! (arrival "t-health" "GET" "/healthz" None None)))
                          :bodies #()))
  (val got [])
  (val parts (ScriptedParts :script script :broken None :note (fn [c] (.append got c))))
  (val serving (RecordsServing :address (HttpAddress :host "127.0.0.1" :port 0) :schema LAW-SCHEMA :roster (law-roster) :prepare prepare
                               :request-handlers #() :max-bytes MAX-BYTES :maintenance None :stop-poll-seconds 1.0 :drain-seconds 0.0
                               :readiness readiness))
  (val code (run (records-process (fn [body] (scripted-foundation parts body)) serving)))
  (val by-ticket {})
  (for [served (if got (get got 0) #())]
    (.setdefault by-ticket served.ticket [])
    (.append (get by-ticket served.ticket) served))
  #(code (! (status-of by-ticket "t-ready")) (! (status-of by-ticket "t-health"))))


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


(deftest test-a-failed-preparation-ends-the-run-with-the-error
  (<- script HttpScript (served-script))
  (var raised None)
  (try
    (! (run-entry script None (failing-prepare)))
    (except [e ConnectionError]
      (:= raised e)))
  (assert (is-not raised None) "表の用意が落ちても run が 0 で終わった"))
