;; heartbeat の間の遅れの計りが、壁の時計が周の途中で戻っても窓に迫った間を名指す事の検(#3825 — #3850 の計りの同じ弱さ)。
;;
;; 実例の形: 前の heartbeat を送ってから今の heartbeat を送るまでの間を、送った刻(壁の時計)の差で計ると、間に時刻が戻った時に間が
;; 縮み、実際は窓の 7 割を越えた間が行を出さない。間の計りは単調な時計(GetMonotonic)で読む。
;; 台本: 仮想の時計の下で、GetTime の答えだけを「最初の読みから n 秒後の読みから b 秒戻した値」にする(GetMonotonic はそのまま仮想の時計が答える)。
;; 拍の台本と宿の代役は test_worker_heartbeat_gap の形(実例と同じ 2 拍: 返事の待ち 4.5 秒・拍の間 2 秒・EnvReport 4 秒 → 間は 10.5 秒)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import datetime [datetime timedelta])
(import doeff [run with-handlers])
(import doeff_core_effects.effects [SlogEffect])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [Delay GetMonotonic GetTimeEffect SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms monotonic-ms-of])
(import doeff_cluster.worker.intent.worker_model [WorldView WorkerPolicy WorkerState DesiredJobs HeartbeatSent
                                                 ReadDesired ObserveWorld EnvReport PublishStatus])
(import doeff_cluster.worker.core.program [worker-tick HEARTBEAT-GAP-LOG])

(val POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500))
(val WORKER "agent-worker-2")
(val LEASE-MS 10000)
;; 最初の読みから何秒後の読みから壁の時計が戻るか。1 拍目(返事の待ち 4.5 秒)が終わり、拍の間(2 秒)の最中 = 2 拍目の頭より前。
(val STEP-AFTER-SECONDS 5.0)


