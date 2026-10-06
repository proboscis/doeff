;; 記録の service の HTTP の口越しの変化の待ち(long-poll — #3074)の失敗ケース。client は待ちの秒つきで service へ撃ち、待つのは
;; service の中の置き場の待ち(client は眠らず、読み直しを繰り返さない)。確かめること:
;;   待ちの最中に別の書き手が書くと、その刻に答える(待ちの要求は 1 回)
;;   変化が無ければ service の上限の秒(WATCH-MAX-SECONDS)で空の答えを返し、client は残りの秒でもう一度撃つ(列の待ちも同じ)
;;   待ちの要求が届かなければ(接続が切れた)Unreachable を返し、同じ位置から待ち直すと、切れた間の書きを取りこぼさない
;; 反例 = 前の形(待たない問い timeout 0 を poll-seconds ごとに撃ち直す・列の待ちは ReadEvents の読み直し)は、待ちの要求の秒の列が
;; 0 の長い列(列の待ちは待ちの要求 0 本)になり、下の列の確かめが赤になる。
;; 組は http-memory(tests/interpreters.hy — client の要求を同じ scheduler の中で service の respond に渡し、待ちと書きが 1 つの仮想の
;; 時計の上で進む)。
(require doeff-hy.macros [deftest defhandler defeffect defk <- val])
(require doeff-hy.record [defrecord])
(import json)
(import pytest)
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff [with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [Spawn Wait])
(import doeff_core_effects.http_effects [HttpRequest HttpFailed HttpFailureKind])
(import doeff_hy.frozen [FrozenMap])
(import doeff_time [GetMonotonic])
(import doeff_records.values [Changes EventsQuiet Unreachable WatchCursor ExpectAbsent])
(import doeff_records.effects [ListRows PutRow WatchChanges WatchEvents AppendEvent])
(import doeff_records.laws [LawHarness MAKER as-writer late-write])
(import doeff_records.wire [WATCH-MAX-SECONDS])
(import doeff_records.http_client [RecordsEndpoint])
(import tests.interpreters [LawSetup])

;; 待ちの秒(上限 WATCH-MAX-SECONDS = 25 秒の 2 倍より長い — 要求が 3 つに分かれる)。
(val LONG-WAIT 60.0)


(defeffect WatchRequestsSoFar
  "ここまでに client が送った変化の待ちの要求の、待ちの秒の列を問う — 検だけの問い(watch-requests-counted が答える)。"
  {:fields []
   :answer tuple
   :tags {:context "records" :role "intent"}})


(defhandler watch-requests-counted
  "外の世界の計器: client が送った変化の待ちの要求(watch-changes・watch-events)の待ちの秒を送った順に覚え、要求はそのまま外へ渡す
   (答えには触れない)。覚えた列は WatchRequestsSoFar で読む。"
  {:tags {:context "records" :role "foundation"}}
  (session var sent #())
  (HttpRequest []
    (when (in "/watch-" effect.url)
      (:= sent (+ sent #((get (json.loads effect.body) "timeout")))))
    (<- answer effect)
    (resume answer))
  (WatchRequestsSoFar []
    (resume sent)))


(defhandler first-watch-cut
  "外の世界の故障: client が送った最初の変化の待ちの要求だけを、届かない失敗(接続が切れた)で答える。他の要求はそのまま外へ渡す。"
  {:tags {:context "records" :role "foundation"}}
  (session var failed False)
  (HttpRequest []
    (if (and (not failed) (in "/watch-" effect.url))
        (do (:= failed True)
            (resume (HttpFailed :url effect.url :detail "接続が切れた(検の故障)" :kind HttpFailureKind.CONNECT-FAILED)))
        (do (<- answer effect)
            (resume answer)))))


(defrecord Waited
  "待ち 1 回の見え方: seconds = 待ちに掛かった仮想の秒・answer = 待ちの答え・sent = 送った待ちの要求の待ちの秒の列。"
  (#^ float seconds)
  (#^ object answer)
  (#^ tuple sent))


(defk watched-once [harness ask]
  {:pre [(: harness LawHarness) (: ask (| WatchChanges WatchEvents))] :post [(: % Waited)] :tags {:context "records" :role "program"}}
  "書き手 MAKER として待ち ask を 1 回撃ち、掛かった秒・答え・送った待ちの要求を見るため(数えの tap は client の外側)。"
  (<- began float (GetMonotonic))
  (<- answer (with_handlers [(state) watch-requests-counted]
                            (do-watch harness ask)))
  (<- ended float (GetMonotonic))
  (Waited :seconds (- ended began) :answer (get answer 0) :sent (get answer 1)))


(defk do-watch [harness ask]
  {:pre [(: harness LawHarness) (: ask (| WatchChanges WatchEvents))] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "数えの tap の内側で待ちを撃ち、答えと送った待ちの要求の列を組にして返すため。"
  (<- answer (as-writer harness MAKER ask))
  (<- sent tuple (WatchRequestsSoFar))
  #(answer sent))


(defk cursor-now [harness]
  {:pre [(: harness LawHarness)] :post [(: % WatchCursor)] :tags {:context "records" :role "program"}}
  "表 parts の今の位置(待ちの起点)を読むため。"
  (<- page (as-writer harness MAKER (ListRows "parts")))
  (WatchCursor page.epoch page.sequence))


(deftest test-a-write-during-the-wait-answers-at-its-instant
  {:interpreters ["http-memory"]}
  ;; 60 秒の待ちの 5 秒目に別の書き手が書く(laws.hy の late-write)— 5 秒で答え、待ちの要求は 1 回(上限の 25 秒の内に起きた)。
  (<- harness (LawSetup))
  (<- cursor WatchCursor (cursor-now harness))
  (<- writer (Spawn (late-write harness)))
  (<- waited Waited (watched-once harness (WatchChanges #("parts") cursor :timeout LONG-WAIT)))
  (<- written (Wait writer))
  (assert (= waited.seconds 5.0) waited)
  (assert (and (isinstance waited.answer Changes) (= (lfor item waited.answer.items #(item.key item.version)) [#(#("late") written.version)]))
          waited)
  (assert (= waited.sent #(WATCH-MAX-SECONDS)) waited))


(deftest test-a-quiet-wait-is-cut-at-the-service-limit-and-asked-again
  {:interpreters ["http-memory"]}
  ;; 変化の無い 60 秒の待ち: service は 1 回の要求で上限の 25 秒まで待って空の答えを返し、client は残りの秒でもう一度撃つ
  ;; (25・25・10)。60 秒で空の答えを返す。列の待ち(WatchEvents)も同じ。
  (<- harness (LawSetup))
  (<- cursor WatchCursor (cursor-now harness))
  (<- changes Waited (watched-once harness (WatchChanges #("parts") cursor :timeout LONG-WAIT)))
  (assert (= changes.seconds LONG-WAIT) changes)
  (assert (= changes.answer (Changes #() cursor #())) changes)
  (assert (= changes.sent #(WATCH-MAX-SECONDS WATCH-MAX-SECONDS (- LONG-WAIT (* 2 WATCH-MAX-SECONDS)))) changes)
  (<- first (as-writer harness MAKER (AppendEvent "journal" "first" {"n" 0})))
  (<- events Waited (watched-once harness (WatchEvents "journal" :after first.sequence :timeout LONG-WAIT)))
  (assert (= events.seconds LONG-WAIT) events)
  (assert (= events.answer (EventsQuiet)) events)
  (assert (= events.sent #(WATCH-MAX-SECONDS WATCH-MAX-SECONDS (- LONG-WAIT (* 2 WATCH-MAX-SECONDS)))) events))


(defrecord Resumed
  "切れた待ちと待ち直しの見え方: lost = 届かなかった待ちの答え・written = 切れた間の書きの答え・again = 同じ位置からの待ち直しの答え。"
  (#^ object lost)
  (#^ object written)
  (#^ object again))


(defk cut-then-waited-again [harness]
  {:pre [(: harness LawHarness)] :post [(: % Resumed)] :tags {:context "records" :role "program"}}
  "最初の待ちの要求が届かない(接続が切れた)間に 1 行書き、同じ位置から待ち直すため。"
  (<- cursor WatchCursor (cursor-now harness))
  (<- lost (as-writer harness MAKER (WatchChanges #("parts") cursor :timeout LONG-WAIT)))
  (<- written (as-writer harness MAKER (PutRow "parts" #("during") (FrozenMap {"label" "d"}) (ExpectAbsent))))
  (<- again (as-writer harness MAKER (WatchChanges #("parts") cursor :timeout LONG-WAIT)))
  (Resumed :lost lost :written written :again again))


(deftest test-the-endpoint-has-no-poll-interval
  ;; 待ちの読み直しの間隔の欄は無い(待ちは long-poll)。欄 poll-seconds を渡す呼び手は組み立てで落ちる(型の検でも赤)— 間隔の設定を
  ;; 黙って受け流さない。
  (with [raised (pytest.raises TypeError)]
    (RecordsEndpoint "http://records.in-process" :poll-seconds 1.0))
  (assert (in "poll_seconds" (str raised.value)) raised.value))


(deftest test-a-cut-wait-resumes-from-its-position-without-losing-a-write
  {:interpreters ["http-memory"]}
  ;; 待ちの要求が届かない時は Unreachable を返し(黙って空の答えにしない)、呼び手が同じ位置から待ち直すと、切れた間の書きが答えに入る。
  (<- harness (LawSetup))
  (<- seen Resumed (with_handlers [(state) first-watch-cut] (cut-then-waited-again harness)))
  (assert (isinstance seen.lost Unreachable) seen)
  (assert (and (isinstance seen.again Changes)
               (= (lfor item seen.again.items #(item.key item.version)) [#(#("during") seen.written.version)]))
          seen))
