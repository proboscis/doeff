;; heartbeat の送りの間は tick の長さに依らない(card ki-e38dbfca7671)— 失敗ケース。
;;
;; 実例(本番 2026-10-09 の agent-worker-2): worker は heartbeat を tick の中(ReadDesired)でだけ送るので、送りの間 ≒ tick の長さに
;; なる(今日の最長 7.3 秒)。lease は 10 秒で、13:15:04 に間 10041 ms で coordinator の名簿が gone と判じた。#4332 の直し(418d4eab6)は
;; 過ぎた期限を捨てないが、tick が lease より長ければ間は lease を越える。
;;   - 本物の調整ループ(run-worker)・本物の coordinator-link(GET /watch を使う形 — 本番の入口と同じ)・本番の tick 間の待ち
;;     (tick-pauses)を仮想時計の下で動かし、3 回目の heartbeat の後の tick の ObserveWorld を 1 回だけ lease より長く(12 秒)止めても、
;;     heartbeat の送りの間はどれも lease の半分(5 秒)以下で、止めた間にも送りが届く。
;;   - 送りは 1 本ずつ: 偽の coordinator の heartbeat の答えを少し待たせても、同時に答えを待つ heartbeat は 1 本まで。
;;   - 拍の外の送りも heartbeat の間の計り(#3850)に数える: heartbeat の間の遅れの行は 0。
;; 直す前の赤: 止めた tick の間は誰も送らず、次の送りは止まりが明けた直後の tick — 間は約 12 秒(遅れの行も 1 つ出る)。
;; 前提: 偽の coordinator と、観測・状態の報告の偽の応答(stalled-coordinator)を仮想時計の下に置く(実時間の sleep は使わない)。
;; tick が同期の処理(file の効果・素の subprocess・CPU)で scheduler を塞ぐ間は、この検の範囲の外(card の「触らない物」)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import doeff [with-handlers])
(import doeff_core_effects.effects [SlogEffect])
(import doeff_core_effects.handlers [slog-handler state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.process_effects [ReadEnvironment])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.intent.worker_model [WorldView WorkerPolicy WorkerState ObserveWorld EnvReport PublishStatus])
(import doeff_cluster.worker.core.program [run-worker HEARTBEAT-GAP-LOG])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import doeff_cluster.worker.protocol.worker_wakes [no-wakes])
(import doeff_cluster.worker.protocol.coordinator_link [LinkState coordinator-link])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import tests.link_rig [LINK-ROUTE])
(import tests.stop_fixtures [stop-signal-never-comes])
(import tests.transport_http [route-cell])

(val POLICY (WorkerPolicy))
(val WORKER "agent-worker-2")
(val LEASE-MS 10000)
(val FENCE-MS 20000)
;; 偽の coordinator の応答の版(GET /watch は「変わっていない」をこの版で答え続ける)。
(val REVISION 3)
;; tick の ObserveWorld を止める秒数(lease の 10 秒より長い)と、何回目の heartbeat の後の tick を止めるか(GET /watch の確認が済んだ後の
;; 落ち着いた tick)。
(val STALL-SECONDS 12.0)
(val STALL-AFTER-BEATS 3)
;; 偽の coordinator が heartbeat に答えるまでの秒(往復の間に、もう 1 本の送りが重ならないかを見る)。
(val REPLY-SECONDS 0.2)
;; 調整ループを止める仮想の時刻(止めた tick の後も、送りが何度か続くまで動かす)。
(val RUN-UNTIL-MS 40000)


