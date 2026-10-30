;; 拍が scheduler を同期で塞いでも、生存の合図の thread が heartbeat を送り続ける(card ki-9b58446e2ad6)— 失敗ケース。
;;
;; 実例(本番 zeus 2026-10-24 05:42〜05:46 JST): 機体の memory が詰まった間、拍の 1 つが 252 秒かかり(区間 ActionsToPublishStatus
;; 250 秒)、heartbeat の間は 197 秒になった。拍の外の背景の task(card ki-e38dbfca7671)も scheduler の上の task なので、拍が scheduler を
;; 塞ぐ間や外の完了が返らない間は送れない。coordinator は切り離した task の lease(60 秒)の切れで worker の死と判じ、機体の task を
;; 全部 lost にした(日次の検証の 04:00 の回を含む)。
;;   - 本物の調整ループ(run-worker)・本物の coordinator-link(GET /watch を使う形 — 本番の入口と同じ)・本番の tick 間の待ち・本番の
;;     生存の合図の thread(foundation/lifeline の start-lifeline と coordinator-link の lifeline-turn・lifeline-settled — 入口と同じ組み)
;;     を、1 つの仮想の時計の下で動かす。STALL-AFTER-BEATS 回の heartbeat の後の拍の PublishStatus(区間 ActionsToPublishStatus の終わり)
;;     が、scheduler の thread を 300 秒(仮想)同期で塞ぐ — 譲らない(Delay も効果も出さない)。その間の時計は塞いでいる答え手が
;;     LIFELINE-PERIOD-SECONDS ずつ進め、進めるたびに生存の合図の thread が 1 周を終えて次の待ちに入るのを待つ(実時間の眠りは無い)。
;;   - 期待: 拍の側と thread の送りを合わせた heartbeat の間はどれも生存の窓の半分(5 秒)以下・塞いだ間にも thread が送る・本物の
;;     coordinator の判断(register-heartbeat・tasks-placed)に、その worker に置いた切り離した task(lease 60 秒)を受けた heartbeat の
;;     刻と本文の順に渡しても、task は lost にならない・塞ぎが明けた後に worker が途絶と数えて自分で止める行が出ない・heartbeat の間の
;;     遅れの行が出ない・thread の送りを拍の log に 1 行出す。
;; 直す前の赤: thread が無く、塞いだ 300 秒の間は誰も送らない — 間は約 300 秒、task は lease の切れで lost、塞ぎが明けた最初の周期で
;; 途絶の柵(20 秒)を越えた worker が自分の job を止める判断を出す。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す
(import datetime [datetime timedelta])
(import json)
(import threading)
(import httpx)
(import doeff [with-handlers])
(import doeff_hy._table [Table table-of])
(import doeff_core_effects [SlogEffect])
(import doeff_core_effects [slog-handler state])
(import doeff_core_effects [os-file-handler])
(import doeff_core_effects [ReadEnvironment])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState TaskRecord])
(import doeff_cluster.coordinator.core.cluster_policy [register-heartbeat tasks-placed])
(import doeff_cluster._foundation.lifeline [LifelineSettings start-lifeline])
(import doeff_cluster.worker.intent.worker_model [WorldView WorkerPolicy WorkerState ObserveWorld EnvReport PublishStatus])
(import doeff_cluster.worker.core.beat_policy [LIFELINE-PERIOD-SECONDS])
(import doeff_cluster.worker.core.program [run-worker HEARTBEAT-GAP-LOG])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import doeff_cluster.worker.protocol.worker_wakes [no-wakes])
(import doeff_cluster.worker.protocol.coordinator_link [LinkState coordinator-link lifeline-turn lifeline-settled])
(import doeff_core_effects [StopRequested])
(import tests.link_rig [LINK-ROUTE])
(import tests.program_rows [heartbeat-of])
(import tests.state_rows [table-from-rows])
(import tests.stop_fixtures [stop-signal-never-comes])
(import tests.transport_http [route-cell])

