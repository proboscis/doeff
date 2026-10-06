;; 名指した追記の列の末尾(tails)を WatchChanges の答えに載せる契約(#3718)の失敗ケース。使い手は 1 拍ごとに待ちの長さ 0 の
;; WatchChanges と、列全体の最後の出来事の刻を得るためだけの ReadEvents を撃っていた — 後の方を無くすため、WatchChanges の
;; 要求に streams(末尾を知りたい列の名)を足し、答え Changes の tails に名指した列ごとの末尾(StreamTail = 位置と刻 / StreamTailEmpty =
;; 生きている出来事が無い)を名指した順で載せる(ReadStreamEnd の答え StreamEnd の形は替えない)。確かめること:
;;   (a) 列を名指した WatchChanges(待ちの長さ 0)の tails は、同じ時点の ReadEvents の最後の出来事と同じ位置と刻。列に積んだ後に読み直すと
;;       新しい末尾
;;   (b) 列に生きている出来事が無い(空・期限が全部過ぎた)時、tails はその列の StreamTailEmpty
;;   (c) 列を名指さない WatchChanges の答えは tails が空で、wire の答えに鍵 tails が無い(古い client の形のまま)— 要求にも鍵 streams が無い
;;   (d) client: 要求が名指したのに答えに tails が無い・数や列が合わない・名指さないのに在る時は WireMalformed で上がる(黙って空にしない)
;;   (e) 宣言に無い列を名指すと、表と同じく UndeclaredTable(HTTP の口越しは 404 not-found の断り)
;;   (f) 長い待ち(timeout > 0)は、名指した列への追記だけでは答えを返さない(表の変化か期限で返る — 列への追記で待ち手を起こさない)
;; 置き場は memory・PostgreSQL・HTTP の口越し(memory / PostgreSQL)の 4 つ(tests/interpreters.hy — PostgreSQL は使い捨ての置き場)。
(require doeff-hy.macros [deftest defhandler defk <- val var])
(import json)
(import doeff [with_handlers])
(import doeff_core_effects.scheduler [Spawn Wait])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse])
(import doeff_time [SimClock sim-time-handler Delay GetMonotonic])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [Appended Changes Event Events ExpectAbsent StreamEmpty StreamTail StreamTailEmpty UndeclaredTable WatchCursor
                              Written])
(import doeff_records.effects [AppendEvent ListRows PutRow ReadEvents ReadStreamEnd WatchChanges])
(import doeff_records.laws [LAW-SCHEMA LawHarness MAKER PULSE-KEEP-SECONDS as-writer law-watch-tails-match-last-events])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.service [RecordsService respond HttpRequest :as ServiceRequest])
(import doeff_records.wire [WireMalformed encode-answer encode-request])
(import doeff_records.http_client [RecordsEndpoint http-records-handler records-unwaited])
(import tests.interpreters [LawSetup])

;; (f) の待ちの秒と、待ちの間に列へ積む刻・表へ書く刻(待ちの始まりからの秒)。
(val QUIET-WAIT 5.0)
(val APPEND-AT 1)
(val WRITE-AT 3)


(defk cursor-now [harness]
  {:pre [(: harness LawHarness)] :post [(: % WatchCursor)] :tags {:context "records" :role "program"}}
  "表 parts の今の位置(待ちの起点)を読むため。"
  (<- page (as-writer harness MAKER (ListRows "parts")))
  (WatchCursor page.epoch page.sequence))