(defrecord HeardBeats
  "偽の coordinator が受けた heartbeat の時刻の列(epoch ms)・ObserveWorld を止めた時刻(None = 止めていない)・同時に答えを待った
   heartbeat の最多の本数・worker が出した heartbeat の間の遅れの行の数。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ (get tuple #(int ...)) beats)
  (#^ (| int None) stalled-at)
  (#^ int most-in-flight)
  (#^ int gap-lines))


(defeffect ReadHeardBeats
  "ここまでに受けた heartbeat の時刻・ObserveWorld を止めた時刻・同時に答えを待った最多の本数・heartbeat の間の遅れの行の数を問う
   — テストだけの effect。"
  {:fields [] :answer HeardBeats :tags {:context "doeff-cluster-test" :role "intent"}})


(defk answered [url body]
  {:pre [(: url str) (: body dict)] :post [(: % HttpResponse)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "偽の coordinator の 200 の応答を作るため。"
  (val text (json.dumps body))
  (HttpResponse 200 {} (.encode text "utf-8") text url 0.0))


(defhandler stalled-coordinator
  "偽の coordinator と、worker の外側の偽の応答: heartbeat は受けた時刻を記録し、REPLY-SECONDS 待ってから空の宣言・timing(lease と
   fence)・版を返す(答えを待つ本数を数える)。GET /watch は問われた秒数だけ仮想時計で待って「変わっていない」と答える。ObserveWorld は
   空の観測を返すが、STALL-AFTER-BEATS 回の heartbeat の後の最初の ObserveWorld だけ STALL-SECONDS 止まる。StopRequested は
   RUN-UNTIL-MS 以後に止めの理由を答える。環境変数は無く、状態の報告は何もしない。heartbeat の間の遅れの行を数えて log へ流す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var beats #())
  (session var stalled-at None)
  (session var in-flight 0)
  (session var most-in-flight 0)
  (session var gap-lines 0)
  (HttpRequest [url params]
    (<- now int (now-epoch-ms))
    ;; coordinator-link が送るのは heartbeat と GET /watch だけ(宣言が空なので Program の取得は無い)。
    (match (get (.rsplit url "/" 1) 1)
      "heartbeat"
        (do (:= beats (+ beats #(now)))
            (:= in-flight (+ in-flight 1))
            (:= most-in-flight (max most-in-flight in-flight))
            (<- (Delay REPLY-SECONDS))
            (:= in-flight (- in-flight 1))
            (<- reply HttpResponse (answered url {"jobs" [] "tasks" [] "warm" [] "draining" False "revision" REVISION
                                                  "timing" {"lease_ms" LEASE-MS "fence_ms" FENCE-MS}}))
            (resume reply))
      _
        (do (<- (Delay (float (get params "timeoutSeconds"))))
            (<- reply HttpResponse (answered url {"revision" REVISION "changed" False}))
            (resume reply))))
  (ReadEnvironment [names]
    (resume #()))
  (EnvReport []
    (resume None))
  (ObserveWorld []
    (when (and (>= (len beats) STALL-AFTER-BEATS) (is stalled-at None))
      (<- at int (now-epoch-ms))
      (:= stalled-at at)
      (<- (Delay STALL-SECONDS)))
    (resume (WorldView #() #())))
  (PublishStatus [statuses note]
    (resume None))
  (SlogEffect []
    (when (= effect.msg HEARTBEAT-GAP-LOG)
      (:= gap-lines (+ gap-lines 1)))
    (reperform effect))
  (StopRequested []
    (<- now int (now-epoch-ms))
    (resume (match (>= now RUN-UNTIL-MS)
              True "signal 15"
              _ None)))
  (ReadHeardBeats []
    (resume (HeardBeats :beats beats :stalled-at stalled-at :most-in-flight most-in-flight :gap-lines gap-lines))))


(defk worker-then-heard []
  {:pre [] :post [(: % HeardBeats)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の調整ループを止めの時刻まで動かし、偽の coordinator が受けた heartbeat の時刻を返すため。"
  (<- _state WorkerState (run-worker POLICY))
  (<- heard HeardBeats (ReadHeardBeats))
  heard)


(defk beats-beside-a-stall [task-dir]
  {:pre [(: task-dir str)] :post [(: % HeardBeats)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の coordinator-link(本番の入口と同じく GET /watch を使う・最後の連絡の時刻 0)と本番の tick 間の待ちを、偽の coordinator と
   仮想時計の下に並べて動かすため。task-dir = coordinator-link が task の印の file を置く dir。"
  (val link (LinkState WORKER #("cpu") 1 0 FENCE-MS task-dir "boot" 0 0 :watch True))
  (<- heard HeardBeats (with-handlers [(state) (sim-time-handler :clock (SimClock)) os-file-handler slog-handler no-wakes stop-signal-never-comes
                                       stalled-coordinator tick-pauses (coordinator-link link (route-cell "http://coord") LINK-ROUTE
                                                                                         (route-cell "http://coord"))]
                         (worker-then-heard)))
  heard)


(deftest test-a-tick-longer-than-the-lease-keeps-every-heartbeat-gap-within-half-the-lease [tmp-path]
  (<- heard HeardBeats (beats-beside-a-stall (str (/ tmp-path "tasks"))))
  (val beats heard.beats)
  ;; ObserveWorld を止めた tick が在り、止めた間にも heartbeat が届いている(lease の 12 秒の間に、送信間隔 2.5 秒ごと)。
  (assert (is-not heard.stalled-at None) heard)
  (val stall-ends (+ heard.stalled-at (int (* STALL-SECONDS 1000))))
  (assert (>= (len (lfor at beats :if (< heard.stalled-at at stall-ends) at)) 4) heard)
  ;; 送りの間はどれも lease の半分以下(tick の長さに依らない)。
  (val gaps (tuple (gfor #(earlier later) (zip beats (cut beats 1 None)) (- later earlier))))
  (assert (<= (max gaps) (// LEASE-MS 2)) #(gaps heard))
  ;; 送りは 1 本ずつ(答えを待つ heartbeat が同時に 2 本にならない)。
  (assert (= heard.most-in-flight 1) heard)
  ;; 拍の外の送りも間の計りに数える(拍の送りだけを数えると、止めた tick を挟む間が遅れの行になる)。
  (assert (= heard.gap-lines 0) heard))