(val POLICY (WorkerPolicy))
(val WORKER "zeus")
(val BOOT "boot")
(val LEASE-MS 10000)
(val FENCE-MS 20000)
;; 偽の coordinator の応答の版(GET /watch は「変わっていない」をこの版で答え続ける)。
(val REVISION 3)
;; 拍の PublishStatus が scheduler を塞ぐ秒(仮想 — 本番の 250 秒より長く、切り離した task の lease 60 秒・途絶の柵 20 秒より長い)と、
;; 何回目の heartbeat の後の拍を塞ぐか(GET /watch の確認が済んだ後の落ち着いた拍)。
(val STALL-SECONDS 300.0)
(val STALL-AFTER-BEATS 3)
;; 偽の coordinator が拍の側の heartbeat に答えるまでの秒。
(val REPLY-SECONDS 0.2)
;; 調整ループを止める仮想の時刻(塞ぎの後も、拍の側の送りが何度か続くまで動かす)。
(val RUN-UNTIL-MS 340000)
;; その worker に置いた切り離した task(送り手の既定の lease 60 秒 — detached_model の DETACHED-DEFAULT-LEASE-SECONDS)。
(val TASK-LEASE-MS 60000)
;; worker が途絶と数えて自分で止める行(coordinator_link の polled)と、thread の送りを拍の log に出す行(lifeline-told)。
(val SELF-STOP-LOG "worker: coordinator からの返事が途絶の柵を越えて無い — 印の無い job と task を止めます")
(val LIFELINE-LOG "worker: 生存の合図の thread が拍の代わりに heartbeat を送りました")
;; 塞ぎの間に、生存の合図の thread の 1 周の後の待ちを待つ実時間の上限(秒 — 越えたら検を赤で止める。眠りではない)。
(val TURN-LIMIT-SECONDS 30)


(defclass SteppedClock [SimClock]
  "仮想の時計を、調整ループの scheduler と生存の合図の thread で分けるため(検だけの資源)。scheduler が時計を進めるたびに、thread の
   待ち(pause)を起こす。塞ぎの間は塞いでいる答え手が step で進め、thread が 1 周を終えて次の待ちに入るまで待つ(段の受け渡し —
   実時間の眠りでなく、thread の待ちという出来事を待つ)。"
  (defn #^ None __post-init__ [self]
    (.__post-init__ (super))
    (setv self.turns (threading.Condition) self.parked-until None))

  (defn #^ datetime advance-to [self #^ datetime target-time]
    (with [_ self.turns]
      (setv moved (.advance-to (super) target-time))
      (.notify-all self.turns))
    moved)

  (defn #^ datetime set-time [self #^ datetime new-time]
    (with [_ self.turns]
      (setv moved (.set-time (super) new-time))
      (.notify-all self.turns))
    moved)

  (defn #^ None pause [self #^ float seconds #^ threading.Event stopped]
    "生存の合図の thread の 1 周の間の待ち: 時計が seconds 進むか、止めの合図が立つまで待つ。"
    (with [_ self.turns]
      (setv until (+ self.current-time (timedelta :seconds seconds)))
      (setv self.parked-until until)
      (.notify-all self.turns)
      (while (and (< self.current-time until) (not (.is-set stopped)))
        (.wait self.turns 1.0))
      (setv self.parked-until None)))

  (defn #^ None step [self #^ float seconds]
    "塞いでいる答え手が時計を seconds 進め、生存の合図の thread がその刻の 1 周を終えて次の待ちに入るまで待つ。"
    (with [_ self.turns]
      (.advance-to self (+ self.current-time (timedelta :seconds seconds)))
      (setv waited 0)
      (while (not (and (is-not self.parked-until None) (> self.parked-until self.current-time)))
        (when (not (.wait self.turns 1.0))
          (setv waited (+ waited 1))
          (when (> waited TURN-LIMIT-SECONDS)
            (raise (TimeoutError "生存の合図の thread が 1 周を終えない")))))))

  (defn #^ None wake [self]
    "止めの合図を立てた後に、待っている thread を起こす。"
    (with [_ self.turns]
      (.notify-all self.turns))))