(defrecord BeatTick
  "拍 1 つの台本: env-seconds = EnvReport が待つ秒・reply-seconds = heartbeat を送ってから返事が届くまでの秒・pause-seconds = この拍の後、
   次の拍までの待ち。どの拍も heartbeat を送る。"
  (#^ float env-seconds)
  (#^ float reply-seconds)
  (#^ float pause-seconds))


(defrecord GapLine
  "heartbeat の間の遅れの行 1 つの欄。"
  (#^ int gap-ms)
  (#^ str slowest)
  (#^ int slowest-ms))


(defeffect NotedGaps
  "ここまでの heartbeat の間の遅れの行を読む — 検だけの問い。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect NextTickScript
  "次の拍の台本へ進んで読む(拍の頭の EnvReport が問う)— 検だけの問い。"
  {:fields [] :answer BeatTick :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect CurrentTickScript
  "今の拍の台本を読む(進めない — ReadDesired が問う)— 検だけの問い。"
  {:fields [] :answer BeatTick :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler wall-clock-stepped [#^ float after-seconds #^ float back-seconds]
  "外の世界の時計の手前に立つ代役: 最初の GetTime の読みから after-seconds 後の読みからは、答えを back-seconds 戻す(壁の時計だけ戻る —
   GetMonotonic は通す)。宿の代役の読み(送りの刻)も拍の読みも変えるよう、宿の代役の外に置く。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに壁の時計が戻る刻と幅を変える(Ask で区別できない)。
  (session var origin None)
  (GetTimeEffect []
    (<- now datetime effect)
    (when (is origin None) (:= origin now))
    (val stepped (>= (- now origin) (timedelta :seconds after-seconds)))
    (resume (if stepped (- now (timedelta :seconds back-seconds)) now))))


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


(defhandler gap-noted
  "log の手前に立つ代役: heartbeat の間の遅れの行を覚え、他の行は受け流す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var gaps #())
  (SlogEffect []
    (val fields effect.kwargs)
    (match effect.msg
      m :if (= m HEARTBEAT-GAP-LOG)
        (:= gaps (+ gaps #((GapLine :gap-ms (get fields "gap_ms") :slowest (get fields "slowest") :slowest-ms (get fields "slowest_ms")))))
      _ None)
    (resume None))
  (NotedGaps []
    (resume gaps)))


(defhandler beat-world
  "本番の宿のうち coordinator への口・観測の代役: EnvReport は拍の台本を進めてその秒だけ待ち、ReadDesired は送りの刻を読んで返事の秒だけ
   待ち、空の宣言に送りの事実を載せて返す。観測は空・状態の報告は何もしない。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (EnvReport []
    (<- tick BeatTick (NextTickScript))
    (<- (Delay tick.env-seconds))
    (resume None))
  (ReadDesired [env-report stopping]
    (<- tick BeatTick (CurrentTickScript))
    (<- at int (now-epoch-ms))
    (<- mono float (GetMonotonic))
    (<- (Delay tick.reply-seconds))
    (resume (DesiredJobs #() :sends #((HeartbeatSent :at at :mono-ms (monotonic-ms-of mono) :worker WORKER :lease-ms LEASE-MS)))))
  (ObserveWorld []
    (resume (WorldView #() #())))
  (PublishStatus [statuses note]
    (resume None)))


(defk ticks-in-a-row [script]
  {:pre [(: script tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の拍を台本の数だけ続けて回し(拍の間は台本の秒だけ仮想の時計で待つ)、heartbeat の間の遅れの行を返すため。"
  (var worker-state (WorkerState))
  (for [tick script]
    (<- ticked tuple (worker-tick worker-state POLICY False))
    (:= worker-state (get ticked 0))
    (<- (Delay tick.pause-seconds)))
  (<- gaps tuple (NotedGaps))
  gaps)


(defk gaps-on [script back-seconds]
  {:pre [(: script tuple) (: back-seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "台本の拍を宿の代役と仮想の時計の下で、最初の読みから STEP-AFTER-SECONDS 後の読みから壁の時計を back-seconds 戻して回すため。"
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) (wall-clock-stepped STEP-AFTER-SECONDS back-seconds)
                                  (beat-script script) beat-world gap-noted]
                    (ticks-in-a-row script)))))


;; 実例と同じ 2 拍: 拍 1 は送った後に返事を 4.5 秒待つ・拍の間 2 秒・拍 2 は EnvReport が 4 秒待ってから送る → 間は 10.5 秒(窓 10 秒を越える)。
(val TWO-TICKS #((BeatTick :env-seconds 0.0 :reply-seconds 4.5 :pause-seconds 2.0)
                 (BeatTick :env-seconds 4.0 :reply-seconds 0.0 :pause-seconds 0.0)))


(deftest test-a-long-heartbeat-gap-is-named-though-the-wall-clock-stepped-back-inside-it
  ;; 間の 10.5 秒のうち 2 拍目の頭から壁の時計が 5 秒戻る: 間は実際に 10.5 秒なので、行が 1 つ出て前の拍の返事の待ちを名指す。
  ;; 送った刻(壁の時計)の差で計ると間が 5.5 秒(窓の 7 割 = 7 秒未満)に縮み、行が出ない。
  (<- gaps tuple (gaps-on TWO-TICKS 5.0))
  (assert (= (len gaps) 1) gaps)
  (val gap (get gaps 0))
  (assert (= gap.gap-ms 10500) gap)
  (assert (= gap.slowest "Previous.ReadDesired") gap)
  (assert (= gap.slowest-ms 4500) gap))


(deftest test-a-quiet-heartbeat-gap-stays-quiet-though-the-wall-clock-stepped-forward
  ;; 対照の反対向き: 壁の時計が 5 秒進んでも、実際の間 6 秒(窓の 7 割未満)は行を出さない。送った刻の差で計ると間が 11 秒に伸び、
  ;; 窓を越えた間と見なして行が出る。
  (<- gaps tuple (gaps-on #((BeatTick :env-seconds 0.0 :reply-seconds 2.0 :pause-seconds 1.0)
                            (BeatTick :env-seconds 3.0 :reply-seconds 0.0 :pause-seconds 0.0))
                          -5.0))
  (assert (= gaps #()) gaps))


(deftest test-a-heartbeat-gap-is-named-as-before-without-a-step
  ;; 対照: 壁の時計が戻らない間は、今も同じ名指し(この検は直す前も後も緑)。
  (<- gaps tuple (gaps-on TWO-TICKS 0.0))
  (assert (= (len gaps) 1) gaps)
  (val gap (get gaps 0))
  (assert (= gap.gap-ms 10500) gap)
  (assert (= gap.slowest "Previous.ReadDesired") gap))
