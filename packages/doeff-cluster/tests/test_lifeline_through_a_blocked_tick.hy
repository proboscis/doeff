;; 拍が scheduler を同期で塞いでも、生存の合図の thread が heartbeat を送り続ける— 失敗ケース。
;;
;; 実例(本番 zeus 2026-10-24 05:42〜05:46 JST): 機体の memory が詰まった間、拍の 1 つが 252 秒かかり(区間 ActionsToPublishStatus
;; 250 秒)、heartbeat の間は 197 秒になった。拍の外の背景の task も scheduler の上の task なので、拍が scheduler を
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
;;
;; ready の file(Pod の readinessProbe が読む — 読み手の deploy/boot.sh の ROLE=ready は READY_MAX_AGE 秒・既定 30 より古い file を
;; NotReady と読む)も同じ組みで見る。実例(本番 zeus 2026-10-26 02:59 JST): 拍が IO で 30〜62 秒止まった間、thread の heartbeat で
;; coordinator には生きて見えたのに、ready の file は拍が返事を解いた時だけ書かれたので古び、Pod が Ready を失い、Pod に載る job の
;; Service の振り先が 0 になった(job は答え続けていた)。
;;   - 期待: 拍の塞ぎ(300 秒 — 本番の 62 秒より長い)の間も、thread の送りに ready を許す返事が届くたびに送りの後の書きの thread が
;;     ready の file を書き、書きの間はどれも READY_MAX_AGE の内。thread の送りの返事が handoverPending(AL-D2k — 前の世代の job を
;;     まだ受け取れない)なら、拍の許しが残っていても塞ぎの間は書かない(今まで通り Ready を失う)。
;;   - 直す前の赤: 塞ぎの間に ready の file を書く者が無く、書きの間は約 300 秒。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す
(import datetime [datetime timedelta])
(import json)
(import pathlib [Path])
(import threading)
(import httpx)
(import doeff [with-handlers])
(import doeff_hy._table [Table table-of])
(import doeff_core_effects [SlogEffect])
(import doeff_core_effects [slog-handler state])
(import doeff_core_effects [os-file-handler])
(import doeff_core_effects [EnvEntry ReadEnvironment WriteText])
(import doeff_core_effects [HttpRequest HttpResponse])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster [now-epoch-ms])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [ClusterState]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord])
(import doeff_cluster._coordinator.core.cluster_policy [register-heartbeat tasks-placed])
(import doeff_cluster._foundation.lifeline [LifelineSettings start-lifeline])
(import doeff_cluster [WorldView WorkerPolicy ObserveWorld EnvReport PublishStatus]
        doeff_cluster._worker.intent.worker_model [WorkerState])
(import doeff_cluster._worker.core.beat_policy [LIFELINE-PERIOD-SECONDS])
(import doeff_cluster._worker.core.program [run-worker HEARTBEAT-GAP-LOG])
(import doeff_cluster [tick-pauses])
(import doeff_cluster [no-wakes])
(import doeff_cluster [coordinator-link]
        doeff_cluster._worker.protocol.coordinator_link [LinkState lifeline-turn lifeline-settled lifeline-ready-written])
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
;; readinessProbe が ready の file を Ready と読む古さの上限(ms — deploy/boot.sh の READY_MAX_AGE の既定 30 秒)。
(val READY-MAX-AGE-MS 30000)


(defclass SteppedClock [SimClock]
  "仮想の時計を、調整ループの scheduler と生存の合図の thread で分けるため(検だけの資源)。scheduler が時計を進めるたびに、thread の
   待ち(pause)を起こす。塞ぎの間は塞いでいる答え手が step で進め、thread が 1 周を終えて次の待ちに入り、その周が求めた送りの後の
   書き(ready の file)も終わるまで待つ(段の受け渡し — 実時間の眠りでなく、thread の待ちと書きの終わりという出来事を待つ)。
   書きの thread は送りの thread を待たせない別の thread なので、書きの終わりを待たないと、負荷の高い機体では時計が書きを置いて
   進み、書きの刻の間が仮想で 86 秒に開いた(2026-11-06 の日次の回)。asks = 書きの求めの数・served = 書き終えた求めの数。"
  (defn #^ None __post-init__ [self]
    (.__post-init__ (super))
    (setv self.turns (threading.Condition) self.parked-until None self.asks 0 self.served 0))

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
      (while (not (and (is-not self.parked-until None) (> self.parked-until self.current-time) (>= self.served self.asks)))
        (when (not (.wait self.turns 1.0))
          (setv waited (+ waited 1))
          (when (> waited TURN-LIMIT-SECONDS)
            (raise (TimeoutError "生存の合図の thread が 1 周と送りの後の書きを終えない")))))))

  (defn #^ None aside-asked [self]
    "送りの thread の 1 周が送りの後の書きを求めた(合図を立てる前に数える)。"
    (with [_ self.turns]
      (setv self.asks (+ self.asks 1))
      (.notify-all self.turns)))

  (defn #^ int aside-started [self]
    "書きの thread が書きを 1 本始める — 合図を下ろした後なので、ここまでの求めは全部この 1 本に畳まれる。その数を返す。"
    (with [_ self.turns]
      (setv upto self.asks))
    upto)

  (defn #^ None aside-done [self #^ int upto]
    "書きの thread が、upto までの求めを畳んだ書きを終えた。"
    (with [_ self.turns]
      (setv self.served (max self.served upto))
      (.notify-all self.turns)))

  (defn #^ None wake [self]
    "止めの合図を立てた後に、待っている thread を起こす。"
    (with [_ self.turns]
      (.notify-all self.turns))))


