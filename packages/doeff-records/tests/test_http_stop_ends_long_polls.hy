;;; 記録の service の入口(serve-records)が止めの合図を受けた時、保留中の変化の待ち(long-poll — WatchChanges・WatchEvents)に直ぐ
;;; 空の答えを返して終わる形の失敗ケース(#3713)。
;;;
;;; 直す前: 止めの見張り(watch-stop)は待ち受けを閉じる(HttpShutdown)だけで、保留中の long-poll は置き場の待ちのまま上限の秒
;;; (WATCH-MAX-SECONDS = 25 秒)まで答えず、受けの loop はそれを Gather で待つ — 入口の run は止めの合図から 25 秒近く終わらない
;;; (本番では worker の猶予 10 秒を使い切って SIGKILL)。
;;; 直した後: 止めの合図で、保留中の待ちは直ぐに空の答え(changes の空・eventsQuiet)を受け、run は合図から止めの見張りの間隔の内に終わる。
;;; 空の答えの位置(cursor)は頼んだ位置のままなので、client が今の取り決めのまま(同じ位置から)次の置き場へ撃ち直すと、止めの後の書きを
;;; 読める。
;;;
;;; 土台: 待ち受け = 台本(scripted-http-server — 台本が尽きると待ち受けは閉じた扱い)・止めの合図 = scripted-stop-handler(筋書きが
;;; RaiseStop を撃つ)・時計 = 仮想の時計。置き場は memory と PostgreSQL の 2 つ(PostgreSQL は使い捨ての PostgreSQL — env
;;; DOEFF_RECORDS_TEST_PG_DSN が無ければ conftest が立てる・立てられなければ理由を名指して skip)。
(require doeff-hy.macros [deftest defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import json)
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import collections.abc [Callable])
(import doeff [Program EffectBase run with-handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [Spawn scheduled])
(import doeff_core_effects.stop_signal_effects [RaiseStop])
(import doeff_core_effects.stop_signal_handlers [scripted-stop-handler])
(import doeff_core_effects.scripted_http_server [scripted-http-server])
(import doeff_core_effects.http_server_effects [HttpAddress HttpHeader HttpRequestArrived HttpScript ReadHttpServed ScriptedBody])
(import doeff_core_effects.postgres_sql [PostgresConnections postgres-sql-handler])
(import doeff_time [Delay GetMonotonic SimClock sim-time-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.pg [drop-records-tables])
(import doeff_records.values [Changes EventsQuiet ExpectAbsent WatchCursor Written])
(import doeff_records.effects [ListRows PutRow WatchChanges WatchEvents])
(import doeff_records.wire [PATH-PREFIX WATCH-MAX-SECONDS decode-answer encode-request])
(import doeff_records.http_server [CloseWaits RecordsListening RecordsPrepared RecordsServing closing-cuts-waits serve-records waits-closing])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason postgres-connections fresh-prefix run-sql prepared-store
                            records-handler-for])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
(val PG-SKIP-REASON (pg-skip-reason))

;; 止めの合図を撃つ仮想の秒・止めの見張りが合図を問い直す間隔・止めの理由。
(val STOP-AT 2.0)
(val STOP-POLL 0.5)
(val STOP-REASON "検の止め")
;; 止めの合図の後に待たない筋書き(撃ち直し)で、止めの合図を撃つ仮想の秒(待ちの上限より後 — 待ちが止めで終わったのではない事を見る)。
(val LATE-STOP-AT (* 2 WATCH-MAX-SECONDS))
;; 保留にする long-poll の札(表 parts・表 tickets の WatchChanges と列 journal の WatchEvents — どれも書かれない)。
(val CHANGES-TICKETS #("t-parts" "t-tickets"))
(val EVENTS-TICKET "t-journal")


(defrecord StoppedRun
  "止めの筋書きを 1 回走らせた見え方: seconds = 入口の run の始まりから終わりまでの仮想の秒・code = 入口の終わりの code・
   served = 台本の待ち受けが受けた命令(HttpServed — 受けた順)・cursor = 待ちの起点の位置。"
  (#^ float seconds)
  (#^ int code)
  (#^ tuple served)
  (#^ WatchCursor cursor))


(defrecord EntryEnded
  "入口の run の終わり: code = 終わりの code・served = 台本の待ち受けが受けた命令(受けた順)。"
  (#^ int code)
  (#^ tuple served))


(defhandler listening-noted
  "入口の名乗り(RecordsListening)と表の用意の告知(RecordsPrepared)を受け流すため(検の土台の代役 — 本番の土台は 1 行ずつ印字する)。"
  {:tags {:context "records" :role "foundation"}}
  (RecordsListening [address]
    (resume None))
  (RecordsPrepared [address seconds]
    (resume None)))


(defrecord Poll
  "台本の long-poll 1 本: ticket = 札・ask = 変化の待ち(client が撃つ公開 effect)。"
  (#^ str ticket)
  (#^ (| WatchChanges WatchEvents) ask))


(defrecord ScriptedPoll
  "long-poll 1 本の台本の部品: arrival = 届く要求・body = その本文の台本。"
  (#^ HttpRequestArrived arrival)
  (#^ ScriptedBody body))


(defk scripted-poll [poll]
  {:pre [(: poll Poll)] :post [(: % ScriptedPoll)] :tags {:context "records" :role "judgment"}}
  "long-poll poll を client と同じ綴り(wire の encode-request)の要求 1 つにするため。"
  (<- wire (encode-request poll.ask))
  (val data (.encode (json.dumps wire.body) "utf-8"))
  (val headers #((HttpHeader :name "X-Records-Writer" :value MAKER) (HttpHeader :name "Content-Length" :value (str (len data)))))
  (val target (+ PATH-PREFIX wire.operation))
  (ScriptedPoll :arrival (HttpRequestArrived :ticket poll.ticket :method "POST" :path target :target target :headers headers
                                             :upgrade False)
                :body (ScriptedBody :ticket poll.ticket :data data)))


(defk script-of [polls]
  {:pre [(: polls (get tuple #(Poll ...)))] :post [(: % HttpScript)] :tags {:context "records" :role "judgment"}}
  "long-poll の列を台本にするため。"
  (var parts #())
  (for [poll polls]
    (<- made ScriptedPoll (scripted-poll poll))
    (:= parts (+ parts #(made))))
  (HttpScript :arrivals (tuple (gfor part parts part.arrival)) :bodies (tuple (gfor part parts part.body))))


(defk raised-later [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "records" :role "program"}}
  "仮想の seconds 秒の後に止めの合図を撃つため(本番の SIGTERM の代役)。"
  (<- (Delay seconds))
  (<- (RaiseStop STOP-REASON))
  None)


(defk served-until-stop [serving script stop-at cursor]
  {:pre [(: serving RecordsServing) (: script HttpScript) (: stop-at float) (: cursor WatchCursor)] :post [(: % StoppedRun)]
   :tags {:context "records" :role "program"}}
  "台本の待ち受けの下で入口を走らせ、仮想の stop-at 秒に止めの合図を撃つため(cursor = 筋書きの待ちの起点 — 見え方に添える)。"
  (<- (Spawn (raised-later stop-at) :daemon True))
  (<- began float (GetMonotonic))
  (<- ended EntryEnded (with-handlers [(scripted-http-server script) listening-noted] (served-then-read serving)))
  (<- finished float (GetMonotonic))
  (StoppedRun :seconds (- finished began) :code ended.code :served ended.served :cursor cursor))


(defk served-then-read [serving]
  {:pre [(: serving RecordsServing)] :post [(: % EntryEnded)] :tags {:context "records" :role "program"}}
  "入口を走らせ、終わった後に台本の待ち受けが受けた命令を読むため。"
  (<- code int (serve-records serving))
  (<- served tuple (ReadHttpServed))
  (EntryEnded :code code :served served))


(defk answers-of [served ticket]
  {:pre [(: served tuple) (: ticket str)] :post [(: % tuple)] :tags {:context "records" :role "judgment"}}
  "受けた命令の列から、札 ticket に返った答えの列(受けた順)を引くため。"
  (tuple (gfor one served :if (= one.ticket ticket) one)))


(defk serving-over [handler-for]
  {:pre [(: handler-for Callable)] :post [(: % RecordsServing)] :tags {:context "records" :role "judgment"}}
  "書き手の名 → 記録の handler の関数 handler-for の上の入口の設定を作るため(用意の I/O は無い・手入れは立てない)。"
  (RecordsServing :address (HttpAddress :host "127.0.0.1" :port 0) :schema LAW-SCHEMA :prepare (prepared-now handler-for)
                  :request-handlers #() :max-bytes 65536 :maintenance None :stop-poll-seconds STOP-POLL :drain-seconds 0.0))


(defk prepared-now [handler-for]
  {:pre [(: handler-for Callable)] :post [(: % Callable)] :tags {:context "records" :role "foundation"}}
  "表の用意の代役(用意し終えた置き場 — 直ぐに 書き手の名 → handler の関数を返す)。"
  handler-for)


(defk start-cursor [handler-for]
  {:pre [(: handler-for Callable)] :post [(: % WatchCursor)] :tags {:context "records" :role "program"}}
  "表 parts の今の位置(待ちの起点)を書き手 MAKER として読むため。"
  (<- page (with-handlers [(handler-for MAKER)] (ListRows "parts")))
  (WatchCursor page.epoch page.sequence))


(defk long-polls-stopped [handler-for]
  {:pre [(: handler-for Callable)] :post [(: % StoppedRun)] :tags {:context "records" :role "program"}}
  "書かれない表と列の long-poll を 3 本保留にした入口に、仮想の STOP-AT 秒で止めの合図を送るため。"
  (<- cursor WatchCursor (start-cursor handler-for))
  (<- script HttpScript (script-of #((Poll :ticket (get CHANGES-TICKETS 0) :ask (WatchChanges #("parts") cursor :timeout WATCH-MAX-SECONDS))
                                     (Poll :ticket (get CHANGES-TICKETS 1) :ask (WatchChanges #("tickets") cursor :timeout WATCH-MAX-SECONDS))
                                     (Poll :ticket EVENTS-TICKET :ask (WatchEvents "journal" :after 0 :timeout WATCH-MAX-SECONDS)))))
  (<- serving RecordsServing (serving-over handler-for))
  (<- ran StoppedRun (served-until-stop serving script STOP-AT cursor))
  ran)


(defk asked-again-after-a-write [handler-for cursor]
  {:pre [(: handler-for Callable) (: cursor WatchCursor)] :post [(: % StoppedRun)] :tags {:context "records" :role "program"}}
  "止めの後に 1 行書き、止めで受けた空の答えの位置 cursor から、次の入口(同じ置き場)へ撃ち直すため(client の今の取り決めの撃ち直し)。"
  (<- written (with-handlers [(handler-for MAKER)] (PutRow "parts" #("after-stop") (FrozenMap {"label" "a"}) (ExpectAbsent))))
  (assert (isinstance written Written) written)
  (<- script HttpScript (script-of #((Poll :ticket "t-again" :ask (WatchChanges #("parts") cursor :timeout WATCH-MAX-SECONDS)))))
  (<- serving RecordsServing (serving-over handler-for))
  (<- ran StoppedRun (served-until-stop serving script LATE-STOP-AT cursor))
  ran)


(defk decoded [ran ticket operation]
  {:pre [(: ran StoppedRun) (: ticket str) (: operation str)] :post [(: % (| Changes EventsQuiet))] :tags {:context "records" :role "judgment"}}
  "札 ticket にちょうど 1 つ返った 200 の答えを、操作 operation の答えとして読むため。"
  (<- answers tuple (answers-of ran.served ticket))
  (assert (= (len answers) 1) #(ticket ran.served))
  (val one (get answers 0))
  (assert (= one.status 200) one)
  (<- answer (decode-answer operation (json.loads one.body)))
  answer)


(defk stopped-quickly-with-empty-answers [ran]
  {:pre [(: ran StoppedRun)] :post [(: % None)] :tags {:context "records" :role "judgment"}}
  "止めの筋書きの断言: run は止めの合図から見張りの間隔の内に 0 で終わり(待ちの上限の 25 秒を待たない)、保留の札はどれも空の答えを
   ちょうど 1 つ受ける(changes は頼んだ位置のまま)。"
  (assert (= ran.code 0) ran)
  (assert (<= ran.seconds (+ STOP-AT STOP-POLL)) #("止めの合図から待ちの上限まで待った" ran.seconds))
  (for [ticket CHANGES-TICKETS]
    (<- changes (decoded ran ticket "watch-changes"))
    (assert (= changes (Changes #() ran.cursor)) #(ticket changes)))
  (<- quiet (decoded ran EVENTS-TICKET "watch-events"))
  (assert (= quiet (EventsQuiet)) quiet)
  None)


(defk read-again-at-once [ran]
  {:pre [(: ran StoppedRun)] :post [(: % None)] :tags {:context "records" :role "judgment"}}
  "撃ち直しの断言: 止めの後の書きを、同じ位置からの撃ち直しが直ぐに(止めの合図を待たずに)読む。"
  (assert (= ran.code 0) ran)
  (assert (< ran.seconds STOP-AT) ran.seconds)
  (<- changes (decoded ran "t-again" "watch-changes"))
  (assert (isinstance changes Changes) changes)
  (assert (= (lfor item changes.items item.key) [#("after-stop")]) changes)
  None)


(defk on-memory [program]
  {:pre [(: program (| Program EffectBase))] :post [(: % "program の答え")] :tags {:context "records" :role "foundation"}}
  "memory の置き場の筋書きの土台: scheduler・session の値の置き場・仮想の時計・台本の止めの合図。"
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) scripted-stop-handler] program))))


(deftest test-stop-answers-pending-long-polls-at-once-on-memory
  (val store (MemoryStore LAW-SCHEMA))
  (val handler-for (fn [writer] (memory-records-handler store writer)))
  (<- stopped StoppedRun (on-memory (long-polls-stopped handler-for)))
  (<- (stopped-quickly-with-empty-answers stopped))
  (<- again StoppedRun (on-memory (asked-again-after-a-write handler-for stopped.cursor)))
  (<- (read-again-at-once again)))


(defrecord LateWatch
  "止めの後に来た待ち 1 本の見え方: seconds = 待ちに掛かった仮想の秒・answer = 待ちの答え。"
  (#^ float seconds)
  (#^ (| Changes EventsQuiet) answer))


(defk watched-after-close [handler-for]
  {:pre [(: handler-for Callable)] :post [(: % LateWatch)] :tags {:context "records" :role "program"}}
  "入口が止めの印を置いた後に、要求の記録の handler の中で変化の待ちを 1 本撃つため(入口の要求ごとの包みと同じ並び — 待ちの切り手の
   内側に記録の handler)。"
  (<- cursor WatchCursor (start-cursor handler-for))
  (<- (CloseWaits :reason STOP-REASON))
  (<- began float (GetMonotonic))
  (<- answer (with-handlers [closing-cuts-waits (handler-for MAKER)] (WatchChanges #("parts") cursor :timeout WATCH-MAX-SECONDS)))
  (<- ended float (GetMonotonic))
  (LateWatch :seconds (- ended began) :answer answer))


(deftest test-a-long-poll-after-the-stop-answers-without-waiting
  ;; 止めの印を置いた後に届いた待ちは、待たずに空の答えを返す(止めの印を鳴らした後の新しい long-poll が 25 秒の待ちを作らない)。
  (val store (MemoryStore LAW-SCHEMA))
  (val handler-for (fn [writer] (memory-records-handler store writer)))
  (<- late LateWatch (on-memory (with-handlers [waits-closing] (watched-after-close handler-for))))
  (assert (= late.seconds 0.0) late)
  (assert (and (isinstance late.answer Changes) (= late.answer.items #())) late))


(defk on-postgres [connections program]
  {:pre [(: connections PostgresConnections) (: program (| Program EffectBase))] :post [(: % "program の答え")] :tags {:context "records" :role "foundation"}}
  "PostgreSQL の置き場の筋書きの土台: memory の土台に本物の SQL の答え手を足した物。"
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) scripted-stop-handler (postgres-sql-handler connections)]
                                 program))))


(deftest test-stop-answers-pending-long-polls-at-once-on-postgres
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 4))
  (val store (prepared-store connections (fresh-prefix)))
  (val handler-for (records-handler-for store))
  (try
    (<- stopped StoppedRun (on-postgres connections (long-polls-stopped handler-for)))
    (<- (stopped-quickly-with-empty-answers stopped))
    (<- again StoppedRun (on-postgres connections (asked-again-after-a-write handler-for stopped.cursor)))
    (<- (read-again-at-once again))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
