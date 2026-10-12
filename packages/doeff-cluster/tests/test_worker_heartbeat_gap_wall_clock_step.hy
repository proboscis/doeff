;; heartbeat の間の遅れの計りが、壁の時計が周の途中で動いても窓に迫った間を名指す事の検(#3825 — #3850 の計りの同じ弱さ)。
;;
;; 実例の形: 前の heartbeat を送ってから今の heartbeat を送るまでの間を、送った刻(壁の時計)の差で計ると、間に時刻が戻った時に間が
;; 縮み、実際は窓の 7 割を越えた間が行を出さない(進んだ時は逆に、静かな間が行を出す)。間の計りは単調な時計(GetMonotonic)で読む。
;; 台本: 仮想の時計の下で、宿の代役が 2 拍目の頭(EnvReport の待ちの前)に壁の時計だけを動かす(effect StepWallClock — 手前の handler が
;; GetTime の答えをずらし、GetMonotonic はそのまま仮想の時計が答える)。動かす契機を時刻の読みの回数や経過秒にしない。
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


(defrecord BeatTick
  "拍 1 つの台本: env-seconds = EnvReport が待つ秒・reply-seconds = heartbeat を送ってから返事が届くまでの秒・pause-seconds = この拍の後、
   次の拍までの待ち・wall-back-seconds = この拍の頭(EnvReport の待ちの前)に壁の時計を戻す秒(負 = 進める・0 = 動かさない)。どの拍も
   heartbeat を送る。"
  (#^ float env-seconds)
  (#^ float reply-seconds)
  (#^ float pause-seconds)
  (setv #^ float wall-back-seconds 0.0))


(defrecord GapLine
  "heartbeat の間の遅れの行 1 つの欄。"
  (#^ int gap-ms)
  (#^ str slowest)
  (#^ int slowest-ms))


(defeffect StepWallClock
  "壁の時計だけを seconds 戻す(負 = 進める)— 検だけの操作。GetMonotonic は動かさない。"
  {:fields [(: seconds float)] :answer None :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect NotedGaps
  "ここまでの heartbeat の間の遅れの行を読む — 検だけの問い。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect NextTickScript
  "次の拍の台本へ進んで読む(拍の頭の EnvReport が問う)— 検だけの問い。"
  {:fields [] :answer BeatTick :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect CurrentTickScript
  "今の拍の台本を読む(進めない — ReadDesired が問う)— 検だけの問い。"
  {:fields [] :answer BeatTick :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler wall-clock-stepped
  "外の世界の時計の手前に立つ代役: StepWallClock の分だけ GetTime の答えをずらす(壁の時計だけ動く — GetMonotonic は通す)。宿の代役の
   読み(送りの刻)も拍の読みも同じくずれるよう、宿の代役の外に置く。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var back-seconds 0.0)
  (StepWallClock [seconds]
    (:= back-seconds (+ back-seconds seconds))
    (resume None))
  (GetTimeEffect []
    (<- now datetime effect)
    (resume (- now (timedelta :seconds back-seconds)))))


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
  "本番の宿のうち coordinator への口・観測の代役: EnvReport は拍の台本を進め、壁の時計を台本の分動かしてからその秒だけ待ち、ReadDesired は
   送りの刻(壁・単調)を読んで返事の秒だけ待ち、空の宣言に送りの事実を載せて返す。観測は空・状態の報告は何もしない。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (EnvReport []
    (<- tick BeatTick (NextTickScript))
    (<- (StepWallClock tick.wall-back-seconds))
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


(defk gaps-on [script]
  {:pre [(: script tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "台本の拍を宿の代役と仮想の時計の下で回し、heartbeat の間の遅れの行を返すため。"
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) wall-clock-stepped (beat-script script) beat-world gap-noted]
                    (ticks-in-a-row script)))))


(defk two-ticks [wall-back-seconds]
  {:pre [(: wall-back-seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "実例と同じ 2 拍の台本: 拍 1 は送った後に返事を 4.5 秒待つ・拍の間 2 秒・拍 2 は EnvReport が 4 秒待ってから送る → 間は 10.5 秒
   (窓 10 秒を越える)。2 拍目の頭に壁の時計を wall-back-seconds 戻す。"
  #((BeatTick :env-seconds 0.0 :reply-seconds 4.5 :pause-seconds 2.0)
    (BeatTick :env-seconds 4.0 :reply-seconds 0.0 :pause-seconds 0.0 :wall-back-seconds wall-back-seconds)))


(deftest test-a-long-heartbeat-gap-is-named-though-the-wall-clock-stepped-back-inside-it
  ;; 間の 10.5 秒の最中(2 拍目の頭)に壁の時計が 5 秒戻る: 間は実際に 10.5 秒なので、行が 1 つ出て前の拍の返事の待ちを名指す。
  ;; 送った刻(壁の時計)の差で計ると間が 5.5 秒(窓の 7 割 = 7 秒未満)に縮み、行が出ない。
  (<- script tuple (two-ticks 5.0))
  (<- gaps tuple (gaps-on script))
  (assert (= (len gaps) 1) gaps)
  (val gap (get gaps 0))
  (assert (= gap.gap-ms 10500) gap)
  (assert (= gap.slowest "Previous.ReadDesired") gap)
  (assert (= gap.slowest-ms 4500) gap))


(deftest test-a-quiet-heartbeat-gap-stays-quiet-though-the-wall-clock-stepped-forward
  ;; 反対向き: 壁の時計が 5 秒進んでも、実際の間 6 秒(窓の 7 割未満)は行を出さない。送った刻の差で計ると間が 11 秒に伸び、
  ;; 窓を越えた間と見なして行が出る。
  (<- gaps tuple (gaps-on #((BeatTick :env-seconds 0.0 :reply-seconds 2.0 :pause-seconds 1.0)
                            (BeatTick :env-seconds 3.0 :reply-seconds 0.0 :pause-seconds 0.0 :wall-back-seconds -5.0))))
  (assert (= gaps #()) gaps))


(deftest test-a-heartbeat-gap-is-named-as-before-without-a-step
  ;; 対照: 壁の時計が動かない間は、今も同じ名指し(この検は直す前も後も緑)。
  (<- script tuple (two-ticks 0.0))
  (<- gaps tuple (gaps-on script))
  (assert (= (len gaps) 1) gaps)
  (val gap (get gaps 0))
  (assert (= gap.gap-ms 10500) gap)
  (assert (= gap.slowest "Previous.ReadDesired") gap))
