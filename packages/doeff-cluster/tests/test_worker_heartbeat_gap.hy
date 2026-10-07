;; worker が前の heartbeat を送ってから次の heartbeat を送るまでの間を測り、生存の窓(lease-ms)の 7 割を越えた時だけ 1 行出す検(#3850)。
;;
;; 実例(2026-10-07 13:18:20 JST): 本番の worker agent-worker-2 の生きている印が coordinator で false に瞬いた(lease 10 秒を越えて
;; heartbeat が届かなかった)が、worker の拍の遅れの行(TICK-LAG-MS 5 秒 — 拍 1 つの中だけを測る)は出なかった。heartbeat は拍の中の
;; ReadDesired でしか送られず、heartbeat を挟む隣り合う 2 つの拍がそれぞれ 5 秒未満でも、heartbeat の間は 10 秒を越えうる。
;;   - 隣り合う 2 つの拍がどちらも 5 秒未満(拍の遅れの行は出ない)で、heartbeat の間が 10.5 秒 → heartbeat の間の遅れの行が 1 つ出て、
;;     間の ms・worker の名・UTC の時刻・いちばん長かった区間の名(前の拍の heartbeat の返事の待ち)を名乗る。
;;   - 間が 6 秒(窓 10 秒の 7 割未満)なら出ない。
;;   - 間に heartbeat を送らない拍を挟んでも、前の送りから数える(区間の名は前の拍より前の部分 EarlierTicks)。
;;   - coordinator への口(worker/protocol/coordinator_link)は、送った読みに送りの事実(刻・名・返事の timing の lease_ms)を載せる。
;; 反例 = 直す前の形(heartbeat の間を測らない)は、上の 1 つ目と 3 つ目で行が 0 — 断言が赤(直す前の commit で確かめた)。
;; 土台: worker-tick を宿の代役(beat-world)と仮想の時計の下で続けて回す(本物の sleep は使わない)。
(require doeff-hy.macros [deftest defk deff defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import datetime [datetime timedelta])
(import httpx)
(import doeff [run with-handlers])
(import doeff_core_effects.effects [SlogEffect])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.intent.worker_model [WorldView WorkerPolicy WorkerState DesiredJobs DesiredUnreadable HeartbeatSent
                                                 ReadDesired ObserveWorld EnvReport PublishStatus])
(import doeff_cluster.worker.core.program [worker-tick TICK-LAG-LOG HEARTBEAT-GAP-LOG])
(import tests.link_rig [LinkRig])

(val POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500))
(val WORKER "agent-worker-2")
(val LEASE-MS 10000)


