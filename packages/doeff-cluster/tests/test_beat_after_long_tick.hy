;; heartbeat を送った tick の後半が送信間隔より長くかかっても、次の heartbeat の期限を落とさない(#4332)— 失敗ケース。
;;
;; 実例(本番 2026-10-09 11:47・12:10 の agent-worker-2): heartbeat を送った tick の後半が送信間隔(lease 10 秒の 4 分の 1 = 2.5 秒)を
;; 越えると、worker は別の起床の要因が来るまで 10〜20 秒眠り、coordinator は lease を越えた沈黙と判定した。
;;   - 本物の調整ループ(run-worker)・本物の coordinator-link(GET /watch を使う形 — 本番の入口と同じ)・本番の tick 間の待ち
;;     (tick-pauses)を仮想時計の下で動かし、heartbeat を送った tick の ObserveWorld の応答を 1 回だけ 3 秒止めても、heartbeat の送信の
;;     間隔は lease の 10 秒を越えない。
;; 直す前の赤: coordinator-link が tick の後に組み立てる heartbeat の期限(応答の時刻 + 2.5 秒)は、tick の後に読み直した時刻より前なので
;; 「判断が処理済みの時刻」として捨てられた。次に起きるのは fence の期限(応答の時刻 + 20 秒 + 1 ms)で、その tick は fence を越えたので
;; heartbeat を送らずに job を止め、0.5 秒後の再送で送る — 送信の間隔は 20.5 秒。
;; 前提: 偽の coordinator と、観測・状態の報告の偽の応答(stalled-coordinator)を仮想時計の下に置く(実時間の sleep は使わない)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import doeff [with-handlers])
(import doeff_core_effects.handlers [slog-handler state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.process_effects [ReadEnvironment])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.intent.worker_model [WorldView WorkerPolicy WorkerState ObserveWorld EnvReport PublishStatus])
(import doeff_cluster.worker.core.program [run-worker])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import doeff_cluster.worker.protocol.worker_wakes [no-wakes])
(import doeff_cluster.worker.protocol.coordinator_link [LinkState coordinator-link])
(import tests.link_rig [LINK-ROUTE])
(import tests.stop_fixtures [stop-signal-never-comes])
(import tests.transport_http [route-cell])

(val POLICY (WorkerPolicy))
(val WORKER "agent-worker-2")
(val LEASE-MS 10000)
(val FENCE-MS 20000)
;; 偽の coordinator の応答の版(GET /watch は「変わっていない」をこの版で答え続ける)。
(val REVISION 3)
;; heartbeat を送った tick の ObserveWorld を止める秒数(送信間隔 2.5 秒を越える)と、何回目の heartbeat を送った tick を止めるか
;; (GET /watch の確認が済んだ後の落ち着いた tick)。
(val STALL-SECONDS 3.0)
(val STALL-AFTER-BEATS 3)
;; 調整ループを止める仮想の時刻(止めた tick の後に、fence の 20 秒と再送の 0.5 秒が過ぎるまで動かす)。
(val RUN-UNTIL-MS 40000)


(defrecord HeardBeats
  "偽の coordinator が受けた heartbeat の時刻の列(epoch ms)と、ObserveWorld を止めた時刻(None = 止めていない)。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ (get tuple #(int ...)) beats)
  (#^ (| int None) stalled-at))


(defeffect ReadHeardBeats
  "ここまでに受けた heartbeat の時刻と、ObserveWorld を止めた時刻を問う — テストだけの effect。"
  {:fields [] :answer HeardBeats :tags {:context "doeff-cluster-test" :role "intent"}})


(defk answered [url body]
  {:pre [(: url str) (: body dict)] :post [(: % HttpResponse)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "偽の coordinator の 200 の応答を作るため。"
  (val text (json.dumps body))
  (HttpResponse 200 {} (.encode text "utf-8") text url 0.0))


(defhandler stalled-coordinator
  "偽の coordinator と、worker の外側の偽の応答: heartbeat は受けた時刻を記録して空の宣言・timing(lease と fence)・版を返し、
   GET /watch は問われた秒数だけ仮想時計で待って「変わっていない」と答える。ObserveWorld は空の観測を返すが、STALL-AFTER-BEATS 回目の
   heartbeat を送った tick の ObserveWorld だけ STALL-SECONDS 止まる。StopRequested は RUN-UNTIL-MS 以後に止めの理由を答える。
   環境変数は無く、状態の報告は何もしない。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var beats #())
  (session var stalled-at None)
  (HttpRequest [url params]
    (<- now int (now-epoch-ms))
    ;; coordinator-link が送るのは heartbeat と GET /watch だけ(宣言が空なので Program の取得は無い)。
    (match (get (.rsplit url "/" 1) 1)
      "heartbeat"
        (do (:= beats (+ beats #(now)))
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
    (when (and (= (len beats) STALL-AFTER-BEATS) (is stalled-at None))
      (<- at int (now-epoch-ms))
      (:= stalled-at at)
      (<- (Delay STALL-SECONDS)))
    (resume (WorldView #() #())))
  (PublishStatus [statuses note]
    (resume None))
  (StopRequested []
    (<- now int (now-epoch-ms))
    (resume (match (>= now RUN-UNTIL-MS)
              True "signal 15"
              _ None)))
  (ReadHeardBeats []
    (resume (HeardBeats :beats beats :stalled-at stalled-at))))


(defk worker-then-heard []
  {:pre [] :post [(: % HeardBeats)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の調整ループを止めの時刻まで動かし、偽の coordinator が受けた heartbeat の時刻を返すため。"
  (<- _state WorkerState (run-worker POLICY))
  (<- heard HeardBeats (ReadHeardBeats))
  heard)


(defk beats-around-a-stall [task-dir]
  {:pre [(: task-dir str)] :post [(: % HeardBeats)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の coordinator-link(GET /watch を使う・最後の連絡の時刻 0)と本番の tick 間の待ちを、偽の coordinator と仮想時計の下に並べて
   動かすため。task-dir = coordinator-link が task の印の file を置く dir。"
  (val link (LinkState WORKER #("cpu") 1 0 FENCE-MS task-dir "boot" 0 0 :watch True))
  (<- heard HeardBeats (with-handlers [(state) (sim-time-handler :clock (SimClock)) os-file-handler slog-handler no-wakes stop-signal-never-comes
                                       stalled-coordinator tick-pauses (coordinator-link link (route-cell "http://coord") LINK-ROUTE
                                                                                         (route-cell "http://coord"))]
                         (worker-then-heard)))
  heard)


(deftest test-a-long-tick-after-a-heartbeat-keeps-the-next-heartbeat-inside-the-lease [tmp-path]
  (<- heard HeardBeats (beats-around-a-stall (str (/ tmp-path "tasks"))))
  (val beats heard.beats)
  ;; ObserveWorld を止めた tick が在り、その後も heartbeat が届いている。
  (assert (is-not heard.stalled-at None) heard)
  (assert (> (len (lfor at beats :if (> at heard.stalled-at) at)) 0) heard)
  ;; 送信の間隔はどれも lease より短い(止めた tick の後も、次の heartbeat は止まりが明けた直後の tick で送る)。
  (val gaps (tuple (gfor #(earlier later) (zip beats (cut beats 1 None)) (- later earlier))))
  (assert (< (max gaps) LEASE-MS) #(gaps heard)))