(defclass ThreadHeard []
  "偽の coordinator が生存の合図の thread から受けた heartbeat の #(刻 本文) の列(書くのは thread だけ — 検だけの資源)。"
  (defn #^ None __init__ [self]
    (setv self.beats #())))


(defk stepped-pause [clock seconds stopped]
  {:pre [(: clock SteppedClock) (: seconds float) (: stopped threading.Event)] :post [(: % None)]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "生存の合図の thread の 1 周の間の待ちを、仮想の時計の進みで待つため(本番の event-pause の代わり)。"
  (.pause clock seconds stopped)
  None)


(defk reply-body []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "偽の coordinator の heartbeat の返事(空の宣言・timing・版)を作るため。"
  {"jobs" [] "tasks" [] "warm" [] "draining" False "revision" REVISION "timing" {"lease_ms" LEASE-MS "fence_ms" FENCE-MS}})


(defk thread-coordinator [clock heard]
  {:pre [(: clock SteppedClock) (: heard ThreadHeard)] :post [(: % httpx.MockTransport)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "生存の合図の thread の送りを受ける偽の coordinator(httpx の偽の相手 — thread の中から呼ばれる): 受けた仮想の刻と本文を記録し、
   拍の側と同じ返事を返す。"
  (defn answer [request]  ; httpx の偽の相手が thread の中から呼ぶ callback
    (setv heard.beats (+ heard.beats #(#((run-clock-ms clock) (json.loads request.content)))))
    (httpx.Response 200 :json (run-reply-body)))
  (httpx.MockTransport answer))


(defn run-clock-ms [clock]  ; httpx の偽の相手の callback の中から読む(VM の外)
  (int (* (.timestamp clock.current-time) 1000)))


(defn run-reply-body []  ; httpx の偽の相手の callback の中から読む(VM の外)
  {"jobs" [] "tasks" [] "warm" [] "draining" False "revision" REVISION "timing" {"lease_ms" LEASE-MS "fence_ms" FENCE-MS}})


(defrecord HeardBeats
  "偽の coordinator が拍の側から受けた heartbeat の #(刻 本文) の列・塞いだ時刻(None = 塞いでいない)・heartbeat の間の遅れの行の数・
   途絶と数えて自分で止める行の数・thread の送りを出した行の数。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ tuple beats)
  (#^ (| int None) stalled-at)
  (#^ int gap-lines)
  (#^ int self-stop-lines)
  (#^ int lifeline-lines))


(defeffect ReadHeardBeats
  "ここまでに拍の側から受けた heartbeat・塞いだ時刻・数えた行を問う — テストだけの effect。"
  {:fields [] :answer HeardBeats :tags {:context "doeff-cluster-test" :role "intent"}})


(defk answered [url body]
  {:pre [(: url str) (: body dict)] :post [(: % HttpResponse)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "偽の coordinator の 200 の応答を作るため。"
  (val text (json.dumps body))
  (HttpResponse 200 {} (.encode text "utf-8") text url 0.0))


(defhandler blocking-coordinator [clock]
  "偽の coordinator(拍の側)と、worker の外側の偽の応答: heartbeat は受けた時刻と本文を記録し、REPLY-SECONDS 待って返事を返す。
   GET /watch は問われた秒数だけ仮想時計で待って「変わっていない」と答える。ObserveWorld は空の観測。PublishStatus は、
   STALL-AFTER-BEATS 回の heartbeat の後の最初の 1 回だけ、scheduler の thread を STALL-SECONDS 同期で塞ぐ(譲らずに時計を
   LIFELINE-PERIOD-SECONDS ずつ進め、生存の合図の thread の 1 周を待つ)。StopRequested は RUN-UNTIL-MS 以後に止めの理由を答える。
   heartbeat の間の遅れ・途絶の自己停止・thread の送りの行を数えて log へ流す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 塞ぎの間に進める時計は、生存の合図の thread と分ける 1 つの資源(検が作って両方に渡す)。
  (session var beats #())
  (session var stalled-at None)
  (session var gap-lines 0)
  (session var self-stop-lines 0)
  (session var lifeline-lines 0)
  (HttpRequest [url params body]
    (<- now int (now-epoch-ms))
    ;; coordinator-link が送るのは heartbeat と GET /watch だけ(宣言が空なので Program の取得は無い)。
    (match (get (.rsplit url "/" 1) 1)
      "heartbeat"
        (do (:= beats (+ beats #(#(now (dict body)))))
            (<- (Delay REPLY-SECONDS))
            (<- reply-text dict (reply-body))
            (<- reply HttpResponse (answered url reply-text))
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
    (resume (WorldView #() #())))
  (PublishStatus [statuses note]
    (when (and (>= (len beats) STALL-AFTER-BEATS) (is stalled-at None))
      (<- at int (now-epoch-ms))
      (:= stalled-at at)
      ;; 同期の区間: 効果を出さずに scheduler の thread を塞いだまま、時計だけを進める。
      (for [_ (range (int (/ STALL-SECONDS LIFELINE-PERIOD-SECONDS)))]
        (.step clock LIFELINE-PERIOD-SECONDS)))
    (resume None))
  (SlogEffect []
    (cond
      (= effect.msg HEARTBEAT-GAP-LOG) (:= gap-lines (+ gap-lines 1))
      (= effect.msg SELF-STOP-LOG) (:= self-stop-lines (+ self-stop-lines 1))
      (= effect.msg LIFELINE-LOG) (:= lifeline-lines (+ lifeline-lines 1)))
    (reperform effect))
  (StopRequested []
    (<- now int (now-epoch-ms))
    (resume (match (>= now RUN-UNTIL-MS)
              True "signal 15"
              _ None)))
  (ReadHeardBeats []
    (resume (HeardBeats :beats beats :stalled-at stalled-at :gap-lines gap-lines :self-stop-lines self-stop-lines
                        :lifeline-lines lifeline-lines))))


(defk worker-then-heard []
  {:pre [] :post [(: % HeardBeats)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の調整ループを止めの時刻まで動かし、偽の coordinator が拍の側から受けた heartbeat を返すため。"
  (<- _state WorkerState (run-worker POLICY))
  (<- heard HeardBeats (ReadHeardBeats))
  heard)


(defk beats-through-a-blocked-tick [task-dir clock heard-by-thread]
  {:pre [(: task-dir str) (: clock SteppedClock) (: heard-by-thread ThreadHeard)] :post [(: % HeardBeats)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の coordinator-link(GET /watch を使う・最後の連絡の時刻 0)と本番の tick 間の待ちと本番の生存の合図の thread を、偽の coordinator と
   1 つの仮想の時計の下に並べて動かすため。thread は入口と同じ組み(lifeline-turn・lifeline-settled)で起こし、ループの後に止める。"
  (val link (LinkState WORKER #("cpu") 0 1 FENCE-MS task-dir BOOT 0 0 0 :watch True))
  (<- transport httpx.MockTransport (thread-coordinator clock heard-by-thread))
  (<- stop (start-lifeline (LifelineSettings :turn (fn [] (lifeline-turn link))
                                             :settle (fn [status detail] (lifeline-settled link status detail))
                                             :handlers [(sim-time-handler :clock clock)] :period-seconds LIFELINE-PERIOD-SECONDS
                                             :reply-seconds 5.0 :connect-seconds 1.0 :headers {"X-Actor" WORKER}
                                             :pause (fn [seconds stopped] (stepped-pause clock seconds stopped))
                                             :transport transport)))
  (try
    (<- heard HeardBeats (with-handlers [(state) (sim-time-handler :clock clock) os-file-handler slog-handler no-wakes stop-signal-never-comes
                                         (blocking-coordinator clock) tick-pauses
                                         (coordinator-link link (route-cell "http://coord") LINK-ROUTE (route-cell "http://coord"))]
                           (worker-then-heard)))
    (finally
      (stop)
      (.wake clock)))
  heard)


(defk placed-task []
  {:pre [] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "その worker の世代に置いた切り離した task(lease 60 秒・刻 0 に置いた・子を起こして報告済み)の行を作るため。"
  (TaskRecord "t1" "digest" None "rev" #() #() TASK-LEASE-MS TASK-LEASE-MS 0
              :phase "assigned" :worker WORKER :detached True :boot BOOT :reported True :started-ms 0))


(defk phases-at-each-beat [beats]
  {:pre [(: beats tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の coordinator の判断に、受けた heartbeat を刻の順に渡し、各 heartbeat を受ける直前と最後の刻(RUN-UNTIL-MS)の task の phase を
   集めるため(task の判断 tasks-placed → heartbeat の写し register-heartbeat の順 — coordinator の拍と同じ)。"
  (val timing (ClusterTiming))
  (<- tasks Table (table-from-rows {"t1" (! (placed-task))}))
  (var cluster (ClusterState :tasks tasks))
  (var phases #())
  (for [#(at body) beats]
    (<- placed Table (tasks-placed at cluster (table-of #()) timing))
    (:= phases (+ phases #(#(at (. (.row placed "t1") phase)))))
    (:= cluster (replace cluster :tasks placed))
    (<- parsed (heartbeat-of body))
    (<- heard ClusterState (register-heartbeat cluster parsed at))
    (:= cluster heard))
  (<- last Table (tasks-placed RUN-UNTIL-MS cluster (table-of #()) timing))
  (+ phases #(#(RUN-UNTIL-MS (. (.row last "t1") phase)))))


(deftest test-a-tick-that-blocks-the-scheduler-for-300-seconds-keeps-every-heartbeat-gap-within-half-the-lease [tmp-path]
  (val clock (SteppedClock))
  (val by-thread (ThreadHeard))
  (<- heard HeardBeats (beats-through-a-blocked-tick (str (/ tmp-path "tasks")) clock by-thread))
  (assert (is-not heard.stalled-at None) heard)
  (val beats (tuple (sorted (+ heard.beats by-thread.beats) :key (fn [beat] (get beat 0)))))
  (val times (tuple (gfor beat beats (get beat 0))))
  ;; 塞いだ 300 秒の間にも heartbeat が届く(生存の合図の thread — 送信間隔 2.5 秒 × 1.5 ごと)。
  (val stall-ends (+ heard.stalled-at (int (* STALL-SECONDS 1000))))
  (val during (tuple (gfor at times :if (< heard.stalled-at at stall-ends) at)))
  (assert (>= (len during) 60) #((len during) heard.stalled-at))
  ;; 送りの間はどれも生存の窓の半分以下(拍が scheduler を塞ぐ長さに依らない)。
  (val gaps (tuple (gfor #(earlier later) (zip times (cut times 1 None)) (- later earlier))))
  (assert (<= (max gaps) (// LEASE-MS 2)) #((max gaps) heard.stalled-at))
  ;; 本物の coordinator の判断は、その worker に置いた切り離した task を lost にしない(lease の切れ = worker の死の判断が出ない)。
  ;; 渡すのは止まり始めの名乗りの前の heartbeat(RUN-UNTIL-MS の後の止まる世代の名乗りは、報告に無い task を正しく lost にする —
  ;; 塞ぎと関わらない)。
  (<- phases tuple (phases-at-each-beat (tuple (gfor beat beats :if (not (.get (get beat 1) "stopping" False)) beat))))
  (val lost (tuple (gfor #(at phase) phases :if (!= phase "assigned") #(at phase))))
  (assert (= lost #()) lost)
  ;; 塞ぎが明けた後、coordinator が生きていると見ていた間を途絶と数えて自分の job を止めない。
  (assert (= heard.self-stop-lines 0) heard)
  ;; thread の送りも heartbeat の間の計り(#3850)に数える: 遅れの行は 0。thread の送りは拍の log に出る。
  (assert (= heard.gap-lines 0) heard)
  (assert (>= heard.lifeline-lines 1) heard))