(defrecord BeatTick
  "拍 1 つの台本: env-seconds = EnvReport が待つ秒・reply-seconds = heartbeat を送ってから返事が届くまでの秒(ReadDesired の残り)・
   beats = この拍で heartbeat を送るか・pause-seconds = この拍の後、次の拍までの待ち。"
  (#^ float env-seconds)
  (#^ float reply-seconds)
  (#^ bool beats)
  (#^ float pause-seconds))


(defrecord GapLine
  "heartbeat の間の遅れの行 1 つの欄。"
  (#^ str at)
  (#^ str worker)
  (#^ int gap-ms)
  (#^ str slowest)
  (#^ int slowest-ms))


(defrecord BeatSeen
  "拍を回した後の見え方: gaps = heartbeat の間の遅れの行・tick-lags = 拍の遅れの行の数・sent = 送った刻(epoch ms)。"
  (#^ (get tuple #(GapLine ...)) gaps)
  (#^ int tick-lags)
  (#^ (get tuple #(int ...)) sent))


(defeffect NotedBeats
  "ここまでの行と送った刻を読む — 検だけの問い。"
  {:fields [] :answer BeatSeen :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect NextTickScript
  "次の拍の台本へ進んで読む(拍の頭の EnvReport が問う)— 検だけの問い。"
  {:fields [] :answer BeatTick :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect CurrentTickScript
  "今の拍の台本を読む(進めない — ReadDesired が問う)— 検だけの問い。"
  {:fields [] :answer BeatTick :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler beat-script [#^ tuple ticks]
  "拍の台本を順に渡す代役。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに拍の台本を変える(Ask で区別できない)。
  (session var index -1)
  (NextTickScript []
    (:= index (+ index 1))
    (resume (get ticks index)))
  (CurrentTickScript []
    (resume (get ticks index))))


(defhandler beat-noted
  "log の手前に立つ代役: heartbeat の間の遅れの行と拍の遅れの行を覚え、他の行は受け流す。送った刻も覚える。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var gaps #())
  (session var tick-lags 0)
  (session var sent #())
  (SlogEffect []
    (val fields effect.kwargs)
    (match effect.msg
      m :if (= m HEARTBEAT-GAP-LOG)
        (:= gaps (+ gaps #((GapLine :at (get fields "at") :worker (get fields "worker") :gap-ms (get fields "gap_ms")
                                    :slowest (get fields "slowest") :slowest-ms (get fields "slowest_ms")))))
      m :if (= m TICK-LAG-LOG) (:= tick-lags (+ tick-lags 1))
      _ None)
    (resume None))
  (ReadDesired [env-report stopping]
    (<- answer (| DesiredJobs DesiredUnreadable) effect)
    (when (is-not answer.sent None)
      (:= sent (+ sent #(answer.sent.at))))
    (resume answer))
  (NotedBeats []
    (resume (BeatSeen :gaps gaps :tick-lags tick-lags :sent sent))))


(defhandler beat-world
  "本番の宿のうち coordinator への口・観測の代役: EnvReport は拍の台本を進めてその秒だけ待ち、ReadDesired は送る拍なら送りの刻を
   読んで返事の秒だけ待ち、空の宣言に送りの事実を載せて返す。観測は空・状態の報告は何もしない。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (EnvReport []
    (<- tick BeatTick (NextTickScript))
    (<- (Delay tick.env-seconds))
    (resume None))
  (ReadDesired [env-report stopping]
    (<- tick BeatTick (CurrentTickScript))
    (if tick.beats
        (do (<- at int (now-epoch-ms))
            (<- (Delay tick.reply-seconds))
            (resume (DesiredJobs #() :sent (HeartbeatSent :at at :worker WORKER :lease-ms LEASE-MS))))
        (resume (DesiredJobs #()))))
  (ObserveWorld []
    (resume (WorldView #() #())))
  (PublishStatus [statuses note]
    (resume None)))


(defk ticks-in-a-row [script]
  {:pre [(: script tuple)] :post [(: % BeatSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の拍を台本の数だけ続けて回し(拍の間は台本の秒だけ仮想の時計で待つ)、見え方を返すため。"
  (var worker-state (WorkerState))
  (for [tick script]
    (<- ticked tuple (worker-tick worker-state POLICY False))
    (:= worker-state (get ticked 0))
    (<- (Delay tick.pause-seconds)))
  (<- seen BeatSeen (NotedBeats))
  seen)


(defk beats-on [script]
  {:pre [(: script tuple)] :post [(: % BeatSeen)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "台本の拍を宿の代役と仮想の時計の下で回すため。"
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) (beat-script script) beat-world beat-noted]
                                 (ticks-in-a-row script)))))


(deftest test-two-short-ticks-around-a-long-heartbeat-gap-name-the-gap
  ;; 拍 1: 送った後に返事を 4.5 秒待つ(拍 4.5 秒)・拍の間 2 秒・拍 2: EnvReport が 4 秒待ってから送る(拍 4 秒)。どちらの拍も
  ;; 拍の遅れの線 5 秒の内なのに、heartbeat の間は 10.5 秒(窓 10 秒を越える)— 行が 1 つ出て、前の拍の返事の待ちを名指す。
  (<- seen BeatSeen (beats-on #((BeatTick :env-seconds 0.0 :reply-seconds 4.5 :beats True :pause-seconds 2.0)
                                (BeatTick :env-seconds 4.0 :reply-seconds 0.0 :beats True :pause-seconds 0.0))))
  (assert (= seen.tick-lags 0) seen)
  (assert (= (len seen.sent) 2) seen)
  (assert (= (len seen.gaps) 1) seen)
  (val gap (get seen.gaps 0))
  (assert (= gap.gap-ms (- (get seen.sent 1) (get seen.sent 0)) 10500) gap)
  (assert (= gap.worker WORKER) gap)
  (assert (= gap.slowest "Previous.ReadDesired") gap)
  (assert (= gap.slowest-ms 4500) gap)
  (val at (datetime.fromisoformat gap.at))
  (assert (= (.utcoffset at) (timedelta 0)) gap)
  (assert (= (int (* (.timestamp at) 1000)) (get seen.sent 1)) gap))


(deftest test-a-heartbeat-gap-under-seven-tenths-of-the-lease-is-quiet
  ;; 返事 2 秒・拍の間 1 秒・EnvReport 3 秒 → heartbeat の間 6 秒(窓 10 秒の 7 割 = 7 秒未満)— 行は出ない。
  (<- seen BeatSeen (beats-on #((BeatTick :env-seconds 0.0 :reply-seconds 2.0 :beats True :pause-seconds 1.0)
                                (BeatTick :env-seconds 3.0 :reply-seconds 0.0 :beats True :pause-seconds 0.0))))
  (assert (= (len seen.sent) 2) seen)
  (assert (= (- (get seen.sent 1) (get seen.sent 0)) 6000) seen)
  (assert (= seen.gaps #()) seen))


(deftest test-a-tick-without-a-heartbeat-in-between-counts-from-the-last-heartbeat
  ;; 拍 1 が送り(返事 3 秒)・拍 2 は送らない(EnvReport 2 秒)・拍 3 が EnvReport 1 秒の後に送る(拍の間はどれも 1 秒)→ heartbeat の間は
  ;; 0 → 8 秒(7 秒を越える)。いちばん長いのは前の拍(拍 2)の頭より前の部分 EarlierTicks(0 → 4 秒)。
  (<- seen BeatSeen (beats-on #((BeatTick :env-seconds 0.0 :reply-seconds 3.0 :beats True :pause-seconds 1.0)
                                (BeatTick :env-seconds 2.0 :reply-seconds 0.0 :beats False :pause-seconds 1.0)
                                (BeatTick :env-seconds 1.0 :reply-seconds 0.0 :beats True :pause-seconds 0.0))))
  (assert (= (len seen.sent) 2) seen)
  (assert (= (len seen.gaps) 1) seen)
  (val gap (get seen.gaps 0))
  (assert (= gap.gap-ms 8000) gap)
  (assert (= gap.slowest "EarlierTicks") gap)
  (assert (= gap.slowest-ms 4000) gap))


;; 偽の coordinator が返事の timing に載せる生存の窓(既定の 10 秒と違う値 — 送りの事実が返事の窓を読んだと分かる)。
(val REPLY-LEASE-MS 8000)


(deff lease-reply [#^ httpx.Request request]  ; defk にできない: httpx の MockTransport が呼ぶ callback
  {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "heartbeat に生存の窓 REPLY-LEASE-MS を載せた返事をする偽の coordinator。"
  (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" [] "draining" False
                             "timing" {"lease_ms" REPLY-LEASE-MS "fence_ms" 20000}}))


(deftest test-the-coordinator-link-tells-the-heartbeat-it-sent [tmp-path]
  ;; 本物の coordinator への口: 送った読みに送りの事実を載せる。最初の送りは返事の前なので既定の窓、次の送りは返事の timing の窓。
  (val link (LinkRig "http://coord" "w-gap" #() 1 0 20000 :task-dir (str (/ tmp-path "tasks"))
                     :transport (httpx.MockTransport lease-reply)))
  (val earlier (.poll link))
  (val later (.poll link))
  (assert (isinstance earlier DesiredJobs) earlier)
  (assert (is-not earlier.sent None) earlier)
  (assert (= earlier.sent.worker "w-gap") earlier.sent)
  (assert (= earlier.sent.lease-ms LEASE-MS) earlier.sent)
  (assert (is-not later.sent None) later)
  (assert (= later.sent.lease-ms REPLY-LEASE-MS) later.sent)
  (assert (>= later.sent.at earlier.sent.at) #(earlier.sent later.sent)))