(defk watched [harness cursor streams]
  {:pre [(: harness LawHarness) (: cursor WatchCursor) (: streams tuple)] :post [(: % Changes)] :tags {:context "records" :role "program"}}
  "表 parts の変化を待ちの長さ 0 で、列 streams の末尾つきで読むため。"
  (<- answer (as-writer harness MAKER (WatchChanges #("parts") cursor :streams streams)))
  (assert (isinstance answer Changes) answer)
  answer)


(defk last-event-of [harness stream]
  {:pre [(: harness LawHarness) (: stream str)] :post [(: % (| Event None))] :tags {:context "records" :role "program"}}
  "列 stream の最後の生きている出来事を ReadEvents で読むため(tails と比べる基準 — 保持の期限を過ぎた出来事は ReadEvents に出ない)。
   無ければ None。"
  (<- read (as-writer harness MAKER (ReadEvents stream)))
  (match read
    (Events :items #()) None
    (Events :items items) (get items -1)))


;; --- (a) ----------------------------------------------------------------------------------------------------

(deftest test-a-named-tails-match-the-last-event-and-follow-appends
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- cursor WatchCursor (cursor-now harness))
  (<- (as-writer harness MAKER (AppendEvent "journal" "tail-1" {"n" 1})))
  (<- (Delay 1))
  (<- second Appended (as-writer harness MAKER (AppendEvent "journal" "tail-2" {"n" 2})))
  (<- first-read Changes (watched harness cursor #("journal")))
  (<- first-last (last-event-of harness "journal"))
  (assert (and (isinstance first-last Event) (= first-last.sequence second.sequence)) first-last)
  (assert (= first-read.tails #((StreamTail :stream "journal" :sequence first-last.sequence :at first-last.at))) #(first-read first-last))
  ;; 列に積んだ後の読み直しは新しい末尾(位置も刻も進む)— 表の変化は無いので items は空のまま。
  (<- (Delay 1))
  (<- third Appended (as-writer harness MAKER (AppendEvent "journal" "tail-3" {"n" 3})))
  (<- again Changes (watched harness cursor #("journal")))
  (<- moved (last-event-of harness "journal"))
  (assert (and (isinstance moved Event) (= moved.sequence third.sequence) (= moved.at (+ first-last.at 1000))) #(first-last moved))
  (assert (= again.tails #((StreamTail :stream "journal" :sequence moved.sequence :at moved.at))) #(again moved))
  (assert (= again.items #()) again))


;; --- (b) ----------------------------------------------------------------------------------------------------

(deftest test-b-a-stream-without-living-events-is-marked-empty
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- cursor WatchCursor (cursor-now harness))
  ;; 空の列: 名指した順に、どちらも空の印。
  (<- empty Changes (watched harness cursor #("pulses" "journal")))
  (assert (= empty.tails #((StreamTailEmpty :stream "pulses") (StreamTailEmpty :stream "journal"))) empty)
  ;; pulses に 1 つ積む: pulses は末尾・journal は空のまま。
  (<- beat Appended (as-writer harness MAKER (AppendEvent "pulses" "beat-1" {"n" 1})))
  (<- live Changes (watched harness cursor #("pulses" "journal")))
  (<- live-last (last-event-of harness "pulses"))
  (assert (and (isinstance live-last Event) (= live-last.sequence beat.sequence)) live-last)
  (assert (= live.tails #((StreamTail :stream "pulses" :sequence beat.sequence :at live-last.at) (StreamTailEmpty :stream "journal")))
          live)
  ;; 保持の期限(PULSE-KEEP-SECONDS)が全部過ぎた列は、回収の前でも空の印(ReadStreamEnd の StreamEmpty・ReadEvents の空と同じ)。
  (<- (Delay (+ PULSE-KEEP-SECONDS 1)))
  (<- expired Changes (watched harness cursor #("pulses" "journal")))
  (<- expired-end (as-writer harness MAKER (ReadStreamEnd "pulses")))
  (<- expired-last (last-event-of harness "pulses"))
  (assert (and (= expired-end (StreamEmpty)) (is expired-last None)) #(expired-end expired-last))
  (assert (= expired.tails #((StreamTailEmpty :stream "pulses") (StreamTailEmpty :stream "journal"))) expired))


;; --- (c) ----------------------------------------------------------------------------------------------------

(deftest test-c-an-unnamed-watch-has-no-tails
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- cursor WatchCursor (cursor-now harness))
  (<- (as-writer harness MAKER (AppendEvent "journal" "plain-1" {"n" 1})))
  (<- plain (as-writer harness MAKER (WatchChanges #("parts") cursor)))
  (assert (and (isinstance plain Changes) (= plain.tails #())) plain))


(deftest test-c-the-wire-has-no-streams-or-tails-key-unless-streams-are-named
  ;; 要求: 名指さない WatchChanges の本文に鍵 streams が無い(古い記録の service の形のまま)。名指せば streams。
  (val cursor (WatchCursor 1 0))
  (<- plain-request (encode-request (WatchChanges #("parts") cursor)))
  (assert (not-in "streams" plain-request.body) plain-request)
  (<- named-request (encode-request (WatchChanges #("parts") cursor :streams #("journal" "pulses"))))
  (assert (= (get named-request.body "streams") ["journal" "pulses"]) named-request)
  ;; 答え: tails が空の Changes の本文に鍵 tails が無い(古い client の形のまま)。
  (<- plain-answer (encode-answer (Changes #() cursor #())))
  (assert (= plain-answer {"kind" "changes" "items" [] "cursor" {"epoch" 1 "sequence" 0}}) plain-answer)
  (<- named-answer (encode-answer (Changes #() cursor #((StreamTail :stream "journal" :sequence 4 :at 1700000000123)
                                                       (StreamTailEmpty :stream "pulses")))))
  (assert (= (get named-answer "tails") [{"kind" "streamTail" "stream" "journal" "sequence" 4 "at" 1700000000123}
                                         {"kind" "streamTailEmpty" "stream" "pulses"}])
          named-answer))


(deftest test-c-the-service-answer-carries-tails-only-when-streams-are-named
  ;; 記録の service の口(respond)の 200 の本文: 名指さない要求の答えに鍵 tails が無く、名指した要求の答えは名指した順の tails。
  (val store (MemoryStore LAW-SCHEMA))
  (val service (RecordsService LAW-SCHEMA (fn [writer] (memory-records-handler store writer))))
  (val base {"tables" ["parts"] "cursor" {"epoch" 1 "sequence" 0} "timeout" 0.0})
  (var bodies [])
  (for [body [base (| base {"streams" ["journal"]})]]
    (<- answer (with_handlers [(sim-time-handler :clock (SimClock))]
                              (respond service (ServiceRequest "POST" "/v1/records/watch-changes" (.encode (json.dumps body) "utf-8")
                                                               MAKER))))
    (assert (= answer.status 200) answer)
    (:= bodies (+ bodies [(json.loads answer.body)])))
  (assert (not-in "tails" (get bodies 0)) bodies)
  (assert (= (get (get bodies 1) "tails") [{"kind" "streamTailEmpty" "stream" "journal"}]) bodies))


;; --- (d) ----------------------------------------------------------------------------------------------------

(defhandler canned-watch-answer [#^ dict body]
  ;; 引数に残す理由: 検ごとに違う、記録の service の答えの本文(壊れた service の顔)。
  "外の世界だけを差し替える答え手: client が送った HTTP の要求に、本文 body の 200 で答えるため(記録の service が tails を落とした・
   数を違えた顔)。"
  {:tags {:context "records" :role "foundation"}}
  (HttpRequest []
    (val text (json.dumps body))
    (resume (HttpResponse 200 {} (.encode text "utf-8") text effect.url 0.0))))


(deftest test-d-the-client-refuses-tails-that-do-not-match-the-request
  (val endpoint (RecordsEndpoint "http://records.test" :writer MAKER))
  (val cursor-json {"epoch" 1 "sequence" 0})
  (val named (WatchChanges #("parts") (WatchCursor 1 0) :streams #("journal" "pulses")))
  (val unnamed (WatchChanges #("parts") (WatchCursor 1 0)))
  (val journal {"kind" "streamTail" "stream" "journal" "sequence" 4 "at" 1700000000123})
  (val pulses {"kind" "streamTailEmpty" "stream" "pulses"})
  ;; 名指したのに鍵 tails が無い・数が足りない・列の順が違う・名指さないのに在る。
  (for [#(ask body) [#(named {"kind" "changes" "items" [] "cursor" cursor-json})
                     #(named {"kind" "changes" "items" [] "cursor" cursor-json "tails" [journal]})
                     #(named {"kind" "changes" "items" [] "cursor" cursor-json "tails" [pulses journal]})
                     #(unnamed {"kind" "changes" "items" [] "cursor" cursor-json "tails" [journal]})]]
    (try
      (<- answer (with_handlers [(sim-time-handler :clock (SimClock)) records-unwaited (canned-watch-answer body)
                                 (http-records-handler endpoint)]
                                ask))
      (assert False (.format "合わない tails の答えが通った: {!r} → {!r}" body answer))
      (except [refused WireMalformed]
        (assert (in "tails" (str refused)) refused))))
  ;; 合っている答えは通る(比べの基準)。
  (<- matched (with_handlers [(sim-time-handler :clock (SimClock)) records-unwaited
                              (canned-watch-answer {"kind" "changes" "items" [] "cursor" cursor-json "tails" [journal pulses]})
                              (http-records-handler endpoint)]
                             named))
  (assert (= matched (Changes #() (WatchCursor 1 0) #((StreamTail :stream "journal" :sequence 4 :at 1700000000123)
                                                      (StreamTailEmpty :stream "pulses"))))
          matched))


;; --- (e) ----------------------------------------------------------------------------------------------------

(deftest test-e-an-undeclared-stream-is-refused-like-a-table
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- cursor WatchCursor (cursor-now harness))
  (var refused None)
  (try
    (<- answer (as-writer harness MAKER (WatchChanges #("parts") cursor :streams #("journal" "nowhere"))))
    (assert False (.format "宣言に無い列を名指した待ちが答えを返した: {!r}" answer))
    (except [error UndeclaredTable]
      (:= refused error)))
  (assert (= #(refused.tables refused.streams) #(#() #("nowhere"))) refused))


;; --- (f) ----------------------------------------------------------------------------------------------------

(defk late-append-only [harness]
  {:pre [(: harness LawHarness)] :post [(: % Appended)] :tags {:context "records" :role "program"}}
  "待ちの間に、名指した列 journal へだけ積むため(APPEND-AT 秒後)。"
  (<- (Delay APPEND-AT))
  (<- appended (as-writer harness MAKER (AppendEvent "journal" "late-only" {"n" 1})))
  appended)


(defk late-append-then-write [harness]
  {:pre [(: harness LawHarness)] :post [(: % Written)] :tags {:context "records" :role "program"}}
  "待ちの間に、名指した列 journal へ積み(APPEND-AT 秒後)、続いて待つ表 parts へ 1 行書くため(WRITE-AT 秒後)。"
  (<- (Delay APPEND-AT))
  (<- (as-writer harness MAKER (AppendEvent "journal" "late-first" {"n" 2})))
  (<- (Delay (- WRITE-AT APPEND-AT)))
  (<- written (as-writer harness MAKER (PutRow "parts" #("late-row") (FrozenMap {"label" "l"}) (ExpectAbsent))))
  written)


(deftest test-f-an-append-to-a-named-stream-does-not-end-a-long-wait
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- cursor WatchCursor (cursor-now harness))
  ;; 列への追記だけ: 待ちは期限まで続き、期限の答えの tails は答えを返す時点の末尾(待ちの間の追記を含む)。
  (<- appender (Spawn (late-append-only harness)))
  (<- began float (GetMonotonic))
  (<- quiet (as-writer harness MAKER (WatchChanges #("parts") cursor :timeout QUIET-WAIT :streams #("journal"))))
  (<- ended float (GetMonotonic))
  (<- appended Appended (Wait appender))
  (assert (>= (- ended began) QUIET-WAIT) #((- ended began) quiet))
  (assert (and (isinstance quiet Changes) (= quiet.items #())) quiet)
  (<- quiet-last (last-event-of harness "journal"))
  (assert (and (isinstance quiet-last Event) (= quiet-last.sequence appended.sequence)) quiet-last)
  (assert (= quiet.tails #((StreamTail :stream "journal" :sequence quiet-last.sequence :at quiet-last.at))) #(quiet quiet-last))
  ;; 列への追記の後に表を書く: 表の変化で返り、tails は先に積んだ列の末尾。
  (<- writer (Spawn (late-append-then-write harness)))
  (<- woke (as-writer harness MAKER (WatchChanges #("parts") quiet.cursor :timeout 30.0 :streams #("journal"))))
  (<- written Written (Wait writer))
  (assert (and (isinstance woke Changes) (= (lfor item woke.items #(item.key item.version)) [#(#("late-row") written.version)])) woke)
  (<- woke-last (last-event-of harness "journal"))
  (assert (and (isinstance woke-last Event)
               (= woke.tails #((StreamTail :stream "journal" :sequence woke-last.sequence :at woke-last.at))))
          #(woke woke-last))
  (assert (> woke-last.sequence appended.sequence) #(woke-last appended)))


;; --- 法 18(doeff_records.laws の law-watch-tails-match-last-events)を 4 つの置き場で回す -------------------------------------------

(deftest test-the-tails-law-holds-on-every-store
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-watch-tails-match-last-events harness))
  (assert transcript))