(defclass ThreadHeard []
  "偽の coordinator が生存の合図の thread から受けた heartbeat の #(刻 本文) の列(書くのは thread だけ — 検だけの資源)と、thread への
   返事に handoverPending を載せるか(pending — 検が作る時に決める)。"
  (defn #^ None __init__ [self #^ bool [pending False]]
    (setv self.beats #() self.pending pending)))


(defclass ReadyWrites []
  "ready の file への書きの仮想の刻(ms)の列。拍(scheduler の thread)と送りの後の書きの thread の両方が書くので錠で守る(検だけの
   資源)。"
  (defn #^ None __init__ [self]
    (setv self.times #() self.lock (threading.Lock)))

  (defn #^ None note [self #^ int at]
    (with [_ self.lock]
      (setv self.times (+ self.times #(at)))))

  (defn #^ tuple seen [self]
    (with [_ self.lock]
      (setv times self.times))
    times))


(defk stepped-pause [clock seconds stopped]
  {:pre [(: clock SteppedClock) (: seconds float) (: stopped threading.Event)] :post [(: % None)]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "生存の合図の thread の 1 周の間の待ちを、仮想の時計の進みで待つため(本番の event-pause の代わり)。"
  (.pause clock seconds stopped)
  None)


(defk settled-noted [clock link status detail text]
  {:pre [(: clock SteppedClock) (: link LinkState) (: status (| int None)) (: detail str) (: text str)] :post [(: % bool)]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本番の送りの答えの書き(lifeline-settled)を走らせ、送りの後の書きを求めた時は時計に数えさせるため(段の受け渡しが書きの終わりも
   待てるように)。"
  (<- wanted bool (lifeline-settled link status detail text))
  (when wanted
    (.aside-asked clock))
  wanted)


(defk ready-written-noted [clock link]
  {:pre [(: clock SteppedClock) (: link LinkState)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本番の送りの後の書き(lifeline-ready-written)を走らせ、終えた事を時計に知らせるため(書けなかった時も知らせる)。"
  (val upto (.aside-started clock))
  (try
    (<- (lifeline-ready-written link))
    (finally
      (.aside-done clock upto)))
  None)


(defk reply-body []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "偽の coordinator の heartbeat の返事(空の宣言・timing・版)を作るため。"
  {"jobs" [] "tasks" [] "warm" [] "draining" False "revision" REVISION "timing" {"lease_ms" LEASE-MS "fence_ms" FENCE-MS}})


(defk thread-coordinator [clock heard]
  {:pre [(: clock SteppedClock) (: heard ThreadHeard)] :post [(: % httpx.MockTransport)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "生存の合図の thread の送りを受ける偽の coordinator(httpx の偽の相手 — thread の中から呼ばれる): 受けた仮想の刻と本文を記録し、
   拍の側と同じ返事を返す(heard.pending なら handoverPending を載せる)。"
  (defn answer [request]  ; httpx の偽の相手が thread の中から呼ぶ callback
    (setv heard.beats (+ heard.beats #(#((run-clock-ms clock) (json.loads request.content)))))
    (httpx.Response 200 :json (| (run-reply-body) (if heard.pending {"handoverPending" True} {}))))
  (httpx.MockTransport answer))


(defhandler ready-writes-noted [clock ready-path writes]
  "ready の file(ready-path)への書きの仮想の刻を writes に記し、書きは外側の file の答え手へ渡す(拍と送りの後の書きの thread の
   両方に置く)。ほかの file の書きは素通し。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 時計と記しの入れ物は、拍と送りの後の書きの thread が分ける資源(検が作って両方に渡す)。
  (WriteText [path]
    (when (= path ready-path)
      (.note writes (run-clock-ms clock)))
    (reperform effect)))


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


(defhandler blocking-coordinator [clock ready-path]
  "偽の coordinator(拍の側)と、worker の外側の偽の応答: heartbeat は受けた時刻と本文を記録し、REPLY-SECONDS 待って返事を返す。
   GET /watch は問われた秒数だけ仮想時計で待って「変わっていない」と答える。環境変数の読みは ready の file の名だけを答える
   (ready-path が None なら何も答えない — ready の file を持たない worker)。ObserveWorld は空の観測。PublishStatus は、
   STALL-AFTER-BEATS 回の heartbeat の後の最初の 1 回だけ、scheduler の thread を STALL-SECONDS 同期で塞ぐ(譲らずに時計を
   LIFELINE-PERIOD-SECONDS ずつ進め、生存の合図の thread の 1 周を待つ)。StopRequested は RUN-UNTIL-MS 以後に止めの理由を答える。
   heartbeat の間の遅れ・途絶の自己停止・thread の送りの行を数えて log へ流す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 塞ぎの間に進める時計は、生存の合図の thread と分ける 1 つの資源(検が作って両方に渡す)。ready の file の名は
  ;; 検ごとに違う tmp の path で、記しの答え手(ready-writes-noted)と同じ値を検が渡す。
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
    (resume (if (and (is-not ready-path None) (in "DOEFF_WORKER_READY_FILE" names))
                #((EnvEntry :name "DOEFF_WORKER_READY_FILE" :value ready-path))
                #())))
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


(defk beats-through-a-blocked-tick [task-dir clock heard-by-thread ready-path writes]
  {:pre [(: task-dir str) (: clock SteppedClock) (: heard-by-thread ThreadHeard) (: ready-path (| str None)) (: writes ReadyWrites)]
   :post [(: % HeardBeats)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の coordinator-link(GET /watch を使う・最後の連絡の時刻 0)と本番の tick 間の待ちと本番の生存の合図の thread を、偽の coordinator と
   1 つの仮想の時計の下に並べて動かすため。thread は入口と同じ組み(lifeline-turn・lifeline-settled・送りの後の書き
   lifeline-ready-written)で起こし、ループの後に止める。ready の file(ready-path — None なら持たない)への書きの刻を writes に記す。"
  (val link (LinkState WORKER #("cpu") FENCE-MS task-dir BOOT 0 0 0 :watch True))
  (<- transport httpx.MockTransport (thread-coordinator clock heard-by-thread))
  (<- stop (start-lifeline (LifelineSettings :turn (fn [] (lifeline-turn link))
                                             :settle (fn [status detail text] (settled-noted clock link status detail text))
                                             :aside (fn [] (ready-written-noted clock link))
                                             :handlers [(sim-time-handler :clock clock) os-file-handler (ready-writes-noted clock ready-path writes)]
                                             :period-seconds LIFELINE-PERIOD-SECONDS
                                             :reply-seconds 5.0 :connect-seconds 1.0 :headers {"X-Actor" WORKER}
                                             :pause (fn [seconds stopped] (stepped-pause clock seconds stopped))
                                             :transport transport)))
  (try
    (<- heard HeardBeats (with-handlers [(state) (sim-time-handler :clock clock) os-file-handler (ready-writes-noted clock ready-path writes)
                                         slog-handler no-wakes stop-signal-never-comes
                                         (blocking-coordinator clock ready-path) tick-pauses
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
  (<- heard HeardBeats (beats-through-a-blocked-tick (str (/ tmp-path "tasks")) clock by-thread None (ReadyWrites)))
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


(defrecord ReadySeen
  "拍を塞いだ間の ready の file への書き: stalled-at = 塞ぎの始め・stall-ends = 塞ぎの終わり・times = 書きの仮想の刻(順に並べた物)。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ int stalled-at)
  (#^ int stall-ends)
  (#^ tuple times))


(defk ready-through-a-blocked-tick [tmp-path by-thread]
  {:pre [(: tmp-path Path) (: by-thread ThreadHeard)] :post [(: % ReadySeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "ready の file を持つ worker で拍を塞ぎ、塞いだ間と ready の file への書きの刻を返すため。"
  (val clock (SteppedClock))
  (val writes (ReadyWrites))
  (<- heard HeardBeats (beats-through-a-blocked-tick (str (/ tmp-path "tasks")) clock by-thread (str (/ tmp-path "doeff-worker-ready"))
                                                     writes))
  (assert (is-not heard.stalled-at None) heard)
  (ReadySeen :stalled-at heard.stalled-at :stall-ends (+ heard.stalled-at (int (* STALL-SECONDS 1000)))
             :times (tuple (sorted (.seen writes)))))


(deftest test-a-tick-that-blocks-the-scheduler-for-300-seconds-keeps-the-ready-file-within-the-probe-age [#^ Path tmp-path]
  (<- seen ReadySeen (ready-through-a-blocked-tick tmp-path (ThreadHeard)))
  (val times seen.times)
  ;; 拍が塞ぎの前に書き、塞ぎが明けた後にも書く(書きの列が塞ぎを覆う)。
  (assert (and times (< (get times 0) seen.stalled-at) (> (get times -1) seen.stall-ends)) seen)
  ;; 塞ぎの間も書く(thread の送りに ready を許す返事が届くたびに、送りの後の書きの thread が)。
  (assert (any (gfor at times (< seen.stalled-at at seen.stall-ends))) seen)
  ;; 書きの間はどれも readinessProbe の古さの上限の内 — Pod は Ready を失わず、Pod に載る job の Service の振り先は 0 にならない。
  (val gaps (tuple (gfor #(earlier later) (zip times (cut times 1 None)) (- later earlier))))
  (assert (<= (max gaps) READY-MAX-AGE-MS) #((max gaps) seen)))


(deftest test-a-blocked-tick-lets-the-pod-lose-ready-while-the-lifeline-reply-says-handover-pending [#^ Path tmp-path]
  ;; AL-D2k: 同じ名の前の世代の job をまだ受け取れない間(thread の送りの返事の handoverPending)は、拍が塞ぎの前に ready を許して
  ;; いても、thread の送りの後に ready の file を書かない(今まで通り、古びた file で Pod が Ready を失う)。
  (val by-thread (ThreadHeard :pending True))
  (<- seen ReadySeen (ready-through-a-blocked-tick tmp-path by-thread))
  ;; thread は塞ぎの間も送る(coordinator は生きていると見る)。
  (assert (any (gfor beat by-thread.beats (< seen.stalled-at (get beat 0) seen.stall-ends))) by-thread.beats)
  (assert (and seen.times (< (get seen.times 0) seen.stalled-at)) seen)
  (assert (not (any (gfor at seen.times (< seen.stalled-at at seen.stall-ends)))) seen))


(defk settled-on-a-clock [link status text]
  {:pre [(: link LinkState) (: status (| int None)) (: text str)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "生存の合図の thread の送り 1 本の答えを、thread と同じく時計の答え手だけの下で書かせ、送りの後の書きを求めたかを返すため。"
  (<- wanted bool (with-handlers [(sim-time-handler :clock (SimClock))] (lifeline-settled link status "" text)))
  wanted)


(deftest test-the-lifeline-asks-to-renew-the-ready-file-only-while-the-tick-and-its-own-reply-permit-it [#^ Path tmp-path]
  (val link (LinkState WORKER #("cpu") FENCE-MS (str (/ tmp-path "tasks")) BOOT 0 0 0))
  (val plain (json.dumps (run-reply-body)))
  ;; 拍がまだ ready を許していない(返事を解いていない・解いた返事が handoverPending だった)間は、thread の返事が許しても求めない。
  (<- before bool (settled-on-a-clock link 200 plain))
  (assert (not before))
  (setv link.life.ready-file (str (/ tmp-path "doeff-worker-ready")))
  (<- permitted bool (settled-on-a-clock link 200 plain))
  (assert permitted)
  (assert (= link.life.ready-refusal "") link.life.ready-refusal)
  ;; thread の返事が handoverPending・形が違う(draining が無い)・JSON でない → 求めず、訳を残す(拍の口が log に出す)。
  (for [text [(json.dumps (| (run-reply-body) {"handoverPending" True})) (json.dumps {"jobs" []}) "<html>"]]
    (<- refused bool (settled-on-a-clock link 200 text))
    (assert (not refused) text)
    (assert link.life.ready-refusal text))
  ;; 届かない・断られた送りは求めない。
  (<- unreachable bool (settled-on-a-clock link None plain))
  (assert (not unreachable))
  (<- refused bool (settled-on-a-clock link 503 plain))
  (assert (not refused)))
